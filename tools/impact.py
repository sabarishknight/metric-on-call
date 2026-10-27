"""Impact and Forecast tools: "so what is it costing us?"

Counterfactual vs actual in dollars, split by cause where there is more than one, plus a
month-end projection for effects that persist.

Method: for each affected store-day, counterfactual = same-weekday median of the 8 weeks BEFORE
the window, scaled by the day's common movement across unaffected stores (so a rainy day, or a
chain-wide shock, is not blamed on the local cause). Impact = actual - counterfactual.
"""

from __future__ import annotations

import calendar

import numpy as np
import pandas as pd

from tools import hypotheses as H
from tools.data import Warehouse, to_ts
from tools.guards import jsonable

PERSISTENT = {"price_change", "external_unknown"}
DATA_CAUSES = {"pipeline_duplicate_load", "pipeline_missing_data"}


def _store_days(wh: Warehouse, d0, d1) -> pd.DataFrame:
    return wh.daily_compare("net_sales", d0, d1, by=["store_id"], anchor=d0)


def _control_factor(sd: pd.DataFrame, exclude_stores: list[str]) -> pd.Series:
    c = sd[~sd["store_id"].isin(exclude_stores)].groupby("date")[["actual", "baseline"]].sum()
    return (c["actual"] / c["baseline"]).replace([np.inf, -np.inf], np.nan).fillna(1.0)


def _delta(sd: pd.DataFrame, mask: pd.Series, factor: pd.Series) -> float:
    rows = sd[mask].copy()
    rows["f"] = rows["date"].map(factor).fillna(1.0)
    return float((rows["actual"] - rows["baseline"] * rows["f"]).sum())


def _chain_delta(sd: pd.DataFrame) -> float:
    g = sd.groupby("date")[["actual", "baseline"]].sum()
    return float((g["actual"] - g["baseline"]).sum())


def _impact_for(wh: Warehouse, h: dict, d0, d1, sd: pd.DataFrame) -> tuple[float | None, str]:
    cat, ev = h["category"], h["evidence"]
    if cat == "pos_outage":
        pairs = {(r["store_id"], str(pd.Timestamp(r["date"]).date())) for r in ev["store_days"]}
        mask = sd.apply(lambda r: (r["store_id"], str(r["date"].date())) in pairs, axis=1)
        return _delta(sd, mask, _control_factor(sd, ev["stores"])), "affected store-days vs control-adjusted baseline"
    if cat in ("planned_closure", "unplanned_closure"):
        mask = sd["store_id"].isin(ev["stores"]) & (sd["actual"] == 0)
        return _delta(sd, mask, _control_factor(sd, ev["stores"])), "closed store-days vs control-adjusted baseline"
    if cat == "partner_outage":
        dl = wh.daily_compare("net_sales", d0, d1, filters={"channel": "delivery"}, anchor=d0)
        ins = wh.daily_compare("net_sales", d0, d1, filters={"channel": "in_store"}, anchor=d0).set_index("date")
        factor = (ins["actual"] / ins["baseline"]).fillna(1.0)
        dl["f"] = dl["date"].map(factor).fillna(1.0)
        dl = dl[dl["date"].isin(pd.to_datetime(ev["dates"]))]
        return float((dl["actual"] - dl["baseline"] * dl["f"]).sum()), "delivery channel vs in-store-adjusted baseline"
    if cat == "stockout":
        mask = sd["store_id"].isin(ev["stores"])
        return _delta(sd, mask, _control_factor(sd, ev["stores"])), "affected stores in the window vs control-adjusted baseline"
    if cat in ("promo_cannibalisation", "promo_uplift"):
        mask = sd["store_id"].isin(ev["stores"])
        return _delta(sd, mask, _control_factor(sd, ev["stores"])), "promo stores vs control-adjusted baseline"
    if cat == "price_change":
        category = wh.products.loc[wh.products["product_id"] == ev["product"], "category"].iloc[0]
        a = wh.daily_compare("net_sales", d0, d1, filters={"category": category}, anchor=d0)
        others = [c for c in wh.products["category"].unique() if c != category]
        o = wh.daily_compare("net_sales", d0, d1, filters={"category": others}, anchor=d0).set_index("date")
        factor = (o["actual"] / o["baseline"]).fillna(1.0)
        a["f"] = a["date"].map(factor).fillna(1.0)
        return float((a["actual"] - a["baseline"] * a["f"]).sum()), f"{category} category vs other-category-adjusted baseline"
    if cat == "external_unknown":
        mask = sd["store_id"].isin(ev["stores"])
        return _delta(sd, mask, _control_factor(sd, ev["stores"])), "declining stores vs control-adjusted pre-window baseline"
    if cat in ("weather", "calendar_shift", "mix_shift"):
        return _chain_delta(sd), "chain total vs pre-window same-weekday baseline"
    return None, "n/a"


STORE_BASED = {"pos_outage", "planned_closure", "unplanned_closure", "stockout", "promo_cannibalisation",
               "promo_uplift", "external_unknown"}


