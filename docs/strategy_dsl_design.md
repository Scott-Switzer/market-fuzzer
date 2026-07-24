# Safe Internal Strategy DSL for MVP

## 1. Problem Statement

The current strategy compiler (`app/break_test/strategy_compiler.py`) is a keyword-cluster router over plain-text tuples that template-emits `def strategy(...)` code strings. This is not a Clause Ledger. There is no:

- executable semantic model inside the serializer,
- canonical hash,
- deterministic comparison,
- ambiguity detection when multiple clause semantics overlap,
- compile-time mutation-order normalization.

The goal is a statically typed, JSON-convertible, schema-validated internal DSL that compiles to executable objects, derives a deterministic `strategy_id`, enforces explicit `schema_version`, and detects clause-state collisions before runtime.

## 2. Proposed Schema Sketch

A `Strategy` is a discriminated-union root with a deterministic identifier and an ordered clause ledger.

```m3
Strategy = {
  strategy_id: SHA256<canonical(Strategy.root)>,
  schema_version: "v1",
  family: string,
  description: string?,
  execution_policy: ExecutionPolicy,
  clauses: OrderedClauseLedger[Clause]
}

OrderedClauseLedger[Clause] = { order: uint, clause: Clause }*
```

### AbstractClause (discriminant: kind)

```m3
AbstractClause
  | Hold { when: Condition?, note: string? }
  | SmaCrossover { fast: uint { 2..500 }, slow: uint { 2..500 }, confirmation_bars: uint = 0 }
  | RsiReversion { period: uint { 2..200 }, oversold: float { 0..100 }, overbought: float { 0..100 } }
  | ValueQualityLongShort { long_top_n: uint, short_bottom_n: uint, beta_neutralize: bool = false, hedge_universe: string? }
  | BetaNeutralFactor { target_beta: float { -2..2 }, lookback: uint { 20..252 }, hedge_universe: string }
  | MacroGate { indicator: "volatility_regime" | "rates_change" | "credit_spread", threshold: float, retract_by_bar: uint { 1.. }, action_on_breach: "hold" | "flatten" = "hold", note: string? }
  | LiquidityFallback { min_adv: uint, fallback_symbol: string = "CASH" }
  | CostCap { max_bps: float { 0..1000 } }
```

### AbstractCondition (discriminant: kind)

```m3
AbstractCondition
  | Above { indicator: Indicator, threshold: float }
  | Below { indicator: Indicator, threshold: float }
  | CrossedAbove { fast: Indicator, slow: Indicator, confirmation_bars: uint = 0 }
  | CrossedBelow { fast: Indicator, slow: Indicator, confirmation_bars: uint = 0 }
  | And { left: Condition, right: Condition }
  | Or { left: Condition, right: Condition }
  | Not { inner: Condition }
```

### ExecutionPolicy

```m3
ExecutionPolicy = {
  fill_target: "mid" | "ask" | "bid" | "vwap" = "mid",
  max_order_qty: uint,
  time_in_force: "day" | "gtc" | "ioc" = "day",
  allow_fill_outside_regime: bool = false,
  max_open_positions: uint = 0,
  max_exposure_per_symbol: float?
}
```

## 3. Pydantic + Discriminated Unions DSL

