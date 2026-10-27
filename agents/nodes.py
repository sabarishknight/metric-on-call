"""Graph nodes and routers as plain functions: state in, partial state out.

The same functions run in the pure-Python runner (tests, CI, evals) and inside LangGraph.
`state` is a dict; nodes never mutate it. Budget lives on the shared Runtime.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from agents.brief import default_action
from agents.loop import BudgetExceeded, Runtime, run_agent
from agents.schemas import (
    ContextFindings,
    Decision,
    Decomposition,
    DQVerdict,
    Findings,
    HypothesisResult,
    ImpactResult,
    Plan,
    SkepticReview,
)


class Report(BaseModel):
    recommended_action: str
    brief_markdown: str


def _safe(fn):
    def wrapper(state: dict, rt: Runtime) -> dict:
        try:
            return fn(state, rt)
        except BudgetExceeded as e:
            return {"status": "budget_exceeded", "trace": [{"agent": fn.__name__, "error": str(e)}]}
    wrapper.__name__ = fn.__name__
    return wrapper


def _win(state: dict) -> dict:
    return {"window_from": state["window_from"], "window_to": state["window_to"]}


@_safe
def supervisor(state: dict, rt: Runtime) -> dict:
    plan, tr = run_agent(rt, "supervisor", {**_win(state), "incident": state.get("incident"),
                                            "reviewer_comment": state.get("reviewer_comment")}, Plan)
    return {"plan": plan.model_dump(), "trace": tr}


@_safe
def data_quality(state: dict, rt: Runtime) -> dict:
    v, tr = run_agent(rt, "data_quality", _win(state), DQVerdict)
    return {"dq": v.model_dump(), "trace": tr}


@_safe
def decomposer(state: dict, rt: Runtime) -> dict:
    d, tr = run_agent(rt, "decomposer", {**_win(state), "plan": state.get("plan")}, Decomposition)
    return {"decomposition": d.model_dump(), "trace": tr}


@_safe
def context(state: dict, rt: Runtime) -> dict:
    c, tr = run_agent(rt, "context", {**_win(state), "plan": state.get("plan")}, ContextFindings)
    return {"context": c.model_dump(), "trace": tr}


@_safe
def impact(state: dict, rt: Runtime) -> dict:
    # DQ findings are passed so the impact agent can state the size of any reported distortion
    r, tr = run_agent(rt, "impact", {**_win(state), "dq_findings": []}, ImpactResult)
    return {"impact": r.model_dump(), "trace": tr}


@_safe
def hypothesis(state: dict, rt: Runtime) -> dict:
    h, tr = run_agent(rt, "hypothesis", {**_win(state), "decomposition": state.get("decomposition"), "context": state.get("context"),
                                         "rejected_causes": state.get("rejected_causes", [])}, HypothesisResult)
    return {"hypotheses": h.model_dump(), "trace": tr}


@_safe
def skeptic(state: dict, rt: Runtime) -> dict:
    ranked = (state.get("hypotheses") or {}).get("ranked") or []
    top = ranked[0]["category"] if ranked else "unexplained"
    s, tr = run_agent(rt, "skeptic", {**_win(state), "top_cause": top, "impact": state.get("impact")}, SkepticReview)
    update: dict[str, Any] = {"skeptic": s.model_dump(), "skeptic_rounds": state.get("skeptic_rounds", 0) + 1, "trace": tr}
    if not s.survives:
        update["rejected_causes"] = list(state.get("rejected_causes", [])) + [top]
    return update


def assemble_findings(state: dict) -> Findings:
    dq, win = state.get("dq") or {}, _win(state)
    if state.get("status") == "budget_exceeded":
        return Findings(incident_id=state.get("incident_id", ""), status="budget_exceeded", **win)
    if dq and not dq.get("clean", True):
        causes = dq.get("likely_causes") or ["pipeline_duplicate_load"]
        return Findings(incident_id=state.get("incident_id", ""), status="stopped_data_quality", data_quality="failed",
                        cause_category=causes[0], causes=causes, confidence="high", **win,
                        recommended_action=default_action({"status": "stopped_data_quality", "cause_category": causes[0]}))
    hyp = state.get("hypotheses") or {"ranked": []}
    ranked = hyp["ranked"]
    imp = state.get("impact") or {}
    sk = state.get("skeptic") or {}
    top = ranked[0] if ranked else {"category": "unexplained", "confidence": 0.0}
    causes = [r["category"] for r in ranked if r["confidence"] >= 0.7] if hyp.get("multi_cause") else [top["category"]]
    by_cause = {c["category"]: c["impact_dollars"] for c in imp.get("by_cause", []) if c.get("impact_dollars") is not None}
    conf_levels = [str(c.get("confidence")).split()[0] for c in imp.get("by_cause", []) if c.get("confidence")]
    impact_conf = "high" if conf_levels and all(c == "high" for c in conf_levels) else ("medium" if "medium" in conf_levels or "high" in conf_levels else (conf_levels[0] if conf_levels else "unknown"))
    survives = sk.get("survives", True)
    conf = "low"
    if survives and top["confidence"] >= 0.85 and impact_conf in ("high", "unknown"):
        conf = "high"
    elif survives and top["confidence"] >= 0.6:
        conf = "medium"
    decomp = state.get("decomposition") or {}
    unresolved = [o["text"] for o in sk.get("objections", []) if o["severity"] == "major"] if not survives else []
    return Findings(
        incident_id=state.get("incident_id", ""), status="investigated", cause_category=top["category"], causes=causes,
        ranked_hypotheses=ranked, affected_segments=decomp.get("located_filters", {}), dollar_impact=imp.get("total_impact_dollars"),
        impact_by_cause=by_cause, impact_confidence=impact_conf, confidence=conf, data_quality="clean",
        not_in_our_data=top["category"] == "external_unknown", projection=imp.get("projection"),
        skeptic_rounds=state.get("skeptic_rounds", 0), unresolved_objections=unresolved, **win,
    )


@_safe
def reporter(state: dict, rt: Runtime) -> dict:
    findings = assemble_findings(state)
    fd = findings.model_dump()
    if findings.status == "stopped_data_quality":
        fd["reported_distortion_dollars"] = (state.get("dq") or {}).get("phantom_net_sales")
    agents = {k: state.get(k) for k in ("dq", "decomposition", "context", "hypotheses", "impact", "skeptic")}
    agents["data_quality"] = state.get("dq")
    rep, tr = run_agent(rt, "reporter", {"findings": fd, "agents": agents}, Report)
    findings.recommended_action = rep.recommended_action
    return {"findings": findings.model_dump(), "brief": rep.brief_markdown, "trace": tr}


# ---- routers ----------------------------------------------------------------------------
def route_after_dq(state: dict) -> str:
    if state.get("status") == "budget_exceeded":
        return "reporter"
    return "reporter" if not (state.get("dq") or {}).get("clean", True) else "analyse"


def route_after_skeptic(state: dict, max_rounds: int = 2) -> str:
    if state.get("status") == "budget_exceeded":
        return "reporter"
    sk = state.get("skeptic") or {}
    if sk.get("survives", True) or state.get("skeptic_rounds", 0) >= max_rounds:
        return "reporter"
    return "hypothesis"


def route_after_approval(state: dict) -> str:
    d = state.get("decision") or {}
    if d.get("action") == "approve":
        return "publish"
    return "supervisor" if state.get("rejections", 0) < 2 else "end"


def apply_decision(state: dict, decision: Decision) -> dict:
    if decision.action == "approve":
        update: dict[str, Any] = {"decision": decision.model_dump()}
        if decision.edited_brief:
            update["brief"] = decision.edited_brief
        return update
    return {"decision": decision.model_dump(), "rejections": state.get("rejections", 0) + 1, "reviewer_comment": decision.comment,
            "skeptic_rounds": 0, "rejected_causes": []}
