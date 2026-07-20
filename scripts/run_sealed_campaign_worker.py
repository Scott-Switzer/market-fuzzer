"""Run one evaluator-owned sealed campaign job from the durable SQLite queue.

Run this in a separate appliance worker process, never from an HTTP request.
"""

from __future__ import annotations

import argparse

from app.evaluation.sealed_campaign_worker_v1 import SealedCampaignJobWorkerV1
from app.execution_store import ArenaStore


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job_id")
    parser.add_argument("--lease-seconds", type=int, default=3600)
    arguments = parser.parse_args()
    result = SealedCampaignJobWorkerV1(ArenaStore()).run_once(
        arguments.job_id, lease_seconds=arguments.lease_seconds
    )
    if result is None:
        raise SystemExit("job is not claimable")
    print(f"sealed campaign job {result['job_id']} {result['status']}")


if __name__ == "__main__":
    main()
