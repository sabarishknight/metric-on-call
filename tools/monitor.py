"""Monitor (agent 0, no LLM): watch every KPI slice against a seasonal expectation.

Method (deterministic, unit-tested):
  * ~440 daily series: totals, channel, region, payment, category, store, product, store x product.
  * expected(d) = median of the same weekday over the previous 8 weeks (robust to one bad week).
  * deviation pct = actual / expected - 1; noise sigma = robust MAD of pct over the previous 12 weeks.
  * A day is flagged by a single-day rule or by a 3- or 7-day window rule (catches rain weeks),
    and only if the move is also material (per-level minimum percentage).
  * A weekly drift detector compares each store's share of chain sales with its own recent share
    (catches slow declines that a trailing baseline would absorb).
  * Flags are clustered into incident windows; the broadest flagged series is the headline.

The monitor reads the data AS LOADED (raw tables), exactly like a dashboard would, so a
pipeline bug shows up as a "sales" anomaly. The Data Quality Auditor then decides whether the
number can be trusted.
"""

from __future__ import annotations

import warnings

import numpy as np
import pandas as pd

from tools.data import Warehouse, to_ts
from tools.guards import jsonable

LEVEL_ORDER = ["total", "region", "channel", "payment", "category", "store", "product", "store_product"]

# (level, metric, by-dims, min |pct|, sigma floor)
SERIES_SPEC = [
    ("total", "net_sales", [], 0.04, 0.010),
    ("total", "transactions", [], 0.04, 0.010),
    ("total", "visits", [], 0.04, 0.010),
    ("total", "avg_basket", [], 0.03, 0.008),
    ("channel", "net_sales", ["channel"], 0.06, 0.012),
    ("channel", "avg_basket", ["channel"], 0.04, 0.010),
    ("region", "net_sales", ["region_id"], 0.06, 0.012),
    ("region", "avg_basket", ["region_id"], 0.04, 0.010),
    ("payment", "net_sales", ["payment_type"], 0.06, 0.012),
    ("category", "net_sales", ["category"], 0.06, 0.012),
    ("store", "net_sales", ["store_id"], 0.12, 0.015),
    ("product", "units", ["product_id"], 0.08, 0.020),
    ("product", "avg_price_per_unit", ["product_id"], 0.03, 0.005),
    ("store_product", "units", ["store_id", "product_id"], 0.40, 0.040),
]

Z1, Z3, Z7 = 5.0, 3.5, 3.0
MAX_RUN_DAYS = 21  # a permanent level shift is reported once, not until the baseline catches up
Z_CORE = 4.0  # a cluster must contain at least one day with a flagged series at |z| >= this
LEVEL_Z_FACTOR = {"store_product": 1.4, "store": 1.0}
HISTORY_WEEKS = 8  # same-weekday median over the previous 8 weeks
SIGMA_DAYS = 84  # robust noise estimate over up to 12 weeks
MIN_SIGMA_DAYS = 28
FIRST_SCORED_DAY = 7 * HISTORY_WEEKS + MIN_SIGMA_DAYS  # 84 (= 2025-06-23)


def build_matrix(wh: Warehouse) -> tuple[np.ndarray, pd.DataFrame, pd.DatetimeIndex]:
    dates = wh.all_dates()
    rows, meta = [], []
    for level, metric, by, minpct, floor in SERIES_SPEC:
        df = wh.aggregate([metric], by, daily=True)
        if by:
            df["slice"] = df[by].astype(str).agg("|".join, axis=1)
            piv = df.pivot_table(index="slice", columns="date", values=metric, aggfunc="sum")
        else:
            df["slice"] = "all"
            piv = df.pivot_table(index="slice", columns="date", values=metric, aggfunc="sum")
        piv = piv.reindex(columns=dates)
        if metric in ("net_sales", "transactions", "units", "visits"):
            piv = piv.fillna(0.0)
        for sl, vals in piv.iterrows():
            rows.append(vals.to_numpy(dtype=float))
            meta.append(
                {"level": level, "metric": metric, "slice": sl, "by": ",".join(by), "minpct": minpct, "floor": floor}
            )
    return np.vstack(rows), pd.DataFrame(meta), dates


def expected_matrix(X: np.ndarray) -> np.ndarray:
    n, D = X.shape
    stack = np.full((HISTORY_WEEKS, n, D), np.nan)
    for k in range(1, HISTORY_WEEKS + 1):
        if 7 * k < D:
            stack[k - 1, :, 7 * k :] = X[:, : D - 7 * k]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        return np.nanmedian(stack, axis=0)


