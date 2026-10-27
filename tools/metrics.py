"""The ONE definition of every metric (mirrored by warehouse/models/marts and the dbt metrics).

Metric tree (README):
    net_sales = transactions x avg_basket
    transactions = visits x conversion
    avg_basket = units_per_txn x avg_price_per_unit
    avg_price_per_unit = product mix + price changes
"""

from __future__ import annotations

import numpy as np
import pandas as pd

ADDITIVE = ["net_sales", "transactions", "units", "visits"]
RATIOS = {
    "conversion": ("transactions", "visits"),
    "avg_basket": ("net_sales", "transactions"),
    "units_per_txn": ("units", "transactions"),
    "avg_price_per_unit": ("net_sales", "units"),
}
ALL_METRICS = ADDITIVE + list(RATIOS)

# Parent -> children in the metric tree. A "product" node means a multiplicative split.
METRIC_TREE: dict[str, list[str]] = {
    "net_sales": ["transactions", "avg_basket"],
    "transactions": ["visits", "conversion"],
    "avg_basket": ["units_per_txn", "avg_price_per_unit"],
}


def add_ratios(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for name, (num, den) in RATIOS.items():
        if num in out and den in out:
            out[name] = np.where(out[den] > 0, out[num] / out[den].where(out[den] > 0, 1), np.nan)
    return out


def is_additive(metric: str) -> bool:
    return metric in ADDITIVE


def log_decomposition(parent: dict[str, float], child_a: dict[str, float], metric: str) -> dict:
    """Multiplicative split of a change in `metric` between its two children (log method).

    parent/child dicts hold {"base": x0, "now": x1}. Returns each child's share of the change,
    summing to 1 (the log-mean (LMDI) method is exact for a product of two factors).
    """
    ch = METRIC_TREE[metric]
    p0, p1 = parent["base"], parent["now"]
    if p0 <= 0 or p1 <= 0 or p0 == p1:
        return {c: 0.0 for c in ch}
    total_log = np.log(p1 / p0)
    out = {}
    for c in ch:
        a0, a1 = child_a[c]["base"], child_a[c]["now"]
        out[c] = float(np.log(a1 / a0) / total_log) if a0 > 0 and a1 > 0 else 0.0
    return out
