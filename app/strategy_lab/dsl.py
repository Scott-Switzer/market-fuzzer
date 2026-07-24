from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from enum import StrEnum, auto
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# --- canonical JSON + determinism helpers ---


def _canonical(obj: object) -> str:
    if isinstance(obj, Strategy):
        data = obj.model_dump(mode="json", exclude_none=True)
        for key in ["strategy_id", "approval", "provenance", "conflict_report", "is_locked"]:
            data.pop(key, None)
        return json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)
    return json.dumps(
        obj.model_dump(mode="json", exclude_none=True) if isinstance(obj, BaseModel) else obj,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def ledger_hash(obj: object) -> str:
    return hashlib.sha256(_canonical(obj).encode()).hexdigest()


# --- enums ---


class ClauseStatus(StrEnum):
    SUPPORTED_AND_COMPILED = "supported_and_compiled"
    AMBIGUOUS_REQUIRES_RESOLUTION = "ambiguous_requires_resolution"
    UNSUPPORTED_SAVED_FOR_RESEARCH = "unsupported_saved_for_research"
    REJECTED_UNSAFE_OR_INVALID = "rejected_unsafe_or_invalid"


class ClauseResolution(StrEnum):
    APPROVED = "approved"
    DEFERRED_AND_REMOVED = "deferred_and_removed"
    REPLACED = "replaced"
    PENDING = "pending"


class Action(StrEnum):
    hold = auto()
    buy = auto()
    sell = auto()
    buy_short = auto()
    sell_short = auto()
    flatten = auto()


class FillTarget(StrEnum):
    mid = auto()
    ask = auto()
    bid = auto()
    vwap = auto()


class TimeInForce(StrEnum):
    day = auto()
    gtc = auto()
    ioc = auto()


# --- primitives ---


Price = Annotated[float, Field(strict=True, ge=0)]
Pct = Annotated[float, Field(ge=0, le=1000)]
Uint = Annotated[int, Field(ge=0)]


# --- conditions ---


class AbstractCondition(BaseModel, frozen=True):
    model_config = ConfigDict(extra="forbid")


class Above(AbstractCondition):  # type: ignore[misc]
    kind: Literal["Above"] = "Above"
    indicator: Literal["sma", "rsi", "ema", "close", "volatility_regime", "rates_change", "credit_spread"] = (
        "sma"
    )
    threshold: float = Field(description="numeric threshold")


class Below(AbstractCondition):  # type: ignore[misc]
    kind: Literal["Below"] = "Below"
    indicator: Literal["sma", "rsi", "ema", "close", "volatility_regime", "rates_change", "credit_spread"] = (
        "sma"
    )
    threshold: float


class CrossedAbove(AbstractCondition):  # type: ignore[misc]
    kind: Literal["CrossedAbove"] = "CrossedAbove"
    fast: Literal["sma", "rsi", "ema", "close"] = "sma"
    slow: Literal["sma", "rsi", "ema", "close"] = "sma"
    confirmation_bars: Uint = 0


class CrossedBelow(AbstractCondition):  # type: ignore[misc]
    kind: Literal["CrossedBelow"] = "CrossedBelow"
    fast: Literal["sma", "rsi", "ema", "close"] = "sma"
    slow: Literal["sma", "rsi", "ema", "close"] = "sma"
    confirmation_bars: Uint = 0


class And(AbstractCondition):  # type: ignore[misc]
    kind: Literal["And"] = "And"
    left: Condition
    right: Condition


class Or(AbstractCondition):  # type: ignore[misc]
    kind: Literal["Or"] = "Or"
    left: Condition
    right: Condition


class Not(AbstractCondition):  # type: ignore[misc]
    kind: Literal["Not"] = "Not"
    inner: Condition


Condition = Annotated[
    Above | Below | CrossedAbove | CrossedBelow | And | Or | Not,
    Field(discriminator="kind"),
]

# --- clause ledger ---


class ClauseLedgerEntry(BaseModel, frozen=True):
    clause_id: str
    original_text: str
    normalized_text: str | None = None
    status: ClauseStatus
    reason: str | None = None
    user_resolution: ClauseResolution = ClauseResolution.PENDING
    compiler_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    provenance: dict[str, Any] = Field(default_factory=dict)


# --- clauses ---


class AbstractClause(BaseModel, frozen=True):
    model_config = ConfigDict(extra="forbid")


class Hold(AbstractClause):  # type: ignore[misc]
    kind: Literal["Hold"] = "Hold"
    when: Condition | None = None
    note: str | None = None


class SmaCrossover(AbstractClause):  # type: ignore[misc]
    kind: Literal["SmaCrossover"] = "SmaCrossover"
    fast: Uint = Field(ge=2, le=500)
    slow: Uint = Field(ge=2, le=500)
    confirmation_bars: Uint = 0

    @model_validator(mode="after")
    def ensure_fast_below_slow(self) -> SmaCrossover:
        if self.fast >= self.slow:
            raise ValueError("fast must be strictly less than slow")
        return self


class RsiReversion(AbstractClause):  # type: ignore[misc]
    kind: Literal["RsiReversion"] = "RsiReversion"
    period: Uint = Field(ge=2, le=200)
    oversold: float = Field(ge=0, le=100)
    overbought: float = Field(ge=0, le=100)


class ValueQualityLongShort(AbstractClause):  # type: ignore[misc]
    kind: Literal["ValueQualityLongShort"] = "ValueQualityLongShort"
    long_top_n: Uint = Field(ge=0)
    short_bottom_n: Uint = Field(ge=0)
    beta_neutralize: bool = False
    hedge_universe: str | None = None


class BetaNeutralFactor(AbstractClause):  # type: ignore[misc]
    kind: Literal["BetaNeutralFactor"] = "BetaNeutralFactor"
    target_beta: float = Field(ge=-2, le=2)
    lookback: Uint = Field(ge=20, le=252)
    hedge_universe: str = Field(min_length=1)


class MacroGate(AbstractClause):  # type: ignore[misc]
    kind: Literal["MacroGate"] = "MacroGate"
    indicator: Literal["volatility_regime", "rates_change", "credit_spread"] = "volatility_regime"
    threshold: float
    retract_by_bar: Uint = Field(ge=1)
    action_on_breach: Literal["hold", "flatten"] = "hold"
    note: str | None = None


class LiquidityFallback(AbstractClause):  # type: ignore[misc]
    kind: Literal["LiquidityFallback"] = "LiquidityFallback"
    min_adv: Uint = Field(ge=1)
    fallback_symbol: str = Field(default="CASH", min_length=1)


class CostCap(AbstractClause):  # type: ignore[misc]
    kind: Literal["CostCap"] = "CostCap"
    max_bps: Pct


ExecutableClause = Annotated[
    Hold
    | SmaCrossover
    | RsiReversion
    | ValueQualityLongShort
    | BetaNeutralFactor
    | MacroGate
    | LiquidityFallback
    | CostCap,
    Field(discriminator="kind"),
]

# --- execution policy ---


class ExecutionPolicy(BaseModel, frozen=True):
    fill_target: FillTarget = FillTarget.mid
    max_order_qty: Uint
    time_in_force: TimeInForce = TimeInForce.day
    allow_fill_outside_regime: bool = False
    max_open_positions: Uint = 0
    max_exposure_per_symbol: float | None = None


# --- strategy root ---


class ConflictReport(BaseModel, frozen=True):
    ambiguous_due_to_missing_order: Sequence[str] = ()
    contradictory_pairs: Sequence[tuple[int, int]] = ()
    write_after_hold_warnings: Sequence[int] = ()


class OrderedClause(BaseModel, frozen=True):
    order: Uint
    clause: ExecutableClause | None = None
    clause_id: str = ""

    @model_validator(mode="after")
    def attach_id(self) -> OrderedClause:
        object.__setattr__(self, "clause_id", f"c_{self.order}")
        return self


StrategyClause = Annotated[
    SmaCrossover
    | RsiReversion
    | ValueQualityLongShort
    | BetaNeutralFactor
    | MacroGate
    | LiquidityFallback
    | CostCap
    | Hold,
    Field(discriminator="kind"),
]


class Strategy(BaseModel, frozen=True, extra="forbid"):
    strategy_id: str = Field(default="", description="deterministic ledger hash identifier")
    schema_version: Literal["v1"] = "v1"
    family: str
    name: str | None = None
    description: str | None = None
    description_original: str | None = None
    is_locked: bool = False
    execution_policy: ExecutionPolicy
    clauses: Sequence[OrderedClause]
    approval: dict[str, Any] | None = None
    provenance: dict[str, Any] | None = None
    clause_ledger: list[ClauseLedgerEntry] = Field(default_factory=list)
    universe: dict[str, Any] | None = None
    frequency: dict[str, Any] | None = None
    portfolio: dict[str, Any] | None = None
    risk: dict[str, Any] | None = None
    execution: dict[str, Any] | None = None
    benchmark: dict[str, Any] | None = None

    @property
    def ledger_hash(self) -> str:
        if self.strategy_id:
            return self.strategy_id
        return ledger_hash(self)

    @model_validator(mode="after")
    def validate_and_finalize(self) -> Strategy:
        orders = [c.order for c in self.clauses]
        if len(orders) != len(set(orders)):
            raise ValueError("clause orders must be unique")
        if not self.strategy_id:
            object.__setattr__(self, "strategy_id", self.ledger_hash)
        return self

    @model_validator(mode="after")
    def enforce_short_sale_invariant(self) -> Strategy:
        for item in self.clauses:
            clause = item.clause
            if isinstance(clause, ValueQualityLongShort):
                if clause.short_bottom_n > 0 and clause.long_top_n == 0:
                    raise ValueError("pure short without hedge is disallowed")
                if clause.beta_neutralize and not clause.hedge_universe:
                    raise ValueError("beta_neutralize=True requires hedge_universe")
        return self

    def resolve_conflicts(self) -> ConflictReport:
        orders = sorted({c.order for c in self.clauses})
        ambiguities: list[str] = []
        if not orders or orders[0] != 0 or orders != list(range(min(orders), max(orders) + 1)):
            ambiguities.append("clause orders are not a continuous prefix starting at 0")
        write_after_hold_warnings = [
            item.order for item in self.clauses if isinstance(item.clause, (LiquidityFallback, CostCap))
        ]
        object.__setattr__(
            self,
            "conflict_report",
            ConflictReport(
                ambiguous_due_to_missing_order=ambiguities,
                write_after_hold_warnings=write_after_hold_warnings,
            ).model_dump(mode="json"),
        )
        return ConflictReport(
            ambiguous_due_to_missing_order=ambiguities,
            write_after_hold_warnings=write_after_hold_warnings,
        )


__all__ = [
    "Strategy",
    "OrderedClause",
    "ExecutionPolicy",
    "ClauseLedgerEntry",
    "ClauseStatus",
    "ClauseResolution",
    "Condition",
    "AbstractCondition",
    "Hold",
    "SmaCrossover",
    "RsiReversion",
    "ValueQualityLongShort",
    "BetaNeutralFactor",
    "MacroGate",
    "LiquidityFallback",
    "CostCap",
    "ConflictReport",
    "Action",
    "FillTarget",
    "TimeInForce",
    "ledger_hash",
    "Pct",
    "Uint",
    "Price",
]
