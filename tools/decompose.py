"""Metric Decomposer tools: "break the KPI down until the gap is located".

* metric_tree_walk   : which child of the metric tree drove the change (log / LMDI shares)
* mix_rate_decomposition : split a ratio metric's change into rate effect and mix effect per segment
* locate_gap         : ranked drill-down across store / region / channel / daypart / payment / category
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from tools.data import LINE_DIMS, TXN_DIMS, VISIT_DIMS, Warehouse, to_ts
from tools.metrics import ADDITIVE, METRIC_TREE, RATIOS

DRILL_DIMS = ["region_id", "store_id", "channel", "daypart", "payment_type", "category", "product_id"]


def _allowed_dims(metric: str) -> list[set[str]]:
    if metric == "visits":
        return [VISIT_DIMS]
    if metric in ("net_sales", "units", "avg_price_per_unit"):
        return [TXN_DIMS, LINE_DIMS]
    return [TXN_DIMS]


def _compatible(metric: str, dims: set[str]) -> bool:
    return any(dims <= allowed for allowed in _allowed_dims(metric))


# ---------------------------------------------------------------------------------------
def metric_tree_walk(wh: Warehouse, metric: str, date_from, date_to, filters: dict | None = None,
                     baseline_from=None, baseline_to=None) -> dict:
    nodes_needed = ["net_sales", "transactions", "avg_basket", "visits", "conversion", "units_per_txn", "avg_price_per_unit"]
    fkeys = set((filters or {}).keys())
    usable = [m for m in nodes_needed if _compatible(m, fkeys)] if fkeys else nodes_needed
    # visits/conversion can't be filtered by payment_type, product or category
    cmp = wh.compare(usable, date_from, date_to, filters=filters, baseline_from=baseline_from, baseline_to=baseline_to)
    row = cmp.iloc[0]

    def node(m: str) -> dict | None:
        if m not in usable:
            return None
        return {"metric": m, "base": float(row[f"{m}__base"]), "now": float(row[m]), "pct": float(row[f"{m}__pct"])}

    def children(m: str) -> list[dict]:
        out = []
        parent = node(m)
        if parent is None or m not in METRIC_TREE:
            return out
        p0, p1 = parent["base"], parent["now"]
        total_log = np.log(p1 / p0) if p0 > 0 and p1 > 0 else 0.0
        for c in METRIC_TREE[m]:
            n = node(c)
            if n is None:
                continue
            share = float(np.log(n["now"] / n["base"]) / total_log) if total_log and n["base"] > 0 and n["now"] > 0 else 0.0
            out.append({**n, "share_of_change": share, "children": children(c)})
        return out

    root = node(metric)
    if root is None:
        raise ValueError(f"'{metric}' cannot be computed with these filters")
    tree = {**root, "children": children(metric)}

    path, cur = [metric], tree
    while cur["children"]:
        nxt = max(cur["children"], key=lambda c: abs(c["share_of_change"]))
        if abs(nxt["share_of_change"]) < 0.25:
            break
        path.append(nxt["metric"])
        cur = nxt
    return {"window": [str(to_ts(date_from).date()), str(to_ts(date_to).date())], "filters": filters or {},
            "tree": tree, "dominant_driver_path": path}


# ---------------------------------------------------------------------------------------
def mix_rate_decomposition(wh: Warehouse, metric: str, dim: str, date_from, date_to,
                           filters: dict | None = None, baseline_from=None, baseline_to=None) -> dict:
    """Change in a ratio metric = rate effect (within segments) + mix effect (shift in weights)."""
    if metric not in RATIOS:
        raise ValueError(f"'{metric}' is not a ratio metric ({sorted(RATIOS)})")
    num, den = RATIOS[metric]
    cmp = wh.compare([num, den], date_from, date_to, by=[dim], filters=filters,
                     baseline_from=baseline_from, baseline_to=baseline_to)
    n0, d0 = cmp[f"{num}__base"], cmp[f"{den}__base"]
    n1, d1 = cmp[num], cmp[den]
    R0 = np.where(d0 > 0, n0 / d0, 0.0)
    R1 = np.where(d1 > 0, n1 / d1, 0.0)
    w0, w1 = d0 / d0.sum(), d1 / d1.sum()
    rate = w0 * (R1 - R0)  # within-segment change at base weights
    mix = (w1 - w0) * R1  # reweighting at new rates
    overall0, overall1 = n0.sum() / d0.sum(), n1.sum() / d1.sum()
    seg = pd.DataFrame({dim: cmp[dim], "base_rate": R0, "now_rate": R1, "rate_pct": np.where(R0 > 0, R1 / R0 - 1, np.nan),
                        "base_weight": w0, "now_weight": w1, "rate_effect": rate, "mix_effect": mix})
    n_down = int(((seg["now_rate"] < seg["base_rate"]) & (seg["base_weight"] > 0)).sum())
    n_all = int((seg["base_weight"] > 0).sum())
    total_rate, total_mix = float(rate.sum()), float(mix.sum())
    return {
        "metric": metric, "dim": dim,
        "overall_base": float(overall0), "overall_now": float(overall1),
        "overall_pct": float(overall1 / overall0 - 1) if overall0 else None,
        "rate_effect_total": total_rate, "mix_effect_total": total_mix,
        "segments_with_lower_rate": n_down, "segments_total": n_all,
        "simpsons_paradox": bool(overall1 > overall0 and n_all > 0 and n_down / n_all >= 0.8)
        or bool(overall1 < overall0 and n_all > 0 and (n_all - n_down) / n_all >= 0.8),
        "segments": seg.round(4).to_dict("records"),
    }


# ---------------------------------------------------------------------------------------
def _best_step(wh: Warehouse, metric: str, d0, d1, filters: dict, used: set[str],
               baseline_from=None, baseline_to=None) -> dict | None:
    best = None
    for dim in DRILL_DIMS:
        if dim in used:
            continue
        if not _compatible(metric, set(filters) | {dim}):
            continue
        try:
            cmp = wh.compare([metric], d0, d1, by=[dim], filters=filters,
                             baseline_from=baseline_from, baseline_to=baseline_to)
        except ValueError:
            continue
        total = float(cmp[f"{metric}__chg"].sum())
        if abs(total) < 1e-9 or len(cmp) < 2:
            continue
        sign = np.sign(total)
        cmp = cmp.assign(_c=cmp[f"{metric}__chg"] * sign).sort_values("_c", ascending=False)
        gross = float(cmp["_c"].clip(lower=0).sum())  # movement in the direction of the total change
        if gross <= 0:
            continue
        share = (cmp["_c"] / gross).to_numpy()
        # keep material segments (>=10% of the gross move) until 85% is covered
        k, cum = 0, 0.0
        for v in share:
            if v < 0.10 or cum >= 0.85:
                break
            k += 1
            cum += v
        if k == 0:
            continue
        coverage = float(min(cum, 1.0))
        n = len(cmp)
        score = coverage - 0.5 * (k / n)
        if k == n or (k / n > 0.5 and n > 6):
            continue  # the "gap" is spread over (nearly) every segment: not informative
        step = {
            "dim": dim, "score": float(score), "coverage": coverage, "k": k, "n": n,
            "segments": [
                {"value": str(cmp.iloc[i][dim]), "chg": float(cmp.iloc[i][f"{metric}__chg"]),
                 "pct": None if pd.isna(cmp.iloc[i][f"{metric}__pct"]) else float(cmp.iloc[i][f"{metric}__pct"]),
                 "share_of_gap": float(share[i])}
                for i in range(min(k + 2, n))
            ],
            "_values": [cmp.iloc[i][dim] for i in range(k)],
        }
        if best is None or step["score"] > best["score"]:
            best = step
    return best


def headline_filters(level: str, slice_: str) -> tuple[dict, str | None]:
    """Translate a monitor headline (level, slice) into drill-down filters and a default metric hint."""
    parts = slice_.split("|") if slice_ and slice_ != "all" else []
    if level == "region":
        return {"region_id": parts[0]}, None
    if level == "channel":
        return {"channel": parts[0]}, None
    if level == "payment":
        return {"payment_type": parts[0]}, None
    if level == "category":
        return {"category": parts[0]}, None
    if level == "store":
        return {"store_id": parts[0]}, None
    if level == "product":
        return {"product_id": parts[0]}, None
    if level == "store_product":
        return {"store_id": parts[0], "product_id": parts[1]}, None
    return {}, None


def locate_gap(wh: Warehouse, metric: str, date_from, date_to, max_depth: int = 3,
               baseline_from=None, baseline_to=None, filters: dict | None = None) -> dict:
    """Greedy ranked drill-down. Returns the steps taken and the final located segment."""
    d0, d1 = to_ts(date_from), to_ts(date_to)
    filters = dict(filters or {})
    total = wh.compare([metric], d0, d1, filters=filters or None, baseline_from=baseline_from, baseline_to=baseline_to).iloc[0]
    total_chg = float(total[f"{metric}__chg"])
    steps, used = [], set(filters)
    for _ in range(max_depth):
        step = _best_step(wh, metric, d0, d1, filters, used, baseline_from, baseline_to)
        if step is None or step["score"] < 0.55:
            break
        values = step.pop("_values")
        filters[step["dim"]] = values if len(values) > 1 else values[0]
        used.add(step["dim"])
        steps.append(step)
    explained = None
    if steps:
        located = wh.compare([metric], d0, d1, filters=filters, baseline_from=baseline_from, baseline_to=baseline_to).iloc[0]
        explained = float(located[f"{metric}__chg"] / total_chg) if total_chg else None
    # breadth: how many stores moved in the same direction as the total
    st = wh.compare([metric], d0, d1, by=["store_id"], filters=None if "store_id" not in (filters or {}) else None,
                    baseline_from=baseline_from, baseline_to=baseline_to) if metric in ADDITIVE or metric in RATIOS else None
    breadth = None
    if st is not None and total_chg:
        breadth = float((np.sign(st[f"{metric}__chg"]) == np.sign(total_chg)).mean())
    return {
        "metric": metric, "window": [str(d0.date()), str(d1.date())],
        "total_change": total_chg, "total_pct": None if pd.isna(total[f"{metric}__pct"]) else float(total[f"{metric}__pct"]),
        "steps": steps,
        "located_filters": {k: (v if isinstance(v, list) else v) for k, v in filters.items()},
        "explained_share": explained,
        "store_breadth": breadth,
        "broad_based": bool(not steps and breadth is not None and breadth >= 0.75),
    }
