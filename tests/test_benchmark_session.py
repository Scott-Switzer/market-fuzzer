from __future__ import annotations

import json
from dataclasses import replace
from datetime import date
from typing import Any

from app.benchmark.model import SessionResult, TaskKind
from app.benchmark.port import (
    InProcessPort,
    StrategyDecisionPort,
    hold_action,
    passive_maker_port,
    submit_limit_action,
    twap_port,
)
from app.benchmark.session import BenchmarkSession, SessionConfig
from app.benchmark.tasks import build_task_spec
from app.benchmark.universe import (
    HIDDEN_PROFILE,
    PUBLIC_PROFILE,
    BenchmarkUniverse,
    HoldoutProfile,
    build_universe,
)
from app.market.calendar import trading_days
from app.world.rng import SemanticRNG


class RecordingPort:
    def __init__(self, inner: StrategyDecisionPort) -> None:
        self.name = "recording"
        self.inner = inner
        self.observations: list[dict[str, Any]] = []

    def decide(self, observation: dict[str, Any]) -> dict[str, Any]:
        self.observations.append(dict(observation))
        return self.inner.decide(observation)

    def close(self) -> None:
        self.inner.close()


class ScriptedPort:
    """Deterministic scripted port: one action per decision, then hold."""

    def __init__(self, actions: list[Any]) -> None:
        self.name = "scripted"
        self.actions = actions
        self.index = 0
        self.errors = 0
        self.unavailable_failures = 0
        self.protocol_failures = 0
        self.first_failure: str | None = None
        self.observations: list[dict[str, Any]] = []

    def decide(self, observation: dict[str, Any]) -> dict[str, Any]:
        self.observations.append(dict(observation))
        if self.index < len(self.actions):
            action = self.actions[self.index](observation)
        else:
            action = hold_action()
        self.index += 1
        return action

    def close(self) -> None:
        return None


def _universe(
    *,
    count: int = 4,
    sessions: int = 2,
    seed: int = 11,
    profile: HoldoutProfile = PUBLIC_PROFILE,
    universe_id: str = "u-session",
    world_id: str = "w-session",
) -> BenchmarkUniverse:
    return build_universe(
        universe_id=universe_id,
        world_id=world_id,
        seed=seed,
        profile=profile,
        security_count=count,
        sessions=tuple(trading_days(date(2026, 6, 1), date(2026, 6, 30))[:sessions]),
    )


def _run(
    kind: TaskKind,
    port: StrategyDecisionPort,
    *,
    count: int = 4,
    sessions: int = 2,
    steps: int = 6,
    seed: int = 11,
    max_order_quantity: int = 20_000,
    target_quantity: int = 2_000,
    profile: HoldoutProfile = PUBLIC_PROFILE,
    config: SessionConfig | None = None,
) -> SessionResult:
    universe = _universe(count=count, sessions=sessions, seed=seed, profile=profile)
    task = build_task_spec(
        kind, universe, target_quantity=target_quantity, max_order_quantity=max_order_quantity
    )
    session = BenchmarkSession(
        universe=universe,
        profile=profile,
        task=task,
        port=port,
        config=config or SessionConfig(steps_per_day=steps),
    )
    return session.run()


# -- determinism and basic behaviour --------------------------------------------


def test_a_session_is_byte_deterministic() -> None:
    first = _run(TaskKind.EXECUTION, twap_port(slice_quantity=500))
    second = _run(TaskKind.EXECUTION, twap_port(slice_quantity=500))
    assert first.ledger_digest == second.ledger_digest
    assert first.action_digest == second.action_digest
    assert first.agent_fills == second.agent_fills
    assert first.event_count == second.event_count
    assert first.arrival_price_ticks == second.arrival_price_ticks


def test_a_session_generates_events_and_trades() -> None:
    result = _run(TaskKind.EXECUTION, twap_port(slice_quantity=500))
    assert result.event_count > 0
    assert result.trade_count > 0
    assert result.steps_total == 12
    assert result.instruments == ("SYN001", "SYN002", "SYN003", "SYN004")