def deviation(X: np.ndarray, E: np.ndarray) -> np.ndarray:
    with np.errstate(divide="ignore", invalid="ignore"):
        pct = np.where(E > 1e-9, X / E - 1.0, 0.0)
    return np.nan_to_num(pct, nan=0.0, posinf=0.0, neginf=0.0)


def sigma_matrix(pct: np.ndarray, floors: np.ndarray) -> np.ndarray:
    n, D = pct.shape
    sig = np.tile(floors[:, None], (1, D)).astype(float)
    start = 7 * HISTORY_WEEKS
    for d in range(start + MIN_SIGMA_DAYS, D):
        w = pct[:, max(start, d - SIGMA_DAYS) : d]
        med = np.median(w, axis=1)
        mad = np.median(np.abs(w - med[:, None]), axis=1) * 1.4826
        sig[:, d] = np.maximum(mad, floors)
    return sig


def flag_days(pct: np.ndarray, sig: np.ndarray, minpct: np.ndarray, zfac: np.ndarray) -> np.ndarray:
    n, D = pct.shape
    z = pct / sig
    mp = minpct[:, None]
    zf = zfac[:, None]
    F = (np.abs(z) >= Z1 * zf) & (np.abs(pct) >= mp)
    for w, zt, frac, day_z in ((3, Z3, 1.0, 2.0), (7, Z7, 0.6, 1.5)):
        cz = np.cumsum(np.pad(z, ((0, 0), (1, 0))), axis=1)
        cp = np.cumsum(np.pad(pct, ((0, 0), (1, 0))), axis=1)
        mz = (cz[:, w:] - cz[:, :-w]) / w  # mean over window ending at index e = i + w - 1
        mpct = (cp[:, w:] - cp[:, :-w]) / w
        cond = (np.abs(mz) >= zt * zf) & (np.abs(mpct) >= mp * frac)
        for j in range(w):
            zj = z[:, j : j + cond.shape[1]]
            day_ok = (
                (np.sign(zj) == np.sign(mz))
                & (np.abs(zj) >= day_z)
                & (np.abs(pct[:, j : j + cond.shape[1]]) >= 0.8 * mp * frac)
            )
            F[:, j : j + cond.shape[1]] |= cond & day_ok
    F[:, :FIRST_SCORED_DAY] = False
    return cap_runs(F, MAX_RUN_DAYS)


def cap_runs(F: np.ndarray, max_len: int) -> np.ndarray:
    out = F.copy()
    for r in np.flatnonzero(F.any(axis=1)):
        run = 0
        for d in range(F.shape[1]):
            run = run + 1 if F[r, d] else 0
            if run > max_len:
                out[r, d] = False
    return out


def drift_flags(wh: Warehouse, dates: pd.DatetimeIndex) -> list[dict]:
    """Weekly store share of chain sales vs the store's own share 5-12 weeks earlier."""
    wk = wh.aggregate(["net_sales"], ["store_id"], daily=True)
    wk["week"] = wk["date"] - pd.to_timedelta(wk["date"].dt.weekday, unit="D")
    full = wk.groupby(["week", "store_id"], as_index=False)["net_sales"].sum()
    piv = full.pivot(index="week", columns="store_id", values="net_sales").sort_index()
    # only complete weeks
    piv = piv[[True if (w + pd.Timedelta(days=6)) <= dates[-1] else False for w in piv.index]]
    share = piv.div(piv.sum(axis=1), axis=0)
    out = []
    weeks = list(share.index)
    drift = pd.DataFrame(index=share.index, columns=share.columns, dtype=float)
    for i, _w in enumerate(weeks):
        if i < 12:
            continue
        base = share.iloc[i - 12 : i - 4].median()
        drift.iloc[i] = share.iloc[i] / base - 1
    low = drift <= -0.07
    for s in drift.columns:
        flagged = low[s].to_numpy()
        for i, w in enumerate(weeks):
            if flagged[i] and (
                (i > 0 and flagged[i - 1]) or (i + 1 < len(weeks) and flagged[i + 1])
            ):
                out.append({"store_id": s, "week": w, "pct": float(drift.iloc[i][s])})
    return out


def _cluster(days: np.ndarray, gap: int = 1) -> list[tuple[int, int]]:
    idx = np.flatnonzero(days)
    if len(idx) == 0:
        return []
    groups, start, prev = [], idx[0], idx[0]
    for i in idx[1:]:
        if i - prev > gap + 1:
            groups.append((start, prev))
            start = i
        prev = i
    groups.append((start, prev))
    return groups


