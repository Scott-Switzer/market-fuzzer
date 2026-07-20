"""Run one evaluator-owned sealed campaign job from the durable SQLite queue.

Run this in a separate appliance worker process, never from an HTTP request.
"""

from __future__ import annotations

import argparse

from app.evaluation.sealed_campaign_worker_v1 import SealedCampaignJobWorkerV1
from app.execution_store import ArenaStore


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job_id", nargs="?")
    parser.add_argument("--lease-seconds", type=int, default=3600)
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    arguments = parser.parse_args()
    worker = SealedCampaignJobWorkerV1(ArenaStore())
    if arguments.job_id is None:
        worker.run_forever(poll_seconds=arguments.poll_seconds, lease_seconds=arguments.lease_seconds)
        return
    result = worker.run_once(arguments.job_id, lease_seconds=arguments.lease_seconds)
    if result is None:
        raise SystemExit("job is not claimable")
    print(f"sealed campaign job {result['job_id']} {result['status']}")


if __name__ == "__main__":
    main()