def test_the_agent_observation_contract_is_satisfied() -> None:
    port = RecordingPort(twap_port(slice_quantity=500))
    result = _run(TaskKind.EXECUTION, port)
    assert port.observations
    assert result.agent_fills
    for observation in port.observations:
        assert observation["schema_version"] == "2.0"
        assert observation["symbol"] == "SYN001"
        assert observation["mid_ticks"] > 0
        assert observation["spread_bps"] >= 0.0
        assert observation["remaining_quantity"] >= 0
        for order in observation["open_orders"]:
            assert order["limit_price_ticks"] > 0
            assert order["remaining_quantity"] > 0
    remaining = [int(item["remaining_quantity"]) for item in port.observations]
    assert remaining == sorted(remaining, reverse=True)


def test_holding_never_trades_the_agent_account() -> None:
    port = InProcessPort("always-hold", lambda _observation: hold_action())
    result = _run(TaskKind.EXECUTION, port)
    assert result.agent_fills == ()
    assert result.agent_filled_quantity == 0
    assert result.order_count == 0
    assert result.quote_uptime == 0.0


def test_an_aggressive_order_is_partially_filled_across_makers() -> None:
    state = {"done": False}

    def decide(observation: dict[str, Any]) -> dict[str, Any]:
        if state["done"]:
            return hold_action()
        state["done"] = True
        price = int(observation.get("best_ask_ticks") or observation["mid_ticks"])
        return submit_limit_action("buy", 5_000, price, "aggressive")

    result = _run(TaskKind.EXECUTION, InProcessPort("aggressive-once", decide))
    assert len(result.agent_fills) >= 2
    assert all(fill.quantity > 0 and fill.price_ticks > 0 for fill in result.agent_fills)
    assert result.agent_filled_quantity < 5_000


# -- sealed-evaluation opacity ---------------------------------------------------


def test_the_agent_session_id_does_not_reveal_the_world_or_partition() -> None:
    public = _universe(seed=101, universe_id="synth-exchange-execution-0000", world_id="bench-0000-public")
    hidden = _universe(seed=202, universe_id="synth-exchange-execution-0001", world_id="bench-0001-hidden")
    public_id = _public_session_id(public)
    hidden_id = _hidden_session_id(hidden)
    assert public_id != hidden_id
    assert public_id.startswith("sx-") and hidden_id.startswith("sx-")
    for token in ("0000", "0001", "public", "hidden", "bench", "synth-exchange"):
        assert token not in public_id
        assert token not in hidden_id


def _public_session_id(universe: BenchmarkUniverse) -> str:
    return _session_id_for(universe, PUBLIC_PROFILE)


def _hidden_session_id(universe: BenchmarkUniverse) -> str:
    return _session_id_for(universe, HIDDEN_PROFILE)


def _session_id_for(universe: BenchmarkUniverse, profile: HoldoutProfile) -> str:
    task = build_task_spec(TaskKind.EXECUTION, universe, target_quantity=1_000)
    session = BenchmarkSession(
        universe=universe,
        profile=profile,
        task=task,
        port=InProcessPort("opaque-probe", lambda _observation: hold_action()),
        config=SessionConfig(steps_per_day=1),
    )
    return session.session_identifier()


def _observations_for(
    profile: HoldoutProfile, *, universe_id: str, world_id: str, count: int = 3
) -> list[dict[str, Any]]:
    universe = _universe(
        count=count, sessions=1, seed=7, profile=profile, universe_id=universe_id, world_id=world_id
    )
    task = build_task_spec(TaskKind.PORTFOLIO, universe)
    port = RecordingPort(InProcessPort("probe", lambda _observation: hold_action()))
    BenchmarkSession(
        universe=universe,
        profile=profile,
        task=task,
        port=port,
        config=SessionConfig(steps_per_day=2),
    ).run()
    return port.observations


def test_public_and_hidden_observations_expose_the_same_fields() -> None:
    public = _observations_for(PUBLIC_PROFILE, universe_id="u-pub", world_id="w-pub")
    hidden = _observations_for(HIDDEN_PROFILE, universe_id="u-hid", world_id="w-hid")
    assert public and hidden
    assert set(public[0]) == set(hidden[0])


