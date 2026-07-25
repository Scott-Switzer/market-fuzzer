from __future__ import annotations

import hashlib
import json
from typing import Any

from app.strategy_lab.dsl import (
    ClauseLedgerEntry,
    ClauseResolution,
    ClauseStatus,
    ExecutionPolicy,
    FillTarget,
    Hold,
    MacroGate,
    OrderedClause,
    RsiReversion,
    SmaCrossover,
    Strategy,
    TimeInForce,
    ValueQualityLongShort,
)


class StrategyPlanner:
    @staticmethod
    def plan_from_text(raw_text: str) -> dict[str, Any]:
        from app.strategy_lab.compiler.deterministic_fallback import DeterministicFallbackCompiler

        classification = DeterministicFallbackCompiler.classify(raw_text)
        family = classification["template_key"]
        defaults = classification["defaults"]

        if family == "sma_crossover":
            clauses = [
                OrderedClause(order=0, clause=Hold(note="pre_signal"), clause_id="c_0"),
                OrderedClause(
                    order=1,
                    clause=SmaCrossover(fast=defaults.get("fast", 20), slow=defaults.get("slow", 50)),
                    clause_id="c_1",
                ),
                OrderedClause(order=2, clause=Hold(note="exit_when_crossed"), clause_id="c_2"),
            ]
        elif family == "rsi_reversion":
            clauses = [
                OrderedClause(order=0, clause=Hold(note="pre_signal"), clause_id="c_0"),
                OrderedClause(
                    order=1,
                    clause=RsiReversion(
                        period=defaults.get("lookback", 14),
                        oversold=defaults.get("oversold", 30.0),
                        overbought=defaults.get("overbought", 70.0),
                    ),
                    clause_id="c_1",
                ),
            ]
        elif family == "value_quality_long_short" or family == "long_only_momentum":
            is_long_only = family == "long_only_momentum"
            family = "value_quality_long_short"
            clauses = [
                OrderedClause(order=0, clause=Hold(note="pre_signal"), clause_id="c_0"),
                OrderedClause(
                    order=1,
                    clause=ValueQualityLongShort(
                        long_top_n=defaults.get("n", 10),
                        short_bottom_n=0 if is_long_only else 10,
                        beta_neutralize=False,
                    ),
                    clause_id="c_1",
                ),
            ]
        else:
            family = "macro_gated_risk_off"
            clauses = [
                OrderedClause(order=0, clause=Hold(note="pre_signal"), clause_id="c_0"),
                OrderedClause(
                    order=1,
                    clause=MacroGate(
                        indicator="volatility_regime",
                        threshold=0.2,
                        retract_by_bar=1,
                        action_on_breach="hold",
                    ),
                    clause_id="c_1",
                ),
            ]

        strategy = Strategy(
            strategy_id="",
            family=family,
            description=raw_text,
            description_original=raw_text,
            execution_policy=ExecutionPolicy(
                fill_target=FillTarget.mid, max_order_qty=1000, time_in_force=TimeInForce.day
            ),
            clauses=clauses,
            universe={"type": "index_membership", "index": "SP500", "membership_mode": "point_in_time"},
            frequency={"signal": "daily", "rebalance": "daily", "valuation": "daily"},
            portfolio={
                "type": "ranked_long_short",
                "long_quantile": 0.1,
                "short_quantile": 0.1,
                "gross_exposure": 1.0,
                "net_exposure": 0.0,
            },
            risk={"maximum_turnover_per_rebalance": 0.5},
            execution={
                "decision_time": "close",
                "fill_time": "next_open",
                "order_type": "market",
                "commission_bps": 1.0,
            },
            benchmark={"type": "ticker", "symbol": "SPY"},
            clause_ledger=[
                ClauseLedgerEntry(
                    clause_id="c_0",
                    original_text=raw_text,
                    normalized_text=raw_text.strip(),
                    status=ClauseStatus.SUPPORTED_AND_COMPILED,
                    reason=None,
                    user_resolution=ClauseResolution.APPROVED,
                    compiler_confidence=0.8,
                )
            ],
        )
        canonical = _canonical_strategy(strategy)
        strategy_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return {
            "spec": strategy.model_dump(mode="json"),
            "canonical": canonical,
            "strategy_hash": strategy_hash,
            "conflict_report": getattr(strategy, "conflict_report", {}),
        }


def _canonical_strategy(strategy: Strategy) -> str:
    data = strategy.model_dump(mode="json", exclude_none=True)
    data.pop("strategy_id", None)
    data.pop("approval", None)
    data.pop("provenance", None)
    data.pop("conflict_report", None)
    data.pop("is_locked", None)
    return json.dumps(data, sort_keys=True, separators=(",", ":"))
