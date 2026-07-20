"""Restart-safe external worker for queued sealed primary evaluations."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from app.execution_store import ArenaStore

from .sealed_campaign_service_v1 import SealedCampaignServiceV1


class SealedCampaignJobWorkerV1:
    """Claims one durable job; evaluation never runs while the SQLite claim transaction is open."""

    def __init__(
        self, store: ArenaStore, service_factory: Callable[[], SealedCampaignServiceV1] | None = None
    ) -> None:
        self.store = store
        self.service_factory = service_factory or (lambda: SealedCampaignServiceV1(store))

    def run_once(self, job_id: str, *, lease_seconds: int = 3600) -> dict[str, object] | None:
        if lease_seconds < 60 or lease_seconds > 86_400:
            raise ValueError("lease_seconds must be between 60 and 86400")
        lease = (datetime.now(UTC) + timedelta(seconds=lease_seconds)).isoformat()
        job = self.store.claim_sealed_campaign_job(job_id, lease_expires_at=lease)
        if job is None:
            return None
        try:
            self.service_factory().finalize(str(job["campaign_id"]), actor="sealed-campaign-worker")
        except Exception as error:
            self.store.finish_sealed_campaign_job(job_id, status="failed", error=str(error)[:1000])
            raise
        return self.store.finish_sealed_campaign_job(job_id, status="completed")
