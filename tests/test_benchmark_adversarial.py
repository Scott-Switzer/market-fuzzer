"""Adversarial agents that try to exploit benchmark scoring and accounting.

Each agent attacks one specific integrity property. If any of these tests starts
passing for the wrong reason, the sealed-evaluation claim is broken.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

from app.benchmark import port as port_module
from app.benchmark.model import INVALID_AGENT_PROTOCOL, INVALID_AGENT_UNAVAILABLE, TaskKind
from app.benchmark.port import (
    HttpJsonPort,
    InProcessPort,
    StrategyDecisionPort,
    cancel_action,
    crossing_limit_action,
    hold_action,
    passive_maker_port,
)
from app.benchmark.runner import run_benchmark
from app.cli import cli

_SMALL = {"worlds": 2, "security_count": 2, "days": 1, "steps_per_day": 6, "base_seed": 4242}


def _factory(port: StrategyDecisionPort):
    def build(_index: int) -> StrategyDecisionPort:
        return port

    return build


def _run(kind: TaskKind, port: StrategyDecisionPort, **overrides: Any):
    settings = {**_SMALL, **overrides}
    return run_benchmark(kind=kind, port_factory=_factory(port), **settings)


# -- round-trip gaming -----------------------------------------------------------


def test_a_round_trip_gamer_earns_zero_execution_score() -> None:
    state = {"step": 0}

    def decide(observation: dict[str, Any]) -> dict[str, Any]:
        inventory = int(observation["inventory"])
        state["step"] += 1
        if state["step"] % 2 == 1:
            return crossing_limit_action(observation, 10)
        if inventory > 0:
            return crossing_limit_action({**observation, "side": "sell"}, inventory)
        return hold_action()

    report = _run(TaskKind.EXECUTION, InProcessPort("round-trip-gamer", decide), target_quantity=1_000)
    for outcome in report.outcomes:
        assert outcome.metrics["net_delivered_quantity"] <= 0
        assert outcome.metrics["completion"] == 0.0
        assert outcome.score == 0.0


# -- overfill --------------------------------------------------------------------


def test_an_overfill_attempt_cannot_exceed_full_completion() -> None:
    def decide(observation: dict[str, Any]) -> dict[str, Any]:
        return crossing_limit_action(observation, 10_000)

    report = _run(TaskKind.EXECUTION, InProcessPort("overfill", decide), target_quantity=1_000)
    for outcome in report.outcomes:
        assert outcome.metrics["completion"] <= 1.0
        assert outcome.score <= 100.0


# -- invalid lifecycle spam ------------------------------------------------------


def test_an_invalid_order_spammer_cannot_avoid_violations() -> None:
    state = {"step": 0}

    def decide(_observation: dict[str, Any]) -> dict[str, Any]:
        state["step"] += 1
        if state["step"] % 2 == 0:
            return cancel_action("ghost-order")
        return {
            "schema_version": "2.0",
            "action_type": "replace",
            "order_id": "ghost-order",
            "quantity": 10,
            "limit_price_ticks": 100,
            "rationale_code": "spam",
        }

    report = _run(TaskKind.EXECUTION, InProcessPort("invalid-spammer", decide))
    for outcome in report.outcomes:
        assert "agent_lifecycle_unknown_order_id" in outcome.violations
        assert outcome.score == 0.0


# -- maker credit ------------------------------------------------------------------


def test_taker_flow_cannot_receive_maker_credit() -> None:
    # An IOC ("market") order never rests, so the agent can only ever be the
    # taker. A crossing *limit* order would legitimately rest and could later be
    # hit, so it is the wrong instrument for this invariant.
    def decide(_observation: dict[str, Any]) -> dict[str, Any]:
        return {
            "schema_version": "2.0",
            "action_type": "submit",
            "side": "buy",
            "order_type": "market",
            "quantity": 200,
            "rationale_code": "ioc_taker",
        }

    report = _run(TaskKind.MARKET_MAKING, InProcessPort("taker-only", decide))
    for outcome in report.outcomes:
        assert outcome.metrics["maker_fill_count"] == 0
        assert outcome.metrics["maker_share"] == 0.0


def test_maker_then_flatten_still_reports_peak_inventory() -> None:
    from datetime import date

    from app.benchmark.session import BenchmarkSession, SessionConfig
    from app.benchmark.tasks import build_task_spec
    from app.benchmark.universe import FAMILIAR_ECOLOGY, build_universe
    from app.market.calendar import trading_days

    universe = build_universe(
        universe_id="u-adversarial",
        world_id="w-adversarial",
        seed=99,
        ecology=FAMILIAR_ECOLOGY,
        security_count=2,
        sessions=tuple(trading_days(date(2026, 6, 1), date(2026, 6, 30))[:1]),
    )
    task = build_task_spec(TaskKind.MARKET_MAKING, universe)
    state = {"step": 0}

    def decide(observation: dict[str, Any]) -> dict[str, Any]:
        state["step"] += 1
        inventory = int(observation["inventory"])
        if state["step"] == 3 and inventory > 0:
            return crossing_limit_action({**observation, "side": "sell"}, min(inventory, 20_000))
        return passive_maker_port(spread_ticks=2, quantity=100).decide(observation)

    result = BenchmarkSession(
        universe=universe,
        ecology=FAMILIAR_ECOLOGY,
        task=task,
        port=InProcessPort("maker-then-flatten", decide),
        config=SessionConfig(steps_per_day=6),
    ).run()
    focus = result.focus_symbol
    peak = result.agent_peak_inventory[focus]
    assert peak > 0
    assert peak >= abs(result.agent_positions[focus])


# -- agent availability ------------------------------------------------------------


def test_a_dead_http_agent_cannot_receive_an_official_score(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_client = httpx.Client

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        port_module.httpx, "Client", lambda **kwargs: real_client(transport=transport, **kwargs)
    )
    monkeypatch.setenv("FWF_BENCH_ADAPTER_ALLOWED_HOSTS", "127.0.0.1")
    report = run_benchmark(
        kind=TaskKind.EXECUTION,
        port_factory=lambda _index: HttpJsonPort("http://127.0.0.1:9000/decide"),
        **_SMALL,
    )
    assert report.scoreable is False
    assert report.run_status == INVALID_AGENT_UNAVAILABLE
    assert report.valid_worlds == 0
    assert len(report.invalid_worlds) == _SMALL["worlds"]
    assert report.familiar_score == 0.0
    assert report.distribution_score == 0.0
    assert "Official benchmark score: WITHHELD" in report.render()


def test_a_malformed_http_agent_cannot_receive_an_official_score(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_client = httpx.Client
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, content=b"not json"))
    monkeypatch.setattr(
        port_module.httpx, "Client", lambda **kwargs: real_client(transport=transport, **kwargs)
    )
    monkeypatch.setenv("FWF_BENCH_ADAPTER_ALLOWED_HOSTS", "127.0.0.1")
    report = run_benchmark(
        kind=TaskKind.PORTFOLIO,
        port_factory=lambda _index: HttpJsonPort("http://127.0.0.1:9000/decide"),
        **_SMALL,
    )
    assert report.scoreable is False
    assert report.run_status == INVALID_AGENT_PROTOCOL
    assert report.valid_worlds == 0


def test_an_in_process_failure_also_invalidates_the_run() -> None:
    def explode(_observation: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("agent crashed")

    report = _run(TaskKind.EXECUTION, InProcessPort("crashing", explode))
    assert report.scoreable is False
    assert report.run_status == INVALID_AGENT_PROTOCOL


def test_a_valid_hold_agent_stays_scoreable() -> None:
    report = _run(TaskKind.EXECUTION, InProcessPort("always-hold", lambda _o: hold_action()))
    assert report.scoreable is True
    assert report.valid_worlds == _SMALL["worlds"]
    assert report.run_status == "VALID"
    assert all(outcome.score == 0.0 for outcome in report.outcomes)


# -- evaluator-private process family ----------------------------------------------


def test_the_process_family_never_reaches_an_agent_observation() -> None:
    observations: list[dict[str, Any]] = []

    def decide(observation: dict[str, Any]) -> dict[str, Any]:
        observations.append(dict(observation))
        return hold_action()

    report = run_benchmark(
        kind=TaskKind.EXECUTION,
        port_factory=lambda _index: InProcessPort("recorder", decide),
        worlds=3,
        security_count=2,
        days=1,
        steps_per_day=3,
        base_seed=8_675_309,
    )
    assert {outcome.partition for outcome in report.outcomes} == {
        "familiar",
        "distribution",
        "mechanism",
    }
    assert observations
    payload = json.dumps(observations, sort_keys=True)
    for token in (
        "gjr_factor_t_v1",
        "stochastic_vol_factor_t_v1",
        "markov_regime_jump_factor_t_v1",
        "process_family",
        "familiar",
        "distribution",
        "mechanism",
        "holdout",
    ):
        assert token not in payload
    # The family is evaluator-private: it must never be the reason the agent
    # failed or the run went non-scoreable.
    assert report.scoreable is True
    assert report.run_status == "VALID"


def test_a_family_agnostic_agent_has_zero_generalization_gaps() -> None:
    # Holding does nothing in every world, so every partition mean is identical:
    # the gaps are pure differences and inject no family-specific noise.
    report = _run(TaskKind.EXECUTION, InProcessPort("agnostic", lambda _o: hold_action()))
    assert report.scoreable is True
    assert report.familiar_score == report.distribution_score == report.mechanism_score == 0.0
    assert report.distribution_gap == 0.0
    assert report.mechanism_gap == 0.0
    assert report.process_family_gap == 0.0


def test_a_family_sensitive_agent_earns_a_nonzero_process_family_gap() -> None:
    # An agent whose sizing keys off recent mid-price volatility must be scored
    # differently when the underlying process family changes. The mechanism and
    # distribution partitions share an ecology, so any difference is the family.
    def port_factory(_index: int) -> StrategyDecisionPort:
        last: dict[str, int] = {}

        def decide(observation: dict[str, Any]) -> dict[str, Any]:
            symbol = str(observation["symbol"])
            mid = int(observation.get("mid_ticks") or 1)
            previous = last.get(symbol)
            last[symbol] = mid
            quantity = 400
            if previous is not None and previous > 0 and abs(mid - previous) / previous >= 0.002:
                quantity = 80
            return crossing_limit_action(observation, quantity)

        return InProcessPort("vol-reactive", decide)

    report = run_benchmark(
        kind=TaskKind.EXECUTION,
        port_factory=port_factory,
        worlds=9,
        security_count=2,
        days=2,
        steps_per_day=8,
        base_seed=31337,
        target_quantity=5_000,
    )
    assert report.valid_distribution_worlds > 0
    assert report.valid_mechanism_worlds > 0
    assert report.process_family_gap != 0.0


# -- CLI exit-code semantics -------------------------------------------------------


def _cli_args(*extra: str) -> list[str]:
    return [
        "benchmark",
        "run",
        "--task",
        "execution",
        "--worlds",
        "1",
        "--securities",
        "2",
        "--days",
        "1",
        "--steps-per-day",
        "2",
        *extra,
    ]


def test_cli_exits_zero_for_a_valid_builtin_run() -> None:
    result = CliRunner().invoke(cli, _cli_args())
    assert result.exit_code == 0
    assert "Validity: VALID" in result.output


def test_cli_exits_two_when_the_agent_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FWF_BENCH_ADAPTER_ALLOWED_HOSTS", "127.0.0.1")
    result = CliRunner().invoke(cli, _cli_args("--agent", "http://127.0.0.1:9/decide"))
    assert result.exit_code == 2
    assert "WITHHELD" in result.output


def test_cli_allow_invalid_preserves_exit_zero_but_labels_the_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FWF_BENCH_ADAPTER_ALLOWED_HOSTS", "127.0.0.1")
    result = CliRunner().invoke(cli, _cli_args("--agent", "http://127.0.0.1:9/decide", "--allow-invalid"))
    assert result.exit_code == 0
    assert "WITHHELD" in result.output
    assert "INVALID_AGENT_UNAVAILABLE" in result.output
    # The smoke path must stay labelled non-scoreable rather than printing a
    # numeric official score for an agent that never answered.
    assert "Familiar worlds score" in result.output
    assert "Distribution worlds score" in result.output
    assert "Mechanism worlds score" in result.output
    assert "n/a (no valid worlds)" in result.output
    assert "Valid worlds: 0" in result.output