```m3
from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from enum import StrEnum, auto
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator


# canonical JSON + determinism helpers
def _canonical(obj: object) -> str:
    return json.dumps(
        obj.model_dump(mode="json", exclude_none=True) if isinstance(obj, BaseModel) else obj,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def ledger_hash(obj: object) -> str:
    return hashlib.sha256(_canonical(obj).encode()).hexdigest()


# enums
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


# primitives
Pct = Annotated[float, Field(ge=0, le=1000)]
Uint = Annotated[int, Field(ge=0)]


# conditions
class AbstractCondition(BaseModel, frozen=True):
    model_config = ConfigDict(extra="forbid")


class Above(AbstractCondition):
    kind: Literal["Above"] = "Above"
    indicator: Literal["sma", "rsi", "ema", "close", "volatility_regime", "rates_change", "credit_spread"]
    threshold: float


class Below(AbstractCondition):
    kind: Literal["Below"] = "Below"
    indicator: Literal["sma", "rsi", "ema", "close", "volatility_regime", "rates_change", "credit_spread"]
    threshold: float


class CrossedAbove(AbstractCondition):
    kind: Literal["CrossedAbove"] = "CrossedAbove"
    fast: Literal["sma", "rsi", "ema", "close"]
    slow: Literal["sma", "rsi", "ema", "close"]
    confirmation_bars: Uint = 0


class CrossedBelow(AbstractCondition):
    kind: Literal["CrossedBelow"] = "CrossedBelow"
    fast: Literal["sma", "rsi", "ema", "close"]
    slow: Literal["sma", "rsi", "ema", "close"]
    confirmation_bars: Uint = 0


class And(AbstractCondition):
    kind: Literal["And"] = "And"
    left: Condition
    right: Condition


class Or(AbstractCondition):
    kind: Literal["Or"] = "Or"
    left: Condition
    right: Condition


class Not(AbstractCondition):
    kind: Literal["Not"] = "Not"
    inner: Condition


Condition = Annotated[
    Union[Above, Below, CrossedAbove, CrossedBelow, And, Or, Not],
    Field(discriminator="kind"),
]


# clauses
class AbstractClause(BaseModel, frozen=True):
    model_config = ConfigDict(extra="forbid")


class Hold(AbstractClause):
    kind: Literal["Hold"] = "Hold"
    when: Condition | None = None
    note: str | None = None


class SmaCrossover(AbstractClause):
    kind: Literal["SmaCrossover"] = "SmaCrossover"
    fast: Uint = Field(ge=2, le=500)
    slow: Uint = Field(ge=2, le=500)
    confirmation_bars: Uint = 0


class RsiReversion(AbstractClause):
    kind: Literal["RsiReversion"] = "RsiReversion"
    period: Uint = Field(ge=2, le=200)
    oversold: float = Field(ge=0, le=100)
    overbought: float = Field(ge=0, le=100)


class ValueQualityLongShort(AbstractClause):
    kind: Literal["ValueQualityLongShort"] = "ValueQualityLongShort"
    long_top_n: Uint = Field(ge=0)
    short_bottom_n: Uint = Field(ge=0)
    beta_neutralize: bool = False
    hedge_universe: str | None = None


class BetaNeutralFactor(AbstractClause):
    kind: Literal["BetaNeutralFactor"] = "BetaNeutralFactor"
    target_beta: float = Field(ge=-2, le=2)
    lookback: Uint = Field(ge=20, le=252)
    hedge_universe: str


class MacroGate(AbstractClause):
    kind: Literal["MacroGate"] = "MacroGate"
    indicator: Literal["volatility_regime", "rates_change", "credit_spread"]
    threshold: float
    retract_by_bar: Uint = Field(ge=1)
    action_on_breach: Literal["hold", "flatten"] = "hold"
    note: str | None = None


class LiquidityFallback(AbstractClause):
    kind: Literal["LiquidityFallback"] = "LiquidityFallback"
    min_adv: Uint = Field(ge=1)
    fallback_symbol: str = "CASH"


class CostCap(AbstractClause):
    kind: Literal["CostCap"] = "CostCap"
    max_bps: Pct


Clause = Annotated[
    Union[
        Hold,
        SmaCrossover,
        RsiReversion,
        ValueQualityLongShort,
        BetaNeutralFactor,
        MacroGate,
        LiquidityFallback,
        CostCap,
    ],
    Field(discriminator="kind"),
]


# execution policy
class ExecutionPolicy(BaseModel, frozen=True):
    fill_target: FillTarget = FillTarget.mid
    max_order_qty: Uint
    time_in_force: TimeInForce = TimeInForce.day
    allow_fill_outside_regime: bool = False
    max_open_positions: Uint = 0
    max_exposure_per_symbol: float | None = None


# strategy root
class ConflictReport(BaseModel, frozen=True):
    ambiguous_due_to_missing_order: Sequence[str] = ()
    contradictory_pairs: Sequence[tuple[int, int]] = ()
    write_after_hold_warnings: Sequence[int] = ()


class OrderedClause(BaseModel, frozen=True):
    order: Uint
    clause: Clause
    clause_id: str = ""

    @model_validator(mode="after")
    def attach_id(self) -> "OrderedClause":
        object.__setattr__(self, "clause_id", f"c_{self.order}")
        return self


class Strategy(BaseModel, frozen=True, extra="forbid"):
    strategy_id: str = Field(default="", description="deterministic ledger hash identifier")
    schema_version: Literal["v1"] = "v1"
    family: str
    description: str | None = None
    execution_policy: ExecutionPolicy
    clauses: Sequence[OrderedClause]

    @computed_field
    @property
    def ledger_hash(self) -> str:
        return self.strategy_id or ledger_hash(self)

    @model_validator(mode="after")
    def validate_and_finalize(self) -> "Strategy":
        orders = [c.order for c in self.clauses]
        if len(orders) != len(set(orders)):
            raise ValueError("clause orders must be unique")

        for item in self.clauses:
            clause = item.clause
            if isinstance(clause, ValueQualityLongShort):
                if clause.short_bottom_n > 0 and clause.long_top_n == 0:
                    raise ValueError("pure short without hedge is disallowed")
                if clause.beta_neutralize and not clause.hedge_universe:
                    raise ValueError("beta_neutralize=True requires hedge_universe")

        if not self.strategy_id:
            object.__setattr__(self, "strategy_id", ledger_hash(self))
        object.__setattr__(self, "_clause_map", {item.order: item.clause for item in self.clauses})
        return self

    def resolve_conflicts(self) -> ConflictReport:
        orders = sorted({c.order for c in self.clauses})
        ambiguities: list[str] = []
        if not orders or orders[0] != 0 or orders != list(range(min(orders), max(orders) + 1)):
            ambiguities.append("clause orders are not a continuous prefix starting at 0")

        write_after_hold_warnings = [item.order for item in self.clauses if isinstance(item.clause, (LiquidityFallback, CostCap))]
        return ConflictReport(
            ambiguous_due_to_missing_order=ambiguities,
            write_after_hold_warnings=write_after_hold_warnings,
        )
```

