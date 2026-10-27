"""Read-only MCP server exposing the governed analysis tools.

    python -m tools.mcp_server            # stdio (Claude Desktop, Claude Code, any MCP client)

Works with both MCP Python SDK majors: v2 (`MCPServer`) and v1 (`FastMCP`).
Every tool is read-only; there is no SQL tool and no write tool.
"""

from __future__ import annotations

import inspect
from typing import Any

try:  # mcp >= 2
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server  # type: ignore[no-redef]

from tools.registry import TOOLS, ToolSpec, call_tool


def _wrap(spec: ToolSpec):
    """Build a function whose signature is the tool's own, so the SDK derives the JSON schema."""
    fields = spec.model.model_fields
    params = [
        inspect.Parameter(
            name,
            inspect.Parameter.KEYWORD_ONLY,
            annotation=f.annotation,
            default=inspect.Parameter.empty if f.is_required() else f.default,
        )
        for name, f in fields.items()
    ]

    def fn(**kwargs: Any) -> dict:
        return call_tool(spec.name, kwargs)

    fn.__signature__ = inspect.Signature(params, return_annotation=dict)  # type: ignore[attr-defined]
    fn.__annotations__ = {p.name: p.annotation for p in params} | {"return": dict}
    fn.__name__ = spec.name
    fn.__doc__ = spec.description
    return fn


def build_server():
    server = _Server(
        name="metric-on-call",
        instructions=(
            "Governed, read-only analysis tools for investigating KPI anomalies at Harbour Lane Café & Grocer. "
            "Start with scan_anomalies, then run_data_quality_checks BEFORE any business explanation."
        ),
    )
    for spec in TOOLS.values():
        server.add_tool(_wrap(spec), name=spec.name, description=spec.description)
    return server


def main() -> None:
    build_server().run(transport="stdio")


if __name__ == "__main__":
    main()