def test_serialized_observations_carry_no_partition_metadata() -> None:
    for profile, universe_id, world_id, forbidden in (
        (PUBLIC_PROFILE, "CANARY-UNIVERSE-ALPHA", "CANARY-WORLD-ALPHA", "ALPHA"),
        (HIDDEN_PROFILE, "CANARY-UNIVERSE-BETA", "CANARY-WORLD-BETA", "BETA"),
    ):
        observations = _observations_for(profile, universe_id=universe_id, world_id=world_id, count=3)
        payload = json.dumps(observations, sort_keys=True)
        assert forbidden not in payload
        assert universe_id not in payload
        assert world_id not in payload
        assert profile.label not in payload
        assert profile.holdout not in payload
        assert "holdout" not in payload
        assert "seed" not in payload


# -- maker / taker attribution ---------------------------------------------------


def test_a_crossing_agent_never_receives_maker_credit() -> None:
    # IOC orders never rest, so the agent can only ever be the taker. If maker
    # attribution were inferred from an order-ID prefix, background makers would
    # be miscredited here and this count would be non-zero.
    def decide(_observation: dict[str, Any]) -> dict[str, Any]:
        return {
            "schema_version": "2.0",
            "action_type": "submit",
            "side": "buy",
            "order_type": "market",
            "quantity": 300,
            "rationale_code": "ioc_taker",
        }

    result = _run(TaskKind.EXECUTION, InProcessPort("ioc-only", decide))
    assert result.agent_fills
    assert result.agent_maker_fill_count == 0
    assert result.agent_taker_fill_count == len(result.agent_fills)


def test_a_passive_agent_receives_maker_credit() -> None:
    result = _run(TaskKind.MARKET_MAKING, passive_maker_port(spread_ticks=3, quantity=200))
    assert result.agent_fills
    assert result.agent_maker_fill_count > 0
    assert result.agent_maker_fill_count == sum(1 for fill in result.agent_fills if fill.is_maker)
    assert result.agent_taker_fill_count == sum(1 for fill in result.agent_fills if not fill.is_maker)


# -- parent-order discipline -----------------------------------------------------


def test_twap_cannot_overfill_the_parent_order() -> None:
    result = _run(
        TaskKind.EXECUTION,
        twap_port(slice_quantity=700),
        target_quantity=3_000,
        steps=10,
        sessions=2,
    )
    assert result.agent_net_delivered_quantity <= 3_000
    assert result.agent_peak_inventory[result.focus_symbol] <= 3_000
    assert result.agent_filled_quantity <= 3_000


def test_twap_cancels_live_orders_once_the_target_is_delivered() -> None:
    port = twap_port(slice_quantity=1_000)
    observation = {
        "side": "buy",
        "remaining_quantity": 0,
        "open_orders": (
            {"order_id": "live-1", "side": "buy", "remaining_quantity": 250, "limit_price_ticks": 99},
        ),
    }
    action = port.decide(observation)
    assert action["action_type"] == "cancel"
    assert action["order_id"] == "live-1"


# -- accounting integrity --------------------------------------------------------


def test_the_equity_curve_starts_at_the_pre_decision_value() -> None:
    result = _run(TaskKind.EXECUTION, twap_port(slice_quantity=500))
    assert result.equity_curve_cents[0] == result.agent_initial_value_cents
    assert len(result.equity_curve_cents) == result.steps_total + 1


def test_fill_step_indices_agree_with_the_steps_the_agent_observed() -> None:
    """A fill must carry the step ordinal the agent was acting at.

    The equity curve carries a pre-decision baseline entry, so its length is not
    the trading step. Deriving ``step_index`` from that length shifted every fill
    forward by one and let a final-step fill report ``step_index == steps_total``,
    so agents received fills attributed to a step they never observed.
    """

    port = RecordingPort(twap_port(slice_quantity=500))
    result = _run(TaskKind.EXECUTION, port, count=2, sessions=1, steps=4, target_quantity=2_000)
    assert result.agent_fills, "the TWAP baseline should trade in this configuration"
    observed_steps = {int(observation["step"]) for observation in port.observations}
    assert observed_steps == {0, 1, 2, 3}
    fill_steps = {fill.step_index for fill in result.agent_fills}
    assert fill_steps <= observed_steps
    assert min(fill_steps) == 0, "a first-step fill must be step 0, not the curve offset"
    assert max(fill_steps) < result.steps_total
    assert result.steps_total == 4