## 4. Validation Invariants

A compilation pipeline should enforce the following at parse time.

1. **Schema Version Pinning**---`schema_version` is explicit. A v2 payload with a v1 parser must fail closed.
2. **Order Uniqueness**---each clause in the ledger must have a unique integer `order`; duplicate orders are rejected before compilation.
3. **Startup Warm-Up Safety**---if a clause uses a lookback indicator, `observations` length must satisfy:
   `warmup_bars >= max(fast, slow, period) + 1`.
4. **Circular Mutation Prevention**---compiled clauses are stateless predicates over `(state, observation)` that return an `Action`; a one-pass sequence compiler eliminates write/read races within a single bar.
5. **Quantity Bounds**---all runtime quantities are bounded using the execution policy’s `max_order_qty`, `lot_size`, and a hard notional ceiling, e.g. `1_000_000_000`.
6. **Determinism Reproducibility**---given the same `Strategy`, `seed`, and `world_id`, the compiled artifact is byte-identical.
7. **Short-Sale Invariant**---if `short_bottom_n > 0` then `long_top_n > 0` is required.
8. **Beta-Capped Exposure**---when `target_beta` is active, the submitted position is checked against the exposure ceiling before entering the order book.

## 5. Canonical Representation and Hashing

The canonical form is deterministic JSON with sorted keys, no whitespace, and stable conversion for non-JSON-safe objects:

```m3
canonical(x) := JSON(x.model_dump(mode="json", exclude_none=True), sort_keys=True, separators=(",", ":"), default=str)
ledger_hash(x) := SHA256(canonical(x))
```

For multi-clause strategies, clause-set hashes sort by `order` first, so different JSON key orders in the incoming payload still converge to a single digest.

## 6. Compilation Architecture

```m3
StrategyDSL
  parser
    strategy_loader                           # JSON -> Strategy
    validator                                 # invariants 1-8 + schema constraints
    canon                                     # deterministic canonicalization
  compiler
    primitive_sandbox                         # SMA, RSI, macro-gateeval layers
    sequence_compiler                         # compiled_at -> Sequence[CompiledClause]
    portfolio_state_machine                   # action gating and state transition
    rendered_executor                         # emits an exec() contract for the exchange
  runtime
    executor                                  # compiled sequence on seed/world
    state_transition_audit                    # immutable transition ledgers
    determinism_assert                        # same inputs -> identical outputs
  cli
    compile
    validate
    hash
```

The compiler turns each `Clause` into a predicate/action pair over `(state, observation)` that can run forward in one pass over observations. The executor then iterates the ordered clause ledger and emits only the highest-priority non-`Hold` action each bar.

## 7. Example Compilations

These show the DSL surface, not the runtime engine.

**SMA crossover with cost cap**

```json
{
  "schema_version": "v1",
  "family": "Trending Momentum",
  "description": "Fast/slow SMA crossover, capped cost, mid-fill.",
  "execution_policy": {
    "fill_target": "mid",
    "max_order_qty": 100,
    "time_in_force": "day",
    "max_open_positions": 1
  },
  "clauses": [
    { "order": 0, "clause": { "kind": "Hold" } },
    { "order": 1, "clause": { "kind": "CostCap", "max_bps": 30 } },
    { "order": 2, "clause": { "kind": "SmaCrossover", "fast": 20, "slow": 50 } }
  ]
}
```

Compiled behavior: warmup requires 51 bars; if bar count is insufficient, `Hold` wins at order 0.

**RSI reversion**

```json
{
  "schema_version": "v1",
  "family": "Mean Reversion",
  "description": "Buy after drops, sell after rises.",
  "execution_policy": {
    "fill_target": "mid",
    "max_order_qty": 100,
    "time_in_force": "day"
  },
  "clauses": [
    { "order": 0, "clause": { "kind": "Hold" } },
    { "order": 1, "clause": { "kind": "RsiReversion", "period": 14, "oversold": 30, "overbought": 70 } }
  ]
}
```

Compiled behavior: warmup requires 15 bars; action flips to `buy` on oversold and `sell` on overbought, otherwise `Hold`.

