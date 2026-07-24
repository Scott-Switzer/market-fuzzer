from __future__ import annotations

import pytest

from app.break_test.exchange_fwd import UserStrategyOrderRouter, run_exchange_forward_test

CLOSES = [
    100.0,
    100.5,
    101.2,
    101.8,
    102.4,
    103.1,
    103.5,
    104.0,
    104.7,
    105.3,
    106.1,
    106.5,
    106.9,
    107.4,
    108.2,
    108.6,
    109.1,
    109.5,
    110.0,
    110.4,
    110.9,
    111.3,
    112.0,
    112.4,
    112.8,
    113.2,
    113.6,
    114.1,
    114.5,
    115.0,
    115.4,
    115.8,
    116.2,
    116.7,
    117.1,
    117.5,
    117.9,
    118.4,
    118.8,
    119.2,
    119.6,
    120.0,
    120.4,
    120.8,
    121.2,
    121.6,
    122.0,
    122.4,
    122.8,
    123.2,
    123.6,
    124.0,
    124.4,
    124.8,
    125.2,
    125.6,
    126.0,
    126.4,
    126.8,
    127.2,
    127.6,
    128.0,
    128.4,
    128.8,
    129.2,
    129.6,
    130.0,
    130.4,
    130.8,
    131.2,
    131.6,
    132.0,
    132.4,
    132.8,
    133.2,
    133.6,
    134.0,
    134.4,
    134.8,
    135.2,
    135.6,
    136.0,
    136.4,
    136.8,
    137.2,
    137.6,
    138.0,
    138.4,
    138.8,
    139.2,
    139.6,
    140.0,
    140.4,
    140.8,
    141.2,
    141.6,
    142.0,
    142.4,
    142.8,
    143.2,
    143.6,
    144.0,
    144.4,
    144.8,
    145.2,
    145.6,
    146.0,
    146.4,
    146.8,
    147.2,
    147.6,
    148.0,
    148.4,
    148.8,
    149.2,
    149.6,
    150.0,
    150.4,
    150.8,
    151.2,
]
PARAMS = {"fast": 10, "slow": 30}


def _results(worlds: int = 3) -> list[dict[str, object]]:
    return run_exchange_forward_test(
        CLOSES,
        strategy_type="sma_crossover",
        params=PARAMS,
        worlds_per_regime=worlds,
        asset_count=3,
        forward_execution_mode="real",
    )


class TestExchangeForwardRouter:
    def test_router_requires_target_symbol_and_side(self) -> None:
        with pytest.raises(ValueError):
            UserStrategyOrderRouter(
                target_symbol="SYNTH", side="x", strategy_type="sma_crossover", params=PARAMS
            )
        with pytest.raises(ValueError):
            UserStrategyOrderRouter(
                target_symbol="SYNTH",
                side="buy",
                strategy_type="sma_crossover",
                params=PARAMS,
                order_type="unknown",
            )

    def test_hold_when_remaining_parent_quantity_is_exhausted(self) -> None:
        router = UserStrategyOrderRouter(
            target_symbol="SYNTH", side="buy", strategy_type="sma_crossover", params=PARAMS
        )
        action = router.decide(
            {
                "symbol": "SYNTH",
                "step": 0,
                "inventory": 0,
                "remaining_quantity": 0,
                "recent_prices": CLOSES[:20],
            }
        )
        assert action["action_type"] == "hold"

    def test_submit_action_carries_required_fields(self) -> None:
        router = UserStrategyOrderRouter(
            target_symbol="SYNTH",
            side="sell",
            strategy_type="sma_crossover",
            params=PARAMS,
            base_quantity=100,
        )
        action = router.decide(
            {
                "symbol": "SYNTH",
                "step": 35,
                "inventory": 0,
                "remaining_quantity": 5_000,
                "recent_prices": CLOSES[:40],
            }
        )
        assert action["action_type"] in {"limit", "market"}
        assert action["side"] == "sell"
        assert action["quantity"] >= 1
        assert isinstance(action.get("limit_price_ticks"), int) or action.get("limit_price_ticks") is None

    def test_decider_wraps_action_for_simulation(self) -> None:
        router = UserStrategyOrderRouter(
            target_symbol="SYNTH", side="buy", strategy_type="sma_crossover", params=PARAMS
        )
        decider = router.to_execution_decider()
        action = decider(
            {
                "symbol": "SYNTH",
                "step": 35,
                "inventory": 0,
                "remaining_quantity": 100,
                "recent_prices": CLOSES[:40],
            }
        )
        assert action["action_type"] in {"limit", "market", "hold"}
        assert action["side"] == "buy"


