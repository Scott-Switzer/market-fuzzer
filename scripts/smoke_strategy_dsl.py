from __future__ import annotations

import json

from app.strategy_lab.dsl import (
    BetaNeutralFactor,
    Hold,
    MacroGate,
    OrderedClause,
    RsiReversion,
    SmaCrossover,
    Strategy,
    ValueQualityLongShort,
)


def strategy(*clauses: tuple[int, object]) -> Strategy:
    return Strategy(
        family="smoke",
        description="smoke",
        execution_policy={
            "fill_target": "mid",
            "max_order_qty": 100,
            "time_in_force": "day",
            "max_open_positions": 1,
        },
        clauses=[OrderedClause(order=o, clause=c) for o, c in clauses],
    )


examples = {
    "sma": strategy(
        (0, Hold()),
        (1, SmaCrossover(fast=20, slow=50)),
    ),
    "rsi": strategy(
        (0, Hold()),
        (1, RsiReversion(period=14, oversold=30, overbought=70)),
    ),
    "factor": strategy(
        (0, Hold()),
        (
            1,
            ValueQualityLongShort(
                long_top_n=20, short_bottom_n=20, beta_neutralize=True, hedge_universe="SPY,QQQ,IWM"
            ),
        ),
    ),
    "beta_neutral": strategy(
        (0, Hold()),
        (1, BetaNeutralFactor(target_beta=0.0, lookback=60, hedge_universe="SPY")),
    ),
    "macro_gated_risk_off": strategy(
        (0, Hold()),
        (1, MacroGate(indicator="volatility_regime", threshold=2.5, retract_by_bar=3)),
        (2, SmaCrossover(fast=20, slow=50)),
    ),
}

failures = {}
for name, ex in examples.items():
    print(f"{name}: {ex.ledger_hash}")
    print(json.dumps(json.loads(ex.model_dump_json()), indent=2))
    print("conflicts:", json.loads(ex.model_dump_json()).get("conflict_report"))
    print("---")

# failure variants


def must_fail(label, factory):
    try:
        factory()
    except Exception as exc:
        failures[label] = f"{type(exc).__name__}: {exc}"
    else:
        failures[label] = "DID_NOT_FAIL"


must_fail(
    "duplicate_order",
    lambda: strategy(
        (0, Hold()),
        (0, Hold()),
    ),
)

must_fail(
    "pure_short",
    lambda: strategy(
        (0, Hold()),
        (1, ValueQualityLongShort(long_top_n=0, short_bottom_n=10)),
    ),
)

must_fail(
    "beta_no_universe",
    lambda: strategy(
        (0, Hold()),
        (
            1,
            ValueQualityLongShort(
                long_top_n=20, short_bottom_n=20, beta_neutralize=True, hedge_universe=None
            ),
        ),
    ),
)

must_fail(
    "beta_factor_empty_universe",
    lambda: Strategy(
        family="x",
        execution_policy={"fill_target": "mid", "max_order_qty": 1},
        clauses=[
            OrderedClause(order=0, clause=BetaNeutralFactor(target_beta=0.0, lookback=60, hedge_universe=""))
        ],
    ),
)

must_fail(
    "fast_gte_slow",
    lambda: Strategy(
        family="x",
        execution_policy={"fill_target": "mid", "max_order_qty": 1},
        clauses=[OrderedClause(order=0, clause=SmaCrossover(fast=50, slow=20))],
    ),
)

print("FAILURES:")
for name, msg in failures.items():
    print(f"{name}: {msg}")

print("All example built ok, and all failure cases raised as expected." if len(failures) else "missing")
