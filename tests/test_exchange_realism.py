from __future__ import annotations

import numpy as np

from app.break_test.costs import (
    locate_failure_probability,
    temporary_impact_decay_bps,
)
from app.exchange.latency import LatencyDistribution
from app.exchange.order_book import OrderBook
from app.exchange.orders import Order, OrderType, Side
from app.exchange.volume_profile import (
    displayed_depth_autor,
    u_shaped_intraday_volume_weights,
)
from app.simulation import run_simulation
from app.world import build_demo_world


def _book() -> OrderBook:
    return OrderBook("TEST", tick_size_cents=1, lot_size=1, volume_time_priority=True)


def test_queue_conservation_and_fifo_tiebreak() -> None:
    book = _book()
    a = Order("a", "m1", "TEST", Side.BUY, OrderType.LIMIT, 10, 0, price_ticks=100)
    b = Order("b", "m2", "TEST", Side.BUY, OrderType.LIMIT, 10, 0, price_ticks=100)
    book.submit(a, 0)
    book.submit(b, 0)
    assert book.queue_ahead_at_price(Side.BUY, 100) == 20
    taker = Order("t", "t1", "TEST", Side.SELL, OrderType.MARKET, 5, 1)
    trades = book.submit(taker, 1)
    assert sum(t.quantity for t in trades) == 5
    assert trades[0].maker_order_id == "a"  # earlier sequence wins on equal priority


def test_fill_probability_decreases_with_depth() -> None:
    shallow = OrderBook._fill_probability(
        Order("i", "t", "TEST", Side.BUY, OrderType.MARKET, 1, 0),
        Order("m", "m", "TEST", Side.SELL, OrderType.LIMIT, 1, 0, price_ticks=100),
        depth_ahead=0,
        time_in_queue=10,
    )
    deep = OrderBook._fill_probability(
        Order("i", "t", "TEST", Side.BUY, OrderType.MARKET, 1, 0),
        Order("m", "m", "TEST", Side.SELL, OrderType.LIMIT, 1, 0, price_ticks=100),
        depth_ahead=5_000,
        time_in_queue=0,
    )
    assert shallow > deep


def test_latency_arrival_never_before_request() -> None:
    dist = LatencyDistribution()
    rng = np.random.default_rng(0)
    for _ in range(50):
        req = 1_000_000
        lat = dist.sample_entry(rng)
        arrive = dist.arrival_time_us(req, lat)
        assert arrive >= req


def test_temporary_impact_decays_with_time() -> None:
    near = temporary_impact_decay_bps(0.05, 0.0)
    far = temporary_impact_decay_bps(0.05, 10.0)
    assert near > far


def test_locate_failure_rises_with_inventory() -> None:
    low = locate_failure_probability(0.0, htb_supply=100_000)
    high = locate_failure_probability(500_000, htb_supply=100_000)
    assert high > low


def test_u_shape_volume_profile_has_open_close_spikes() -> None:
    # Hours 11-12: volume-time clustering via intraday U-shape + return scaling.
    weights = u_shaped_intraday_volume_weights(12)
    assert abs(sum(weights) - 1.0) < 1e-9
    assert weights[0] > weights[len(weights) // 2]
    assert weights[-1] > weights[len(weights) // 2]


def test_stress_depth_decay_in_profile() -> None:
    # Hours 11-12: displayed_depth_autor applies exponential return decay.
    calm = displayed_depth_autor(1_000, "normal", abs_return=0.0, regime_key="steady_trend")
    stressed = displayed_depth_autor(1_000, "normal", abs_return=0.05, regime_key="high_volatility")
    assert stressed < calm


def test_volume_simulator_return_scaling() -> None:
    from app.exchange.volume_simulator import VolumeSimulator

    sim = VolumeSimulator()
    returns = np.array([0.01, -0.02, 0.0, 0.03])
    base = sim.generate("steady_trend", returns, seed=7)
    scaled = sim.generate("steady_trend", returns, seed=7, r_gamma=20.0)
    # Return scaling should raise volume at high absolute returns.
    assert float(np.mean(scaled)) >= float(np.mean(base)) - 1e-9


def test_run_simulation_determinism_digest() -> None:

    world = build_demo_world(7)
    a = run_simulation(world, collect_timeline=True, collect_agent_states=False, collect_strategy_steps=False)
    b = run_simulation(world, collect_timeline=True, collect_agent_states=False, collect_strategy_steps=False)
    assert a.summary.get("ledger_digest") == b.summary.get("ledger_digest")
    assert a.summary.get("filled_quantity") == b.summary.get("filled_quantity")
