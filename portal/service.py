"""Portal logic (no web framework in here, so it is unit-testable).

Runs are manual only: nothing in this module schedules anything. A run starts when a person clicks Run.
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from common.config import ROOT, TRUTH_PATH, get_settings
from tools.guards import jsonable

OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", ROOT / "outputs"))

# ----------------------------------------------------------------------------------------------
# Agent catalogue (what the Agents page and the pipeline diagram show)
# ----------------------------------------------------------------------------------------------
AGENTS: list[dict] = [
    {"id": "monitor", "label": "Monitor", "kind": "code", "color": "teal", "group": "detect",
     "role": "Watches ~440 KPI series against a seasonal expectation and opens an incident. No language model: pure statistics.",
     "tool_group": None},
    {"id": "supervisor", "label": "Supervisor", "kind": "llm", "color": "indigo", "group": "plan",
     "role": "Plans the investigation, decides where to look first and routes work to the specialists.", "tool_group": None},
    {"id": "data_quality", "label": "Data Quality Auditor", "kind": "llm", "color": "orange", "group": "gate",
     "role": "Asks 'is the data even right?' first. Checks freshness, duplicates and missing stores. A broken load stops the run and raises a ticket.",
     "tool_group": "data_quality"},
    {"id": "decomposer", "label": "Metric Decomposer", "kind": "llm", "color": "blue", "group": "analyse",
     "role": "Walks the metric tree (mix vs rate) and drills down by store, region, channel, category and daypart until the gap is located.",
     "tool_group": "decomposer"},
    {"id": "context", "label": "Context Investigator", "kind": "llm", "color": "purple", "group": "analyse",
     "role": "Checks what else happened: promos, price changes, IT outages, store events, holidays and weather.",
     "tool_group": "context"},
    {"id": "impact", "label": "Impact and Forecast", "kind": "llm", "color": "green", "group": "analyse",
     "role": "Sizes the damage in dollars against a counterfactual and projects it forward.", "tool_group": "impact"},
    {"id": "hypothesis", "label": "Hypothesis Tester", "kind": "llm", "color": "pink", "group": "reason",
     "role": "Ranks competing explanations and tests them against control stores and pre/post windows.", "tool_group": "hypothesis"},
    {"id": "skeptic", "label": "Skeptic", "kind": "llm", "color": "red", "group": "reason",
     "role": "Tries to break the answer: seasonality, holiday shifts, Simpson's paradox, dollar reconciliation. Can send it back (max 2 rounds).",
     "tool_group": "skeptic"},
    {"id": "reporter", "label": "Reporter", "kind": "llm", "color": "yellow", "group": "report",
     "role": "Writes the one-page brief for the reader, with the evidence and the recommended action.", "tool_group": None},
    {"id": "approval", "label": "Human approval", "kind": "human", "color": "mint", "group": "report",
     "role": "Nothing is published until a person approves. Reject sends it back with a comment.", "tool_group": None},
    {"id": "publish", "label": "Publish", "kind": "code", "color": "cyan", "group": "report",
     "role": "Writes the brief, annotates the dashboard, raises data tickets and posts to the team channel (outbox when no webhook is set).",
     "tool_group": None},
]
AGENT_LABEL = {a["id"]: a["label"] for a in AGENTS}

PROVIDERS = [
    {"id": "scripted", "label": "Offline baseline", "note": "Deterministic rules calling the same governed tools. No model, no cost, no network."},
    {"id": "ollama", "label": "Ollama (local LLM)", "note": "Free model on this Mac. Needs `ollama serve` and the model pulled."},
    {"id": "gemini", "label": "Gemini (free tier)", "note": "Needs GOOGLE_API_KEY in .env. Free-tier daily limits apply."},
]


def _default(o: Any) -> Any:
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        f = float(o)
        return None if f != f or f in (float("inf"), float("-inf")) else f
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, (pd.Timestamp, datetime)):
        return o.isoformat()[:10] if isinstance(o, pd.Timestamp) else o.isoformat()
    if isinstance(o, (set, tuple)):
        return list(o)
    if hasattr(o, "isoformat"):
        return o.isoformat()
    if hasattr(o, "model_dump"):
        return o.model_dump()
    return str(o)


def clean(obj: Any) -> Any:
    """JSON-safe deep copy (numpy/pandas values, NaN, sets...)."""
    return json.loads(json.dumps(jsonable(obj), default=_default, allow_nan=False)) if obj is not None else None


# ----------------------------------------------------------------------------------------------
# Warehouse warm-up (BigQuery mode takes a while to load, so it happens in the background)
# ----------------------------------------------------------------------------------------------
_STATE: dict[str, Any] = {"ready": False, "error": None, "started": False, "seconds": None}
_LOCK = threading.Lock()


def warm_up(block: bool = False) -> None:
    def work() -> None:
        t0 = time.time()
        try:
            from tools.registry import get_warehouse

            get_warehouse()
            _STATE["ready"], _STATE["error"] = True, None
        except Exception as e:  # noqa: BLE001 - surfaced to the UI
            _STATE["error"] = f"{type(e).__name__}: {e}"
        _STATE["seconds"] = round(time.time() - t0, 1)

    with _LOCK:
        if _STATE["started"]:
            return
        _STATE["started"] = True
    if block:
        work()
    else:
        threading.Thread(target=work, daemon=True).start()


def reset_for_tests() -> None:
    _STATE.update({"ready": False, "error": None, "started": False, "seconds": None})


def wh():
    from tools.registry import get_warehouse

    return get_warehouse()


# ----------------------------------------------------------------------------------------------
# Static-ish info
# ----------------------------------------------------------------------------------------------
def meta() -> dict:
    s = get_settings()
    window = None
    if _STATE["ready"]:
        w = wh()
        window = {"from": str(w.first_date.date()), "to": str(w.last_date.date())}
    mode = os.environ.get("WAREHOUSE", s.warehouse)
    return clean({
        "ready": _STATE["ready"], "error": _STATE["error"], "load_seconds": _STATE["seconds"],
        "warehouse": mode, "project": os.environ.get("GCP_PROJECT") if mode == "bigquery" else None,
        "default_provider": s.model_provider if s.model_provider in {p["id"] for p in PROVIDERS} else "scripted",
        "data_window": window, "schedule": "none", "providers": PROVIDERS,
        "max_steps": s.max_steps, "max_skeptic_rounds": s.max_skeptic_rounds, "outputs_dir": str(OUTPUT_DIR),
    })


def load_truth() -> list[dict]:
    if not TRUTH_PATH.exists():
        return []
    return json.loads(TRUTH_PATH.read_text())["incidents"]


def incidents() -> list[dict]:
    keep = ("id", "name", "category", "difficulty", "held_out", "date_from", "date_to", "scope")
    return clean([{k: t.get(k) for k in keep} for t in load_truth()])


def truth_for(incident_id: int) -> dict | None:
    for t in load_truth():
        if t["id"] == incident_id:
            keys = ("causes", "planted_impact_dollars", "planted_impact_pct", "expected_stop_at_data_gate", "expected_answer", "scope")
            return clean({k: t.get(k) for k in keys})
    return None


def agents() -> list[dict]:
    from tools.registry import TOOLS

    out = []
    for a in AGENTS:
        tools = [{"name": t.name, "description": t.description} for t in TOOLS.values() if a["tool_group"] and t.agent_group == a["tool_group"]]
        out.append({**a, "tools": tools})
    return clean(out)


# ----------------------------------------------------------------------------------------------
# Dashboard
# ----------------------------------------------------------------------------------------------
DASH_METRICS = ["net_sales", "transactions", "visits", "avg_basket", "conversion"]
KPI_LABELS = {"net_sales": "Net sales", "transactions": "Transactions", "avg_basket": "Average basket", "conversion": "Conversion"}


def dashboard() -> dict:
    w = wh()
    last = w.last_date
    daily = w.aggregate(DASH_METRICS, daily=True).sort_values("date").reset_index(drop=True)
    dates = [str(d.date()) for d in daily["date"]]
    series = {m: [None if pd.isna(v) else float(v) for v in daily[m]] for m in DASH_METRICS}

    a, b = last - pd.Timedelta(days=27), last
    pa, pb = a - pd.Timedelta(days=28), a - pd.Timedelta(days=1)
    now = w.aggregate(DASH_METRICS, date_from=a, date_to=b).iloc[0]
    prev = w.aggregate(DASH_METRICS, date_from=pa, date_to=pb).iloc[0]
    kpis = []
    for k, label in KPI_LABELS.items():
        v, p = float(now[k]), float(prev[k])
        kpis.append({"key": k, "label": label, "value": v, "prev": p, "pct": (v / p - 1) if p else None,
                     "spark": series[k][-28:]})

    reg_now = w.aggregate(["net_sales"], by=["region_id"], date_from=a, date_to=b).set_index("region_id")["net_sales"]
    reg_prev = w.aggregate(["net_sales"], by=["region_id"], date_from=pa, date_to=pb).set_index("region_id")["net_sales"]
    regions = [{"region": r, "net_sales": float(v), "pct": (float(v) / float(reg_prev.get(r, 0)) - 1) if reg_prev.get(r, 0) else None}
               for r, v in reg_now.sort_values(ascending=False).items()]

    return clean({
        "dates": dates, "series": series, "kpis": kpis, "regions": regions,
        "window": {"from": str(a.date()), "to": str(b.date())},
        "incidents": [{"id": t["id"], "name": t["name"], "from": str(t["date_from"]), "to": str(t["date_to"]), "category": t["category"]}
                      for t in load_truth()],
        "annotations": published()["annotations"],
    })


# ----------------------------------------------------------------------------------------------
# Runs
# ----------------------------------------------------------------------------------------------
FINISHED = {"awaiting_approval", "published", "rejected", "error"}


@dataclass
class Run:
    id: str
    incident: dict
    provider: str
    pace: float
    created: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    status: str = "queued"
    events: list[dict] = field(default_factory=list)
    state: dict | None = None
    error: str | None = None
    published: dict | None = None
    comment: str = ""
    approvable: bool = True
    t0: float = field(default_factory=time.time)

    def add(self, ev: dict) -> None:
        with _LOCK:
            ev = {**ev, "seq": len(self.events), "t": round(time.time() - self.t0, 2)}
            ev.setdefault("label", AGENT_LABEL.get(ev.get("agent", ""), ev.get("agent", "")))
            self.events.append(clean(ev))

    def summary(self) -> dict:
        f = (self.state or {}).get("findings") or {}
        return {"id": self.id, "incident": {k: self.incident.get(k) for k in ("id", "name", "category", "difficulty")},
                "provider": self.provider, "status": self.status, "created": self.created,
                "cause": f.get("cause_category"), "impact": f.get("dollar_impact"), "confidence": f.get("confidence"),
                "run_status": f.get("status"), "approvable": self.approvable}

    def snapshot(self, since: int = 0) -> dict:
        s = self.state or {}
        result = None
        if self.status in FINISHED and s:
            keys = ("findings", "brief", "budget", "plan", "dq", "decomposition", "context", "impact", "hypotheses", "skeptic", "incident", "trace")
            result = {k: s.get(k) for k in keys}
        return clean({**self.summary(), "events": self.events[since:], "n_events": len(self.events), "result": result,
                      "error": self.error, "published": self.published, "comment": self.comment})

    def to_disk(self) -> dict:
        return clean({**self.summary(), "events": self.events, "state": self.state, "error": self.error,
                      "published": self.published, "comment": self.comment, "pace": self.pace})


RUNS: dict[str, Run] = {}


def _runs_dir() -> Path:
    d = OUTPUT_DIR / "runs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _save(run: Run) -> None:
    try:
        (_runs_dir() / f"{run.id}.json").write_text(json.dumps(run.to_disk(), indent=1))
    except OSError:
        pass


def _load_disk_runs() -> None:
    for p in sorted(_runs_dir().glob("*.json")):
        rid = p.stem
        if rid in RUNS:
            continue
        try:
            d = json.loads(p.read_text())
            r = Run(id=rid, incident=d["incident"], provider=d.get("provider", "scripted"), pace=d.get("pace", 0), created=d.get("created", ""))
            r.status, r.events, r.state = d.get("status", "awaiting_approval"), d.get("events", []), d.get("state")
            r.error, r.published, r.comment, r.approvable = d.get("error"), d.get("published"), d.get("comment", ""), False
            RUNS[rid] = r
        except (OSError, ValueError, KeyError):
            continue


def active_run() -> Run | None:
    return next((r for r in RUNS.values() if r.status in ("queued", "running")), None)


def start_run(incident_id: int, provider: str = "scripted", pace: float = 0.0) -> Run:
    if not _STATE["ready"]:
        raise RuntimeError("data is still loading")
    inc = next((t for t in load_truth() if t["id"] == incident_id), None)
    if inc is None:
        raise KeyError(f"unknown incident {incident_id}")
    if provider not in {p["id"] for p in PROVIDERS}:
        raise ValueError(f"unknown provider '{provider}'")
    if active_run():
        raise RuntimeError("a run is already in progress")
    pace = max(0.0, min(float(pace), 3.0))
    run = Run(id=f"{datetime.now():%Y%m%d-%H%M%S}-i{incident_id}", incident=clean(inc), provider=provider, pace=pace)
    RUNS[run.id] = run
    threading.Thread(target=_execute, args=(run,), daemon=True).start()
    return run


def _execute(run: Run) -> None:
    from agents.models import get_model
    from agents.runner import investigate

    run.status = "running"
    run.add({"type": "info", "agent": "system", "label": "Run", "summary": f"Started investigation of incident {run.incident['id']}: {run.incident['name']}"})
    try:
        model = get_model(run.provider)

        def hook(ev: dict) -> None:
            run.add(ev)
            if ev.get("type") == "done" and run.pace:
                time.sleep(run.pace)

        run.state = investigate(str(run.incident["date_from"]), str(run.incident["date_to"]), model=model, wh=wh(),
                                approver=None, on_event=hook)
        run.state = clean(run.state)
        run.status = "awaiting_approval"
        f = run.state.get("findings") or {}
        run.add({"type": "approval", "agent": "approval", "summary": "Brief ready. Waiting for a human decision.",
                 "output": {"cause": f.get("cause_category"), "impact": f.get("dollar_impact"), "confidence": f.get("confidence")}})
    except Exception as e:  # noqa: BLE001 - shown in the UI instead of dying silently
        run.status, run.error = "error", f"{type(e).__name__}: {e}"
        run.add({"type": "error", "agent": "system", "label": "Run", "summary": run.error})
    _save(run)


def decide(run_id: str, action: str, comment: str = "") -> Run:
    from agents import nodes
    from agents.publish import publish
    from agents.schemas import Decision

    run = RUNS.get(run_id)
    if run is None:
        raise KeyError(run_id)
    if run.status != "awaiting_approval" or not run.state or not run.approvable:
        raise RuntimeError("this run cannot be approved or rejected (already decided, or loaded from disk)")
    if action == "approve":
        state = run.state | nodes.apply_decision(run.state, Decision(action="approve"))
        run.published = clean(publish(state)["published"])
        run.status = "published"
        run.add({"type": "publish", "agent": "publish", "summary": "Approved and published.", "output": run.published})
    elif action == "reject":
        run.status, run.comment = "rejected", comment
        run.add({"type": "info", "agent": "approval", "summary": "Rejected: nothing published." + (f" Comment: {comment}" if comment else "")})
    else:
        raise ValueError("action must be 'approve' or 'reject'")
    _save(run)
    return run


def history() -> list[dict]:
    _load_disk_runs()
    return clean(sorted((r.summary() for r in RUNS.values()), key=lambda s: s["created"], reverse=True))


# ----------------------------------------------------------------------------------------------
# Published artefacts and system info
# ----------------------------------------------------------------------------------------------
def _jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def published() -> dict:
    briefs = []
    bd = OUTPUT_DIR / "briefs"
    if bd.exists():
        for p in sorted(bd.glob("*.md"), reverse=True)[:25]:
            briefs.append({"name": p.name, "text": p.read_text()})
    return clean({"annotations": _jsonl(OUTPUT_DIR / "annotations.jsonl"), "tickets": _jsonl(OUTPUT_DIR / "tickets.jsonl"),
                  "slack_outbox": _jsonl(OUTPUT_DIR / "slack_outbox.jsonl"), "briefs": briefs})


def system() -> dict:
    s = get_settings()
    mode = os.environ.get("WAREHOUSE", s.warehouse)
    tables = []
    if _STATE["ready"]:
        tables = [{"name": k, "rows": int(len(v))} for k, v in wh().tables.items()]
    project = os.environ.get("GCP_PROJECT") or "metric-on-call"
    region = os.environ.get("GCP_REGION", "australia-southeast1")
    return clean({
        "warehouse": mode, "tables": tables, "project": project, "region": region, "schedule": "none: runs only when a person clicks Run",
        "guardrails": {"max_tool_calls_per_run": s.max_steps, "max_skeptic_rounds": s.max_skeptic_rounds,
                       "max_bytes_billed": s.max_bytes_billed, "tools_read_only": True, "human_approval_required": True},
        "console": {"bigquery": f"https://console.cloud.google.com/bigquery?project={project}",
                    "project": f"https://console.cloud.google.com/home/dashboard?project={project}",
                    "storage": f"https://console.cloud.google.com/storage/browser?project={project}",
                    "billing": "https://console.cloud.google.com/billing/budgets"},
    })
