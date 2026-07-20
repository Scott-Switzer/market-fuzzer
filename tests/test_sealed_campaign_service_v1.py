from __future__ import annotations

import hashlib
import json

import pytest

from app.evaluation import CampaignPolicyV1, SealedCampaignServiceError, SealedCampaignServiceV1
from app.execution_store import ArenaStore
from app.strategy_lab import ExternalAdapterContract
from app.strategy_protocol import StrategyActionV2
from app.strategy_runtime import StrategyResponseRecordV1


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class _HoldPort:
    def __init__(self, artifact_digest: str) -> None:
        self.artifact_digest = artifact_digest

    def decide(self, observation: dict) -> StrategyResponseRecordV1:
        action = StrategyActionV2(action_type="hold", rationale_code="test").model_dump(mode="json")
        request = _digest(observation)
        artifact = self.artifact_digest
        return StrategyResponseRecordV1(
            idempotency_key=_digest({"artifact": artifact, "request": request}),
            artifact_digest=artifact,
            request_digest=request,
            response_digest=_digest(action),
            action=action,
        )


def _store(tmp_path) -> ArenaStore:
    store = ArenaStore(tmp_path / "arena.sqlite3")
    contract = ExternalAdapterContract(
        adapter_id="container_jsonl_v1",
        adapter_version="1.0.0",
        policy_id="twap",
        input_observation_schema="market_observation_v2",
        output_action_schema="execution_action_v2",
        timeout_ms=100,
        image_digest="registry.example/strategy@sha256:" + "d" * 64,
        command=("strategy",),
    ).model_dump(mode="json")
    store.create_strategy(
        "strategy-v2",
        {
            "name": "V2 test strategy",
            "description": "A digest-pinned isolated V2 strategy used by the sealed service test.",
            "strategy_type": "external_adapter",
            "builtin_policy_id": "twap",
            "version_label": "1.0.0",
            "intended_use": "strategy_research",
            "external_adapter": contract,
        },
        "operator",
    )
    return store


def _policy() -> CampaignPolicyV1:
    return CampaignPolicyV1(
        same_family_ids=("heterogeneous_agent_v1",),
        holdout_family_ids=("regime_switching_point_process_v1", "correlated_latent_factor_v1"),
        worlds_per_family=1,
        hidden_parameter_ranges=(),
        scoring_policy_digest="a" * 64,
    )


def test_service_persists_private_commitment_freezes_runs_and_reveals_after_finalization(tmp_path) -> None:
    service = SealedCampaignServiceV1(
        _store(tmp_path), session_factory=lambda artifact: _HoldPort(artifact.artifact_digest)
    )
    prepared = service.prepare(
        campaign_id="campaign-v2",
        strategy_id="strategy-v2",
        policy=_policy(),
        instruments=("NOVA", "ORBIT"),
        steps=2,
        actor="operator",
        seed_material=b"s" * 32,
    )
    assert prepared["state"] == "prepared"
    assert "secret_seed_material_hex" not in prepared
    assert "policy" not in prepared
    assert prepared["public_document"]["generator_bundle_digest"]
    with pytest.raises(SealedCampaignServiceError, match="only after primary"):
        service.reveal("campaign-v2")

    frozen = service.freeze("campaign-v2", actor="operator")
    assert frozen["state"] == "frozen"
    assert len(frozen["artifact_digest"]) == 64
    finalized = service.finalize("campaign-v2", actor="operator")
    assert finalized["state"] == "finalized"
    assert finalized["result"]["result_namespace"] == "sealed_primary_v1"
    assert len(finalized["result"]["worlds"]) == 3
    assert "heterogeneous_agent_v1" not in json.dumps(finalized)

    reveal = service.reveal("campaign-v2")
    assert reveal["secret_seed_material_hex"] == (b"s" * 32).hex()
    assert set(reveal["policy_preimage"]["same_family_ids"]) == {"heterogeneous_agent_v1"}


def test_service_rejects_legacy_container_contract(tmp_path) -> None:
    store = _store(tmp_path)
    with store.connection() as connection:
        contract = json.loads(
            connection.execute(
                "SELECT contract_json FROM strategy_adapters WHERE strategy_id = ?", ("strategy-v2",)
            ).fetchone()["contract_json"]
        )
        contract["input_observation_schema"] = "market_observation_v1"
        contract["output_action_schema"] = "execution_action_v1"
        connection.execute(
            "UPDATE strategy_adapters SET contract_json = ? WHERE strategy_id = ?",
            (json.dumps(contract, sort_keys=True), "strategy-v2"),
        )
    service = SealedCampaignServiceV1(
        store, session_factory=lambda artifact: _HoldPort(artifact.artifact_digest)
    )
    with pytest.raises(SealedCampaignServiceError, match="V2 container"):
        service.prepare(
            campaign_id="legacy-campaign",
            strategy_id="strategy-v2",
            policy=_policy(),
            instruments=("NOVA", "ORBIT"),
            steps=2,
            actor="operator",
            seed_material=b"l" * 32,
        )
