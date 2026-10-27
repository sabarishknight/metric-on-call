"""Context Investigator tools: "what else happened that week?"

Promo, price, ops/IT, store-event, holiday and weather lookups. Read-only, row-capped.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from tools.data import Warehouse, to_ts
from tools.guards import cap_rows, jsonable


def _store_list(wh: Warehouse, store_ids: list[str] | None, region_id: str | None) -> list[str] | None:
    if store_ids:
        return list(store_ids)
    if region_id:
        return wh.stores_in(region_id)
    return None


def weather_summary(wh: Warehouse, d0, d1, region_id: str | None = None) -> dict:
    wx = wh.ctx["ctx_weather"]
    d0, d1 = to_ts(d0), to_ts(d1)
    if region_id:
        wx = wx[wx["region_id"] == region_id]
    win = wx[wx["date"].between(d0, d1)]
    # baseline rain: the same number of days over the previous 8 weeks, same weekdays
    base_days = []
    for k in range(1, 9):
        a, b = d0 - pd.Timedelta(days=7 * k), d1 - pd.Timedelta(days=7 * k)
        base_days.append(wx[wx["date"].between(a, b)]["rain_mm"].mean())
    base = float(np.nanmedian(base_days)) if base_days else float("nan")
    return {
        "mean_rain_mm": float(win["rain_mm"].mean()),
        "baseline_mean_rain_mm": base,
        "days_over_15mm": int(win.groupby("date")["rain_mm"].mean().gt(15).sum()),
        "days_in_window": int(win["date"].nunique()),
        "mean_temp_c": float(win["temp_c"].mean()),
    }


def lookup_context(wh: Warehouse, date_from, date_to, store_ids: list[str] | None = None,
                   region_id: str | None = None, lookback_days_for_prices: int = 45) -> dict:
    """Everything the logs say about a window (and optionally a set of stores)."""
    d0, d1 = to_ts(date_from), to_ts(date_to)
    stores = _store_list(wh, store_ids, region_id)

    promos = wh.overlapping("ctx_promos", d0, d1, stores)
    ops = wh.overlapping("ctx_ops_log", d0, d1, stores)
    events = wh.overlapping("ctx_store_events", d0, d1, stores)
    holidays = wh.ctx["ctx_holidays"]
    near = holidays[holidays["date"].between(d0 - pd.Timedelta(days=1), d1 + pd.Timedelta(days=1))]
    prices = wh.ctx["ctx_price_changes"]
    prices = prices[prices["effective_date"].between(d0 - pd.Timedelta(days=lookback_days_for_prices), d1)]

    promo_summary = (
        promos.groupby(["promo_id", "promo_name", "date_from", "date_to", "product_id", "mechanic"], dropna=False)
        .agg(stores=("store_id", lambda s: sorted(s.unique().tolist())))
        .reset_index()
    )
    ops_summary = (
        ops.groupby(["system", "severity", "description", "date_from", "date_to"], dropna=False)
        .agg(stores=("store_id", lambda s: sorted(s.unique().tolist())))
        .reset_index()
    )
    return jsonable(
        {
            "window": [str(d0.date()), str(d1.date())],
            "promos": cap_rows(promo_summary),
            "price_changes": cap_rows(prices),
            "ops_log": cap_rows(ops_summary),
            "store_events": cap_rows(events[events["event_type"] == "planned_closure"]),
            "other_store_events": {"n": int((events["event_type"] != "planned_closure").sum())},
            "holidays": cap_rows(near),
            "weather": weather_summary(wh, d0, d1, region_id),
            "note": "Absence of a log entry is NOT proof of no cause: say 'not in our data' when nothing explains a move.",
        }
    )


def holiday_aligned_comparison(wh: Warehouse, holiday_name: str, year: int) -> dict:
    """Compare a holiday's effect with the same holiday in another year (calendar-aligned, not ISO-week)."""
    h = wh.ctx["ctx_holidays"]
    h = h[h["holiday_name"] == holiday_name].sort_values("date")
    if len(h) < 1:
        return {"holiday": holiday_name, "available": False}
    effects = []
    for d in h["date"]:
        daily = wh.daily_compare("visits", d, d)
        if daily.empty or pd.isna(daily["pct"].iloc[0]):
            continue
        effects.append({"date": str(d.date()), "visits_vs_baseline_pct": float(daily["pct"].iloc[0])})
    return jsonable({"holiday": holiday_name, "available": True, "occurrences": effects})