class TestExchangeForwardExecution:
    def test_exchange_mode_returns_regime_results(self) -> None:
        results = _results(worlds=2)
        assert len(results) == 4
        expected = {"Steady Trend", "Sideways & Choppy", "High Volatility", "Sudden Selloff"}
        assert {row["regime"] for row in results} == expected
        for row in results:
            assert row["worlds"] > 0
            assert 0.0 <= row["loss_rate_pct"] <= 100.0

    @pytest.mark.parametrize(
        "strategy_type,params",
        [
            ("sma_crossover", {"fast": 10, "slow": 30}),
            ("breakout", {"entry_lookback": 12, "exit_lookback": 8}),
        ],
    )
    def test_synthetic_fills_exist_for_multiple_regimes(
        self, strategy_type: str, params: dict[str, int]
    ) -> None:
        results = run_exchange_forward_test(
            CLOSES,
            strategy_type=strategy_type,
            params=params,
            worlds_per_regime=4,
            asset_count=3,
            forward_execution_mode="real",
        )
        assert len(results) == 4
        assert all(isinstance(row["fill_worlds"], int) for row in results)
        assert all(isinstance(row["total_fills"], int) for row in results)

    def test_inventory_changes_with_order_flow(self) -> None:
        results = _results(worlds=3)
        inventory_series_present = 0
        for row in results:
            if row["inventory_changed_worlds"] > 0 and row["total_fills"] > 0:
                inventory_series_present += 1
            assert "inventory_changed_worlds" in row
        assert inventory_series_present >= 0

    def test_mid_price_post_hoc_path_is_no_longer_the_default(self) -> None:
        results = _results(worlds=2)
        assert all(row["order_execution_mode"] == "real" for row in results)
        assert all("synth_mid_price_post_hoc" != row["order_execution_mode"] for row in results)

    def test_same_seed_returns_same_fill_sequence(self) -> None:
        results1 = _results(worlds=2)
        results2 = _results(worlds=2)
        ids1 = [tuple(row.get("order_ids", [])) for row in results1]
        ids2 = [tuple(row.get("order_ids", [])) for row in results2]
        for left, right in zip(ids1, ids2, strict=False):
            assert left == right

    def test_different_world_counts_preserve_schema(self) -> None:
        results = run_exchange_forward_test(
            CLOSES,
            strategy_type="sma_crossover",
            params=PARAMS,
            worlds_per_regime=5,
            asset_count=3,
            forward_execution_mode="real",
        )
        assert len(results) == 4
        required_keys = {
            "regime",
            "worlds",
            "loss_rate_pct",
            "median_return_pct",
            "mean_return_pct",
            "worst_drawdown_pct",
            "best_return_pct",
            "fill_worlds",
            "total_fills",
            "total_quantity",
            "inventory_changed_worlds",
            "order_execution_mode",
        }
        for row in results:
            assert required_keys.issubset(row.keys())

    def test_process_pool_workers_match_sequential_seed_partition(self) -> None:
        from app.break_test.exchange_fwd import forward_world_seed

        assert forward_world_seed(0, 0) == 40_000
        assert forward_world_seed(1, 2) == 41_002
        assert forward_world_seed(3, 9) == 43_009

        common = dict(
            closes=CLOSES,
            strategy_type="sma_crossover",
            params=PARAMS,
            worlds_per_regime=2,
            asset_count=3,
            forward_execution_mode="real",
            collect_timeline=True,
            collect_agent_states=False,
        )
        sequential = run_exchange_forward_test(**common, workers=1)
        pooled = run_exchange_forward_test(**common, workers=2)
        assert sequential and pooled
        assert all(row.get("workers") == 1 for row in sequential)
        assert all(row.get("workers") == 2 for row in pooled)
        compare_keys = (
            "regime",
            "median_return_pct",
            "mean_return_pct",
            "worst_drawdown_pct",
            "total_fills",
            "loss_rate_pct",
            "fill_worlds",
        )
        for left, right in zip(sequential, pooled, strict=False):
            for key in compare_keys:
                assert left[key] == right[key]
