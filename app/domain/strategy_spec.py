"""Canonical, versioned Fenrix strategy specification.

``StrategySpec`` is the ONE authoritative strategy contract. Every compiler,
template, backtest, synthetic run, exchange replay, and evidence package must
consume the same approved object. Its canonical hash is computed over a
normalized serialization that EXCLUDES volatile metadata (ids, timestamps,
approval envelopes, compiler traces), so the same declarative intent always
produces the same ``canonical_hash``.

Design rules (reset brief section 5):

* Never silently discard or simplify a clause. Anything the compiler could not
  support is preserved in ``unsupported_clauses``; anything requiring the user's
  decision is preserved in ``user_resolutions`` / ``assumptions``.
* Execution must be blocked (``blocking_reasons`` non-empty) when a clause is
  unresolved, required data is unavailable, the requested strategy type is
  unsupported, constraints contradict each other, an input would require
  look-ahead data, the selected universe is invalid, or the strategy requests
  unrestricted arbitrary code execution.
* The canonical hash MUST change when any executable parameter changes and MUST
  be stable across volatile-metadata churn.
"""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = "strategy-spec/v1"

# Keys excluded from the canonical serialization used for the hash. These are
# volatile (they change without changing the declarative intent) and must never
# influence ``canonical_hash``. Kept in one place so both sides that hash a spec
# exclude the SAME keys (a divergence here silently breaks the hash invariant).
VOLATILE_KEYS: frozenset[str] = frozenset(
    {
        "strategy_id",
        "strategy_version",
        "compiler_metadata",
        "canonical_hash",
        "created_at",
        "updated_at",
    }
)


class AssetClass(StrEnum):
    EQUITY = "equity"
    ETF = "etf"
    # v1 supports equity/ETF only. Others are declared for schema stability but
    # rejected by the executable-type guard until an executor exists.
    FUTURES = "futures"
    FX = "fx"
    CRYPTO = "crypto"


class StrategyType(StrEnum):
    """Executable strategy families. Every family here MUST map to a real
    executor in ``app/strategies/executors``. UI must never surface a type
    that is not in this enum with a registered executor."""

    CROSS_SECTIONAL_FACTOR = "cross_sectional_factor"  # A: long/short factor
    LONG_ONLY_RANKING = "long_only_ranking"  # B: rotation
    TIME_SERIES_SIGNAL = "time_series_signal"  # C: single-asset technical
    STATIC_ALLOCATION = "static_allocation"  # D: 60/40 etc.
    TACTICAL_ALLOCATION = "tactical_allocation"  # E: relative momentum / trend
    UNSUPPORTED = "unsupported"  # requested but no executor -> blocks execution


