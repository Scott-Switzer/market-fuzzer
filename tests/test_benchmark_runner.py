from __future__ import annotations

from app.benchmark.model import TaskKind
from app.benchmark.port import twap_port
from app.benchmark.runner import builtin_port_factory, run_benchmark


def _run(worlds: int = 6):
    return run_benchmark(
        kind=TaskKind.EXECUTION,
        port_factory=builtin_port_factory(TaskKind.EXECUTION),
        worlds=worlds,
        security_count=4,
        days=2,
        steps_per_day=8,
        base_seed=12345,
    )


def test_a_benchmark_run_scores_every_world() -> None:
    report = _run()
    assert report.evaluation_worlds == 6
    assert len(report.outcomes) == 6
    assert report.securities_encountered == 4
    assert report.exchange_events > 0
    assert all(0.0 <= outcome.score <= 100.0 for outcome in report.outcomes)


def test_worlds_are_partitioned_into_public_and_hidden() -> None:
    report = _run()
    holdouts = [outcome.holdout for outcome in report.outcomes]
    assert holdouts == ["public", "hidden", "public", "hidden", "public", "hidden"]


def test_generalization_gap_is_hidden_minus_public() -> None:
    report = _run()
    assert report.generalization_gap == report.hidden_score - report.public_score


def test_a_run_is_reproducible() -> None:
    first = _run()
    second = _run()
    assert first.replay_package["replay_digest"] == second.replay_package["replay_digest"]
    assert [outcome.score for outcome in first.outcomes] == [outcome.score for outcome in second.outcomes]


def test_the_replay_package_records_every_world() -> None:
    report = _run()
    worlds = report.replay_package["worlds"]
    assert len(worlds) == 6
    for entry in worlds:
        assert len(entry["ledger_digest"]) == 64
        assert len(entry["market_logical_sha256"]) == 64
        assert entry["event_count"] > 0


def test_the_report_renders_the_headline_story() -> None:
    report = _run()
    text = report.render()
    assert "FINANCIAL WORLD FACTORY BENCHMARK" in text
    assert "Optimal Execution" in text
    assert "Generalization gap" in text
    assert "Weakest environment" in text
    assert "did not exist before" not in text  # wording is explicit
    assert "existed" in text


def test_world_seeds_are_distinct() -> None:
    report = _run()
    seeds = {outcome.seed for outcome in report.outcomes}
    assert len(seeds) == 6


def test_a_custom_agent_port_is_used() -> None:
    report = run_benchmark(
        kind=TaskKind.EXECUTION,
        port_factory=lambda _index: twap_port(slice_quantity=1_000, name="my-agent"),
        worlds=2,
        security_count=4,
        days=1,
        steps_per_day=6,
        base_seed=99,
    )
    assert report.agent_name == "my-agent"
