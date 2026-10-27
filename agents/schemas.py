"""Typed outputs of every agent, and the `Findings` object the evaluation scores."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

CAUSE_CATEGORIES = [
    "pos_outage", "pipeline_duplicate_load", "pipeline_missing_data", "promo_cannibalisation", "promo_uplift",
    "price_change", "external_unknown", "weather", "calendar_shift", "stockout", "partner_outage",
    "planned_closure", "unplanned_closure", "mix_shift", "unexplained",
]


class Plan(BaseModel):
    summary: str
    decomposer_metric: str = "net_sales"
    decomposer_filters: dict[str, Any] = Field(default_factory=dict)
    context_store_ids: list[str] | None = None
    context_region_id: str | None = None
    notes: list[str] = Field(default_factory=list)


class DQVerdict(BaseModel):
    clean: bool
    likely_causes: list[str] = Field(default_factory=list)
    summary: str
    ticket: dict[str, Any] | None = None
    phantom_net_sales: float | None = None


class Decomposition(BaseModel):
    metric: str
    located_filters: dict[str, Any] = Field(default_factory=dict)
    explained_share: float | None = None
    broad_based: bool = False
    driver_path: list[str] = Field(default_factory=list)
    summary: str


class ContextFindings(BaseModel):
    promos: list[str] = Field(default_factory=list)
    price_changes: list[str] = Field(default_factory=list)
    ops_log: list[str] = Field(default_factory=list)
    store_events: list[str] = Field(default_factory=list)
    holidays: list[str] = Field(default_factory=list)
    weather: str = ""
    summary: str


class RankedHypothesis(BaseModel):
    category: str
    confidence: float
    rationale: str
    evidence: dict[str, Any] = Field(default_factory=dict)


class HypothesisResult(BaseModel):
    ranked: list[RankedHypothesis]
    multi_cause: bool = False
    summary: str


class Objection(BaseModel):
    check: str
    severity: Literal["major", "minor"]
    text: str


class SkepticReview(BaseModel):
    survives: bool
    objections: list[Objection] = Field(default_factory=list)
    summary: str


class ImpactResult(BaseModel):
    total_impact_dollars: float | None
    by_cause: list[dict[str, Any]] = Field(default_factory=list)
    projection: dict[str, Any] | None = None
    reported_distortion_dollars: float | None = None
    summary: str


class Findings(BaseModel):
    """The structured object compared automatically with ground truth."""

    incident_id: str
    window_from: str
    window_to: str
    status: Literal["investigated", "stopped_data_quality", "budget_exceeded"] = "investigated"
    cause_category: str = "unexplained"
    causes: list[str] = Field(default_factory=list)
    ranked_hypotheses: list[RankedHypothesis] = Field(default_factory=list)
    affected_segments: dict[str, Any] = Field(default_factory=dict)
    dollar_impact: float | None = None
    impact_by_cause: dict[str, float] = Field(default_factory=dict)
    impact_confidence: str = "unknown"
    confidence: Literal["high", "medium", "low"] = "low"
    data_quality: Literal["clean", "failed"] = "clean"
    not_in_our_data: bool = False
    recommended_action: str = ""
    projection: dict[str, Any] | None = None
    skeptic_rounds: int = 0
    unresolved_objections: list[str] = Field(default_factory=list)


class Decision(BaseModel):
    action: Literal["approve", "reject"]
    comment: str = ""
    edited_brief: str | None = None