def scan(wh: Warehouse) -> list[dict]:
    """Return every incident the monitor raises over the whole history, oldest first."""
    cached = getattr(wh, "_scan_cache", None)
    if cached is not None:
        return cached
    X, meta, dates = build_matrix(wh)
    E = expected_matrix(X)
    pct = deviation(X, E)
    sig = sigma_matrix(pct, meta["floor"].to_numpy())
    zfac = meta["level"].map(LEVEL_Z_FACTOR).fillna(1.0).to_numpy()
    F = flag_days(pct, sig, meta["minpct"].to_numpy(), zfac)
    z = pct / sig
    core = (F & (np.abs(z) >= Z_CORE * zfac[:, None])).any(axis=0)

    incidents: list[dict] = []
    for a, b in _cluster(core):
        rows = np.flatnonzero(F[:, a : b + 1].any(axis=1))
        series = []
        for r in rows:
            m = F[r, a : b + 1]
            days = np.arange(a, b + 1)[m]
            series.append(
                {
                    "level": meta.at[r, "level"],
                    "metric": meta.at[r, "metric"],
                    "slice": meta.at[r, "slice"],
                    "flagged_days": int(m.sum()),
                    "observed": float(X[r, days].sum()),
                    "expected": float(E[r, days].sum()),
                    "pct_dev": float(X[r, days].sum() / E[r, days].sum() - 1) if E[r, days].sum() > 0 else None,
                    "max_abs_z": float(np.abs(z[r, days]).max()),
                    "_r": int(r),
                }
            )
        series.sort(key=lambda s: (LEVEL_ORDER.index(s["level"]), -s["max_abs_z"]))
        head = series[0]
        top = sorted(series, key=lambda s: -s["max_abs_z"])[:12]
        incidents.append(
            {
                "incident_id": f"INC-F-{dates[a]:%Y%m%d}",
                "kind": "forecast",
                "date_from": dates[a],
                "date_to": dates[b],
                "n_series_flagged": len(series),
                "headline": {k: head[k] for k in ("level", "metric", "slice", "observed", "expected", "pct_dev", "max_abs_z")},
                "top_series": [{k: v for k, v in s.items() if k != "_r"} for s in top],
                "direction": "up" if (head["pct_dev"] or 0) > 0 else "down",
                "severity": float(max(s["max_abs_z"] for s in series)),
            }
        )

    # weekly drift -> separate incidents (per run of consecutive flagged weeks)
    drifts = drift_flags(wh, dates)
    if drifts:
        df = pd.DataFrame(drifts)
        wk_flag = {w: g for w, g in df.groupby("week")}
        weeks = sorted(wk_flag)
        run: list[pd.Timestamp] = []
        runs: list[list[pd.Timestamp]] = []
        for w in weeks:
            if run and (w - run[-1]).days > 14:
                runs.append(run)
                run = []
            run.append(w)
        if run:
            runs.append(run)
        for r in runs:
            sub = df[df["week"].isin(r)]
            by_store = sub.groupby("store_id")["pct"].mean().sort_values()
            incidents.append(
                {
                    "incident_id": f"INC-D-{r[0]:%Y%m%d}",
                    "kind": "drift",
                    "date_from": r[0],
                    "date_to": r[-1] + pd.Timedelta(days=6),
                    "n_series_flagged": int(len(by_store)),
                    "headline": {
                        "level": "store_drift",
                        "metric": "share_of_chain_net_sales",
                        "slice": "|".join(by_store.index[:4]),
                        "observed": None,
                        "expected": None,
                        "pct_dev": float(by_store.iloc[0]),
                        "max_abs_z": None,
                    },
                    "top_series": [
                        {"level": "store_drift", "metric": "share_of_chain_net_sales", "slice": s, "pct_dev": float(p)}
                        for s, p in by_store.head(12).items()
                    ],
                    "direction": "down",
                    "severity": float(abs(by_store.iloc[0]) * 50),
                }
            )
    incidents.sort(key=lambda i: (i["date_from"], i["incident_id"]))
    out = jsonable(incidents)
    wh._scan_cache = out  # type: ignore[attr-defined]
    return out


def incidents_between(wh: Warehouse, d0, d1) -> list[dict]:
    a, b = to_ts(d0), to_ts(d1)
    return [
        i
        for i in scan(wh)
        if pd.Timestamp(i["date_from"]) <= b and pd.Timestamp(i["date_to"]) >= a
    ]
