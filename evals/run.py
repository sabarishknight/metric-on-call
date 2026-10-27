"""Evaluation harness.

    python -m evals.run --config scripted        # offline, deterministic pipeline (default)
    python -m evals.run --config naive           # ungoverned baseline: no DQ gate, no skeptic
    python -m evals.run --config governed --provider ollama   # LLM agents + governed tools
    python -m evals.run --smoke                  # 3 incidents, for CI

Writes evals/results/<config>.json and prints a table with dev and held-out split reported separately.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import date, timedelta

from agents.models import get_model
from agents.runner import investigate
from common.config import ROOT, TRUTH_PATH
from evals import holdout
from evals.scorers import score_incident, summarise
from tools import monitor
from tools.registry import call_tool, get_warehouse

RESULTS = ROOT / "evals" / "results"
SMOKE_IDS = {1, 2, 6}


def naive_findings(truth: dict) -> dict:
    """What an ungoverned single agent does: believe the dashboard. Headline move = impact, no data gate."""
    wf, wt = str(truth["date_from"]), str(truth["date_to"])
    scan = call_tool("scan_anomalies", {"date_from": wf, "date_to": wt})
    inc = (scan.get("incidents") or [None])[0]
    cause, impact = "unexplained", None
    if inc:
        h = inc["headline"]
        if h.get("observed") is not None and h.get("expected") is not None:
            impact = round(h["observed"] - h["expected"], 2)
    ctx = call_tool("lookup_context", {"date_from": wf, "date_to": wt})
    log = ((ctx.get("ops_log") or {}).get("rows") or [])
    if log:
        cause = {"pos": "pos_outage"}.get(log[0].get("system"), "partner_outage")
    elif (ctx.get("promos") or {}).get("rows"):
        cause = "promo_uplift"
    return {"status": "investigated", "cause_category": cause, "causes": [cause], "ranked_hypotheses": [{"category": cause}],
            "dollar_impact": impact, "not_in_our_data": False, "confidence": "high"}


def false_alarms(wh) -> tuple[int, int]:
    import yaml
    weeks = yaml.safe_load((ROOT / "generator" / "incidents.yaml").read_text()).get("quiet_weeks", [])
    n = 0
    for w in weeks:
        a, b = str(w), str(w + timedelta(days=6))
        n += bool(monitor.incidents_between(wh, a, b))
    return n, len(weeks)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="scripted", choices=["scripted", "naive", "governed"])
    ap.add_argument("--provider", default=None)
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--lock", action="store_true", help="lock current prompts for held-out scoring")
    a = ap.parse_args()
    if a.lock:
        print("locked prompts:", holdout.lock())
        return

    truth = json.loads(TRUTH_PATH.read_text())["incidents"]
    if a.smoke:
        truth = [t for t in truth if t["id"] in SMOKE_IDS]
    wh = get_warehouse()
    model = get_model("scripted" if a.config == "scripted" else (a.provider or "ollama")) if a.config != "naive" else None

    rows, t0 = [], time.time()
    for t in truth:
        if a.config == "naive":
            f, extra = naive_findings(t), {"steps": 3}
        else:
            runs = [investigate(str(t["date_from"]), str(t["date_to"]), model=model, wh=wh) for _ in range(2 if a.config == "scripted" else 1)]
            f = runs[0]["findings"]
            extra = {"steps": runs[0]["budget"]["steps"], "tokens": runs[0]["budget"]["input_tokens"] + runs[0]["budget"]["output_tokens"],
                     "consistent": len({r["findings"]["cause_category"] for r in runs}) == 1}
        rows.append(score_incident(t, f) | extra)

    dev = [r for r in rows if not r["held_out"]]
    held = [r for r in rows if r["held_out"]]
    fa, nw = false_alarms(wh)
    report = {"config": a.config, "date": str(date.today()), "seconds": round(time.time() - t0, 1), "prompt_hash": holdout.prompt_hash(),
              "held_out_valid": holdout.held_out_valid(), "dev": summarise(dev), "held_out": summarise(held) if held else None,
              "all": summarise(rows), "monitor_false_alarm_weeks": f"{fa}/{nw}", "per_incident": rows}
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / f"{a.config}.json").write_text(json.dumps(report, indent=2, default=str))

    print(f"config={a.config}  prompt_hash={report['prompt_hash']}  held_out_valid={report['held_out_valid']}")
    print(f"{'split':10}{'n':>3}{'top1':>7}{'top3':>7}{'gate':>7}{'honest':>8}{'multi':>7}{'$err med':>10}")
    for name in ("dev", "held_out", "all"):
        s = report[name]
        if s:
            f = lambda v, w, d=2: f"{'-' if v is None else round(v, d)!s:>{w}}"  # noqa: E731
            print(f"{name:10}{s['n']:>3}{f(s['top1'], 7)}{f(s['top3'], 7)}{f(s['data_gate_correct'], 7)}{f(s['honest_unknown'], 8)}{f(s['multi_cause_recall'], 7)}{f(s['dollar_err_median'], 10, 3)}")
    print("monitor false-alarm weeks:", report["monitor_false_alarm_weeks"])
    if not report["held_out_valid"]:
        print("NOTE: prompts not locked/changed since lock; held-out row is not a valid generalisation estimate.")


if __name__ == "__main__":
    main()
