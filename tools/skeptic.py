"""Skeptic tools: "break the answer before a stakeholder does".

Three deterministic attacks plus one that bundles them:
  seasonality_check   : did the same thing happen at this time before? (calendar / seasonal recurrence)
  simpsons_check      : does the aggregate move disagree with every segment?
  reconcile_dollars   : do the segment impacts add up to the headline impact?
  placebo_check       : how often does a move this size happen in ordinary weeks?
  challenge           : runs the relevant ones for a findings object and returns objections
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from tools.data import Warehouse, to_ts
from tools.decompose import mix_rate_decomposition
from tools.guards import jsonable

DATA_CAUSES = {"pipeline_duplicate_load", "pipeline_missing_data"}
CHAIN_WIDE = {"weather", "calendar_shift", "mix_shift"}


def seasonality_check(wh: Warehouse, metric: str, date_from, date_to) -> dict:
    """Compare with the same dates one year earlier (if the history exists) and the holiday calendar."""
    d0, d1 = to_ts(date_from), to_ts(date_to)
    now = wh.aggregate([metric], (), d0, d1).iloc[0][metric]
    prev0, prev1 = d0 - pd.Timedelta(days=364), d1 - pd.Timedelta(days=364)
    out: dict = {"metric": metric, "has_prior_year": bool(prev0 >= wh.first_date)}
    if out["has_prior_year"]:
        then = wh.aggregate([metric], (), prev0, prev1).iloc[0][metric]
        out["naive_yoy_pct"] = float(now / then - 1) if then else None
        base_now = wh.baseline([metric], d0, d1).iloc[0][metric]
        base_then = wh.baseline([metric], prev0, prev1).iloc[0][metric] if prev0 - pd.Timedelta(days=7) >= wh.first_date else None
        out["dev_vs_baseline_pct"] = float(now / base_now - 1) if base_now else None
        if base_then:
            then_pct = float(then / base_then - 1)
            out["prior_year_dev_vs_baseline_pct"] = then_pct
            out["recurs_seasonally"] = bool(
                out["dev_vs_baseline_pct"] is not None
                and abs(out["dev_vs_baseline_pct"]) > 0.03
                and np.sign(then_pct) == np.sign(out["dev_vs_baseline_pct"])
                and abs(then_pct) >= 0.5 * abs(out["dev_vs_baseline_pct"])
            )
    hol = wh.overlapping("ctx_holidays", d0, d1)
    prev_hol = wh.overlapping("ctx_holidays", prev0, prev1)
    out["holidays_in_window"] = sorted(hol["holiday_name"].unique().tolist())
    out["holidays_in_prior_year_window"] = sorted(prev_hol["holiday_name"].unique().tolist())
    out["calendar_misaligned"] = bool(set(out["holidays_in_window"]) != set(out["holidays_in_prior_year_window"]))
    return jsonable(out)


def simpsons_check(wh: Warehouse, metric: str, dim: str, date_from, date_to) -> dict:
    r = mix_rate_decomposition(wh, metric, dim, date_from, date_to)
    return jsonable({
        "metric": metric, "dim": dim, "overall_pct": r["overall_pct"],
        "segments_moving_opposite_to_overall": (
            r["segments_with_lower_rate"] if r["overall_now"] > r["overall_base"]
            else r["segments_total"] - r["segments_with_lower_rate"]
        ),
        "segments_total": r["segments_total"],
        "simpsons_paradox": r["simpsons_paradox"],
        "rate_effect": r["rate_effect_total"], "mix_effect": r["mix_effect_total"],
    })


def reconcile_dollars(segment_impacts: list[float], headline_impact: float, tolerance: float = 0.05) -> dict:
    total = float(sum(segment_impacts))
    gap = total - headline_impact
    rel = abs(gap) / abs(headline_impact) if headline_impact else (0.0 if total == 0 else float("inf"))
    return {"sum_of_segments": total, "headline": headline_impact, "gap": gap, "relative_gap": rel,
            "reconciles": bool(rel <= tolerance)}


def placebo_check(wh: Warehouse, metric: str, date_from, date_to, n_windows: int = 20) -> dict:
    """How unusual is this move? Compare to the same-length windows in the weeks before."""
    d0, d1 = to_ts(date_from), to_ts(date_to)
    length = (d1 - d0).days + 1
    now = wh.compare([metric], d0, d1).iloc[0][f"{metric}__pct"]
    vals = []
    for j in range(1, n_windows + 1):
        a, b = d0 - pd.Timedelta(days=7 * j * max(1, int(np.ceil(length / 7)))), d1 - pd.Timedelta(days=7 * j * max(1, int(np.ceil(length / 7))))
        if a - pd.Timedelta(days=56) < wh.first_date:
            break
        try:
            vals.append(float(wh.compare([metric], a, b).iloc[0][f"{metric}__pct"]))
        except ValueError:
            break
    if len(vals) < 5:
        return {"available": False}
    arr = np.array(vals)
    p = float((np.abs(arr) >= abs(now)).mean())
    return jsonable({"available": True, "move_pct": now, "n_windows": len(vals),
                     "share_of_ordinary_windows_this_large": p, "unusual": bool(p <= 0.05)})


def challenge(wh: Warehouse, findings: dict) -> dict:
    """Try to break a findings object. Returns objections (empty list means the answer survives).

    `findings` keys used: window [from,to], top_cause, causes, impact (by_cause list), metric.
    """
    d0, d1 = findings["window"]
    cause = findings.get("top_cause")
    if cause in DATA_CAUSES:
        return {"objections": [], "survives": True,
                "checks": {"note": "Data-quality finding: business-cause attacks do not apply."}}
    metric = findings.get("metric", "net_sales")
    objections: list[dict] = []
    checks: dict = {}

    seas = seasonality_check(wh, metric, d0, d1)
    checks["seasonality"] = seas
    if seas.get("recurs_seasonally") and cause not in ("calendar_shift",):
        objections.append({"check": "seasonality", "severity": "major",
                           "text": f"The same dip appeared at this time last year ({seas['prior_year_dev_vs_baseline_pct']:+.1%}); "
                                   f"this may be seasonal rather than '{cause}'."})
    if seas.get("calendar_misaligned") and cause == "calendar_shift" and seas.get("naive_yoy_pct") is not None:
        checks["calendar_note"] = (f"Naive same-week year-on-year is {seas['naive_yoy_pct']:+.1%} but holidays are misaligned; "
                                   "the holiday-aligned comparison is the fair one.")

    simp = simpsons_check(wh, "avg_basket", "region_id", d0, d1)
    checks["simpsons"] = simp
    if simp["simpsons_paradox"] and cause != "mix_shift":
        objections.append({"check": "simpsons_paradox", "severity": "major",
                           "text": "Average basket moves opposite to nearly every region: a traffic mix shift, not a within-region cause."})

    impacts = findings.get("impact", {}).get("by_cause", [])
    total = findings.get("impact", {}).get("total_impact_dollars")
    if impacts and total is not None:
        rec = reconcile_dollars([c["impact_dollars"] for c in impacts], total, tolerance=0.10)
        checks["reconciliation"] = rec
        if not rec["reconciles"]:
            objections.append({"check": "dollar_reconciliation", "severity": "major",
                               "text": f"Cause impacts sum to {rec['sum_of_segments']:,.0f} but the headline is {total:,.0f}."})

    if cause in CHAIN_WIDE:  # a chain-level placebo says nothing about a local cause
        pl = placebo_check(wh, metric, d0, d1)
        checks["placebo"] = pl
        if pl.get("available") and not pl["unusual"]:
            objections.append({"check": "placebo", "severity": "minor",
                               "text": f"Moves this large occur in {pl['share_of_ordinary_windows_this_large']:.0%} of ordinary windows."})
    for c in impacts:
        if str(c.get("confidence", "")).startswith("low"):
            objections.append({"check": "impact_precision", "severity": "minor",
                               "text": f"The {c['category']} impact is within the estimator's noise; report it as approximate."})
    return jsonable({"objections": objections, "survives": not any(o["severity"] == "major" for o in objections), "checks": checks})
