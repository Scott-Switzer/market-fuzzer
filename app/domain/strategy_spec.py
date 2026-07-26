"""Canonical, versioned Fenrix strategy specification (schema strategy-spec/v1.1).

``StrategySpec`` is the ONE authoritative strategy contract. Every compiler,
template, backtest, synthetic run, exchange replay, and evidence package consumes
the same approved object.

Correctness properties (reset brief Phase 1.1):

* **Identity is NOT content.** ``strategy_id`` is a stable UUID for the logical
  strategy across versions; it is never derived from content. ``canonical_hash``
  is a deterministic hash of the version's *semantic* content and is a computed
  property (never a stored field), so it can never drift from the content.
* **Decimal for contract-level numbers.** Weights, exposure limits, cost bps and
  thresholds that enter the canonical hash are ``Decimal`` normalized to a
  canonical form, so ``0.60`` and ``0.6`` hash identically and floats never leak
  binary-representation noise into the hash. NaN / +-inf are rejected.
* **Never silently drop or reinterpret a clause.** Unsupported/unresolved clauses
  are preserved and block execution.
* **Fail closed.** ``blocking_reasons`` returns every reason execution must be
  blocked; empty means executable. A strategy type is executable only when the
  caller passes it in ``supported_types`` (the registry decides -- the domain
  never imports the registry).

The draft ``StrategySpec`` is frozen at the top level: transform it only by
constructing and validating a NEW model, never by in-place mutation. The
immutable *approved* snapshot lives in ``strategy_version.ApprovedStrategyVersion``.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION = "strategy-spec/v1.1"

# Keys excluded from the canonical serialization used for the hash. These are
# identity/volatile OR human-facing intent / compiler-trace metadata: they change
# without changing the *executable* strategy, so they must never influence
# ``canonical_hash``. Kept in ONE place so every consumer excludes the SAME keys
# (a divergence here silently breaks the hash invariant). Consequence (reset brief
# item 26): a template and its plain-English equivalent hash identically when all
# executable fields match, even though their ``name``/``original_thesis`` differ.
VOLATILE_KEYS: frozenset[str] = frozenset(
    {
        # identity / versioning
        "strategy_id",
        "strategy_version",
        "compiler_metadata",
        # human-facing intent (not executable)
        "name",
        "original_thesis",
        "intended_use",
        "known_limitations",
        "expected_failure_conditions",
        # compiler trace / clause ledger (not executable)
        "clauses",
        "unsupported_clauses",
        "assumptions",
        "user_resolutions",
    }
)

# Tolerance for "weights sum to exactly 1" checks, in Decimal space.
WEIGHT_SUM_TOLERANCE = Decimal("1e-9")


# ---------------------------------------------------------------------------
# Decimal contract-number type
# ---------------------------------------------------------------------------
def _to_canonical_decimal(v: Any) -> Decimal:
    """Coerce int/float/str/Decimal to a finite, canonically-normalized Decimal.

    * floats go through ``str`` first so we get ``Decimal('0.1')`` not the binary
      expansion ``Decimal('0.1000000000000000055...')``;
    * NaN / +-inf are rejected;
    * the value is normalized to fixed-point canonical form (no trailing zeros,
      no scientific notation) so equal values share one representation.
    """
    if isinstance(v, bool):  # bool is an int subclass; reject to avoid surprises
        raise ValueError("boolean is not a valid decimal contract number")
    try:
        d = v if isinstance(v, Decimal) else Decimal(str(v))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"invalid decimal value: {v!r}") from exc
    if not d.is_finite():
        raise ValueError(f"decimal must be finite, got {v!r}")
    # format(..., 'f') forces fixed-point; normalize() drops trailing zeros.
    return Decimal(format(d.normalize(), "f"))


FinDecimal = Annotated[Decimal, BeforeValidator(_to_canonical_decimal)]


def _canonical_json_default(o: Any) -> Any:
    if isinstance(o, Decimal):
        return format(o.normalize(), "f")
    raise TypeError(f"not JSON serializable: {type(o)!r}")


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------
class AssetClass(StrEnum):
    EQUITY = "equity"
    ETF = "etf"
    FUTURES = "futures"
    FX = "fx"
    CRYPTO = "crypto"


class StrategyType(StrEnum):
    """Executable strategy families. A type is only *executable* when a concrete
    executor is registered for it (the registry -- not this enum -- is the source
    of truth for support)."""

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
    FIXED = "fixed"  # explicit target weights (static allocation)


class ExecutionTiming(StrEnum):
    NEXT_OPEN = "next_open"  # decide at close(t), fill at open(t+1) -- default
    SAME_CLOSE = "same_close"  # only valid when order_policy.allow_same_bar


class ClauseState(StrEnum):
    RESOLVED = "resolved"
    UNRESOLVED = "unresolved"
    UNSUPPORTED = "unsupported"
    REJECTED = "rejected"


class OrderType(StrEnum):
    MARKET = "market"
    LIMIT = "limit"


class TimeInForce(StrEnum):
    DAY = "day"
    GTC = "gtc"


# ---------------------------------------------------------------------------
# Typed signal definitions (discriminated union on ``kind``)
# ---------------------------------------------------------------------------
class _SignalBase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str


class MomentumSignal(_SignalBase):
    kind: Literal["momentum"] = "momentum"
    lookback: int = Field(default=252, ge=2)  # total window (trading bars)
    skip: int = Field(default=21, ge=0)  # recent bars skipped (12-1 => skip 21)

    @model_validator(mode="after")
    def _check(self) -> MomentumSignal:
        if self.skip >= self.lookback:
            raise ValueError("momentum skip must be < lookback")
        return self


class RealizedVolatilitySignal(_SignalBase):
    kind: Literal["realized_volatility"] = "realized_volatility"
    lookback: int = Field(default=63, ge=2)


class SimpleMovingAverageSignal(_SignalBase):
    kind: Literal["sma"] = "sma"
    fast_window: int = Field(ge=1)
    slow_window: int = Field(ge=2)

    @model_validator(mode="after")
    def _check(self) -> SimpleMovingAverageSignal:
        if self.fast_window >= self.slow_window:
            raise ValueError("fast_window must be < slow_window")
        return self


class RelativeMomentumSignal(_SignalBase):
    kind: Literal["relative_momentum"] = "relative_momentum"
    lookback: int = Field(default=252, ge=2)


class TrendFilterSignal(_SignalBase):
    kind: Literal["trend_filter"] = "trend_filter"
    window: int = Field(default=200, ge=2)


SignalDefinition = Annotated[
    MomentumSignal
    | RealizedVolatilitySignal
    | SimpleMovingAverageSignal
    | RelativeMomentumSignal
    | TrendFilterSignal,
    Field(discriminator="kind"),
]


# ---------------------------------------------------------------------------
# Clause ledger
# ---------------------------------------------------------------------------
class Clause(BaseModel):
    """A single interpreted unit of the user's thesis. Never dropped."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    original_text: str
    normalized_text: str | None = None
    state: ClauseState = ClauseState.UNRESOLVED
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    reason: str | None = None
    resolution: str | None = None


