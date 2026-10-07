from __future__ import annotations

from typing import Any

from app.benchmark.model import TaskKind
from app.benchmark.port import InProcessPort, StrategyDecisionPort
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


def test_a_healthy_run_is_scoreable() -> None:
    report = _run()
    assert report.scoreable is True
    assert report.run_status == "VALID"
    assert report.valid_worlds == 6
    assert report.invalid_worlds == ()
    assert all(outcome.scoreable for outcome in report.outcomes)


def test_worlds_are_partitioned_into_public_and_hidden() -> None:
    report = _run()
    holdouts = [outcome.holdout for outcome in report.outcomes]
    assert holdouts == ["public", "hidden", "public", "hidden", "public", "hidden"]


def test_the_gap_is_a_distribution_generalization_gap() -> None:
    report = _run()
    assert report.generalization_gap == report.hidden_score - report.public_score
    assert report.generalization_scope == "distribution"
    # M10.5 is a parameter/distribution holdout, not a process-family holdout.
    assert all("mechanism" not in outcome.profile_label for outcome in report.outcomes)


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
        assert entry["scoreable"] is True
        assert entry["agent_failure"] is None


def test_the_report_renders_the_headline_story() -> None:
    report = _run()
    text = report.render()
    assert "FINANCIAL WORLD FACTORY BENCHMARK" in text
    assert "Optimal Execution" in text
    assert "Distribution generalization gap" in text
    assert "Weakest environment" in text
    assert "Validity: VALID" in text
    assert "existed" in text
    assert "did not exist before" not in text  # wording is explicit


def test_world_seeds_are_distinct() -> None:
    report = _run()
    seeds = {outcome.seed for outcome in report.outcomes}
    assert len(seeds) == 6


def test_a_custom_agent_port_is_used() -> None:
    from app.benchmark.port import twap_port

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


def test_invalid_worlds_are_excluded_from_official_means() -> None:
    from app.benchmark.port import twap_port

    def port_factory(index: int) -> StrategyDecisionPort:
        if index == 1:
            return InProcessPort("broken", _explode)
        return twap_port(slice_quantity=2_500)

    report = run_benchmark(
        kind=TaskKind.EXECUTION,
        port_factory=port_factory,
        worlds=4,
        security_count=2,
        days=1,
        steps_per_day=4,
        base_seed=777,
        target_quantity=5_000,
    )
    assert report.scoreable is False
    assert report.run_status == "INVALID_AGENT_PROTOCOL"
    assert report.valid_worlds == 3
    assert len(report.invalid_worlds) == 1
    assert report.outcomes[1].scoreable is False
    assert "Official benchmark score: WITHHELD" in report.render()
    # The healthy worlds still produce non-zero summaries, but an invalid world
    # means the run carries no official score at all.
    assert report.public_score > 0.0
    assert report.hidden_score > 0.0


def test_the_replay_package_records_validity() -> None:
    def port_factory(_index: int) -> StrategyDecisionPort:
        return InProcessPort("broken", _explode)

    report = run_benchmark(
        kind=TaskKind.EXECUTION,
        port_factory=port_factory,
        worlds=2,
        security_count=2,
        days=1,
        steps_per_day=2,
        base_seed=5,
    )
    assert report.replay_package["scoreable"] is False
    assert report.replay_package["run_status"] == "INVALID_AGENT_PROTOCOL"
    assert all(entry["agent_failure"] == "agent_protocol" for entry in report.replay_package["worlds"])


def test_a_run_with_no_valid_worlds_presents_no_numeric_scores() -> None:
    """A dead agent must not be rendered as if it had earned scores of 0.0."""

    def port_factory(_index: int) -> StrategyDecisionPort:
        return InProcessPort("dead", _explode)

    report = run_benchmark(
        kind=TaskKind.EXECUTION,
        port_factory=port_factory,
        worlds=4,
        security_count=2,
        days=1,
        steps_per_day=3,
        base_seed=4242,
    )
    assert report.scoreable is False
    assert report.valid_worlds == 0
    assert report.valid_public_worlds == 0
    assert report.valid_hidden_worlds == 0
    text = report.render()
    assert "Public worlds score      n/a (no valid worlds)" in text
    assert "Hidden worlds score      n/a (no valid worlds)" in text
    assert "Distribution generalization gap n/a (both partitions required)" in text
    assert "Task metrics             withheld (no valid worlds)" in text
    assert "withheld (no valid worlds)" in text
    assert "Official benchmark score: WITHHELD" in text
    assert "0.0" not in text


def _explode(_observation: dict[str, Any]) -> dict[str, Any]:
    raise RuntimeError("adversarial port failure")
