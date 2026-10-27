"""Portal tests: service logic (runs, approval, dashboard) and the HTTP API. No model calls, no network."""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

import pytest

from portal import service

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def svc(wh, truth, tmp_path, monkeypatch):
    from tools import registry

    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(service, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(service, "load_truth", lambda: truth["incidents"])
    registry.set_warehouse(wh)
    service._STATE.update({"ready": True, "error": None, "started": True, "seconds": 0})
    service.RUNS.clear()
    yield service
    service.RUNS.clear()
    registry.set_warehouse(None)
    service.reset_for_tests()


def wait_for(run, timeout: float = 90.0) -> None:
    t0 = time.time()
    while run.status in ("queued", "running") and time.time() - t0 < timeout:
        time.sleep(0.05)
    assert run.status not in ("queued", "running"), "run did not finish in time"


def test_dashboard_shape(svc):
    d = svc.dashboard()
    assert len(d["dates"]) == len(d["series"]["net_sales"]) > 300
    assert [k["key"] for k in d["kpis"]] == ["net_sales", "transactions", "avg_basket", "conversion"]
    assert all(len(k["spark"]) == 28 for k in d["kpis"])
    assert d["regions"] and len(d["incidents"]) == 13


def test_meta_incidents_agents(svc):
    assert svc.meta()["ready"] and svc.meta()["schedule"] == "none"
    assert len(svc.incidents()) == 13
    ids = [a["id"] for a in svc.agents()]
    assert ids[0] == "monitor" and "skeptic" in ids and "publish" in ids
    assert svc.truth_for(1)["causes"] == ["pos_outage"]
    assert svc.truth_for(999) is None


def test_run_then_approve_publishes(svc, tmp_path):
    run = svc.start_run(1, "scripted", 0)
    wait_for(run)
    assert run.status == "awaiting_approval", run.error
    f = run.state["findings"]
    assert f["cause_category"] == "pos_outage" and f["status"] == "investigated"
    agents_seen = [e["agent"] for e in run.events if e["type"] == "done"]
    assert agents_seen[0] == "monitor" and "data_quality" in agents_seen and "reporter" in agents_seen
    assert any(e.get("tool_calls") for e in run.events), "tool calls are reported"
    assert any("result_preview" in c for e in run.events for c in (e.get("tool_calls") or []))
    assert not list(tmp_path.rglob("*.md")), "nothing is published before approval"
    snap = run.snapshot(0)
    assert snap["result"]["brief"] and snap["approvable"]
    svc.decide(run.id, "approve")
    assert run.status == "published" and list((tmp_path / "briefs").glob("*.md"))
    assert (tmp_path / "runs" / f"{run.id}.json").exists()
    assert svc.published()["annotations"]


def test_data_fault_stops_at_gate_and_raises_ticket(svc, tmp_path):
    run = svc.start_run(2, "scripted", 0)
    wait_for(run)
    f = run.state["findings"]
    assert f["status"] == "stopped_data_quality" and f["cause_category"] == "pipeline_duplicate_load"
    skipped = {e["agent"] for e in run.events if e["type"] == "done"}
    assert "decomposer" not in skipped and "skeptic" not in skipped
    svc.decide(run.id, "approve")
    assert (tmp_path / "tickets.jsonl").exists()


def test_reject_publishes_nothing(svc, tmp_path):
    run = svc.start_run(1, "scripted", 0)
    wait_for(run)
    svc.decide(run.id, "reject", "not convinced")
    assert run.status == "rejected" and run.comment == "not convinced" and not list(tmp_path.rglob("*.md"))
    with pytest.raises(RuntimeError):
        svc.decide(run.id, "approve")  # already decided


def test_one_run_at_a_time_and_bad_input(svc):
    run = svc.start_run(1, "scripted", 0.3)  # paced, so it is still running for the next line
    with pytest.raises(RuntimeError):
        svc.start_run(6, "scripted", 0)
    wait_for(run)
    with pytest.raises(KeyError):
        svc.start_run(999, "scripted", 0)
    with pytest.raises(ValueError):
        svc.start_run(1, "chatgpt", 0)


def test_unavailable_provider_is_reported_not_raised(svc, monkeypatch):
    def boom(provider=None):
        raise ConnectionError("ollama is not running")

    monkeypatch.setattr("agents.models.get_model", boom)
    run = svc.start_run(1, "ollama", 0)
    wait_for(run)
    assert run.status == "error" and "ollama is not running" in run.error
    assert svc.history()[0]["status"] == "error"


def test_history_reloads_from_disk(svc):
    run = svc.start_run(6, "scripted", 0)
    wait_for(run)
    svc.RUNS.clear()
    h = svc.history()
    assert h and h[0]["id"] == run.id and h[0]["approvable"] is False


# ---------------------------------------------------------------- HTTP layer
def test_http_api(svc):
    pytest.importorskip("httpx")
    from starlette.testclient import TestClient

    from portal.server import create_app

    c = TestClient(create_app(warm=False))
    assert "Metric On-Call" in c.get("/").text
    assert c.get("/static/app.js").status_code == 200 and c.get("/static/views.js").status_code == 200
    assert c.get("/api/meta").json()["ready"] is True
    assert len(c.get("/api/incidents").json()) == 13
    assert c.get("/api/dashboard").json()["kpis"]
    assert c.get("/api/system").json()["schedule"].startswith("none")
    assert c.get("/api/incidents/1/truth").json()["causes"] == ["pos_outage"]
    assert c.get("/api/incidents/999/truth").status_code == 404
    assert c.post("/api/runs", json={"incident_id": 999}).status_code == 404
    assert c.post("/api/runs", json={"incident_id": "abc"}).status_code == 400
    assert c.get("/api/runs/nope").status_code == 404

    rid = c.post("/api/runs", json={"incident_id": 1, "provider": "scripted", "pace": 0}).json()["run_id"]
    wait_for(service.RUNS[rid])
    snap = c.get(f"/api/runs/{rid}").json()
    assert snap["status"] == "awaiting_approval" and snap["result"]["findings"]["cause_category"] == "pos_outage"
    later = c.get(f"/api/runs/{rid}?since={snap['n_events']}").json()
    assert later["events"] == [] and later["n_events"] == snap["n_events"]
    done = c.post(f"/api/runs/{rid}/decision", json={"action": "approve"}).json()
    assert done["status"] == "published" and done["published"]["brief_path"]
    assert c.post(f"/api/runs/{rid}/decision", json={"action": "approve"}).status_code == 409
    assert c.get("/api/published").json()["briefs"]
    assert c.get("/api/runs").json()[0]["id"] == rid


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
@pytest.mark.parametrize("script", ["portal_lib.test.js", "portal_views.test.js"])
def test_frontend_unit_tests(script):
    r = subprocess.run(["node", str(ROOT / "tests" / script)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