def test_a_first_step_loss_is_visible_in_the_curve() -> None:
    # A small order fills inside one price level, so the only first-step value
    # change is the taker fee the hidden profile charges. If the curve did not
    # start at the pre-decision value, that loss would escape the drawdown.
    def decide(_observation: dict[str, Any]) -> dict[str, Any]:
        return {
            "schema_version": "2.0",
            "action_type": "submit",
            "side": "buy",
            "order_type": "market",
            "quantity": 50,
            "rationale_code": "fee_payer",
        }

    result = _run(TaskKind.EXECUTION, InProcessPort("fee-payer", decide), profile=HIDDEN_PROFILE)
    assert result.agent_fills
    assert result.equity_curve_cents[0] == result.agent_initial_value_cents
    assert result.equity_curve_cents[1] < result.equity_curve_cents[0]


def test_peak_inventory_survives_flattening() -> None:
    state: list[int] = []

    def build(position: int) -> Any:
        def decide(observation: dict[str, Any]) -> dict[str, Any]:
            index = len(state)
            state.append(index)
            if index == 0:
                price = int(observation.get("best_ask_ticks") or observation["mid_ticks"])
                return submit_limit_action("buy", position, price, "build")
            inventory = int(observation["inventory"])
            if inventory > 0:
                # Keep flattening with marketable orders until the book is flat;
                # a single resting sell could sit unfilled in a thin moment.
                return {
                    "schema_version": "2.0",
                    "action_type": "submit",
                    "side": "sell",
                    "order_type": "market",
                    "quantity": inventory,
                    "rationale_code": "flatten",
                }
            return hold_action()

        return decide

    result = _run(TaskKind.EXECUTION, InProcessPort("build-then-flatten", build(500)))
    peak = result.agent_peak_inventory[result.focus_symbol]
    assert peak > 0
    assert peak > abs(result.agent_positions[result.focus_symbol])


def test_market_making_holds_only_tradable_instruments() -> None:
    result = _run(TaskKind.MARKET_MAKING, passive_maker_port(spread_ticks=3, quantity=200))
    focus = result.focus_symbol
    held = {symbol: value for symbol, value in result.agent_positions.items() if value}
    assert set(held) == {focus}
    assert all(value == 0 for symbol, value in result.agent_positions.items() if symbol != focus)


def test_an_untradable_instrument_cannot_change_agent_pnl() -> None:
    baseline = _universe(count=4, sessions=2, seed=11)
    focus = "SYN001"
    # Shock the daily price path of an instrument the execution task cannot trade.
    shocked_securities = tuple(
        replace(
            security,
            daily_open_ticks=tuple(value * 3 for value in security.daily_open_ticks),
            daily_close_ticks=tuple(value * 3 for value in security.daily_close_ticks),
        )
        if security.symbol != focus
        else security
        for security in baseline.securities
    )
    shocked = replace(baseline, securities=shocked_securities)

    def evaluate_universe(universe: BenchmarkUniverse) -> SessionResult:
        task = build_task_spec(TaskKind.EXECUTION, universe, target_quantity=2_000)
        return BenchmarkSession(
            universe=universe,
            profile=PUBLIC_PROFILE,
            task=task,
            port=twap_port(slice_quantity=500),
            config=SessionConfig(steps_per_day=6),
        ).run()

    assert evaluate_universe(shocked).agent_final_value_cents == (
        evaluate_universe(baseline).agent_final_value_cents
    )


# -- order lifecycle -------------------------------------------------------------


def _passive_buy(observation: dict[str, Any]) -> dict[str, Any]:
    mid = int(observation.get("mid_ticks") or 1)
    return submit_limit_action("buy", 100, max(1, mid - 10), "rest")


