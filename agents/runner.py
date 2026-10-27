"""Pure-Python executor mirroring the LangGraph topology (agents/graph.py). Used by tests, CI and evals.

    dq -> (STOP -> reporter) | (decomposer + context -> impact -> hypothesis <-> skeptic) -> reporter
       -> human approval -> publish ; reject -> supervisor (max 2)
"""

from __future__ import annotations

import time
from collections.abc import Callable

from agents import nodes
from agents.loop import Budget, Runtime
from agents.models import ChatModel, get_model
from agents.publish import publish
from agents.schemas import Decision
from tools import monitor
from tools.data import Warehouse

Approver = Callable[[str, dict], Decision]


def auto_approve(brief: str, findings: dict) -> Decision:
    return Decision(action="approve")


def _merge(state: dict, update: dict) -> dict:
    new = dict(state)
    for k, v in update.items():
        new[k] = (state.get(k, []) + v) if k == "trace" else v
    return new


# node function name -> the state key that holds that agent's own output
OUTPUT_KEY = {"supervisor": "plan", "data_quality": "dq", "decomposer": "decomposition", "context": "context",
              "impact": "impact", "hypothesis": "hypotheses", "skeptic": "skeptic", "reporter": "findings"}

EventHook = Callable[[dict], None]


def _step(state: dict, name: str, fn: Callable, rt: Runtime, on_event: EventHook | None) -> dict:
    """Run one node; optionally report start/finish (used by the portal for live progress)."""
    if on_event:
        on_event({"type": "start", "agent": name})
    t0 = time.perf_counter()
    update = fn(state, rt)
    if on_event:
        tr = update.get("trace", [])
        out = update.get(OUTPUT_KEY.get(name, ""), None)
        on_event({"type": "done", "agent": name, "ms": round((time.perf_counter() - t0) * 1000),
                  "tool_calls": [t for t in tr if "tool" in t],
                  "output": out, "error": next((t["error"] for t in tr if t.get("error") and "tool" not in t), None),
                  "brief": update.get("brief")})
    return _merge(state, update)


def find_incident(wh: Warehouse, date_from: str, date_to: str) -> dict | None:
    inc = monitor.incidents_between(wh, date_from, date_to)
    return inc[0] if inc else None


def investigate(date_from: str, date_to: str, model: ChatModel | None = None, approver: Approver | None = auto_approve,
                wh: Warehouse | None = None, max_steps: int = 40, do_publish: bool = False, incident: dict | None = None,
                on_event: EventHook | None = None) -> dict:
    """Run a full investigation. approver=None stops before approval (state['findings'] / ['brief'] ready)."""
    from tools.registry import get_warehouse

    wh = wh or get_warehouse()
    rt = Runtime(model=model or get_model("scripted"), wh=wh, budget=Budget(max_steps=max_steps))
    state: dict = {"window_from": date_from, "window_to": date_to, "incident_id": f"INV-{date_from}", "trace": [],
                   "incident": incident or find_incident(wh, date_from, date_to), "rejections": 0, "skeptic_rounds": 0, "rejected_causes": []}
    if on_event:
        on_event({"type": "done", "agent": "monitor", "ms": 0, "tool_calls": [], "output": state["incident"], "error": None})

    while True:
        state = _step(state, "supervisor", nodes.supervisor, rt, on_event)
        state = _step(state, "data_quality", nodes.data_quality, rt, on_event)
        if nodes.route_after_dq(state) == "analyse":
            state = _step(state, "decomposer", nodes.decomposer, rt, on_event)   # parallel in graph.py
            state = _step(state, "context", nodes.context, rt, on_event)
            state = _step(state, "impact", nodes.impact, rt, on_event)
            while True:
                state = _step(state, "hypothesis", nodes.hypothesis, rt, on_event)
                state = _step(state, "skeptic", nodes.skeptic, rt, on_event)
                if nodes.route_after_skeptic(state, rt.max_skeptic_rounds) == "reporter":
                    break
        state = _step(state, "reporter", nodes.reporter, rt, on_event)
        if approver is None:
            break
        decision = approver(state["brief"], state["findings"])
        state = _merge(state, nodes.apply_decision(state, decision))
        route = nodes.route_after_approval(state)
        if route == "publish":
            if do_publish:
                state = _merge(state, publish(state))
            break
        if route == "end":
            state["status"] = "rejected"
            break
    state["budget"] = {"steps": rt.budget.steps, "input_tokens": rt.budget.input_tokens, "output_tokens": rt.budget.output_tokens}
    return state
