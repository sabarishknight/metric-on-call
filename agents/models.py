"""Model layer: one tiny interface, three providers.

    scripted : deterministic, offline, no API key. A rule-based policy that calls the same governed
               tools an LLM would. Used by tests, CI and the Replay demo. It is a BASELINE, not an LLM.
    ollama   : free local model through LangChain (`ollama pull <tag>` first).
    gemini   : Gemini API free tier through LangChain.

Switch with MODEL_PROVIDER in .env.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

from common.config import get_settings


@dataclass
class ToolCall:
    name: str
    arguments: dict
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])


@dataclass
class ModelReply:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0


class ChatModel(Protocol):
    name: str

    def chat(self, system: str, messages: list[dict], tools: list[dict] | None = None) -> ModelReply: ...


# --------------------------------------------------------------------------------------
# Scripted (offline) policy
# --------------------------------------------------------------------------------------
class ScriptedModel:
    """Deterministic stand-in for an LLM. The system prompt starts with 'ROLE: <role>'."""

    name = "scripted"

    def chat(self, system: str, messages: list[dict], tools: list[dict] | None = None) -> ModelReply:
        from agents import scripted  # local import: avoids a cycle

        role = system.split("ROLE:", 1)[1].split()[0].strip() if "ROLE:" in system else "unknown"
        return scripted.policy(role, messages)


# --------------------------------------------------------------------------------------
# LangChain-backed providers
# --------------------------------------------------------------------------------------
class LangChainModel:
    def __init__(self, chat_model: Any, name: str):
        self.chat_model = chat_model
        self.name = name

    def chat(self, system: str, messages: list[dict], tools: list[dict] | None = None) -> ModelReply:
        from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

        lc: list[Any] = [SystemMessage(content=system)]
        for m in messages:
            if m["role"] == "user":
                lc.append(HumanMessage(content=m["content"]))
            elif m["role"] == "assistant":
                calls = [{"name": c["name"], "args": c["arguments"], "id": c["id"]} for c in m.get("tool_calls", [])]
                lc.append(AIMessage(content=m.get("content", ""), tool_calls=calls))
            elif m["role"] == "tool":
                lc.append(ToolMessage(content=m["content"], tool_call_id=m["tool_call_id"]))
        model = self.chat_model.bind_tools(tools) if tools else self.chat_model
        out = model.invoke(lc)
        usage = getattr(out, "usage_metadata", None) or {}
        return ModelReply(
            content=out.content if isinstance(out.content, str) else json.dumps(out.content),
            tool_calls=[ToolCall(c["name"], c.get("args", {}), c.get("id") or uuid.uuid4().hex[:8]) for c in (out.tool_calls or [])],
            input_tokens=int(usage.get("input_tokens", 0)),
            output_tokens=int(usage.get("output_tokens", 0)),
        )


def get_model(provider: str | None = None) -> ChatModel:
    s = get_settings()
    provider = (provider or s.model_provider).lower()
    if provider == "scripted":
        return ScriptedModel()
    if provider == "ollama":
        from langchain_ollama import ChatOllama

        return LangChainModel(ChatOllama(model=s.ollama_model, base_url=s.ollama_base_url, temperature=0), f"ollama:{s.ollama_model}")
    if provider == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI

        return LangChainModel(ChatGoogleGenerativeAI(model=s.gemini_model, temperature=0), f"gemini:{s.gemini_model}")
    raise ValueError(f"unknown MODEL_PROVIDER '{provider}' (scripted | ollama | gemini)")
