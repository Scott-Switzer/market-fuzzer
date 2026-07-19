from fastapi.testclient import TestClient

from app.api.app import app
from app.strategy_language import compile_strategy_brief


def test_plain_english_brief_compiles_to_reviewable_policy() -> None:
    result = compile_strategy_brief(
        "Protect completion under latency and spread stress while participating with market volume."
    )
    assert result["compiler_version"] == "strategy_brief_v1"
    assert result["requires_confirmation"] is True
    assert result["proposal"]["builtin_policy_id"] == "guarded_pov"
    assert "stress_guardrails" in result["matched_intents"]
    assert result["claim_boundary"].startswith("bounded_policy_proposal")


def test_unmatched_brief_is_fail_closed_to_confirmation() -> None:
    result = compile_strategy_brief("Use a clever strategy for the market.")
    assert result["proposal"]["builtin_policy_id"] == "guarded_pov"
    assert result["ambiguities"]


def test_api_exposes_compile_and_reference_adapter_contract() -> None:
    client = TestClient(app)
    compiled = client.post(
        "/api/enterprise/strategies/compile-brief",
        json={"brief": "Use TWAP slices evenly and pause when the spread widens."},
    )
    assert compiled.status_code == 200
    assert compiled.json()["proposal"]["builtin_policy_id"] == "twap"
    action = client.post(
        "/api/enterprise/adapter-reference/guarded-pov",
        json={
            "schema_version": "1.0",
            "session_id": "world:execution-agent",
            "step": 2,
            "symbol": "NOVA",
            "side": "buy",
            "mid_ticks": 100,
            "best_bid_ticks": 99,
            "best_ask_ticks": 101,
            "spread_bps": 20,
            "observed_volume": 100,
            "inventory": 0,
            "remaining_quantity": 100,
            "exchange_latency_profile": "normal",
            "intervention_active": False,
        },
    )
    assert action.status_code == 200
    assert action.json()["schema_version"] == "1.0"
    assert action.json()["action_type"] == "market"
