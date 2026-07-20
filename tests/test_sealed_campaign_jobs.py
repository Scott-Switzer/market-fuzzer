from datetime import UTC, datetime, timedelta

import pytest

from app.evaluation.sealed_campaign_worker_v1 import SealedCampaignJobWorkerV1
from app.execution_store import ArenaStore


def _campaign(store: ArenaStore, suffix: str = "1") -> str:
    strategy_id = f"strategy-{suffix}"
    campaign_id = f"campaign-{suffix}"
    store.create_strategy(
        strategy_id,
        {
            "name": "test",
            "description": "test strategy",
            "strategy_type": "arena_policy",
            "version_label": "1.0.0",
            "intended_use": "strategy_research",
        },
        "creator",
    )
    store.create_sealed_campaign(
        campaign_id=campaign_id,
        strategy_id=strategy_id,
        public_document={},
        commitment_digest="a" * 64,
        policy={},
        generator_bundle_digest="b" * 64,
        secret_seed_material_hex="00" * 32,
        instruments=("NOVA",),
        steps=1,
        actor="creator",
    )
    return campaign_id


def test_sealed_campaign_job_claim_is_atomic_and_expired_lease_recovers(tmp_path) -> None:
    store = ArenaStore(tmp_path / "jobs.sqlite3")
    _campaign(store)
    store.create_sealed_campaign_job("job-1", "campaign-1", "creator")
    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    assert store.claim_sealed_campaign_job("job-1", lease_expires_at=future) is not None
    assert store.claim_sealed_campaign_job("job-1", lease_expires_at=future) is None
    expired = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    with store.connection() as connection:
        connection.execute(
            "UPDATE sealed_campaign_jobs SET lease_expires_at = ? WHERE job_id = 'job-1'", (expired,)
        )
    recovered = store.claim_sealed_campaign_job("job-1", lease_expires_at=future)
    assert recovered is not None and recovered["attempt"] == 2


def test_worker_finishes_or_records_failure_without_exposing_private_campaign_material(tmp_path) -> None:
    store = ArenaStore(tmp_path / "worker.sqlite3")
    _campaign(store)
    store.create_sealed_campaign_job("job-1", "campaign-1", "creator")

    class Service:
        def finalize(self, campaign_id: str, *, actor: str) -> None:
            assert campaign_id == "campaign-1" and actor == "sealed-campaign-worker"

    completed = SealedCampaignJobWorkerV1(store, service_factory=lambda: Service()).run_once("job-1")
    assert completed is not None and completed["status"] == "completed"
    assert "secret_seed_material_hex" not in completed

    store.create_sealed_campaign_job("job-2", _campaign(store, "2"), "creator")
    with pytest.raises(RuntimeError, match="boom"):
        SealedCampaignJobWorkerV1(
            store, service_factory=lambda: (_ for _ in ()).throw(RuntimeError("boom"))
        ).run_once("job-2")
    assert store.sealed_campaign_job("job-2")["status"] == "failed"
