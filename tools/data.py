"""Read-only data access for the analysis tools.

`Warehouse` wraps the tables as pandas frames. Tools never take SQL; they call the typed methods
below. Swapping DuckDB for BigQuery later means re-implementing only this class.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable

import numpy as np
import pandas as pd

from common.store import load_tables
from tools.metrics import ADDITIVE, RATIOS, add_ratios

TXN_DIMS = {"store_id", "region_id", "channel", "daypart", "payment_type"}
LINE_DIMS = {"store_id", "region_id", "channel", "product_id", "category"}
VISIT_DIMS = {"store_id", "region_id", "channel", "daypart"}
DIMENSIONS = sorted(TXN_DIMS | LINE_DIMS)


def to_ts(x) -> pd.Timestamp:
    return pd.Timestamp(x).normalize()


class Warehouse:
    def __init__(self, tables: dict[str, pd.DataFrame] | None = None):
        t = tables if tables is not None else load_tables()
        self.tables = t
        self.stores = t["dim_store"]
        self.products = t["dim_product"]
        reg = self.stores[["store_id", "region_id"]]
        self.txn = t["raw_sales_txn"].merge(reg, on="store_id", how="left")
        self.lines = (
            t["raw_sales_lines"]
            .merge(reg, on="store_id", how="left")
            .merge(self.products[["product_id", "category"]], on="product_id", how="left")
        )
        self.visits = t["raw_visits"].merge(reg, on="store_id", how="left")
        self.ctx = {k: v for k, v in t.items() if k.startswith("ctx_")}
        self.load_log = t["raw_load_log"]
        self.first_date = self.txn["date"].min()
        self.last_date = self.txn["date"].max()

    # ---- helpers ----------------------------------------------------------------------
    def all_dates(self) -> pd.DatetimeIndex:
        return pd.date_range(self.first_date, self.last_date, freq="D")

    def region_of(self, store_id: str) -> str:
        return self.stores.loc[self.stores["store_id"] == store_id, "region_id"].iloc[0]

    def stores_in(self, region_id: str) -> list[str]:
        return self.stores.loc[self.stores["region_id"] == region_id, "store_id"].tolist()

    # ---- core aggregation -------------------------------------------------------------
    @staticmethod
    def _filter(df: pd.DataFrame, filters: dict | None) -> pd.DataFrame:
        if not filters:
            return df
        m = pd.Series(True, index=df.index)
        for k, v in filters.items():
            if k not in df.columns:
                raise ValueError(f"cannot filter by '{k}' on this grain")
            m &= df[k].isin(list(v) if isinstance(v, (list, tuple, set)) else [v])
        return df[m]

    def _source(self, base: str, dims: set[str]) -> tuple[pd.DataFrame, set[str]]:
        if dims & {"product_id", "category"}:
            if base not in ("net_sales", "units"):
                raise ValueError(f"'{base}' is not defined by product or category")
            return self.lines, LINE_DIMS
        if base == "visits":
            if dims - VISIT_DIMS:
                raise ValueError("visits can only be split by store, region, channel, daypart")
            return self.visits, VISIT_DIMS
        return self.txn, TXN_DIMS

    def aggregate(
        self,
        metrics: Iterable[str],
        by: Iterable[str] = (),
        date_from=None,
        date_to=None,
        filters: dict | None = None,
        daily: bool = False,
    ) -> pd.DataFrame:
        by = list(by)
        d0 = to_ts(date_from) if date_from is not None else self.first_date
        d1 = to_ts(date_to) if date_to is not None else self.last_date
        needed: set[str] = set()
        for m in metrics:
            if m in ADDITIVE:
                needed.add(m)
            elif m in RATIOS:
                needed.update(RATIOS[m])
            else:
                raise ValueError(f"unknown metric '{m}'")
        dims = set(by) | set((filters or {}).keys())
        group = (["date"] if daily else []) + by
        frames = []
        for base in sorted(needed):
            src, allowed = self._source(base, dims)
            if dims - allowed:
                raise ValueError(f"'{base}' cannot be split by {sorted(dims - allowed)}")
            s = self._filter(src[src["date"].between(d0, d1)], filters)
            if group:
                frames.append(s.groupby(group, as_index=False)[base].sum())
            else:
                frames.append(pd.DataFrame({base: [s[base].sum()]}))
        out = frames[0]
        for f in frames[1:]:
            out = out.merge(f, on=group, how="outer") if group else pd.concat([out, f], axis=1)
        out = add_ratios(out.fillna(0.0) if not group else out.fillna({c: 0.0 for c in needed}))
        keep = group + [m for m in metrics]
        return out[keep]

    # ---- baselines --------------------------------------------------------------------
    def baseline_windows(self, d0, d1, n: int = 8) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
        d0, d1 = to_ts(d0), to_ts(d1)
        out = []
        for k in range(1, n + 1):
            a, b = d0 - pd.Timedelta(days=7 * k), d1 - pd.Timedelta(days=7 * k)
            if a >= self.first_date:
                out.append((a, b))
        return out

    def baseline(
        self,
        metrics: list[str],
        d0,
        d1,
        by: Iterable[str] = (),
        filters: dict | None = None,
        n: int = 8,
    ) -> pd.DataFrame:
        """Median over the n previous same-weekday windows (robust to a few contaminated weeks)."""
        by = list(by)
        wins = self.baseline_windows(d0, d1, n)
        if not wins:
            raise ValueError("not enough history before the window for a baseline")
        parts = [self.aggregate(metrics, by, a, b, filters) for a, b in wins]
        if not by:
            return pd.DataFrame({m: [float(np.median([p[m].iloc[0] for p in parts]))] for m in metrics})
        allp = pd.concat(parts, ignore_index=True)
        return allp.groupby(by, as_index=False)[metrics].median()

    def compare(
        self,
        metrics: list[str],
        d0,
        d1,
        by: Iterable[str] = (),
        filters: dict | None = None,
        baseline_from=None,
        baseline_to=None,
    ) -> pd.DataFrame:
        """Window vs baseline per group, with absolute and percentage change."""
        by = list(by)
        now = self.aggregate(metrics, by, d0, d1, filters)
        if baseline_from is not None and baseline_to is not None:
            base = self.aggregate(metrics, by, baseline_from, baseline_to, filters)
        else:
            base = self.baseline(metrics, d0, d1, by, filters)
        if by:
            m = now.merge(base, on=by, how="outer", suffixes=("", "__base")).fillna(0.0)
        else:
            m = pd.concat([now, base.add_suffix("__base")], axis=1)
        for k in metrics:
            m[f"{k}__chg"] = m[k] - m[f"{k}__base"]
            m[f"{k}__pct"] = np.where(m[f"{k}__base"] != 0, m[f"{k}__chg"] / m[f"{k}__base"], np.nan)
        return m


    def daily_compare(
        self,
        metric: str,
        d0,
        d1,
        by: Iterable[str] = (),
        filters: dict | None = None,
        n: int = 8,
        anchor=None,
    ) -> pd.DataFrame:
        """One row per (date, *by): actual, same-weekday median baseline, change, pct.

        By default the baseline for a date is the median of the same weekday over the previous n
        weeks. With `anchor` set (e.g. the start of an incident window) every baseline value is
        taken from BEFORE the anchor, so long windows never contaminate their own baseline.
        """
        import warnings

        by = list(by)
        d0, d1 = to_ts(d0), to_ts(d1)
        anchor = to_ts(anchor) if anchor is not None else None
        span_weeks = int(np.ceil(((d1 - (anchor or d0)).days + 1) / 7)) if anchor is not None else 0
        K = n + span_weeks
        lo = max(self.first_date, d0 - pd.Timedelta(days=7 * (K + 1)))
        df = self.aggregate([metric], by, lo, d1, filters, daily=True)
        grid = pd.DataFrame({"date": pd.date_range(lo, d1)})
        if by:
            keys = df[by].drop_duplicates()
            df = keys.merge(grid, how="cross").merge(df, on=["date", *by], how="left")
        else:
            df = grid.merge(df, on="date", how="left")
        if metric in ADDITIVE:
            df[metric] = df[metric].fillna(0.0)
        parts = []
        groups = df.groupby(by) if by else [((), df)]
        for _, g in groups:
            g = g.sort_values("date").reset_index(drop=True)
            vals = g[metric].to_numpy(dtype=float)
            L = len(g)
            if anchor is None:
                kmin = np.ones(L, dtype=int)
            else:
                days_in = (g["date"] - anchor).dt.days.to_numpy()
                kmin = np.where(days_in >= 0, days_in // 7 + 1, 1)
            stack = np.full((K, L), np.nan)
            for k in range(1, K + 1):
                if 7 * k < L:
                    row = np.full(L, np.nan)
                    row[7 * k :] = vals[: L - 7 * k]
                    use = (k >= kmin) & (k < kmin + n)
                    stack[k - 1, use] = row[use]
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", category=RuntimeWarning)
                g["baseline"] = np.nanmedian(stack, axis=0)
            parts.append(g)
        out = pd.concat(parts, ignore_index=True)
        out = out[(out["date"] >= d0) & (out["date"] <= d1)].copy()
        out = out.rename(columns={metric: "actual"})
        out["chg"] = out["actual"] - out["baseline"]
        out["pct"] = np.where(out["baseline"] > 0, out["chg"] / out["baseline"], np.nan)
        return out.reset_index(drop=True)

    # ---- context ----------------------------------------------------------------------
    def overlapping(self, name: str, d0, d1, store_ids: list[str] | None = None) -> pd.DataFrame:
        df = self.ctx[name]
        d0, d1 = to_ts(d0), to_ts(d1)
        if "date_from" in df:
            m = (df["date_from"] <= d1) & (df["date_to"] >= d0)
        elif "effective_date" in df:
            m = df["effective_date"] <= d1  # a price change stays in force after it happens
        else:
            m = df["date"].between(d0, d1)
        out = df[m]
        if store_ids is not None and "store_id" in out:
            out = out[out["store_id"].isin(store_ids + ["ALL"]) | out["store_id"].isna()]
        return out


def week_start(d) -> pd.Timestamp:
    d = to_ts(d)
    return d - pd.Timedelta(days=d.weekday())


def iso(d) -> str:
    return dt.date.fromisoformat(str(pd.Timestamp(d).date())).isoformat()
