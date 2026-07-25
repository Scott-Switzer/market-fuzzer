from __future__ import annotations

from pathlib import Path

from app.strategy_lab.robustness.failure_taxonomy import (
    PREDICATE_REGISTRY,
    FailureCategory,
    FailureSeverity,
    ThresholdPredicate,
    build_predicates,
    evaluate_predicates,
)
from app.strategy_lab.robustness.minimizer import minimize
from app.strategy_lab.robustness.replay import build_artifact, store_artifact
from app.strategy_lab.robustness.search import search
from app.strategy_lab.robustness.suggestions import EvidenceLinkedSuggestionEngine


def test_failure_taxonomy_contract() -> None:
    assert FailureCategory.TREND_REVERSAL == "trend_reversal"
    assert FailureSeverity.HIGH == "high"
    preds = build_predicates([ThresholdPredicate(metric="total_return_pct", comparator="le", threshold=0.0)])
    assert evaluate_predicates({"total_return_pct": -5.0, "sharpe": 0.5}, preds) == [True]
    assert evaluate_predicates({"total_return_pct": 5.0, "sharpe": 0.5}, preds) == [False]
    assert "total_return_pct_le" in PREDICATE_REGISTRY
    assert "sharpe_le" in PREDICATE_REGISTRY
    assert "max_drawdown_pct_le" in PREDICATE_REGISTRY


def test_deterministic_search_sobol_and_lhs_differ() -> None:
    predicates = [ThresholdPredicate(metric="sharpe", comparator="le", threshold=-10.0)]
    sobol_result = search(
        strategy_type="sma_crossover",
        params={"fast": 20, "slow": 50},
        search_space={"drift": [0.0, 0.1]},
        predicates=predicates,
        budget=8,
        seed=1,
        method="sobol",
    )
    lhs_result = search(
        strategy_type="sma_crossover",
        params={"fast": 20, "slow": 50},
        search_space={"drift": [0.0, 0.1]},
        predicates=predicates,
        budget=8,
        seed=1,
        method="lhs",
    )
    assert sobol_result["status"] == "ok"
    assert lhs_result["status"] == "ok"
    assert sobol_result["method"] == "sobol"
    assert lhs_result["method"] == "lhs"
    assert sobol_result["evaluated"] == lhs_result["evaluated"]
    assert len(sobol_result["failures"]) == len(lhs_result["failures"])


def test_threshold_predicate_falsification_returns_failures() -> None:
    predicates = [ThresholdPredicate(metric="total_return_pct", comparator="le", threshold=999.0)]
    result = search(
        strategy_type="sma_crossover",
        params={"fast": 5, "slow": 10},
        search_space={"liquidity_term": [-1.0, 1.0]},
        predicates=predicates,
        budget=4,
        seed=7,
        method="sobol",
    )
    assert result["evaluated"] == 4
    assert isinstance(result["failures"], list)
    assert all("failed_predicates" in f for f in result["failures"])


def test_delta_debug_minimizer_completes() -> None:
    failure = {
        "campaign_id": "cmp-1",
        "evaluation_index": 0,
        "category": "trend_reversal",
        "severity": "high",
        "failed_predicates": [
            {"predicate": "total_return_pct_le", "threshold": 0.0, "observed": -12.4, "hash": "abcd1234"},
        ],
        "world_spec": {"type": "sma_crossover", "params": {"drift": 0.1}, "seeds": [1, 2, 3]},
        "parameters": {"fast": 5, "slow": 10},
        "metrics": {"total_return_pct": -12.4, "sharpe": -1.2, "max_drawdown_pct": -30.0, "trades": 1},
        "method": "sobol",
    }
    result = minimize(failure, max_iterations=8, seed=0)
    assert result["status"] == "completed"
    assert "original_failure" in result
    assert "minimized_failure" in result
    assert "delta" in result
    assert result["iterations"] <= 8


def test_replay_artifact_contract_round_trip() -> None:
    artifact = build_artifact(
        campaign_id="cmp",
        evaluation_index=1,
        strategy_type="sma_crossover",
        parameters={"fast": 20, "slow": 50},
        world={"params": {"drift": 0.0}},
        prices=[100.0, 101.0, 102.0],
        positions=[0.0, 1.0, 1.0],
        metrics={"total_return_pct": 2.0, "sharpe": 0.8, "max_drawdown_pct": -0.5, "trades": 1},
    )
    assert artifact.artifact_id
    assert artifact.checksum
    output = store_artifact(None, artifact)
    assert output["status"] == "stored"
    assert output["path"].endswith(".replay.json")
    assert Path(output["path"]).exists()


def test_evidence_linked_suggestion_engine_returns_structured_output() -> None:
    failure = {
        "category": "liquidity_deterioration",
        "failed_predicates": [{"predicate": "sharpe_le", "threshold": 0.0}],
        "metrics": {"sharpe": -2.1, "max_drawdown_pct": -28.0},
    }
    result = EvidenceLinkedSuggestionEngine.suggest(failure)
    assert isinstance(result, list)
    assert all("change_category" in item for item in result)
