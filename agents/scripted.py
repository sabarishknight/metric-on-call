"""Offline, deterministic role policies behind ScriptedModel.

These follow the same protocol as an LLM: first turn requests tools, second turn (after tool
results) answers with JSON. They contain NO knowledge of the incidents; they only forward what
the governed tools returned. They are the reference baseline the LLM configurations are compared with.
"""

from __future__ import annotations

import json

from agents.models import ModelReply, ToolCall


def _results(messages: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for m in messages:
        if m["role"] == "tool":
            out[m["name"]] = json.loads(m["content"])
    return out


def _final(obj: dict) -> ModelReply:
    return ModelReply(content=json.dumps(obj, default=str))


def _calls(*calls: tuple[str, dict]) -> ModelReply:
    return ModelReply(tool_calls=[ToolCall(n, a) for n, a in calls])


def _short(row: dict) -> str:
    return "; ".join(f"{k}={v}" for k, v in row.items() if v not in (None, "", []))


def policy(role: str, messages: list[dict]) -> ModelReply:
    payload = json.loads(messages[0]["content"])
    res = _results(messages)
    wf, wt = payload.get("window_from"), payload.get("window_to")

    if role == "supervisor":
        inc = payload.get("incident") or {}
        head = inc.get("headline") or {}
        notes = []
        if payload.get("reviewer_comment"):
            notes.append(f"Reviewer asked: {payload['reviewer_comment']}")
        return _final({
            "summary": f"Investigate {head.get('metric', 'net_sales')} {inc.get('direction', 'move')} for {head.get('slice', 'all')} {wf} to {wt}: data quality first, then decompose and gather context in parallel.",
            "decomposer_metric": "net_sales", "decomposer_filters": {}, "notes": notes,
        })

    if role == "data_quality":
        if not res:
            return _calls(("run_data_quality_checks", {"date_from": wf, "date_to": wt}))
        r = res["run_data_quality_checks"]
        clean = bool(r.get("clean"))
        ticket = None
        if not clean:
            blocking = [f for f in r.get("findings", []) if f.get("severity") == "blocking"]
            ticket = {"title": f"Data quality: {', '.join(r.get('likely_causes', [])) or 'load problem'} ({wf} to {wt})",
                      "severity": "high", "findings": r.get("n_findings", 0),
                      "detail": "; ".join(f.get("detail", "") for f in blocking[:3])}
        phantom = round(sum(f.get("phantom_net_sales", 0) for f in r.get("findings", []) if f.get("table") == "raw_sales_txn"), 2) or None
        return _final({"clean": clean, "phantom_net_sales": phantom, "likely_causes": r.get("likely_causes", []),
                       "summary": "Data quality gate passed." if clean else f"STOP: {r.get('n_findings')} data-quality findings ({', '.join(r.get('likely_causes', []))}). Sales numbers in this window are not trustworthy.",
                       "ticket": ticket})

    if role == "decomposer":
        metric = (payload.get("plan") or {}).get("decomposer_metric", "net_sales")
        if not res:
            return _calls(("walk_metric_tree", {"metric": metric, "date_from": wf, "date_to": wt}),
                          ("locate_gap", {"metric": metric, "date_from": wf, "date_to": wt}))
        tree, gap = res.get("walk_metric_tree", {}), res.get("locate_gap", {})
        path = tree.get("dominant_driver_path", [])
        loc = gap.get("located_filters", {})
        broad = bool(gap.get("broad_based"))
        where = "broad-based, no segment concentrates it" if broad or not loc else "concentrated in " + ", ".join(f"{k}={v}" for k, v in loc.items())
        return _final({"metric": metric, "located_filters": loc, "explained_share": gap.get("explained_share"),
                       "broad_based": broad, "driver_path": path,
                       "summary": f"{metric} moved {tree.get('total_pct')}; driver path {' > '.join(path) or 'n/a'}; {where}."})

    if role == "context":
        plan = payload.get("plan") or {}
        if not res:
            return _calls(("lookup_context", {"date_from": wf, "date_to": wt, "store_ids": plan.get("context_store_ids"), "region_id": plan.get("context_region_id")}))
        r = res["lookup_context"]

        def rows(k: str) -> list[str]:
            return [_short(x) for x in (r.get(k) or {}).get("rows", [])]

        w = r.get("weather") or {}
        return _final({
            "promos": rows("promos"), "price_changes": rows("price_changes"), "ops_log": rows("ops_log"),
            "store_events": rows("store_events"), "holidays": rows("holidays"),
            "weather": f"mean rain {w.get('mean_rain_mm')}mm vs baseline {w.get('baseline_mean_rain_mm')}mm; {w.get('days_over_15mm')} day(s) over 15mm" if w else "",
            "summary": f"{len(rows('ops_log'))} ops entries, {len(rows('promos'))} promos, {len(rows('price_changes'))} price changes, {len(rows('store_events'))} store events, {len(rows('holidays'))} holidays in window. {r.get('note', '')}",
        })

    if role == "hypothesis":
        if not res:
            return _calls(("rank_hypotheses", {"date_from": wf, "date_to": wt}))
        hyps = res["rank_hypotheses"].get("hypotheses", [])
        rejected = set(payload.get("rejected_causes") or [])
        kept = [h for h in hyps if h["category"] not in rejected] or hyps
        return _final({
            "ranked": [{"category": h["category"], "confidence": h["score"], "rationale": h["summary"], "evidence": h.get("evidence", {})} for h in kept],
            "multi_cause": bool(res["rank_hypotheses"].get("multi_cause")),
            "summary": f"Top explanation: {kept[0]['category']} ({kept[0]['score']})." if kept else "No explanation found.",
        })

    if role == "impact":
        if not res:
            return _calls(("estimate_impact", {"date_from": wf, "date_to": wt}))
        r = res["estimate_impact"]
        dq = payload.get("dq_findings") or []
        distortion = round(sum(f.get("phantom_net_sales", 0) for f in dq if f.get("table") == "raw_sales_txn"), 2) or None
        return _final({"total_impact_dollars": r.get("total_impact_dollars"), "by_cause": r.get("by_cause", []),
                       "projection": r.get("projection"), "reported_distortion_dollars": distortion,
                       "summary": f"Estimated impact {r.get('total_impact_dollars')} ({r.get('total_impact_pct_of_baseline')} of baseline)."})

    if role == "skeptic":
        if not res:
            return _calls(("challenge_findings", {"window_from": wf, "window_to": wt, "top_cause": payload.get("top_cause", "unexplained"),
                                                  "impact_result": payload.get("impact")}))
        r = res["challenge_findings"]
        objs = [{"check": o.get("check", "?"), "severity": o.get("severity", "major"),
                 "text": o.get("text") or o.get("detail") or o.get("message") or json.dumps({k: v for k, v in o.items() if k not in ("check", "severity")}, default=str)[:300]}
                for o in r.get("objections", [])]
        return _final({"survives": bool(r.get("survives")), "objections": objs,
                       "summary": "Survives challenge." if r.get("survives") else f"{sum(o['severity'] == 'major' for o in objs)} major objection(s)."})

    if role == "reporter":
        from agents.brief import default_action, render_brief

        f = payload["findings"]
        return _final({"recommended_action": default_action(f), "brief_markdown": render_brief(payload["findings"], payload.get("agents", {}), default_action(f))})

    return _final({"error": f"no scripted policy for role {role}"})
