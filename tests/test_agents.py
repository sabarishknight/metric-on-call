"""Agent-team tests with the offline ScriptedModel: no API key, no network."""

from __future__ import annotations

import pytest

from agents.loop import Runtime, run_agent
from agents.models import ModelReply, ToolCall, get_model
from agents.runner import investigate
from agents.schemas import Decision, DQVerdict


def run(by_id, k, **kw):
    i = by_id[k]
    return investigate(str(i["date_from"]), str(i["date_to"]), wh=kw.pop("wh", None), **kw)


@pytest.fixture(autouse=True)
def _wh(wh):
    from tools.registry import set_warehouse
    set_warehouse(wh)
    yield
    set_warehouse(None)


def test_pos_outage_investigated(by_id):
    s = run(by_id, 1)
    f = s["findings"]
    assert f["status"] == "investigated" and f["cause_category"] == "pos_outage"
    assert f["dollar_impact"] < 0 and s["budget"]["steps"] <= 10


@pytest.mark.parametrize("k", [2, 3])
def test_data_incidents_stop_at_gate(by_id, k):
    s = run(by_id, k)
    assert s["findings"]["status"] == "stopped_data_quality"
    assert s["findings"]["dollar_impact"] is None
    assert "hypotheses" not in s and "decomposition" not in s  # no business story


def test_unknown_cause_is_honest(by_id):
    f = run(by_id, 6)["findings"]
    assert f["not_in_our_data"] and f["cause_category"] == "external_unknown"


def test_brief_renders(by_id):
    b = run(by_id, 1)["brief"]
    assert b.startswith("# Incident brief") and "Recommended action" in b


def test_approval_gate_blocks_publish(by_id, tmp_path, monkeypatch):
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path))
    s = run(by_id, 1, approver=None, do_publish=True)
    assert "published" not in s and not list(tmp_path.rglob("*.md"))
    s = run(by_id, 1, do_publish=True)
    assert list((tmp_path / "briefs").glob("*.md")) and (tmp_path / "annotations.jsonl").exists()


def test_reject_goes_back_to_supervisor(by_id):
    calls = []

    def approver(brief, findings):
        calls.append(1)
        return Decision(action="reject", comment="check card payments") if len(calls) == 1 else Decision(action="approve")

    s = run(by_id, 1, approver=approver)
    assert len(calls) == 2 and s["rejections"] == 1 and s["decision"]["action"] == "approve"


def test_step_budget_enforced(by_id):
    s = run(by_id, 1, max_steps=2)
    assert s["findings"]["status"] == "budget_exceeded"


def test_agent_cannot_use_foreign_tool(wh):
    class Rogue:
        name = "rogue"
        n = 0

        def chat(self, system, messages, tools=None):
            self.n += 1
            if self.n == 1:
                return ModelReply(tool_calls=[ToolCall("estimate_impact", {"date_from": "2025-07-23", "date_to": "2025-07-24"})])
            assert "not available" in messages[-1]["content"]
            return ModelReply(content='{"clean": true, "summary": "ok"}')

    obj, trace = run_agent(Runtime(model=Rogue(), wh=wh), "data_quality", {"window_from": "2025-07-23", "window_to": "2025-07-24"}, DQVerdict)
    assert obj.clean and trace[0]["error"]


def test_provider_factory():
    assert get_model("scripted").name == "scripted"
    with pytest.raises(ValueError):
        get_model("nope")
