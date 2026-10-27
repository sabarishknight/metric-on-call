"""The tool-using agent loop shared by every role, with a hard budget."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from agents.models import ChatModel
from tools.data import Warehouse
from tools.registry import TOOLS, call_tool, tool_schemas

PROMPT_DIR = Path(__file__).parent / "prompts"

ROLE_GROUPS: dict[str, set[str]] = {
    "supervisor": set(),
    "data_quality": {"data_quality"},
    "decomposer": {"decomposer"},
    "context": {"context"},
    "hypothesis": {"hypothesis"},
    "skeptic": {"skeptic"},
    "impact": {"impact"},
    "reporter": set(),
}


class BudgetExceeded(RuntimeError):
    pass


@dataclass
class Budget:
    max_steps: int = 40
    steps: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    def take_step(self) -> bool:
        if self.steps >= self.max_steps:
            return False
        self.steps += 1
        return True


@dataclass
class Runtime:
    """Everything a node needs; shared (mutable budget) across nodes."""

    model: ChatModel
    wh: Warehouse | None = None
    budget: Budget = field(default_factory=Budget)
    prompt_version: str = "v1"
    max_skeptic_rounds: int = 2

    def system_prompt(self, role: str) -> str:
        text = (PROMPT_DIR / self.prompt_version / f"{role}.md").read_text()
        return f"ROLE: {role}\n\n{text}"


def _preview(result: dict, limit: int = 1600) -> str:
    """Short JSON preview of a tool result, for the trace / portal (the model still sees the full result)."""
    try:
        raw = json.dumps(result, default=str)
    except (TypeError, ValueError):
        raw = str(result)
    return raw if len(raw) <= limit else raw[:limit] + " ...(truncated)"


def parse_json(text: str) -> dict:
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1).strip()
    if not text.startswith("{"):
        a, b = text.find("{"), text.rfind("}")
        if a != -1 and b > a:
            text = text[a : b + 1]
    return json.loads(text)


def run_agent(rt: Runtime, role: str, payload: dict, schema: type[BaseModel], max_rounds: int = 6) -> tuple[BaseModel, list[dict]]:
    """Run one role to a validated schema object. Returns (result, trace)."""
    system = rt.system_prompt(role)
    allowed = ROLE_GROUPS[role]
    schemas = tool_schemas(allowed) if allowed else None
    messages: list[dict] = [{"role": "user", "content": json.dumps(payload, default=str)}]
    trace: list[dict] = []
    repaired = False
    for _ in range(max_rounds + 2):
        reply = rt.model.chat(system, messages, schemas)
        rt.budget.input_tokens += reply.input_tokens
        rt.budget.output_tokens += reply.output_tokens
        if reply.tool_calls:
            messages.append({"role": "assistant", "content": reply.content,
                             "tool_calls": [{"id": c.id, "name": c.name, "arguments": c.arguments} for c in reply.tool_calls]})
            for c in reply.tool_calls:
                if c.name not in TOOLS or TOOLS[c.name].agent_group not in allowed:
                    result: dict[str, Any] = {"error": f"tool '{c.name}' is not available to the {role} agent"}
                elif not rt.budget.take_step():
                    raise BudgetExceeded(f"tool budget of {rt.budget.max_steps} calls exhausted")
                else:
                    result = call_tool(c.name, c.arguments, rt.wh)
                trace.append({"agent": role, "tool": c.name, "arguments": c.arguments, "error": result.get("error"),
                              "result_preview": _preview(result)})
                messages.append({"role": "tool", "tool_call_id": c.id, "name": c.name, "content": json.dumps(result, default=str)})
            continue
        try:
            obj = schema.model_validate(parse_json(reply.content))
            trace.append({"agent": role, "output": obj.model_dump()})
            return obj, trace
        except (ValueError, ValidationError) as e:
            if repaired:
                raise
            repaired = True
            messages.append({"role": "assistant", "content": reply.content})
            messages.append({"role": "user", "content": f"Your reply was not valid for the required JSON shape ({str(e)[:300]}). Reply with the JSON object only."})
    raise BudgetExceeded(f"{role} did not finish within {max_rounds} tool rounds")
