"""LangGraph supervisor graph: the same nodes as agents/runner.py, with parallel fan-out,
checkpointing and a real human-approval interrupt.

    graph = build_graph(model); graph.invoke(init, config); graph.invoke(Command(resume=decision), config)

Requires `pip install .[agents]` (langgraph). Not exercised by the offline test suite in
environments without langgraph; tests/test_graph.py is skipped there and runs in CI.
"""

from __future__ import annotations

import operator
from functools import partial
from typing import Annotated, TypedDict

from agents import nodes
from agents.loop import Budget, Runtime
from agents.models import ChatModel, get_model
from agents.publish import publish
from agents.schemas import Decision


class State(TypedDict, total=False):
    window_from: str
    window_to: str
    incident_id: str
    incident: dict
    plan: dict
    dq: dict
    decomposition: dict
    context: dict
    impact: dict
    hypotheses: dict
    skeptic: dict
    skeptic_rounds: int
    rejected_causes: list
    findings: dict
    brief: str
    decision: dict
    rejections: int
    reviewer_comment: str
    status: str
    published: dict
    trace: Annotated[list, operator.add]


def build_graph(model: ChatModel | None = None, wh=None, max_steps: int = 40):
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import interrupt

    rt = Runtime(model=model or get_model("scripted"), wh=wh, budget=Budget(max_steps=max_steps))

    def approval(state: dict) -> dict:
        raw = interrupt({"brief": state["brief"], "findings": state["findings"]})
        decision = raw if isinstance(raw, Decision) else Decision.model_validate(raw)
        return nodes.apply_decision(state, decision)

    def publish_node(state: dict) -> dict:
        return publish(state)

    g = StateGraph(State)
    for name in ("supervisor", "data_quality", "decomposer", "context", "impact", "hypothesis", "skeptic", "reporter"):
        g.add_node(name, partial(getattr(nodes, name), rt=rt))
    g.add_node("approval", approval)
    g.add_node("publish", publish_node)

    g.add_edge(START, "supervisor")
    g.add_edge("supervisor", "data_quality")
    g.add_node("fanout", lambda state: {})  # pass-through so both branches start only when the gate passed
    g.add_conditional_edges("data_quality", nodes.route_after_dq, {"analyse": "fanout", "reporter": "reporter"})
    g.add_edge("fanout", "decomposer")
    g.add_edge("fanout", "context")
    g.add_edge(["decomposer", "context"], "impact")
    g.add_edge("impact", "hypothesis")
    g.add_edge("hypothesis", "skeptic")
    g.add_conditional_edges("skeptic", partial(nodes.route_after_skeptic, max_rounds=rt.max_skeptic_rounds),
                            {"reporter": "reporter", "hypothesis": "hypothesis"})
    g.add_edge("reporter", "approval")
    g.add_conditional_edges("approval", nodes.route_after_approval, {"publish": "publish", "supervisor": "supervisor", "end": END})
    g.add_edge("publish", END)
    return g.compile(checkpointer=MemorySaver())
