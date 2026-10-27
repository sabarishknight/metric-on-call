"""Publish step: only runs after a human approves. Writes locally; Slack posts when SLACK_WEBHOOK_URL is set."""

from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path

from common.config import ROOT


def out_dir() -> Path:
    d = Path(os.environ.get("OUTPUT_DIR", ROOT / "outputs"))
    (d / "briefs").mkdir(parents=True, exist_ok=True)
    return d


def publish(state: dict) -> dict:
    f, brief = state["findings"], state["brief"]
    d = out_dir()
    name = f"{f['window_from']}_{f['window_to']}_{f['cause_category']}.md"
    (d / "briefs" / name).write_text(brief)
    with (d / "annotations.jsonl").open("a") as fh:  # dashboard annotation feed
        fh.write(json.dumps({"from": f["window_from"], "to": f["window_to"], "label": f["cause_category"],
                             "impact": f["dollar_impact"], "confidence": f["confidence"]}) + "\n")
    published = {"brief_path": str(d / "briefs" / name), "annotation": True}
    ticket = (state.get("dq") or {}).get("ticket")
    if ticket and f["status"] == "stopped_data_quality":
        with (d / "tickets.jsonl").open("a") as fh:
            fh.write(json.dumps(ticket) + "\n")
        published["ticket"] = ticket["title"]
    hook = os.environ.get("SLACK_WEBHOOK_URL")
    msg = {"text": f"*{f['cause_category']}* {f['window_from']} to {f['window_to']}: {f['recommended_action']}"}
    if hook:
        req = urllib.request.Request(hook, data=json.dumps(msg).encode(), headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=10)  # noqa: S310
            published["slack"] = "sent"
        except OSError as e:
            published["slack"] = f"failed: {e}"
    else:
        with (d / "slack_outbox.jsonl").open("a") as fh:
            fh.write(json.dumps(msg) + "\n")
        published["slack"] = "outbox (no SLACK_WEBHOOK_URL set)"
    return {"published": published}
