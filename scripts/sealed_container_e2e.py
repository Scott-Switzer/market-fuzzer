"""Run a real digest-pinned container strategy through a complete sealed campaign."""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path

from app.evaluation import CampaignPolicyV1, SealedCampaignServiceV1
from app.evaluation.sealed_campaign_worker_v1 import SealedCampaignJobWorkerV1
from app.execution_store import ArenaStore
from app.strategy_lab import ExternalAdapterContract

HOLD_LOOP = "import sys,json\nfor line in sys.stdin:\n json.loads(line); print(json.dumps({'schema_version':'2.0','action_type':'hold','quantity':0,'rationale_code':'e2e_hold'}),flush=True)"


def pinned_image(tag: str) -> str:
    subprocess.run(["docker", "pull", tag], check=True, capture_output=True, text=True)
    digests = json.loads(
        subprocess.run(
            ["docker", "image", "inspect", tag, "--format", "{{json .RepoDigests}}"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    )
    if not digests:
        raise RuntimeError("strategy image has no repository digest")
    return str(digests[0])


def run(database: Path, image: str) -> dict[str, object]:
    store = ArenaStore(database)
    contract = ExternalAdapterContract(
        adapter_id="container_jsonl_v1",
        adapter_version="1.0.0",
        policy_id="twap",
        input_observation_schema="market_observation_v2",
        output_action_schema="execution_action_v2",
        timeout_ms=1000,
        image_digest=image,
        command=("python", "-u", "-c", HOLD_LOOP),
    ).model_dump(mode="json")
    store.create_strategy(
        "rc-container-strategy",
        {
            "name": "RC container hold",
            "description": "Digest-pinned no-network strategy for release evidence.",
            "strategy_type": "external_adapter",
            "builtin_policy_id": "twap",
            "version_label": "1.0.0",
            "intended_use": "strategy_research",
            "external_adapter": contract,
        },
        "release-gate",
    )
    service = SealedCampaignServiceV1(store)
    service.prepare(
        campaign_id="rc-sealed-campaign",
        strategy_id="rc-container-strategy",
        policy=CampaignPolicyV1(
            same_family_ids=("heterogeneous_agent_v1",),
            holdout_family_ids=("regime_switching_point_process_v1", "correlated_latent_factor_v1"),
            worlds_per_family=1,
            hidden_parameter_ranges=(),
            scoring_policy_digest="0" * 64,
        ),
        instruments=("RC-ASSET", "RC-HEDGE"),
        steps=2,
        actor="release-gate",
    )
    service.freeze("rc-sealed-campaign", actor="release-gate")
    store.create_sealed_campaign_job("rc-job", "rc-sealed-campaign", "release-gate")
    job = SealedCampaignJobWorkerV1(store, worker_id="rc-worker").run_next()
    reveal = service.reveal("rc-sealed-campaign")
    public = store.sealed_campaign("rc-sealed-campaign")
    assert job and job["status"] == "completed" and public["state"] == "finalized"
    assert "secret_seed_material_hex" not in public
    return {
        "schema_version": "sealed_container_e2e_v1",
        "campaign_id": public["campaign_id"],
        "commitment_digest": public["commitment_digest"],
        "artifact_digest": public["artifact_digest"],
        "job_status": job["status"],
        "worker_attempt": job["attempt"],
        "reveal_verified": bool(reveal["secret_seed_material_hex"]),
        "strategy_image": image,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default="python:3.12-slim")
    parser.add_argument("--database", type=Path)
    args = parser.parse_args()
    image = args.image if "@sha256:" in args.image else pinned_image(args.image)
    if args.database:
        result = run(args.database, image)
    else:
        with tempfile.TemporaryDirectory(prefix="sealed-container-e2e-") as directory:
            result = run(Path(directory) / "arena.sqlite3", image)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
