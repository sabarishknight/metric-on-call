"""Compare a Findings dict with the planted ground truth. Pure functions, no model calls."""

from __future__ import annotations


def score_incident(truth: dict, f: dict) -> dict:
    causes = set(truth.get("causes") or [truth["category"]])
    top3 = {h["category"] for h in f.get("ranked_hypotheses", [])[:3]} | {f.get("cause_category")}
    stopped = f.get("status") == "stopped_data_quality"
    expect_stop = bool(truth.get("expected_stop_at_data_gate"))
    planted = truth.get("planted_impact_dollars")
    got = f.get("dollar_impact")
    dollar_err = None
    if not expect_stop and planted and got is not None:
        dollar_err = abs(got - planted) / abs(planted)
    return {
        "id": truth["id"], "category": truth["category"], "held_out": bool(truth.get("held_out")),
        "top1": f.get("cause_category") in causes,
        "top3": bool(causes & top3),
        "all_causes": causes <= set(f.get("causes", [])) if len(causes) > 1 else None,
        "expect_stop": expect_stop, "stopped": stopped, "data_gate_correct": stopped == expect_stop,
        "honest_unknown": (bool(f.get("not_in_our_data")) if truth["category"] == "external_unknown" else None),
        "dollar_err": dollar_err,
        "status": f.get("status"), "confidence": f.get("confidence"),
    }


def summarise(rows: list[dict]) -> dict:
    def mean(xs):
        xs = [x for x in xs if x is not None]
        return round(sum(xs) / len(xs), 3) if xs else None

    def pct(key):
        return mean([float(r[key]) if r[key] is not None else None for r in rows])

    errs = sorted(r["dollar_err"] for r in rows if r["dollar_err"] is not None)
    return {
        "n": len(rows), "top1": pct("top1"), "top3": pct("top3"), "data_gate_correct": pct("data_gate_correct"),
        "honest_unknown": pct("honest_unknown"), "multi_cause_recall": pct("all_causes"),
        "dollar_err_mean": mean(errs), "dollar_err_median": errs[len(errs) // 2] if errs else None,
    }
