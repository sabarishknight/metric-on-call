"""Held-out discipline.

Incidents flagged `held_out` in generator/incidents.yaml are scored separately and must not be used
to tune prompts. We enforce it mechanically: a hash of the prompts is locked in evals/prompt.lock.
If prompts change after locking, held-out numbers are reported as INVALID until re-locked on purpose.

Honest caveat (also in docs/decisions.md): the deterministic tool thresholds were tuned while
building and DID see all 13 incidents, so held-out accuracy of the scripted pipeline is not a clean
generalisation estimate. The held-out split is clean for LLM prompt changes made after the lock.
"""

from __future__ import annotations

import hashlib
import json

from common.config import ROOT, TRUTH_PATH

LOCK = ROOT / "evals" / "prompt.lock"
PROMPTS = ROOT / "agents" / "prompts"


def prompt_hash() -> str:
    h = hashlib.sha256()
    for p in sorted(PROMPTS.rglob("*.md")):
        h.update(p.relative_to(PROMPTS).as_posix().encode())
        h.update(p.read_bytes())
    return h.hexdigest()[:16]


def held_out_ids() -> set[int]:
    return {i["id"] for i in json.loads(TRUTH_PATH.read_text())["incidents"] if i.get("held_out")}


def lock() -> str:
    LOCK.write_text(prompt_hash() + "\n")
    return prompt_hash()


def held_out_valid() -> bool:
    return LOCK.exists() and LOCK.read_text().strip() == prompt_hash()
