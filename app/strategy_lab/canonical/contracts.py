"""Typed request/response contracts for the authoritative v2 product API.

These models are the public boundary between the browser and the canonical
application services. They deliberately avoid unstructured ``dict[str, Any]``
except where a value is explicitly arbitrary JSON metadata.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.domain.strategy_spec import StrategySpec


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------
class CompileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    description: str


class ResolveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    spec_draft: StrategySpec
    resolutions: dict[str, Any] = Field(default_factory=dict)


class ApproveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: str
    spec_draft: StrategySpec
    actor: str = "user"
    idempotency_key: str


class DataSourceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str = Field(pattern="^(demo_fixture|yfinance|uploaded_csv)$")
    universe: list[str] = Field(default_factory=list)
    benchmark: str | None = None
    start: str | None = None
    end: str | None = None
    seed: int | None = None
    # uploaded_csv only
    csv_b64: str | None = None


class BacktestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    strategy_id: str
    strategy_version: int
    expected_canonical_hash: str
    data_source: DataSourceRequest
    initial_capital: Decimal = Decimal("1000000")
    idempotency_key: str


class CampaignRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    strategy_id: str
    strategy_version: int
    expected_canonical_hash: str
    baseline_run_id: str | None = None
    mechanism_families: list[str] = Field(
        default_factory=lambda: ["drawdown", "vol_spike", "correlation_breakdown"]
    )
    seed_list: list[int] = Field(default_factory=lambda: [1, 2, 3, 4, 5])
    world_budget: int = Field(default=12, ge=1, le=200)
    failure_predicates: list[str] = Field(default_factory=lambda: ["sharpe_below_0"])
    data_source: DataSourceRequest | None = None
    idempotency_key: str = Field(min_length=1, max_length=255)


class ProjectCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------
class ClauseEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    original_text_span: str
    normalized_interpretation: str
    status: str
    confidence: float
    assumption: str | None = None
    resolution: str | None = None


class CompileResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    api_version: str = "v2"
    original_thesis: str
    strategy_type: str
    spec_draft: Any  # StrategySpec (serialized dict)
    canonical_hash: str
    clauses: list[ClauseEntry]
    assumptions: list[str]
    required_user_resolutions: list[str]
    unsupported_clauses: list[ClauseEntry]
    required_data: list[str]
    contradictions: list[str]
    compiler_kind: str
    compiler_version: str
    confidence_by_clause: dict[str, float]
    is_supported: bool
    is_approvable: bool
    blocking_reasons: list[str]


class ResolveResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    api_version: str = "v2"
    strategy_type: str
    spec_draft: Any
    canonical_hash: str
    is_supported: bool
    is_approvable: bool
    blocking_reasons: list[str]


class ApproveResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    api_version: str = "v2"
    project_id: str
    strategy_id: str
    strategy_version: int
    canonical_hash: str
    approved_by: str
    approved_at: datetime
    schema_version: str


class DataSourceProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str
    source_name: str
    requested_symbols: list[str]
    returned_symbols: list[str]
    benchmark: str | None
    start_date: str | None
    end_date: str | None
    retrieval_timestamp: str | None
    adjustment_policy: str
    calendar_policy: str
    missing_data_policy: str
    coverage_by_symbol: dict[str, float]
    warnings: list[str]
    content_digest: str | None


class BacktestResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    api_version: str = "v2"
    run_id: str
    job_id: str
    strategy_id: str
    strategy_version: int
    canonical_hash: str
    status: str
    metrics: dict[str, Any]
    benchmark_metrics: dict[str, Any] | None
    equity_summary: dict[str, Any]
    trade_summary: dict[str, Any]
    exposure_summary: dict[str, Any]
    turnover: float | None
    cost_summary: dict[str, Any]
    warnings: list[str]
    reasons_to_distrust: list[str]
    data_provenance: DataSourceProvenance
    artifact_references: list[dict[str, Any]]


class FailureRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    failure_id: str
    campaign_id: str
    world_id: str
    mechanism: str
    seed: int
    parameters: dict[str, Any]
    strategy_id: str
    strategy_version: int
    canonical_hash: str
    predicate: str
    metrics: dict[str, Any]


class MinimizationRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dimension: str
    minimized_value: float
    passing_value: float | None
    monotone: bool
    stored_scenario_ref: str | None


class AdjacentPassRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scenario_ref: str
    metrics: dict[str, Any]
    description: str


class CampaignResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    api_version: str = "v2"
    campaign_id: str
    strategy_id: str
    strategy_version: int
    canonical_hash: str
    evaluated_worlds: int
    requested_worlds: int = 0
    predicate_failures: int = 0
    evaluation_errors: int = 0
    error_rate: float = 0.0
    errors_by_mechanism: dict[str, int] = Field(default_factory=dict)
    confirmed_failures: list[FailureRecord]
    failure_rate_by_mechanism: dict[str, float]
    minimization: MinimizationRecord | None
    adjacent_pass: AdjacentPassRecord | None
    warnings: list[str]
    artifact_references: list[dict[str, Any]]


class PredicateResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    metric: str
    operator: str
    threshold: Decimal
    value: Decimal
    passed: bool  # False == the failure predicate is satisfied (strategy failed)
    failed: bool  # True == the failure predicate is satisfied (strategy failed)


class ConfirmationPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    required_successes: int
    total_trials: int
    independent_seeds: list[int] = Field(default_factory=list)


class ScenarioMechanism(str, Enum):  # noqa: UP042  (str-enum for clean serialization)
    DRAWDOWN = "drawdown"
    VOL_SPIKE = "vol_spike"
    CORRELATION_BREAKDOWN = "correlation_breakdown"


class ProjectCreatedResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: str
    name: str
    created_at: str


class AuditRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    api_version: str = "v2"
    project_id: str | None = None
    strategy_id: str
    strategy_version: int
    canonical_hash: str
    run_id: str | None
    campaign_id: str | None
    failure_id: str | None
    data_source_digest: str | None
    artifact_hashes: list[str]
    compiler_version: str
    schema_version: str
    limitations: list[str]


__all__ = [
    "CompileRequest",
    "ResolveRequest",
    "ApproveRequest",
    "DataSourceRequest",
    "BacktestRequest",
    "CampaignRequest",
    "ClauseEntry",
    "CompileResponse",
    "ResolveResponse",
    "ApproveResponse",
    "DataSourceProvenance",
    "BacktestResponse",
    "FailureRecord",
    "MinimizationRecord",
    "AdjacentPassRecord",
    "CampaignResponse",
    "PredicateResult",
    "ConfirmationPolicy",
    "ScenarioMechanism",
    "AuditRecord",
    "ProjectCreatedResponse",
]
