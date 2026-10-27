"""Tool registry: the ONLY door agents (and MCP clients) have to the data.

Every tool is a plain typed function taking the Warehouse first. The registry derives a pydantic
input model from the signature (validation + JSON schema for LLM function-calling), runs the
call, converts errors into structured results and applies the byte cap. There is no SQL tool:
"revenue" means one thing and a hallucinated query cannot run.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, get_type_hints

import pydantic

from tools import context as ctx_tools
from tools import decompose, dq, hypotheses, impact, monitor, skeptic
from tools.data import DIMENSIONS, Warehouse
from tools.guards import cap_bytes, cap_rows, jsonable
from tools.metrics import ALL_METRICS, METRIC_TREE

Filters = dict[str, str | list[str]]


@dataclass
class ToolSpec:
    name: str
    description: str
    fn: Callable[..., Any]
    model: type[pydantic.BaseModel]
    agent_group: str

    def json_schema(self) -> dict:
        return self.model.model_json_schema()


TOOLS: dict[str, ToolSpec] = {}


def _model_from(fn: Callable[..., Any]) -> type[pydantic.BaseModel]:
    hints = get_type_hints(fn)
    fields: dict[str, Any] = {}
    for p in list(inspect.signature(fn).parameters.values())[1:]:  # skip wh
        ann = hints.get(p.name, Any)
        fields[p.name] = (ann, ... if p.default is inspect.Parameter.empty else p.default)
    return pydantic.create_model(f"{fn.__name__}_input", **fields)


def tool(name: str, description: str, group: str):
    def deco(fn: Callable[..., Any]):
        TOOLS[name] = ToolSpec(name, description, fn, _model_from(fn), group)
        return fn

    return deco


# ----------------------------------------------------------------------------- monitor
@tool("scan_anomalies", "List KPI anomalies the monitor raised, optionally within a date range. "
      "Reads data as loaded, so a pipeline bug appears as a sales anomaly.", "monitor")
def scan_anomalies(wh: Warehouse, date_from: str | None = None, date_to: str | None = None) -> dict:
    incidents = monitor.scan(wh) if not (date_from and date_to) else monitor.incidents_between(wh, date_from, date_to)
    slim = [{k: v for k, v in i.items() if k != "top_series"} | {"top_series": i["top_series"][:5]} for i in incidents]
    return {"n_incidents": len(slim), "incidents": slim}


# ------------------------------------------------------------------------ data quality
@tool("run_data_quality_checks", "Freshness, duplicate keys, missing store-days, row shape, nulls and reconciliation "
      "for a window. A STOP_DATA_QUALITY verdict means: raise a ticket, do not tell a business story.", "data_quality")
def run_data_quality_checks(wh: Warehouse, date_from: str, date_to: str) -> dict:
    return dq.run_checks(wh, date_from, date_to)


# ------------------------------------------------------------------------ decomposition
@tool("walk_metric_tree", "Walk the metric tree (net sales = transactions x basket, ...) and show which child "
      "drove the change vs the same-weekday baseline.", "decomposer")
def walk_metric_tree(wh: Warehouse, metric: str, date_from: str, date_to: str, filters: Filters | None = None) -> dict:
    return decompose.metric_tree_walk(wh, metric, date_from, date_to, filters)


@tool("locate_gap", "Ranked drill-down across region/store/channel/daypart/payment/category/product to find where "
      "a metric's change is concentrated. 'broad_based' means no segment concentrates it.", "decomposer")
def locate_gap(wh: Warehouse, metric: str, date_from: str, date_to: str, filters: Filters | None = None,
               baseline_from: str | None = None, baseline_to: str | None = None) -> dict:
    return decompose.locate_gap(wh, metric, date_from, date_to, filters=filters, baseline_from=baseline_from, baseline_to=baseline_to)


@tool("mix_rate_decomposition", "Split a ratio metric's change (e.g. avg_basket) into within-segment rate effect "
      "and mix effect; flags Simpson's paradox.", "decomposer")
def mix_rate_decomposition(wh: Warehouse, metric: str, dim: str, date_from: str, date_to: str,
                           filters: Filters | None = None) -> dict:
    return decompose.mix_rate_decomposition(wh, metric, dim, date_from, date_to, filters)


@tool("query_metric", "Governed read of metrics from the semantic layer, by chosen dimensions. Row-capped.", "decomposer")
def query_metric(wh: Warehouse, metrics: list[str], date_from: str, date_to: str, by: list[str] | None = None,
                 filters: Filters | None = None, daily: bool = False) -> dict:
    df = wh.aggregate(metrics, by or [], date_from, date_to, filters, daily=daily)
    return cap_rows(df.round({c: 4 for c in df.select_dtypes('number').columns}))


@tool("describe_metrics", "Definitions of every metric, the metric tree and the available dimensions.", "decomposer")
def describe_metrics(wh: Warehouse) -> dict:
    return {"metrics": ALL_METRICS, "metric_tree": METRIC_TREE, "dimensions": DIMENSIONS,
            "definitions": {"net_sales": "sum of net_sales", "transactions": "count of sales", "visits": "store visits / delivery sessions",
                            "conversion": "transactions / visits", "avg_basket": "net_sales / transactions",
                            "units_per_txn": "units / transactions", "avg_price_per_unit": "net_sales / units"}}


# ----------------------------------------------------------------------------- context
@tool("lookup_context", "Promos, price changes, ops/IT log, store events, holidays and weather for a window "
      "(optionally for given stores or a region). Absence of an entry is not proof of no cause.", "context")
def lookup_context(wh: Warehouse, date_from: str, date_to: str, store_ids: list[str] | None = None,
                   region_id: str | None = None) -> dict:
    return ctx_tools.lookup_context(wh, date_from, date_to, store_ids, region_id)


@tool("holiday_comparison", "How a named holiday moved visits each time it occurred (calendar-aligned, not ISO-week).", "context")
def holiday_comparison(wh: Warehouse, holiday_name: str) -> dict:
    return ctx_tools.holiday_aligned_comparison(wh, holiday_name, 0)


# --------------------------------------------------------------------------- hypotheses
@tool("diff_in_diff", "Control-store difference-in-differences with a placebo noise floor.", "hypothesis")
def diff_in_diff(wh: Warehouse, metric: str, treated_stores: list[str], pre_from: str, pre_to: str,
                 post_from: str, post_to: str, control_stores: list[str] | None = None) -> dict:
    return hypotheses.diff_in_diff(wh, metric, treated_stores, pre_from, pre_to, post_from, post_to, control_stores)


@tool("pre_post_test", "Before/after change of a metric with a placebo noise floor.", "hypothesis")
def pre_post_test(wh: Warehouse, metric: str, pre_from: str, pre_to: str, post_from: str, post_to: str,
                  filters: Filters | None = None) -> dict:
    return hypotheses.pre_post(wh, metric, pre_from, pre_to, post_from, post_to, filters)


@tool("cannibalisation_check", "Did a promoted product gain unit share at the expense of same-category products?", "hypothesis")
def cannibalisation_check(wh: Warehouse, promo_product: str, treated_stores: list[str], pre_from: str, pre_to: str,
                          post_from: str, post_to: str) -> dict:
    return hypotheses.cannibalisation_check(wh, promo_product, treated_stores, pre_from, pre_to, post_from, post_to)


@tool("rank_hypotheses", "Test every candidate explanation (pipeline, POS outage, partner outage, closure, stockout, "
      "promo, price, weather, calendar, mix shift, unexplained external) against the data and return them ranked "
      "with evidence.", "hypothesis")
def rank_hypotheses(wh: Warehouse, date_from: str, date_to: str) -> dict:
    return hypotheses.rank_hypotheses(wh, date_from, date_to)


# ----------------------------------------------------------------------------- skeptic
@tool("challenge_findings", "Attack a findings object: seasonality recurrence, Simpson's paradox, dollar reconciliation, "
      "placebo. Returns objections; empty major objections means the answer survives.", "skeptic")
def challenge_findings(wh: Warehouse, window_from: str, window_to: str, top_cause: str,
                       impact_result: dict | None = None, metric: str = "net_sales") -> dict:
    return skeptic.challenge(wh, {"window": [window_from, window_to], "top_cause": top_cause,
                                  "metric": metric, "impact": impact_result or {}})


@tool("seasonality_check", "Compare a window with the same dates last year and the holiday calendar.", "skeptic")
def seasonality_check(wh: Warehouse, metric: str, date_from: str, date_to: str) -> dict:
    return skeptic.seasonality_check(wh, metric, date_from, date_to)


# ------------------------------------------------------------------------------- impact
@tool("estimate_impact", "Dollar impact (counterfactual vs actual) split by cause, with a placebo noise floor and a "
      "month-end projection for effects that persist.", "impact")
def estimate_impact(wh: Warehouse, date_from: str, date_to: str) -> dict:
    return impact.estimate_impact(wh, date_from, date_to)


# --------------------------------------------------------------------------- dispatcher
_WH: Warehouse | None = None


def get_warehouse() -> Warehouse:
    global _WH
    if _WH is None:
        _WH = Warehouse()
    return _WH


def set_warehouse(wh: Warehouse | None) -> None:
    global _WH
    _WH = wh


def call_tool(name: str, arguments: dict | None = None, wh: Warehouse | None = None) -> dict:
    """Validate, run, cap. Never raises: errors come back as {"error": ...} so an agent can recover."""
    spec = TOOLS.get(name)
    if spec is None:
        return {"error": f"unknown tool '{name}'", "available": sorted(TOOLS)}
    try:
        args = spec.model(**(arguments or {})).model_dump()
    except pydantic.ValidationError as e:
        return {"error": "invalid arguments", "details": [f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors()]}
    try:
        return cap_bytes(jsonable(spec.fn(wh or get_warehouse(), **args)))
    except (ValueError, KeyError) as e:
        return {"error": str(e)}


def tool_schemas(groups: set[str] | None = None) -> list[dict]:
    """OpenAI/Gemini/Ollama-style function definitions for the tools an agent may use."""
    return [
        {"name": t.name, "description": t.description, "parameters": t.json_schema()}
        for t in TOOLS.values()
        if groups is None or t.agent_group in groups
    ]
