"""One-page brief template (used by the scripted reporter and as the fallback for LLM reporters)."""

from __future__ import annotations

ACTIONS = {
    "pos_outage": "Confirm the terminal fix with IT; recover lost card sales by reminding stores to log manual sales; no commercial action.",
    "pipeline_duplicate_load": "Do not act on these numbers. Raise a data-engineering ticket to de-duplicate the load and re-run the day.",
    "pipeline_missing_data": "Do not act on these numbers. Raise a data-engineering ticket to backfill the missing store-days.",
    "promo_cannibalisation": "Review promo design: the promoted item is pulling sales from same-category items. Reconsider depth or targeting.",
    "promo_uplift": "Promo is working; decide whether to extend or repeat it, watching margin.",
    "price_change": "Review the price change against elasticity; consider rolling back at the affected stores if volume loss outweighs margin.",
    "weather": "Nothing to fix. Adjust forecast and staffing rules for severe-rain days.",
    "calendar_shift": "Not a performance problem. Compare against the aligned holiday, not the ISO week.",
    "stockout": "Fix replenishment for the affected product at the affected stores.",
    "partner_outage": "Escalate with the delivery partner and track recovery.",
    "planned_closure": "No action; expected.",
    "unplanned_closure": "Find out why the store closed and whether it repeats.",
    "mix_shift": "Read the rate and mix effects separately before acting; check for Simpson's paradox.",
    "external_unknown": "Not in our data. Needs an on-the-ground check at the affected stores (local competitor, construction, roster, service).",
    "unexplained": "Not explained by anything we hold. Escalate for human review.",
}


def default_action(f: dict) -> str:
    if f.get("status") == "stopped_data_quality":
        return ACTIONS.get(f.get("cause_category", ""), "Do not act on these numbers. Raise a data-engineering ticket.")
    return ACTIONS.get(f.get("cause_category", "unexplained"), ACTIONS["unexplained"])


def _money(x) -> str:
    return "n/a" if x is None else (f"-${-x:,.0f}" if x < 0 else f"${x:,.0f}")


def render_brief(f: dict, agents: dict, action: str) -> str:
    lines = [f"# Incident brief: {f['window_from']} to {f['window_to']}", ""]
    if f["status"] == "stopped_data_quality":
        lines += ["**Stopped at the data-quality gate.** The sales numbers in this window are not trustworthy, so no business explanation is offered.", ""]
        t = (agents.get("data_quality") or {}).get("ticket") or {}
        lines += [f"- Cause: `{f['cause_category']}`", f"- Ticket: {t.get('title', 'n/a')}", f"- Detail: {t.get('detail', 'n/a')}"]
        d = f.get("reported_distortion_dollars")
        if d:
            lines.append(f"- Reported sales are overstated by about {_money(d)} in the raw load.")
    elif f["status"] == "budget_exceeded":
        lines += ["**Investigation stopped: step budget exhausted.** Partial results only; escalate for human review."]
    else:
        lines += [f"**Answer: `{f['cause_category']}`** (confidence: {f['confidence']})", ""]
        if f["not_in_our_data"]:
            lines += ["Nothing in our data (ops log, promos, prices, events, weather) explains this move.", ""]
        if len(f["causes"]) > 1:
            lines.append(f"Multiple causes: {', '.join(f['causes'])}.")
        lines.append(f"- Dollar impact: **{_money(f['dollar_impact'])}** (impact confidence: {f['impact_confidence']})")
        for c, v in f["impact_by_cause"].items():
            lines.append(f"  - {c}: {_money(v)}")
        if f["affected_segments"]:
            lines.append("- Where: " + ", ".join(f"{k}={v}" for k, v in f["affected_segments"].items()))
        p = f.get("projection") or {}
        if p.get("effect_persists"):
            lines.append(f"- Projected month-end impact: {_money(p.get('projected_month_end_dollars'))}")
        lines += ["", "## Evidence"]
        for h in f["ranked_hypotheses"][:3]:
            lines.append(f"- {h['category']} ({h['confidence']:.2f}): {h['rationale']}")
        lines += ["", "## What I'm not sure of"]
        if f["unresolved_objections"]:
            lines += [f"- {o}" for o in f["unresolved_objections"]]
        else:
            lines.append(f"- Skeptic challenged the answer {f['skeptic_rounds']} time(s); no unresolved major objections.")
        lines.append("- Dollar impact is a counterfactual estimate with a noise floor, not an accounting figure.")
    lines += ["", "## Recommended action", action, ""]
    return "\n".join(lines)
