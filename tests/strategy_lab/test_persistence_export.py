from __future__ import annotations

import json
import os
from typing import Any

import pytest

from app.execution_store import ArenaStore
from app.strategy_lab.evidence.exports import EvidencePackager
from app.strategy_lab.persistence.models import PersistenceModels
from app.strategy_lab.persistence.repository import StrategyLabRepository


@pytest.fixture()
def store(tmp_path: Any) -> ArenaStore:
    db_path = tmp_path / "arena.sqlite3"
    os.environ["ARENA_DB_PATH"] = str(db_path)
    return ArenaStore()


@pytest.fixture()
def repository(store: ArenaStore) -> StrategyLabRepository:
    repo = StrategyLabRepository(store)
    repo.initialize()
    return repo


def test_persistence_models_expected_shape() -> None:
    model = PersistenceModels.strategy_version_model()
    assert model["strategy_id"] == "string"
    assert model["is_locked"] is False


def test_strategy_version_lifecycle(repository: StrategyLabRepository) -> None:
    payload = {
        "name": "alpha",
        "description": "desc",
        "strategy_type": "sma_crossover",
        "version_label": "v1",
        "intended_use": "lab",
        "created_by": "tester",
        "spec": {"fast": 10, "slow": 20},
    }
    saved = repository.save_strategy_version(payload)
    assert saved["strategy_id"]
    assert saved["canonical_hash"]

    loaded = repository.strategy_version(saved["strategy_id"])
    assert loaded["spec"] == payload["spec"]
    assert loaded["name"] == "alpha"

    versions = repository.strategy_versions()
    assert any(item["strategy_id"] == saved["strategy_id"] for item in versions)


def test_clause_and_approval_queries(repository: StrategyLabRepository) -> None:
    strategy = repository.save_strategy_version(
        {
            "name": "beta",
            "description": "d",
            "strategy_type": "momentum",
            "version_label": "v1",
            "intended_use": "lab",
            "created_by": "tester",
            "spec": {"threshold": 0.5},
        }
    )
    clause = repository.save_clause(
        {
            "strategy_id": strategy["strategy_id"],
            "order_index": 1,
            "kind": "risk",
            "original_text": "limit exposure",
            "normalized_text": "limit exposure",
            "status": "accepted",
            "user_resolution": "accepted",
            "clause": {"text": "limit exposure"},
        }
    )
    assert clause["kind"] == "risk"
    clauses = repository.clauses_for_strategy(strategy["strategy_id"])
    assert len(clauses) == 1

    approval = repository.save_approval(
        {
            "strategy_id": strategy["strategy_id"],
            "status": "approved",
            "approved_by": "reviewer",
            "approval": {"note": "ok"},
        }
    )
    assert approval["status"] == "approved"
    approvals = repository.approvals_for_strategy(strategy["strategy_id"])
    assert len(approvals) == 1


def test_backtest_campaign_failure_replay(repository: StrategyLabRepository) -> None:
    strategy = repository.save_strategy_version(
        {
            "name": "gamma",
            "description": "d",
            "strategy_type": "mean_revert",
            "version_label": "v1",
            "intended_use": "lab",
            "created_by": "tester",
            "spec": {"lookback": 32},
        }
    )
    backtest = repository.save_backtest(
        {
            "strategy_id": strategy["strategy_id"],
            "status": "completed",
            "result": {"sharpe": 1.1},
            "metrics": {"max_drawdown": -0.2},
        }
    )
    assert backtest["result"]["sharpe"] == 1.1

    campaign = repository.save_campaign(
        {
            "strategy_id": strategy["strategy_id"],
            "state": "prepared",
            "public_document": {"instrument_count": 2},
            "instruments": ["A", "B"],
            "steps": 16,
        }
    )
    assert campaign["public_document"]["instrument_count"] == 2

    failure = repository.save_failure(
        {
            "strategy_id": strategy["strategy_id"],
            "campaign_id": campaign["campaign_id"],
            "category": "slippage",
            "severity": "high",
            "evidence": {"scenario": "extreme"},
            "suggestions": ["reduce size"],
        }
    )
    assert failure["category"] == "slippage"
    failures = repository.failures_for_campaign(campaign["campaign_id"])
    assert len(failures) == 1

    _event = repository.save_replay_event(
        {
            "failure_id": failure["failure_id"],
            "campaign_id": campaign["campaign_id"],
            "step_index": 0,
            "event_kind": "observation",
            "event": {"price": 101.0},
        }
    )
    events = repository.replay_events_for_failure(failure["failure_id"])
    assert len(events) == 1
    assert events[0]["event"]["price"] == 101.0


def test_evidence_package_produces_manifest_report_csv(repository: StrategyLabRepository) -> None:
    strategy = repository.save_strategy_version(
        {
            "name": "delta",
            "description": "d",
            "strategy_type": "breakout",
            "version_label": "v1",
            "intended_use": "lab",
            "created_by": "tester",
            "spec": {"window": 20},
        }
    )
    repository.save_clause(
        {
            "strategy_id": strategy["strategy_id"],
            "order_index": 0,
            "kind": "entry",
            "original_text": "breakout entry",
            "status": "accepted",
            "user_resolution": "accepted",
            "clause": {"window": 20},
        }
    )
    campaign = repository.save_campaign(
        {
            "strategy_id": strategy["strategy_id"],
            "state": "prepared",
            "public_document": {},
            "instruments": ["NOVA"],
            "steps": 8,
        }
    )
    package = EvidencePackager.build(
        repository=repository, campaign_id=campaign["campaign_id"], creator="tester"
    )
    assert package["export_id"]
    assert package["campaign_id"] == campaign["campaign_id"]
    assert package["manifest"]["strategy_id"] == strategy["strategy_id"]
    assert package["manifest"]["backtest_count"] == 0
    assert "<!doctype html>" in package["report_html"].lower()
    assert "Strategy Lab Evidence Report" in package["report_html"]
    assert package["report_json"]["campaign_id"] == campaign["campaign_id"]
    assert set(package["csv_hashes"].keys()) == {"strategy", "campaign", "failures", "replay"}

    content = package["content"]
    assert content["strategy"]["original_description.txt"] == "d"
    assert content["strategy"]["strategy_hash.txt"] == strategy["canonical_hash"]
    assert "data_provenance.json" in content["historical"]
    assert "campaign_manifest_public.json" in content["synthetic"]
    assert "minimized_failure.json" in content["replay"]
    assert "report.json" in content["report"]
    assert "audit_log.jsonl" in content["provenance"]
    assert "secret_seed_material_hex" not in json.dumps(package)
    assert "hidden_parameter_ranges" not in json.dumps(package)

    saved = repository.evidence_export(package["export_id"])
    assert saved["manifest"]["schema_version"] == "strategy-lab-evidence/1.0"
    assert saved["csv_hashes"] == package["csv_hashes"]
    assert saved["manifest"]["layout"]["strategy"]["strategy_hash.txt"] == strategy["canonical_hash"]
