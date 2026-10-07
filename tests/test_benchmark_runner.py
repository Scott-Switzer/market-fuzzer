from __future__ import annotations

from typing import Any

from app.benchmark.model import TaskKind
from app.benchmark.plan import (
    DEFAULT_PLAN_ID,
    DEFAULT_PLAN_VERSION,
    DatasetSplit,
    default_evaluation_plan,
    plan_worlds,
)
from app.benchmark.port import InProcessPort, StrategyDecisionPort
from app.benchmark.process_registry import EVALUATOR_PRIVATE_FAMILY, default_process_registry
from app.benchmark.runner import builtin_port_factory, run_benchmark
from app.benchmark.universe import DISTRIBUTION_ECOLOGY, FAMILIAR_ECOLOGY, EvaluationPartition
from app.market.process import FAMILIAR_FAMILY, MECHANISM_FAMILIES


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


def test_worlds_are_partitioned_into_familiar_distribution_and_mechanism() -> None:
    report = _run()
    partitions = [outcome.partition for outcome in report.outcomes]
    assert partitions == ["familiar", "distribution", "mechanism", "familiar", "distribution", "mechanism"]


def test_the_familiar_and_distribution_partitions_share_the_familiar_family() -> None:
    report = _run()
    for outcome in report.outcomes:
        if outcome.partition in (EvaluationPartition.FAMILIAR, EvaluationPartition.DISTRIBUTION):
            assert outcome.process_family == FAMILIAR_FAMILY.value


def test_mechanism_worlds_use_only_held_out_families() -> None:
    report = _run(worlds=12)
    held_out = {family.value for family in MECHANISM_FAMILIES}
    seen: set[str] = set()
    for outcome in report.outcomes:
        if outcome.partition == EvaluationPartition.MECHANISM:
            assert outcome.process_family in held_out
            assert outcome.process_family != FAMILIAR_FAMILY.value
            seen.add(outcome.process_family)
    # The mechanism partition rotates through every held-out family.
    assert seen == held_out
    assert set(report.mechanism_families) == held_out


def test_the_default_plan_reproduces_the_m10_6_world_assignment() -> None:
    """World identity, seed, partition, generator, and ecology, exactly as M10.6."""

    expected = (
        EvaluationPartition.FAMILIAR,
        EvaluationPartition.DISTRIBUTION,
        EvaluationPartition.MECHANISM,
    )
    worlds = plan_worlds(
        plan=default_evaluation_plan(),
        registry=default_process_registry(),
        count=12,
        base_seed=12_345,
    )
    for index, world in enumerate(worlds):
        partition = expected[index % 3]
        assert world.partition is partition
        assert world.world_id == f"bench-{index:04d}-{partition.value}"
        assert world.seed == (12_345 * 1_000_003 + index * 7_919) % 2_147_483_647
        assert world.split is DatasetSplit.PUBLIC_EVAL
        if partition is EvaluationPartition.MECHANISM:
            assert world.family_id in {family.value for family in MECHANISM_FAMILIES}
            assert world.ecology is DISTRIBUTION_ECOLOGY
        else:
            assert world.family_id == FAMILIAR_FAMILY.value
            assert world.ecology is (
                FAMILIAR_ECOLOGY if partition is EvaluationPartition.FAMILIAR else DISTRIBUTION_ECOLOGY
            )


def test_a_run_is_labelled_with_the_plan_that_produced_it() -> None:
    report = _run()
    assert report.evaluation_plan_id == DEFAULT_PLAN_ID
    assert report.evaluation_plan_version == DEFAULT_PLAN_VERSION
    assert report.sealed_worlds == 0
    text = report.render()
    assert f"Evaluation plan: {DEFAULT_PLAN_ID} ({DEFAULT_PLAN_VERSION})" in text
    assert "evaluator-private" not in text


def test_a_public_run_names_its_process_families() -> None:
    report = _run(worlds=12)
    assert set(report.mechanism_families) == {family.value for family in MECHANISM_FAMILIES}
    for outcome in report.outcomes:
        assert outcome.split == DatasetSplit.PUBLIC_EVAL.value
        assert outcome.process_family != EVALUATOR_PRIVATE_FAMILY


def test_the_replay_record_carries_the_provenance_an_authorized_replay_needs() -> None:
    report = _run()
    for entry in report.replay_package["worlds"]:
        assert entry["evaluation_plan_id"] == DEFAULT_PLAN_ID
        assert entry["evaluation_plan_version"] == DEFAULT_PLAN_VERSION
        assert entry["split"] == DatasetSplit.PUBLIC_EVAL.value
        assert entry["process_family_visibility"] == "public"
        assert len(entry["process_family_digest"]) == 64
        assert len(entry["seed_material_digest"]) == 64
        assert len(entry["generator_bundle_digest"]) == 64


def test_provenance_digests_are_stable_across_runs() -> None:
    first, second = _run(), _run()
    for field in ("process_family_digest", "generator_bundle_digest", "seed_material_digest"):
        assert [entry[field] for entry in first.replay_package["worlds"]] == [
            entry[field] for entry in second.replay_package["worlds"]
        ]


def test_the_distribution_gap_isolates_the_ecology_shift() -> None:
    report = _run()
    assert report.distribution_gap == report.distribution_score - report.familiar_score


def test_the_mechanism_gap_compares_unseen_families_to_the_familiar_one() -> None:
    report = _run()
    assert report.mechanism_gap == report.mechanism_score - report.familiar_score
    assert report.generalization_scope == "process-family"


def test_the_process_family_gap_isolates_the_generator_at_a_fixed_ecology() -> None:
    report = _run()
    # mechanism and distribution share the same shifted ecology, so their
    # difference is a pure process-family effect.
    assert report.process_family_gap == report.mechanism_score - report.distribution_score
    assert report.process_family_gap == report.mechanism_gap - report.distribution_gap


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
        assert entry["partition"] in {"familiar", "distribution", "mechanism"}
        assert len(entry["process_family"]) > 0


def test_the_report_renders_the_headline_story() -> None:
    report = _run()
    text = report.render()
    assert "FINANCIAL WORLD FACTORY BENCHMARK" in text
    assert "Optimal Execution" in text
    assert "Familiar worlds score" in text
    assert "Distribution worlds score" in text
    assert "Mechanism worlds score" in text
    assert "Distribution generalization gap" in text
    assert "Mechanism generalization gap" in text
    assert "Process-family generalization gap" in text
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
    assert report.familiar_score > 0.0
    assert report.distribution_score == 0.0  # the only distribution world failed
    assert report.mechanism_score > 0.0


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
    assert report.valid_familiar_worlds == 0
    assert report.valid_distribution_worlds == 0
    assert report.valid_mechanism_worlds == 0
    text = report.render()
    assert "Familiar worlds score" in text and "n/a (no valid worlds)" in text
    assert "Distribution worlds score" in text
    assert "Mechanism worlds score" in text
    assert "Distribution generalization gap" in text
    assert "Mechanism generalization gap" in text
    assert "Process-family generalization gap" in text
    assert text.count("n/a (both partitions required)") == 3
    assert "Task metrics             withheld (no valid worlds)" in text
    assert "withheld (no valid worlds)" in text
    assert "Official benchmark score: WITHHELD" in text
    assert "0.0" not in text


def _explode(_observation: dict[str, Any]) -> dict[str, Any]:
    raise RuntimeError("adversarial port failure")