**Long-short value-quality with beta neutralization**

```json
{
  "schema_version": "v1",
  "family": "Factor ValueQuality",
  "description": "Long top N, short bottom N, beta neutralized.",
  "execution_policy": {
    "fill_target": "mid",
    "max_order_qty": 50,
    "time_in_force": "day",
    "max_open_positions": 25
  },
  "clauses": [
    { "order": 0, "clause": { "kind": "Hold" } },
    { "order": 1, "clause": { "kind": "ValueQualityLongShort", "long_top_n": 20, "short_bottom_n": 20, "beta_neutralize": true, "hedge_universe": "SPY,QQQ,IWM" } }
  ]
}
```

**Beta-neutral factor rotation**

```json
{
  "schema_version": "v1",
  "family": "Factor Rotation",
  "description": "Factor tilt, beta neutralized against universe.",
  "execution_policy": {
    "fill_target": "mid",
    "max_order_qty": 75,
    "time_in_force": "day"
  },
  "clauses": [
    { "order": 0, "clause": { "kind": "Hold" } },
    { "order": 1, "clause": { "kind": "BetaNeutralFactor", "target_beta": 0.0, "lookback": 60, "hedge_universe": "SPY" } }
  ]
}
```

**Macro-gated risk-off**

```json
{
  "schema_version": "v1",
  "family": "Macro Gated",
  "description": "Flip to HOLD when volatility regime exceeds threshold.",
  "execution_policy": {
    "fill_target": "mid",
    "max_order_qty": 100,
    "time_in_force": "day"
  },
  "clauses": [
    { "order": 0, "clause": { "kind": "Hold" } },
    { "order": 1, "clause": { "kind": "MacroGate", "indicator": "volatility_regime", "threshold": 2.5, "retract_by_bar": 3, "action_on_breach": "hold" } },
    { "order": 2, "clause": { "kind": "SmaCrossover", "fast": 20, "slow": 50 } }
  ]
}
```

Compiled behavior: `MacroGate` denies new entries for 3 bars after a breach; `Hold` at order 0 remains the fallback action.

## 8. Failure Cases

**Duplicate clause orders**

```json
[
  { "order": 0, "clause": { "kind": "Hold" } },
  { "order": 0, "clause": { "kind": "Hold" } }
]
```

Parser raises `ValueError: clause orders must be unique`.

**Pure short without hedge**

```json
{
  "kind": "ValueQualityLongShort",
  "long_top_n": 0,
  "short_bottom_n": 10
}
```

Parser raises `ValueError: pure short without hedge is disallowed`.

**Beta neutralization missing universe**

```json
{
  "kind": "ValueQualityLongShort",
  "long_top_n": 20,
  "short_bottom_n": 0,
  "beta_neutralize": true,
  "hedge_universe": null
}
```

Parser raises `ValueError: beta_neutralize=True requires hedge_universe`.

**Beta neutral factor without universe**

```json
{
  "kind": "BetaNeutralFactor",
  "target_beta": 0.0,
  "lookback": 60,
  "hedge_universe": ""
}
```

Schema validation fails because `hedge_universe` requires a non-empty string.

**Unrecognized clause kind**

```json
{ "kind": "MovingAverageEnvelope" }
```

Schema validation fails because `kind` is closed; this surfaces at parse time, not runtime.

## 9. Migration Plan From Existing Execution-Policy Schemas

**Phase 0: freeze current templates.** Make `app/break_test/strategy_compiler.py` read-only from the write path and create a mapping module that translates legacy `template_key`/`params` into the new `Strategy` root.

**Phase 1: transliterator.** Convert every legacy template into an equivalent `Strategy` instance using the新任 dispatcher:

```m3
LEGACY_RECIPE_MAP = {
  "trending_momentum": lambda params: [
    OrderedClause(order=0, clause=Hold()),
    OrderedClause(order=1, clause=SmaCrossover(fast=params.get("fast", 20), slow=params.get("slow", 50))),
  ],
  "mean_reversion": lambda params: [
    OrderedClause(order=0, clause=Hold()),
    OrderedClause(order=1, clause=RsiReversion(period=params.get("lookback", 20), oversold=params.get("threshold", 1.5) if float(params.get("threshold", 1.5)) < 30 else 30, overbought=70)),
  ],
  ...
}
```

**Phase 2: validate at write.** All persisted strategies are deserialized through `Strategy.model_validate`, invariants run, and `strategy_id` is assigned immediately.

**Phase 3: parity execution.** Replace `compute_positions(...)` with a sequence compiler that evaluates the clause ledger and returns the same execution contract shape, preserving the existing bridge inputs.

**Phase 4: deprecation.** Mark the old template-based write path deprecated for one release cycle and remove it once all stored strategies are migrated.