def noise_floor(wh: Warehouse, h: dict, d0, d1, n_windows: int = 6) -> float | None:
    """How big a 'fake' impact does this estimator produce in earlier windows of the same length?

    Runs the same estimator on up to six earlier windows (same scope, same length) and returns
    the robust standard deviation of the results. An impact below ~2x this is not distinguishable
    from noise.
    """
    d0, d1 = to_ts(d0), to_ts(d1)
    length = (d1 - d0).days + 1
    step = 7 * int(np.ceil((length + 7) / 7))
    cat, ev = h["category"], h["evidence"]
    vals = []
    for j in range(1, n_windows + 1):
        a, b = d0 - pd.Timedelta(days=step * j), d1 - pd.Timedelta(days=step * j)
        if a - pd.Timedelta(days=60) < wh.first_date:
            break
        sd = _store_days(wh, a, b)
        if cat in STORE_BASED:
            v = _delta(sd, sd["store_id"].isin(ev["stores"]), _control_factor(sd, ev["stores"]))
        elif cat in ("weather", "calendar_shift", "mix_shift"):
            v = _chain_delta(sd)
        elif cat == "partner_outage":
            v, _ = _impact_for(wh, {"category": cat, "evidence": {"dates": [str(x.date()) for x in pd.date_range(a, b)]}}, a, b, sd)
        elif cat == "price_change":
            v, _ = _impact_for(wh, h, a, b, sd)
        else:
            return None
        if v is not None:
            vals.append(v)
    if len(vals) < 3:
        return None
    arr = np.array(vals)
    return float(max(1.4826 * np.median(np.abs(arr - np.median(arr))), arr.std() * 0.5))


def _projection(wh: Warehouse, cat: str, d0, d1, impact: float, sd: pd.DataFrame, stores: list[str] | None) -> dict:
    d1 = to_ts(d1)
    last_day_of_month = calendar.monthrange(d1.year, d1.month)[1]
    remaining = last_day_of_month - d1.day
    in_month = sd[(sd["date"].dt.month == d1.month) & (sd["date"].dt.year == d1.year)]
    persists = cat in PERSISTENT
    run_rate = 0.0
    if persists:
        tail = sd[sd["date"] > d1 - pd.Timedelta(days=7)]
        if stores:
            tail = tail[tail["store_id"].isin(stores)]
        n_days = max(1, tail["date"].nunique())
        run_rate = float((tail["actual"] - tail["baseline"]).sum() / n_days)
    mtd = float(impact if in_month["date"].nunique() >= (d1 - to_ts(d0)).days + 1 else impact)
    return {
        "effect_persists": persists,
        "daily_run_rate_dollars": run_rate if persists else 0.0,
        "days_left_in_month": remaining,
        "projected_additional_dollars": run_rate * remaining if persists else 0.0,
        "projected_month_end_dollars": mtd + (run_rate * remaining if persists else 0.0),
    }


def estimate_impact(wh: Warehouse, date_from, date_to, hypotheses: list[dict] | None = None) -> dict:
    d0, d1 = to_ts(date_from), to_ts(date_to)
    if hypotheses is None:
        hypotheses = H.rank_hypotheses(wh, d0, d1)["hypotheses"]
    strong = [h for h in hypotheses if h["score"] >= 0.7]
    data = [h for h in strong if h["category"] in DATA_CAUSES]
    if data:
        dqr = H.dq.run_checks(wh, d0, d1)
        phantom = sum(f.get("phantom_net_sales") or 0 for f in dqr["findings"] if f["check"] == "unique_key")
        return jsonable({
            "window": [str(d0.date()), str(d1.date())], "total_impact_dollars": 0.0, "by_cause": [],
            "reported_distortion_dollars": phantom or None,
            "note": "Data-quality incident: no real business impact. Loaded numbers are distorted; fix the pipeline first.",
            "projection": None,
        })
    sd = _store_days(wh, d0, d1)
    by_cause = []
    for h in strong:
        val, method = _impact_for(wh, h, d0, d1, sd)
        if val is not None:
            by_cause.append({"category": h["category"], "impact_dollars": val, "method": method, "_h": h})
    # two causes that both move chain totals: split instead of double counting
    cats = {c["category"] for c in by_cause}
    if "weather" in cats and "pos_outage" in cats:
        pos = next(c for c in by_cause if c["category"] == "pos_outage")["impact_dollars"]
        for c in by_cause:
            if c["category"] == "weather":
                c["impact_dollars"] = _chain_delta(sd) - pos
                c["method"] = "chain total minus the outage store-days (split of overlapping causes)"
    total = sum(c["impact_dollars"] for c in by_cause)
    if "weather" in cats and "pos_outage" in cats:
        total = _chain_delta(sd)
    main = max(by_cause, key=lambda c: abs(c["impact_dollars"])) if by_cause else None
    projection = None
    if main:
        stores = main["_h"]["evidence"].get("stores")
        projection = _projection(wh, main["category"], d0, d1, main["impact_dollars"], sd, stores)
    base_total = float(sd.groupby("date")["baseline"].sum().sum())
    for c in by_cause:
        nf = noise_floor(wh, c["_h"], d0, d1)
        c["noise_floor_dollars"] = nf  # placebo spread: a LOWER BOUND on the estimate's uncertainty
        ratio = abs(c["impact_dollars"]) / nf if nf else None
        c["clearly_above_noise"] = bool(ratio is not None and ratio >= 3)
        c["confidence"] = (
            "high" if ratio is not None and ratio >= 3
            else "medium" if ratio is not None and ratio >= 2
            else "low (within the estimator's noise)"
        )
        c.pop("_h")
    return jsonable({
        "window": [str(d0.date()), str(d1.date())],
        "total_impact_dollars": total,
        "total_impact_pct_of_baseline": total / base_total if base_total else None,
        "by_cause": by_cause,
        "projection": projection,
        "baseline_dollars": base_total,
    })