class Frequency(StrEnum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


class Weighting(StrEnum):
    EQUAL = "equal"
    INVERSE_VOLATILITY = "inverse_volatility"
    FIXED = "fixed"  # for static allocation weights


class ExecutionTiming(StrEnum):
    NEXT_OPEN = "next_open"  # decide at close(t), fill at open(t+1) -- default
    SAME_CLOSE = "same_close"  # only valid for explicitly-declared same-bar policy


class ClauseState(StrEnum):
    RESOLVED = "resolved"
    UNRESOLVED = "unresolved"
    UNSUPPORTED = "unsupported"
    REJECTED = "rejected"


class SignalDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    kind: str  # e.g. "momentum", "volatility", "value", "sma", "breakout"
    source: str = "price"  # price | return | fundamental
    field: str = "close"
    formula: str | None = None
    lookback: int | None = Field(default=None, ge=1)
    params: dict[str, Any] = Field(default_factory=dict)


class Clause(BaseModel):
    """A single interpreted unit of the user's thesis. Never dropped."""

    model_config = ConfigDict(extra="forbid")
    id: str
    original_text: str
    normalized_text: str | None = None
    state: ClauseState = ClauseState.UNRESOLVED
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    reason: str | None = None
    resolution: str | None = None  # what the user decided, if anything


class CostModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    commission_bps: float = Field(default=5.0, ge=0)
    spread_bps: float = Field(default=2.0, ge=0)
    slippage_bps: float = Field(default=3.0, ge=0)
    borrow_bps_annual: float = Field(default=50.0, ge=0)  # only accrues while short
    locate_bps: float = Field(default=10.0, ge=0)  # one-time on new shorts
    model_type: str = "heuristic_flat_bps"
    calibrated: bool = False


class PortfolioConstruction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    long_short: bool = False
    weighting: Weighting = Weighting.EQUAL
    long_count: int | None = Field(default=None, ge=0)
    short_count: int | None = Field(default=None, ge=0)
    long_quantile: float | None = Field(default=None, ge=0, le=1)
    short_quantile: float | None = Field(default=None, ge=0, le=1)
    gross_exposure_limit: float = Field(default=1.0, ge=0)
    net_exposure_limit: float | None = None
    max_position_weight: float = Field(default=0.10, ge=0, le=1)
    # for static/tactical allocation: explicit target weights by symbol
    target_weights: dict[str, float] = Field(default_factory=dict)


class RiskConstraints(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_gross: float | None = None
    max_net: float | None = None
    max_position: float | None = None
    sector_or_factor_constraints: dict[str, Any] = Field(default_factory=dict)


class StrategySpec(BaseModel):
    """The canonical strategy contract."""

    model_config = ConfigDict(extra="forbid")

    # --- identity / versioning (VOLATILE -- excluded from hash) ---
    schema_version: str = SCHEMA_VERSION
    strategy_id: str = ""
    strategy_version: int = 1
    canonical_hash: str = ""

    # --- human-facing intent ---
    name: str
    original_thesis: str
    intended_use: str | None = None
    known_limitations: list[str] = Field(default_factory=list)
    expected_failure_conditions: list[str] = Field(default_factory=list)

    # --- executable type + universe ---
    strategy_type: StrategyType
    asset_class: AssetClass = AssetClass.EQUITY
    universe: list[str] = Field(default_factory=list)
    benchmark: str | None = None
    data_requirements: list[str] = Field(default_factory=list)
    frequency: Frequency = Frequency.MONTHLY
    lookback_windows: dict[str, int] = Field(default_factory=dict)

    # --- signal / selection ---
    signal_definitions: list[SignalDefinition] = Field(default_factory=list)
    signal_combination: dict[str, float] = Field(default_factory=dict)
    ranking_or_threshold_rules: dict[str, Any] = Field(default_factory=dict)
    entry_rules: dict[str, Any] = Field(default_factory=dict)
    exit_rules: dict[str, Any] = Field(default_factory=dict)

    # --- portfolio / risk / execution ---
    portfolio_construction: PortfolioConstruction = Field(default_factory=PortfolioConstruction)
    long_short_policy: str = "long_only"  # long_only | long_short | dollar_neutral
    position_sizing: str = "equal_weight"
    rebalance_policy: Frequency = Frequency.MONTHLY
    execution_timing: ExecutionTiming = ExecutionTiming.NEXT_OPEN
    order_policy: dict[str, Any] = Field(default_factory=dict)
    cost_model: CostModel = Field(default_factory=CostModel)
    borrow_policy: dict[str, Any] = Field(default_factory=dict)
    risk_constraints: RiskConstraints = Field(default_factory=RiskConstraints)
    warmup_policy: str = "exclude_until_lookback_satisfied"
    missing_data_policy: str = "drop_no_forward_fill"

    # --- clause ledger (NEVER dropped) ---
    clauses: list[Clause] = Field(default_factory=list)
    unsupported_clauses: list[Clause] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    user_resolutions: dict[str, str] = Field(default_factory=dict)

    # --- volatile compiler metadata (excluded from hash) ---
    compiler_metadata: dict[str, Any] = Field(default_factory=dict)

    # ------------------------------------------------------------------
    # canonical hashing
    # ------------------------------------------------------------------
    def canonical_dict(self) -> dict[str, Any]:
        """Normalized, hash-stable serialization (volatile keys removed)."""
        data = self.model_dump(mode="json", exclude_none=False)
        for key in VOLATILE_KEYS:
            data.pop(key, None)
        return data

    def canonical_json(self) -> str:
        return json.dumps(self.canonical_dict(), sort_keys=True, separators=(",", ":"), default=str)

    def compute_hash(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    @model_validator(mode="after")
    def _finalize(self) -> StrategySpec:
        # strategy_id defaults to the canonical hash; canonical_hash is always
        # recomputed so it can never drift from the content.
        h = self.compute_hash()
        object.__setattr__(self, "canonical_hash", h)
        if not self.strategy_id:
            object.__setattr__(self, "strategy_id", h)
        return self

    # ------------------------------------------------------------------
    # execution gating (reset brief section 5)
    # ------------------------------------------------------------------
    def blocking_reasons(self, *, available_symbols: set[str] | None = None) -> list[str]:
        """Return the list of reasons execution must be blocked. Empty == OK.

        Fails closed: any ambiguity yields a blocking reason.
        """
        reasons: list[str] = []

        # unresolved / rejected clauses
        for c in self.clauses:
            if c.state == ClauseState.UNRESOLVED:
                reasons.append(f"clause '{c.id}' is unresolved")
            if c.state == ClauseState.REJECTED:
                reasons.append(f"clause '{c.id}' was rejected as unsafe/invalid")
        if self.unsupported_clauses:
            ids = ", ".join(c.id for c in self.unsupported_clauses)
            reasons.append(f"unsupported clauses present and unresolved: {ids}")

        # unsupported strategy type
        if self.strategy_type == StrategyType.UNSUPPORTED:
            reasons.append("requested strategy type is unsupported (no executor)")

        # invalid universe
        if not self.universe:
            reasons.append("selected universe is empty/invalid")
        if available_symbols is not None:
            missing = [s for s in self.universe if s not in available_symbols]
            if missing:
                reasons.append(f"required data unavailable for: {', '.join(missing)}")

        # benchmark accidentally tradable
        if self.benchmark and self.benchmark in self.universe:
            reasons.append(
                f"benchmark '{self.benchmark}' is also in the tradable universe "
                "(must be excluded unless intentionally included)"
            )

        # contradictory exposure constraints
        pc = self.portfolio_construction
        if pc.net_exposure_limit is not None and pc.net_exposure_limit > pc.gross_exposure_limit:
            reasons.append("net exposure limit exceeds gross exposure limit (contradiction)")

        # look-ahead / same-bar execution without explicit declaration
        if self.execution_timing == ExecutionTiming.SAME_CLOSE and "allow_same_bar" not in self.order_policy:
            reasons.append(
                "same-close execution requested but contract does not explicitly allow same-bar fills "
                "(would require look-ahead)"
            )

        # arbitrary code execution
        if self.order_policy.get("arbitrary_code") or self.entry_rules.get("arbitrary_code"):
            reasons.append("strategy requests arbitrary code execution (disallowed in v1)")

        # static allocation must have weights that sum to ~1
        if self.strategy_type == StrategyType.STATIC_ALLOCATION:
            tw = pc.target_weights
            if not tw:
                reasons.append("static allocation requires explicit target_weights")
            elif abs(sum(tw.values()) - 1.0) > 1e-6:
                reasons.append(f"static allocation target_weights sum to {sum(tw.values())}, expected 1.0")

        return reasons

    def is_executable(self, *, available_symbols: set[str] | None = None) -> bool:
        return not self.blocking_reasons(available_symbols=available_symbols)


__all__ = [
    "SCHEMA_VERSION",
    "VOLATILE_KEYS",
    "AssetClass",
    "StrategyType",
    "Frequency",
    "Weighting",
    "ExecutionTiming",
    "ClauseState",
    "SignalDefinition",
    "Clause",
    "CostModel",
    "PortfolioConstruction",
    "RiskConstraints",
    "StrategySpec",
]
