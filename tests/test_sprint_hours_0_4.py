from __future__ import annotations

from app.break_test.exchange_fwd import (
    clear_world_prototype_cache,
    clone_world_from_prototype,
    get_world_prototype,
)
from app.simulation import run_simulation
from app.world import build_demo_world


def test_run_simulation_defaults_to_v2_engine():
    result = run_simulation(build_demo_world(11))
    assert result.summary["exchange_engine"] == "v2"
    assert result.summary["ledger_digest"]
    assert int(result.summary["event_kernel_event_count"]) > 0


def test_run_simulation_omit_collection_flags():
    result = run_simulation(
        build_demo_world(12),
        collect_timeline=False,
        collect_agent_states=False,
        collect_strategy_steps=False,
    )
    assert result.timeline == []
    assert result.strategy_steps == []
    # Final agent snapshot is retained for inventory lookups.
    assert result.agent_states
    assert all(row["step"] == build_demo_world(12).clock.steps - 1 for row in result.agent_states)
    assert result.summary["filled_quantity"] >= 0
    assert result.summary["collection"] == {
        "timeline": False,
        "agent_states": False,
        "strategy_steps": False,
    }


def test_v1_and_v2_engines_match_fills():
    world = build_demo_world(15)
    v1 = run_simulation(world, exchange_engine="v1")
    v2 = run_simulation(world, exchange_engine="v2")
    assert v1.summary["filled_quantity"] == v2.summary["filled_quantity"]
    assert len(v1.trades) == len(v2.trades)


def test_world_prototype_cache_clones_seed_only():
    clear_world_prototype_cache()
    prototype = get_world_prototype(regime_key="high_volatility", asset_count=3)
    again = get_world_prototype(regime_key="high_volatility", asset_count=3)
    assert prototype is again
    clone = clone_world_from_prototype(prototype, seed=42_001, regime_key="high_volatility")
    assert clone.seed == 42_001
    assert clone.world_id == "fwd-high_volatility-42001"
    assert prototype.seed == 0
    assert clone.assets[0].ticker == prototype.assets[0].ticker


def test_u_shaped_volume_and_step_cap_partial_fills():
    from app.exchange.volume_profile import displayed_depth_autor, u_shaped_intraday_volume_weights
    from app.schemas import WorldSpec

    weights = u_shaped_intraday_volume_weights(12)
    assert abs(sum(weights) - 1.0) < 1e-9
    assert weights[0] > weights[len(weights) // 2]
    assert weights[-1] > weights[len(weights) // 2]
    assert displayed_depth_autor(600, "thin", volume_weight=0.5) < displayed_depth_autor(
        600, "deep", volume_weight=1.5
    )

    data = build_demo_world(21).model_dump()
    data["exchange"]["intraday_volume_profile"] = "u_shaped"
    data["exchange"]["per_step_volume_cap"] = 50
    capped = run_simulation(WorldSpec.model_validate(data), exchange_engine="v2")
    assert capped.summary["volume_limiting_enabled"] is True
    assert capped.summary["per_step_volume_cap"] == 50


def test_almgren_chriss_and_toxicity_cost_model():
    from app.break_test.costs import TransactionCostModel, almgren_chriss_impact_bps, toxicity_bps
    from app.schemas import ExchangeSpec

    permanent, temporary = almgren_chriss_impact_bps(
        0.1, 0.02, perm_eta=0.05, temp_epsilon=0.005, temp_gamma=0.2
    )
    assert permanent > 0
    assert temporary > permanent
    assert toxicity_bps(1_000, 500, kappa=5.0) > toxicity_bps(10, 500, kappa=5.0)

    model = TransactionCostModel.from_exchange_spec(
        ExchangeSpec(
            htb_schedule=[
                {"threshold_cents": 0, "htb_bps_annual": 50},
                {"threshold_cents": 1_000_000, "htb_bps_annual": 400},
            ],
            toxicity_kappa=8.0,
        )
    )
    assert model.impact_mode == "almgren_chriss"
    impact = model.decompose_impact(trade_qty=5_000, price=100.0, signed_flow_prev=2_000, depth_prev=800)
    assert impact.permanent_bps != 0.0 or impact.temporary_bps != 0.0
    assert impact.toxicity_bps != 0.0


def test_simulation_summary_exposes_signed_flow_and_tca():
    result = run_simulation(build_demo_world(31))
    assert "signed_taker_flow_by_step" in result.summary
    assert len(result.summary["signed_taker_flow_by_step"]) == build_demo_world(31).clock.steps
    assert "slippage_vs_vwap" in result.summary
    assert "slippage_vs_arrival" in result.summary
    assert "opportunity_cost" in result.summary
    assert "completion_rate_penalty_bps" in result.summary
    assert isinstance(result.summary["tca_by_bucket"], list)
    assert result.summary["tca_by_bucket"]


def test_calibration_pack_mutates_exchange_and_repro_metadata():
    from app.break_test.production_audit import reproducibility_metadata
    from app.calibration import apply_calibration_pack_to_exchange, build_demo_calibration_pack

    world = build_demo_world(9)
    pack = build_demo_calibration_pack(seed=7, rows=60)
    calibrated = apply_calibration_pack_to_exchange(world.exchange, pack)
    assert (
        calibrated.baseline_depth != world.exchange.baseline_depth or calibrated.adtv != world.exchange.adtv
    )
    meta = reproducibility_metadata(
        9,
        [100.0, 101.0, 102.0],
        "sma_crossover",
        {"fast": 5, "slow": 20},
        exchange_spec=calibrated,
    )
    assert meta["checkpoint"]["spec_mutable_fields"]["exchange"]["adtv"] == calibrated.adtv
    assert meta["checkpoint"]["spec_mutable_hash"]
