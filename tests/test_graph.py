"""LangGraph topology test. Skipped where langgraph is not installed (runs in CI via `.[agents]`)."""

import pytest

pytest.importorskip("langgraph")

from langgraph.types import Command  # noqa: E402

from agents.graph import build_graph  # noqa: E402


def test_interrupt_then_publish(by_id, wh, tmp_path, monkeypatch):
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path))
    g = build_graph(wh=wh)
    i = by_id[1]
    cfg = {"configurable": {"thread_id": "t1"}}
    init = {"window_from": str(i["date_from"]), "window_to": str(i["date_to"]), "incident_id": "t", "trace": [],
            "rejections": 0, "skeptic_rounds": 0, "rejected_causes": []}
    out = g.invoke(init, cfg)
    assert "__interrupt__" in out and not list(tmp_path.rglob("*.md"))  # paused, nothing published
    out = g.invoke(Command(resume={"action": "approve"}), cfg)
    assert out["findings"]["cause_category"] == "pos_outage" and list((tmp_path / "briefs").glob("*.md"))
