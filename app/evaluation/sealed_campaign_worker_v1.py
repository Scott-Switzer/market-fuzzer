"""Restart-safe external worker for queued sealed primary evaluations."""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from threading import Event, Thread

from app.execution_store import ArenaStore

from .sealed_campaign_service_v1 import SealedCampaignServiceV1

_LOG = logging.getLogger("arena.sealed_worker")


class SealedCampaignJobWorkerV1:
    """Claims one durable job; evaluation never runs while the SQLite claim transaction is open."""

    def __init__(
        self,
        store: ArenaStore,
        service_factory: Callable[[], SealedCampaignServiceV1] | None = None,
        worker_id: str | None = None,
    ) -> None:
        self.store = store
        self.service_factory = service_factory or (lambda: SealedCampaignServiceV1(store))
        self.worker_id = worker_id or f"sealed-worker-{uuid.uuid4().hex[:12]}"

    def run_once(self, job_id: str, *, lease_seconds: int = 3600) -> dict[str, object] | None:
        if lease_seconds < 60 or lease_seconds > 86_400:
            raise ValueError("lease_seconds must be between 60 and 86400")
        lease = (datetime.now(UTC) + timedelta(seconds=lease_seconds)).isoformat()
        job = self.store.claim_sealed_campaign_job(job_id, lease_expires_at=lease)
        if job is None:
            return None
        stop_heartbeat = Event()
        heartbeat = Thread(target=self._heartbeat_until_stopped, args=(stop_heartbeat,), daemon=True)
        heartbeat.start()
        try:
            self.service_factory().finalize(str(job["campaign_id"]), actor="sealed-campaign-worker")
        except Exception as error:
            self.store.finish_sealed_campaign_job(job_id, status="failed", error=str(error)[:1000])
            raise
        finally:
            stop_heartbeat.set()
            heartbeat.join(timeout=1)
        return self.store.finish_sealed_campaign_job(job_id, status="completed")

    def _heartbeat_until_stopped(self, stop: Event) -> None:
        while not stop.wait(10):
            self.store.heartbeat_sealed_worker(self.worker_id)

    def run_next(self, *, lease_seconds: int = 3600) -> dict[str, object] | None:
        self.store.heartbeat_sealed_worker(self.worker_id)
        job_id = self.store.next_claimable_sealed_campaign_job_id()
        return None if job_id is None else self.run_once(job_id, lease_seconds=lease_seconds)

    def run_forever(self, *, poll_seconds: float = 2.0, lease_seconds: int = 3600) -> None:
        if poll_seconds < 0.1 or poll_seconds > 60:
            raise ValueError("poll_seconds must be between 0.1 and 60")
        while True:
            try:
                result = self.run_next(lease_seconds=lease_seconds)
                if result is not None:
                    _LOG.info(
                        json.dumps(
                            {
                                "event": "sealed_job_finished",
                                "worker_id": self.worker_id,
                                "job_id": result["job_id"],
                                "status": result["status"],
                            },
                            sort_keys=True,
                        )
                    )
            except Exception as error:
                _LOG.error(
                    json.dumps(
                        {
                            "event": "sealed_job_failed",
                            "worker_id": self.worker_id,
                            "error_type": type(error).__name__,
                        },
                        sort_keys=True,
                    )
                )
            time.sleep(poll_seconds)