# ---------------------------------------------------------------------------
# Cost model / order policy / portfolio construction / risk
# ---------------------------------------------------------------------------
class CostModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    commission_bps: FinDecimal = Decimal("5")
    spread_bps: FinDecimal = Decimal("2")
    slippage_bps: FinDecimal = Decimal("3")
    borrow_bps_annual: FinDecimal = Decimal("50")  # accrues only while short
    locate_bps: FinDecimal = Decimal("10")  # one-time on new shorts
    model_type: str = "heuristic_flat_bps"
    calibrated: bool = False

    @field_validator("commission_bps", "spread_bps", "slippage_bps", "borrow_bps_annual", "locate_bps")
    @classmethod
    def _nonneg(cls, v: Decimal) -> Decimal:
        if v < 0:
            raise ValueError("cost bps must be non-negative")
        return v


class OrderPolicy(BaseModel):
    """Typed order policy. ``SAME_CLOSE`` execution is only legal when
    ``allow_same_bar`` is truthy -- a present-but-false key must NOT pass."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    order_type: OrderType = OrderType.MARKET
    time_in_force: TimeInForce = TimeInForce.DAY
    allow_same_bar: bool = False
    arbitrary_code: bool = False  # always blocks execution when True


class PortfolioConstruction(BaseModel):
    """Selection + weighting only. Exposure/risk limits live in RiskConstraints
    (single source of truth -- reset brief Phase 1.1 item 6)."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    long_short: bool = False
    weighting: Weighting = Weighting.EQUAL
    long_count: int | None = Field(default=None, ge=0)
    short_count: int | None = Field(default=None, ge=0)
    long_quantile: FinDecimal | None = None
    short_quantile: FinDecimal | None = None
    # explicit target weights by symbol (static / tactical allocation)
    target_weights: dict[str, FinDecimal] = Field(default_factory=dict)

    @field_validator("long_quantile", "short_quantile")
    @classmethod
    def _quantile_range(cls, v: Decimal | None) -> Decimal | None:
        if v is not None and not (Decimal(0) <= v <= Decimal(1)):
            raise ValueError("quantile must be in [0, 1]")
        return v

    @field_validator("target_weights")
    @classmethod
    def _weights_finite_nonneg(cls, v: dict[str, Decimal]) -> dict[str, Decimal]:
        for sym, w in v.items():
            if w < 0:
                raise ValueError(f"target weight for {sym!r} is negative ({w}); shorts not allowed here")
        return v