def test_a_valid_cancel_is_accepted() -> None:
    def cancel_first(observation: dict[str, Any]) -> dict[str, Any]:
        orders = observation["open_orders"]
        assert orders, "expected a resting order to cancel"
        return {
            "schema_version": "2.0",
            "action_type": "cancel",
            "order_id": orders[0]["order_id"],
            "rationale_code": "cancel_live",
        }

    port = ScriptedPort([_passive_buy, cancel_first])
    result = _run(TaskKind.EXECUTION, port)
    assert result.cancel_count == 1
    assert result.violations == ()


def test_a_valid_replace_is_accepted() -> None:
    def replace_first(observation: dict[str, Any]) -> dict[str, Any]:
        orders = observation["open_orders"]
        assert orders, "expected a resting order to replace"
        mid = int(observation.get("mid_ticks") or 1)
        return {
            "schema_version": "2.0",
            "action_type": "replace",
            "order_id": orders[0]["order_id"],
            "quantity": 250,
            "limit_price_ticks": max(1, mid - 8),
            "rationale_code": "amend_live",
        }

    port = ScriptedPort([_passive_buy, replace_first])
    result = _run(TaskKind.EXECUTION, port)
    assert result.replace_count == 1
    assert result.violations == ()


def test_an_unknown_cancel_is_a_violation() -> None:
    def decide(_observation: dict[str, Any]) -> dict[str, Any]:
        return {
            "schema_version": "2.0",
            "action_type": "cancel",
            "order_id": "does-not-exist",
            "rationale_code": "bad_cancel",
        }

    result = _run(TaskKind.EXECUTION, InProcessPort("bad-cancel", decide))
    assert "agent_lifecycle_unknown_order_id" in result.violations
    assert result.cancel_count == 0


def test_an_unknown_replace_is_a_violation() -> None:
    def decide(_observation: dict[str, Any]) -> dict[str, Any]:
        return {
            "schema_version": "2.0",
            "action_type": "replace",
            "order_id": "never-observed",
            "quantity": 10,
            "limit_price_ticks": 100,
            "rationale_code": "bad_replace",
        }

    result = _run(TaskKind.EXECUTION, InProcessPort("bad-replace", decide))
    assert "agent_lifecycle_unknown_order_id" in result.violations
    assert result.replace_count == 0


def test_a_risk_limit_rejection_is_recorded() -> None:
    def decide(observation: dict[str, Any]) -> dict[str, Any]:
        price = int(observation.get("best_ask_ticks") or observation["mid_ticks"])
        return submit_limit_action("buy", 50_000, price, "too_large")

    result = _run(TaskKind.EXECUTION, InProcessPort("oversize", decide), max_order_quantity=1_000)
    assert any(
        violation.startswith("agent_order_rejected:risk_max_order_quantity")
        for violation in result.violations
    )
    # The port submits an oversized order on every decision; every one is
    # rejected by the venue risk limit and none of them trade.
    assert result.order_count == 12
    assert result.agent_fills == ()


def test_day_orders_expire_at_the_session_boundary() -> None:
    port = ScriptedPort([_passive_buy])
    result = _run(TaskKind.EXECUTION, port, sessions=2, steps=3)
    assert result.expired_day_order_count >= 1
    day_two = port.observations[3:]
    assert day_two
    assert all(not observation["open_orders"] for observation in day_two)


def test_the_noise_stream_is_scoped_per_instrument() -> None:
    rng = SemanticRNG("w", 7)
    first = rng.stream("AGENT:noise-01:SYN001", "benchmark.agent").uniform("noise.active", 0, 0)
    second = rng.stream("AGENT:noise-01:SYN002", "benchmark.agent").uniform("noise.active", 0, 0)
    repeat = rng.stream("AGENT:noise-01:SYN001", "benchmark.agent").uniform("noise.active", 0, 0)
    assert first == repeat
    assert first != second


def test_portfolio_task_touches_every_instrument() -> None:
    from app.benchmark.port import accumulate_port

    port = RecordingPort(accumulate_port(slice_quantity=200, max_shares_per_instrument=1_000))
    result = _run(TaskKind.PORTFOLIO, port, count=4)
    symbols = {item["symbol"] for item in port.observations}
    assert symbols == {"SYN001", "SYN002", "SYN003", "SYN004"}
    assert result.steps_total == 12
    assert len(port.observations) == 4 * 12