class RiskConstraints(BaseModel):
    """Single source of truth for exposure/risk limits."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    gross_exposure_limit: FinDecimal = Decimal("1.0")
    net_exposure_target: FinDecimal | None = None
    net_exposure_min: FinDecimal | None = None
    net_exposure_max: FinDecimal | None = None
    max_position_weight: FinDecimal = Decimal("0.10")
    sector_or_factor_constraints: dict[str, Any] = Field(default_factory=dict)

    @field_validator("gross_exposure_limit", "max_position_weight")
    @classmethod
    def _positive(cls, v: Decimal) -> Decimal:
        if v < 0:
            raise ValueError("exposure/position limit must be non-negative")
        return v


# ---------------------------------------------------------------------------
# Universe normalization
# ---------------------------------------------------------------------------
_TICKER_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,14}$")


def _normalize_universe(raw: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, str):
            raise ValueError(f"universe entries must be strings, got {item!r}")
        sym = item.strip().upper()
        if not sym:
            raise ValueError("universe contains a blank ticker")
        if not _TICKER_RE.match(sym):
            raise ValueError(f"invalid ticker symbol: {item!r}")
        if sym in seen:
            raise ValueError(f"duplicate ticker after normalization: {sym!r}")
        seen.add(sym)
        out.append(sym)  # deterministic: preserve input order
    return out


# ---------------------------------------------------------------------------
# StrategySpec
# ---------------------------------------------------------------------------
class StrategySpec(BaseModel):
    """The canonical strategy contract (draft/executable form)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # --- identity / versioning (VOLATILE -- excluded from hash) ---
    schema_version: str = SCHEMA_VERSION
    strategy_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    strategy_version: int = Field(default=1, ge=1)

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
    benchmark_tradable: bool = False
    data_requirements: list[str] = Field(default_factory=list)
    frequency: Frequency = Frequency.MONTHLY
    lookback_windows: dict[str, int] = Field(default_factory=dict)

    # --- signal / selection ---
    signal_definitions: list[SignalDefinition] = Field(default_factory=list)
    signal_combination: dict[str, FinDecimal] = Field(default_factory=dict)
    ranking_or_threshold_rules: dict[str, Any] = Field(default_factory=dict)
    entry_rules: dict[str, Any] = Field(default_factory=dict)
    exit_rules: dict[str, Any] = Field(default_factory=dict)

    # --- portfolio / risk / execution ---
    portfolio_construction: PortfolioConstruction = Field(default_factory=PortfolioConstruction)
    long_short_policy: str = "long_only"  # long_only | long_short | dollar_neutral
    position_sizing: str = "equal_weight"
    rebalance_policy: Frequency = Frequency.MONTHLY
    execution_timing: ExecutionTiming = ExecutionTiming.NEXT_OPEN
    order_policy: OrderPolicy = Field(default_factory=OrderPolicy)
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
    # validators
    # ------------------------------------------------------------------
    @field_validator("name")
    @classmethod
    def _name_nonblank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("name must be non-blank")
        return v

    @field_validator("original_thesis")
    @classmethod
    def _thesis_min_length(cls, v: str) -> str:
        if len(re.sub(r"\s", "", v)) < 10:
            raise ValueError("original_thesis must have at least 10 non-whitespace characters")
        return v

    @field_validator("universe")
    @classmethod
    def _norm_universe(cls, v: list[str]) -> list[str]:
        return _normalize_universe(v)

    @field_validator("benchmark")
    @classmethod
    def _norm_benchmark(cls, v: str | None) -> str | None:
        if v is None:
            return None
        sym = v.strip().upper()
        if not sym or not _TICKER_RE.match(sym):
            raise ValueError(f"invalid benchmark symbol: {v!r}")
        return sym

    # ------------------------------------------------------------------
    # canonical hashing (hash is a COMPUTED property -- no stored field)
    # ------------------------------------------------------------------
    def canonical_dict(self) -> dict[str, Any]:
        """Normalized, hash-stable serialization (volatile keys removed)."""
        data = self.model_dump(mode="python", exclude_none=False)
        for key in VOLATILE_KEYS:
            data.pop(key, None)
        return data

    def canonical_json(self) -> str:
        return json.dumps(
            self.canonical_dict(),
            sort_keys=True,
            separators=(",", ":"),
            default=_canonical_json_default,
        )

    def compute_hash(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    def full_json(self) -> str:
        """Complete, deterministic serialization for durable STORAGE/reconstruction.

        Unlike ``canonical_json`` (which drops volatile/intent keys so the hash is
        stable), this keeps every field EXCEPT pure identity/versioning (which the
        approved envelope carries separately). It lets an approved snapshot be
        reconstructed byte-for-byte including ``name``/``original_thesis``.
        """
        data = self.model_dump(mode="python", exclude_none=False)
        for key in ("strategy_id", "strategy_version", "compiler_metadata"):
            data.pop(key, None)
        return json.dumps(data, sort_keys=True, separators=(",", ":"), default=_canonical_json_default)

    @property
    def canonical_hash(self) -> str:
        """Deterministic hash of the version's semantic content.

        Computed every access, so it can never drift from the content.
        """
        return self.compute_hash()

    # ------------------------------------------------------------------
    # execution gating (fail closed)
    # ------------------------------------------------------------------
    def blocking_reasons(
        self,
        *,
        available_symbols: set[str] | None = None,
        supported_types: set[StrategyType] | None = None,
    ) -> list[str]:
        """Every reason execution must be blocked. Empty == executable.

        ``supported_types`` is supplied by the caller (the executor registry);
        the domain never imports the registry. When omitted, only ``UNSUPPORTED``
        is treated as unexecutable (pure-domain checks).
        """
        reasons: list[str] = []

        for c in self.clauses:
            if c.state == ClauseState.UNRESOLVED:
                reasons.append(f"clause '{c.id}' is unresolved")
            if c.state == ClauseState.REJECTED:
                reasons.append(f"clause '{c.id}' was rejected as unsafe/invalid")
        if self.unsupported_clauses:
            ids = ", ".join(c.id for c in self.unsupported_clauses)
            reasons.append(f"unsupported clauses present and unresolved: {ids}")

        # strategy type support
        if self.strategy_type == StrategyType.UNSUPPORTED:
            reasons.append("requested strategy type is unsupported (no executor)")
        elif supported_types is not None and self.strategy_type not in supported_types:
            reasons.append(
                f"strategy type '{self.strategy_type}' has no registered executor "
                f"(supported: {sorted(t.value for t in supported_types)})"
            )

        # universe
        if not self.universe:
            reasons.append("selected universe is empty/invalid")
        if available_symbols is not None:
            missing = [s for s in self.universe if s not in available_symbols]
            if missing:
                reasons.append(f"required data unavailable for: {', '.join(missing)}")

        # benchmark tradability
        if self.benchmark and self.benchmark in self.universe and not self.benchmark_tradable:
            reasons.append(
                f"benchmark '{self.benchmark}' is in the tradable universe but "
                "benchmark_tradable is false (set benchmark_tradable=true to include it intentionally)"
            )

        # contradictory exposure constraints (single source of truth: risk_constraints)
        rc = self.risk_constraints
        gross = rc.gross_exposure_limit
        if rc.net_exposure_target is not None and abs(rc.net_exposure_target) > gross:
            reasons.append("abs(net exposure target) exceeds gross exposure limit (contradiction)")
        if (
            rc.net_exposure_min is not None
            and rc.net_exposure_max is not None
            and rc.net_exposure_min > rc.net_exposure_max
        ):
            reasons.append("net exposure min exceeds net exposure max (contradiction)")

        # look-ahead / same-bar execution without explicit, TRUE allow flag
        if self.execution_timing == ExecutionTiming.SAME_CLOSE and not self.order_policy.allow_same_bar:
            reasons.append(
                "same-close execution requested but order_policy.allow_same_bar is not true "
                "(would require look-ahead)"
            )

        # arbitrary code execution
        if self.order_policy.arbitrary_code or bool(self.entry_rules.get("arbitrary_code")):
            reasons.append("strategy requests arbitrary code execution (disallowed in v1)")

        # static allocation weight integrity
        if self.strategy_type == StrategyType.STATIC_ALLOCATION:
            reasons.extend(self._static_allocation_reasons())

        return reasons

    def _static_allocation_reasons(self) -> list[str]:
        reasons: list[str] = []
        tw = self.portfolio_construction.target_weights
        if not tw:
            reasons.append("static allocation requires explicit target_weights")
            return reasons
        uni = set(self.universe)
        for sym in tw:
            if sym not in uni:
                reasons.append(f"static allocation weight references {sym!r} outside the universe")
        total = sum((_to_canonical_decimal(w) for w in tw.values()), Decimal(0))
        if abs(total - Decimal(1)) > WEIGHT_SUM_TOLERANCE:
            reasons.append(f"static allocation target_weights sum to {total}, expected 1")
        return reasons

    def is_executable(
        self,
        *,
        available_symbols: set[str] | None = None,
        supported_types: set[StrategyType] | None = None,
    ) -> bool:
        return not self.blocking_reasons(available_symbols=available_symbols, supported_types=supported_types)


__all__ = [
    "SCHEMA_VERSION",
    "VOLATILE_KEYS",
    "WEIGHT_SUM_TOLERANCE",
    "FinDecimal",
    "AssetClass",
    "StrategyType",
    "Frequency",
    "Weighting",
    "ExecutionTiming",
    "ClauseState",
    "OrderType",
    "TimeInForce",
    "MomentumSignal",
    "RealizedVolatilitySignal",
    "SimpleMovingAverageSignal",
    "RelativeMomentumSignal",
    "TrendFilterSignal",
    "SignalDefinition",
    "Clause",
    "CostModel",
    "OrderPolicy",
    "PortfolioConstruction",
    "RiskConstraints",
    "StrategySpec",
]
