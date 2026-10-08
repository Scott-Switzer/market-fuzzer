"""Evaluator-private mechanism holdout tests (M10.6.1).

The open M10.6 benchmark can only use families that live in the participant-facing
repository, because the family axis was the public ``ProcessFamilyKind`` enum.
These tests prove the closure that M10.7 needs:

* a **private** family implements the public :class:`ProcessFamily` interface,
  is registered into a trusted evaluator registry, and is *executed* by a sealed
  evaluation plan -- without joining the public enum, the public registry, or any
  published list;
* the same plan against the public registry fails with ``UNKNOWN_PROCESS_FAMILY``
  before a single world is generated;
* no evaluator-private identifier, canary, or class name reaches the agent or the
  public rendering;
* the evaluator's replay evidence still reproduces the campaign, and a sealed
  world is classified as non-trainable.

The private family, its definition, and the canary campaign live only in this test
module. Nothing here is exported by ``app.benchmark``.
"""

from __future__ import annotations

import inspect
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date
from typing import Any, ClassVar

import pytest

from app.benchmark.model import INVALID_AGENT_PROTOCOL, TaskKind
from app.benchmark.plan import (
    DEFAULT_PLAN_ID,
    SEALED_PLAN_EXPORT_ERROR,
    DatasetSplit,
    EvaluationPlan,
    SealedEvaluationExportError,
    default_ecology_registry,
    default_evaluation_plan,
    is_trainable,
    plan_worlds,
    require_trainable,
    validate_plan,
)
from app.benchmark.plan import (
    EvaluationWorldTemplate as Template,
)
from app.benchmark.port import InProcessPort, StrategyDecisionPort, twap_port
from app.benchmark.process_registry import (
    EVALUATOR_PRIVATE_FAMILY,
    UNKNOWN_PROCESS_FAMILY,
    DuplicateProcessFamilyError,
    FamilyVisibility,
    MarketProcessRegistry,
    ProcessFamilyDefinition,
    ProcessNodeRole,
    UnknownProcessFamilyError,
    default_process_registry,
    validate_family_id,
)
from app.benchmark.runner import BenchmarkReport, run_benchmark
from app.benchmark.universe import DISTRIBUTION_ECOLOGY, FAMILIAR_ECOLOGY, EvaluationPartition
from app.market.engine import standardized_t_draw
from app.market.process import FAMILIAR_FAMILY, GJR_FACTOR_T_V1, ProcessFamily, ProcessFamilyKind
from app.strategy_protocol import StrategyObservationV2
from app.world.rng import SemanticStream

# --- the private fixture ----------------------------------------------------------

#: The private family's identifier: stable, versioned, and absent from the public
#: enum and from the public registry.
PRIVATE_FAMILY_ID = "test_private_mean_reverting_family_v1"

#: The sealed plan's identifier.
SEALED_TEST_PLAN_ID = "sealed_test_plan_v1"

# --- canaries ---------------------------------------------------------------------
#
# One distinctive string is planted in each place an evaluator-private fact could
# leak from: the family identifier, the family class name, private evaluator
# metadata carried *on the family instance*, and the evaluation plan (identifier
# and description).

CANARY = "SUPER_SECRET_EVALUATOR_FAMILY_CANARY_9328"
CANARY_SLUG = "super_secret_evaluator_family_canary_9328"
CANARY_CLASS_TOKEN = "EvaluatorFamilyCanary9328"
CANARY_FAMILY_ID = f"{CANARY_SLUG}_v1"
CANARY_PLAN_ID = f"sealed_test_plan_{CANARY_SLUG}_v1"
CANARY_METADATA = f"{CANARY}: latent regime schedule, generator config, family registry key"
CANARIES = (CANARY, CANARY_SLUG, CANARY_CLASS_TOKEN, CANARY_FAMILY_ID, CANARY_PLAN_ID)

#: The agent-visible protocol. Any other field in an observation is a leak.
PUBLIC_OBSERVATION_FIELDS = frozenset(StrategyObservationV2.model_fields) | {"schema_version"}

#: Per-role (kappa, nu) for the private family; sigma is solved so the family's
#: unconditional variance matches the published baseline's.
_PRIVATE_SHAPE: dict[ProcessNodeRole, tuple[float, float]] = {
    ProcessNodeRole.MARKET: (0.35, 6.0),
    ProcessNodeRole.SECTOR: (0.42, 7.0),
    ProcessNodeRole.ENTITY: (0.30, 5.0),
}


def _baseline_variance() -> dict[ProcessNodeRole, float]:
    """The published families' unconditional variance per role, at scale 1."""

    registry = default_process_registry()
    return {
        role: registry.build(GJR_FACTOR_T_V1, role=role, volatility_scale=1.0).unconditional_variance()
        for role in ProcessNodeRole
    }


_BASELINE_VARIANCE = _baseline_variance()


@dataclass(frozen=True)
class MeanRevertingFactorT(ProcessFamily):
    """A test-only evaluator-private family: an AR(1) *level*-reverting process.

    ``eps_t = kappa * eps_{t-1} + sigma * z_t`` with unit-variance Student-t
    shocks, started in the stationary distribution. It differs structurally from
    every published family: the level mean-reverts and the volatility is constant,
    so there is no volatility clustering, no latent volatility process, and no
    regime switching.

    The unconditional variance is ``sigma^2 / (1 - kappa^2)``, so the definition
    below normalizes it to the published baseline's variance exactly and keeps the
    sealed holdout about *structure* rather than about volatility level.
    """

    kappa: float
    sigma: float
    nu: float
    volatility_scale: float = 1.0
    #: Private evaluator metadata. Carried on the instance so that any code path
    #: that ever serialized a generator into agent-visible data is caught.
    provenance_note: str = ""

    name: ClassVar[str] = PRIVATE_FAMILY_ID

    def __post_init__(self) -> None:
        if not 0.0 <= self.kappa < 1.0:
            raise ValueError("kappa must lie in [0, 1)")
        if self.sigma <= 0.0:
            raise ValueError("sigma must be positive")
        if self.nu <= 2.0:
            raise ValueError("nu must exceed 2 for a finite variance")
        if self.volatility_scale <= 0.0:
            raise ValueError("volatility_scale must be positive")

    def unconditional_variance(self) -> float:
        return self.volatility_scale**2 * self.sigma**2 / (1.0 - self.kappa**2)

    def innovations(
        self, stream: SemanticStream, sessions: Sequence[date], variable: str
    ) -> tuple[float, ...]:
        stationary_sd = self.sigma / math.sqrt(1.0 - self.kappa**2)
        level = 0.0
        path: list[float] = []
        for ordinal in range(len(sessions)):
            shock = standardized_t_draw(stream, f"{variable}.ou", ordinal, self.nu)
            level = stationary_sd * shock if ordinal == 0 else self.kappa * level + self.sigma * shock
            path.append(self.volatility_scale * level)
        return tuple(path)


@dataclass(frozen=True)
class SuperSecretEvaluatorFamilyCanary9328FactorT(MeanRevertingFactorT):
    """The same private process under a class name that carries the canary."""

    name: ClassVar[str] = CANARY_FAMILY_ID


class MeanRevertingDefinition(ProcessFamilyDefinition):
    """A test-only evaluator-private definition: never published, never listed."""

    family_id: ClassVar[str] = PRIVATE_FAMILY_ID
    visibility: ClassVar[FamilyVisibility] = FamilyVisibility.EVALUATOR_PRIVATE

    family_class: ClassVar[type[MeanRevertingFactorT]] = MeanRevertingFactorT
    provenance_note: ClassVar[str] = ""

    def build(self, *, role: ProcessNodeRole, volatility_scale: float) -> ProcessFamily:
        kappa, nu = _PRIVATE_SHAPE[role]
        sigma = math.sqrt(_BASELINE_VARIANCE[role] * (1.0 - kappa**2))
        return self.family_class(
            kappa=kappa,
            sigma=sigma,
            nu=nu,
            volatility_scale=volatility_scale,
            provenance_note=self.provenance_note,
        )


class CanaryDefinition(MeanRevertingDefinition):
    """A private definition whose identifier, class name, and metadata are canaries."""

    family_id: ClassVar[str] = CANARY_FAMILY_ID
    family_class: ClassVar[type[MeanRevertingFactorT]] = SuperSecretEvaluatorFamilyCanary9328FactorT
    provenance_note: ClassVar[str] = CANARY_METADATA


def _trusted_registry() -> MarketProcessRegistry:
    """The public registry plus one evaluator-private family.

    This is the entire injection surface: one ``register`` call from code that
    already runs inside the evaluator. No import path, module name, or
    participant-supplied input is involved.
    """

    registry = default_process_registry()
    registry.register(MeanRevertingDefinition())
    return registry


def _sealed_plan(*, plan_id: str = SEALED_TEST_PLAN_ID, family_id: str = PRIVATE_FAMILY_ID) -> EvaluationPlan:
    """The M10.6 partition cycle with every mechanism world on a private family."""

    return EvaluationPlan(
        plan_id=plan_id,
        version="v1",
        worlds=(
            Template(
                family_id=FAMILIAR_FAMILY.value,
                ecology_id=FAMILIAR_ECOLOGY.label,
                split=DatasetSplit.PUBLIC_EVAL,
                partition=EvaluationPartition.FAMILIAR,
            ),
            Template(
                family_id=FAMILIAR_FAMILY.value,
                ecology_id=DISTRIBUTION_ECOLOGY.label,
                split=DatasetSplit.PUBLIC_EVAL,
                partition=EvaluationPartition.DISTRIBUTION,
            ),
            Template(
                family_id=family_id,
                ecology_id=DISTRIBUTION_ECOLOGY.label,
                split=DatasetSplit.SEALED_EVAL,
                partition=EvaluationPartition.MECHANISM,
            ),
        ),
    )


def _sealed_run(
    *,
    registry: MarketProcessRegistry | None = None,
    worlds: int = 6,
    port: StrategyDecisionPort | None = None,
) -> BenchmarkReport:
    """Run the sealed plan on the trusted registry."""

    return run_benchmark(
        kind=TaskKind.EXECUTION,
        port_factory=lambda _index: port or twap_port(slice_quantity=500),
        worlds=worlds,
        security_count=4,
        days=2,
        steps_per_day=8,
        base_seed=4242,
        target_quantity=4_000,
        plan=_sealed_plan(),
        registry=registry if registry is not None else _trusted_registry(),
    )


# --- registry ---------------------------------------------------------------------


def test_the_default_registry_holds_exactly_the_public_families() -> None:
    registry = default_process_registry()
    assert registry.family_ids() == (
        "gjr_factor_t_v1",
        "markov_regime_jump_factor_t_v1",
        "stochastic_vol_factor_t_v1",
    )
    assert registry.public_family_ids() == registry.family_ids()
    assert all(
        registry.visibility(family_id) is FamilyVisibility.PUBLIC for family_id in registry.family_ids()
    )


def test_the_public_enum_and_the_public_registry_cannot_drift() -> None:
    # The enum stays as convenience metadata for the built-in families; the
    # registry is the authoritative list. Adding a family to one and not the other
    # fails here.
    assert set(default_process_registry().public_family_ids()) == {
        family.value for family in ProcessFamilyKind
    }


def test_the_private_fixture_is_absent_from_the_default_registry() -> None:
    registry = default_process_registry()
    assert registry.contains(PRIVATE_FAMILY_ID) is False
    assert registry.contains(CANARY_FAMILY_ID) is False
    assert PRIVATE_FAMILY_ID not in registry.family_ids()
    assert PRIVATE_FAMILY_ID not in registry.public_family_ids()
    with pytest.raises(UnknownProcessFamilyError):
        registry.resolve(PRIVATE_FAMILY_ID)


def test_a_trusted_registry_reaches_the_private_family_and_its_commitment() -> None:
    registry = _trusted_registry()
    definition = registry.resolve(PRIVATE_FAMILY_ID)
    assert definition.visibility is FamilyVisibility.EVALUATOR_PRIVATE
    assert definition.public_label == EVALUATOR_PRIVATE_FAMILY
    node = registry.build(PRIVATE_FAMILY_ID, role=ProcessNodeRole.MARKET, volatility_scale=1.0)
    assert isinstance(node, MeanRevertingFactorT)
    assert node.name == PRIVATE_FAMILY_ID
    # The private family is normalized to the published baseline's variance, so a
    # sealed score reflects its structure rather than a level shift.
    assert node.unconditional_variance() == pytest.approx(_BASELINE_VARIANCE[ProcessNodeRole.MARKET])
    # A private family is not listed, but the registry still knows its version.
    assert registry.resolve(PRIVATE_FAMILY_ID).version == "v1"
    # Public families are still named; only the private one is redacted.
    assert registry.public_label(GJR_FACTOR_T_V1) == GJR_FACTOR_T_V1
    assert len(registry.commitment(PRIVATE_FAMILY_ID)) == 64


def test_a_duplicate_family_id_is_rejected() -> None:
    # A public family cannot be re-registered over itself...
    registry = default_process_registry()
    with pytest.raises(DuplicateProcessFamilyError):
        registry.register(registry.resolve(GJR_FACTOR_T_V1))
    # ...and a private family cannot silently replace another private family.
    trusted = _trusted_registry()
    with pytest.raises(DuplicateProcessFamilyError):
        trusted.register(MeanRevertingDefinition())
    # The rejected registration left the original definition in place.
    assert isinstance(
        trusted.build(PRIVATE_FAMILY_ID, role=ProcessNodeRole.MARKET, volatility_scale=1.0),
        MeanRevertingFactorT,
    )


def test_a_family_id_must_be_a_stable_versioned_slug() -> None:
    for bad in ("MeanReverting", "mean_reverting", "mean_reverting_v", "_mean_reverting_v1", ""):
        with pytest.raises(ValueError):
            validate_family_id(bad)
    assert validate_family_id("mean_reverting_factor_v12") == "mean_reverting_factor_v12"

    class Unversioned(MeanRevertingDefinition):
        family_id: ClassVar[str] = "no_version_here"

    with pytest.raises(ValueError):
        default_process_registry().register(Unversioned())


def test_an_unknown_family_reports_unknown_process_family() -> None:
    registry = default_process_registry()
    with pytest.raises(UnknownProcessFamilyError) as resolve_error:
        registry.resolve("not_a_registered_family_v1")
    assert resolve_error.value.code == UNKNOWN_PROCESS_FAMILY
    assert resolve_error.value.family_id == "not_a_registered_family_v1"
    assert UNKNOWN_PROCESS_FAMILY in str(resolve_error.value)
    with pytest.raises(UnknownProcessFamilyError):
        registry.build("not_a_registered_family_v1", role=ProcessNodeRole.ENTITY, volatility_scale=1.0)
    assert registry.contains("not_a_registered_family_v1") is False


def test_the_participant_facing_cli_cannot_be_asked_to_load_evaluator_code() -> None:
    """The injection surface is trusted code, never a participant-facing knob.

    A registry definition is a Python object handed over in-process. Nothing the
    CLI exposes may name a family, a plan, a registry, or a module to import, so a
    participant cannot widen the evaluation from outside.
    """

    from app.cli import benchmark_run

    parameters = {name.lower() for name in inspect.signature(benchmark_run).parameters}
    for forbidden in ("plan", "registry", "family", "module", "import", "ecolog", "path"):
        assert not any(forbidden in name for name in parameters), forbidden
    assert {"task", "agent", "worlds", "policy", "output"} <= parameters
    # The public registry and the default plan are what a CLI run uses.
    assert default_process_registry().family_ids() == (
        "gjr_factor_t_v1",
        "markov_regime_jump_factor_t_v1",
        "stochastic_vol_factor_t_v1",
    )


def test_the_private_family_never_had_to_join_the_public_enum() -> None:
    public = {family.value for family in ProcessFamilyKind}
    assert PRIVATE_FAMILY_ID not in public
    assert CANARY_FAMILY_ID not in public
    assert PRIVATE_FAMILY_ID not in default_process_registry().family_ids()


# --- the sealed campaign ----------------------------------------------------------


def test_a_private_evaluation_plan_runs_to_completion_with_the_trusted_registry() -> None:
    """The central acceptance test: an unlisted family is genuinely evaluated."""

    report = _sealed_run()
    assert report.scoreable is True
    assert report.run_status == "VALID"
    assert report.evaluation_worlds == 6
    assert report.valid_worlds == 6
    assert report.valid_familiar_worlds == 2
    assert report.valid_distribution_worlds == 2
    assert report.valid_mechanism_worlds == 2
    assert report.familiar_score > 0.0
    assert report.distribution_score > 0.0
    assert report.mechanism_score > 0.0
    # The mechanism partition ran on the private family, on the *same* ecology as
    # the distribution partition, so the isolated family gap is meaningful.
    mechanism = [item for item in report.outcomes if item.partition == "mechanism"]
    assert len(mechanism) == 2
    for item in mechanism:
        assert item.split == DatasetSplit.SEALED_EVAL.value
        assert item.ecology_label == DISTRIBUTION_ECOLOGY.label
        assert item.replay["process_family"] == PRIVATE_FAMILY_ID
    assert report.process_family_gap == pytest.approx(report.mechanism_score - report.distribution_score)
    # Every world exercised the agent and produced a real ledger.
    assert all(item.replay["event_count"] > 0 for item in report.outcomes)


def test_a_sealed_evaluation_plan_fails_against_the_public_registry() -> None:
    calls: list[int] = []

    def port_factory(index: int) -> StrategyDecisionPort:
        calls.append(index)
        return twap_port(slice_quantity=500)

    for registry in (default_process_registry(), None):
        with pytest.raises(UnknownProcessFamilyError) as excinfo:
            run_benchmark(
                kind=TaskKind.EXECUTION,
                port_factory=port_factory,
                worlds=3,
                security_count=2,
                days=1,
                steps_per_day=4,
                target_quantity=2_000,
                plan=_sealed_plan(),
                registry=registry,
            )
        assert excinfo.value.code == UNKNOWN_PROCESS_FAMILY
        assert excinfo.value.family_id == PRIVATE_FAMILY_ID
    # Fail-fast: the plan resolves before any world is generated, so the agent was
    # never even asked for a port.
    assert calls == []


def test_a_run_shorter_than_the_plan_cycle_is_still_rejected_by_the_public_registry() -> None:
    """A short world count must not weaken the plan it belongs to.

    The private family sits in the *third* template of the sealed plan, so a
    prefix-only check would accept the plan for a two-world run and quietly score
    a campaign that contains no sealed world at all.
    """

    public = default_process_registry()
    with pytest.raises(UnknownProcessFamilyError) as excinfo:
        plan_worlds(plan=_sealed_plan(), registry=public, count=1, base_seed=1)
    assert excinfo.value.family_id == PRIVATE_FAMILY_ID
    with pytest.raises(UnknownProcessFamilyError):
        run_benchmark(
            kind=TaskKind.EXECUTION,
            port_factory=lambda _index: twap_port(slice_quantity=500),
            worlds=2,
            security_count=2,
            days=1,
            steps_per_day=4,
            target_quantity=2_000,
            plan=_sealed_plan(),
            registry=public,
        )
    # The trusted registry validates the same short run and produces it.
    report = _sealed_run(registry=_trusted_registry(), worlds=2)
    assert report.evaluation_worlds == 2
    assert report.sealed_worlds == 0  # only the familiar and distribution templates


def test_validating_a_plan_checks_every_family_and_ecology_it_names() -> None:
    registry = _trusted_registry()
    validate_plan(plan=_sealed_plan(), registry=registry)
    with pytest.raises(UnknownProcessFamilyError):
        validate_plan(plan=_sealed_plan(), registry=default_process_registry())
    with pytest.raises(KeyError):
        validate_plan(
            plan=EvaluationPlan(
                plan_id="unknown_ecology_v1",
                version="v1",
                worlds=(
                    Template(
                        family_id=FAMILIAR_FAMILY.value,
                        ecology_id="missing-ecology",
                        split=DatasetSplit.PUBLIC_EVAL,
                        partition=EvaluationPartition.FAMILIAR,
                    ),
                ),
            ),
            registry=registry,
        )


def test_a_plan_can_name_a_custom_ecology_through_the_evaluator_registry() -> None:
    custom = replace(FAMILIAR_ECOLOGY, label="evaluator-custom", depth_scale=0.8)
    ecologies = default_ecology_registry()
    ecologies.register(custom)
    plan = EvaluationPlan(
        plan_id="custom_ecology_plan_v1",
        version="v1",
        worlds=(
            Template(
                family_id=FAMILIAR_FAMILY.value,
                ecology_id=custom.label,
                split=DatasetSplit.PUBLIC_EVAL,
                partition=EvaluationPartition.FAMILIAR,
            ),
        ),
    )
    report = run_benchmark(
        kind=TaskKind.EXECUTION,
        port_factory=lambda _index: twap_port(slice_quantity=500),
        worlds=1,
        security_count=2,
        days=1,
        steps_per_day=4,
        target_quantity=2_000,
        plan=plan,
        ecologies=ecologies,
    )
    assert report.scoreable is True
    assert report.outcomes[0].ecology_label == custom.label
    # Without the evaluator's ecology registry the same plan cannot run.
    with pytest.raises(KeyError):
        run_benchmark(
            kind=TaskKind.EXECUTION,
            port_factory=lambda _index: twap_port(slice_quantity=500),
            worlds=1,
            security_count=2,
            days=1,
            steps_per_day=4,
            target_quantity=2_000,
            plan=plan,
        )


def test_the_plan_itself_names_the_private_family_as_its_mechanism_generator() -> None:
    plan = _sealed_plan()
    assert plan.family_ids() == (FAMILIAR_FAMILY.value, PRIVATE_FAMILY_ID)


def test_a_sealed_campaign_is_reproducible() -> None:
    first = _sealed_run()
    second = _sealed_run()
    assert first.replay_package["replay_digest"] == second.replay_package["replay_digest"]
    assert [item.score for item in first.outcomes] == [item.score for item in second.outcomes]
    assert [item.replay["ledger_digest"] for item in first.outcomes] == [
        item.replay["ledger_digest"] for item in second.outcomes
    ]


def test_invalid_agent_semantics_are_unchanged_on_a_sealed_campaign() -> None:
    def explode(_observation: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("adversarial port failure")

    report = _sealed_run(port=InProcessPort("dead", explode))
    assert report.scoreable is False
    assert report.run_status == INVALID_AGENT_PROTOCOL
    assert report.valid_worlds == 0
    assert report.sealed_worlds == 2
    text = report.render()
    assert "Official benchmark score: WITHHELD" in text
    assert "n/a (no valid worlds)" in text


# --- redaction and provenance ------------------------------------------------------


def test_the_public_result_redacts_a_private_process_family() -> None:
    report = _sealed_run()
    mechanism = [item for item in report.outcomes if item.partition == "mechanism"]
    assert mechanism
    assert all(item.process_family == EVALUATOR_PRIVATE_FAMILY for item in mechanism)
    assert report.mechanism_families == (EVALUATOR_PRIVATE_FAMILY,)
    # The published families of the same campaign are still named normally.
    assert all(
        item.process_family == FAMILIAR_FAMILY.value
        for item in report.outcomes
        if item.partition != "mechanism"
    )
    text = report.render()
    assert EVALUATOR_PRIVATE_FAMILY in text
    assert PRIVATE_FAMILY_ID not in text
    assert report.sealed_worlds == sum(
        1 for item in report.outcomes if item.split == DatasetSplit.SEALED_EVAL.value
    )
    # A sealed campaign's public view names no evaluator-private campaign either.
    assert report.evaluation_plan_id == SEALED_TEST_PLAN_ID  # the evaluator-side object
    assert SEALED_TEST_PLAN_ID not in text
    assert "evaluator-private plan" in text


def test_an_open_campaign_still_names_its_plan_and_families() -> None:
    report = run_benchmark(
        kind=TaskKind.EXECUTION,
        port_factory=lambda _index: twap_port(slice_quantity=500),
        worlds=3,
        security_count=2,
        days=1,
        steps_per_day=4,
        target_quantity=2_000,
    )
    assert report.evaluation_plan_id == DEFAULT_PLAN_ID
    assert report.sealed_worlds == 0
    text = report.render()
    assert DEFAULT_PLAN_ID in text
    assert EVALUATOR_PRIVATE_FAMILY not in text


def test_the_evaluator_replay_preserves_the_private_provenance() -> None:
    report = _sealed_run()
    commitment = _trusted_registry().commitment(PRIVATE_FAMILY_ID)
    mechanism = [entry for entry in report.replay_package["worlds"] if entry["partition"] == "mechanism"]
    assert len(mechanism) == 2
    for entry in mechanism:
        assert entry["process_family"] == PRIVATE_FAMILY_ID
        assert entry["process_family_visibility"] == FamilyVisibility.EVALUATOR_PRIVATE.value
        assert entry["process_family_digest"] == commitment
        assert entry["split"] == DatasetSplit.SEALED_EVAL.value
        assert entry["evaluation_plan_id"] == SEALED_TEST_PLAN_ID
        assert entry["evaluation_plan_version"] == "v1"
        assert entry["ecology"] == DISTRIBUTION_ECOLOGY.label
        assert len(entry["seed_material_digest"]) == 64
        assert len(entry["generator_bundle_digest"]) == 64
        assert len(entry["ledger_digest"]) == 64
    # The generator commitment is a per-family fact: identical for every sealed
    # world and different from any public family's commitment.
    assert {entry["process_family_digest"] for entry in mechanism} == {commitment}
    assert commitment != _trusted_registry().commitment(GJR_FACTOR_T_V1)


def test_two_private_families_commit_to_different_generators() -> None:
    registry = _trusted_registry()
    registry.register(CanaryDefinition())
    assert registry.commitment(PRIVATE_FAMILY_ID) != registry.commitment(CANARY_FAMILY_ID)
    # Registering a private family never widens the public list.
    assert registry.public_family_ids() == (
        "gjr_factor_t_v1",
        "markov_regime_jump_factor_t_v1",
        "stochastic_vol_factor_t_v1",
    )


# --- the agent boundary ------------------------------------------------------------


class RecordingPort:
    """Records every observation the session hands to the model boundary."""

    def __init__(self, inner: StrategyDecisionPort, sink: list[dict[str, Any]]) -> None:
        self.name = "recording"
        self.inner = inner
        self.sink = sink

    def decide(self, observation: dict[str, Any]) -> dict[str, Any]:
        self.sink.append(dict(observation))
        return self.inner.decide(observation)

    def close(self) -> None:
        return None


def _sealed_canary_run() -> tuple[BenchmarkReport, list[dict[str, Any]]]:
    """Run a canary-laden sealed campaign and collect what the agent saw."""

    observations: list[dict[str, Any]] = []
    registry = _trusted_registry()
    registry.register(CanaryDefinition())
    plan = EvaluationPlan(
        plan_id=CANARY_PLAN_ID,
        version="v1",
        description=CANARY_METADATA,
        worlds=(
            Template(
                family_id=FAMILIAR_FAMILY.value,
                ecology_id=FAMILIAR_ECOLOGY.label,
                split=DatasetSplit.PUBLIC_EVAL,
                partition=EvaluationPartition.FAMILIAR,
            ),
            Template(
                family_id=CANARY_FAMILY_ID,
                ecology_id=DISTRIBUTION_ECOLOGY.label,
                split=DatasetSplit.SEALED_EVAL,
                partition=EvaluationPartition.MECHANISM,
            ),
        ),
    )
    report = run_benchmark(
        kind=TaskKind.EXECUTION,
        port_factory=lambda _index: RecordingPort(twap_port(slice_quantity=500), observations),
        worlds=2,
        security_count=2,
        days=1,
        steps_per_day=4,
        base_seed=77,
        target_quantity=2_000,
        plan=plan,
        registry=registry,
    )
    return report, observations


def test_the_agent_receives_only_the_public_observation_protocol() -> None:
    _report, observations = _sealed_canary_run()
    assert observations
    for observation in observations:
        # No field is added, renamed, or re-typed for a sealed world: the interface
        # is the published protocol schema and nothing else.
        assert set(observation) == PUBLIC_OBSERVATION_FIELDS
        assert observation["schema_version"] == "2.0"
    for forbidden in (
        "family_id",
        "process_family",
        "evaluation_plan",
        "partition",
        "split",
        "registry",
        "mechanism",
        "latent",
        "generator",
        "ecology",
        "seed",
    ):
        assert forbidden not in PUBLIC_OBSERVATION_FIELDS


def test_no_evaluator_private_canary_reaches_the_agent() -> None:
    report, observations = _sealed_canary_run()
    payload = json.dumps(observations, sort_keys=True)
    for canary in CANARIES:
        assert canary not in payload
    # The private family really did run, so the absences above are not vacuous.
    assert any(entry["process_family"] == CANARY_FAMILY_ID for entry in report.replay_package["worlds"])


def test_the_canary_metadata_is_really_attached_to_the_generator() -> None:
    """The canary sweep is not vacuous: the metadata lives *on* the generator.

    Anything that dumped a family instance, its class name, or its configuration
    into agent-visible data would carry the canary, which is why the observations
    above are searched for it.
    """

    registry = _trusted_registry()
    registry.register(CanaryDefinition())
    node = registry.build(CANARY_FAMILY_ID, role=ProcessNodeRole.MARKET, volatility_scale=1.0)
    assert isinstance(node, SuperSecretEvaluatorFamilyCanary9328FactorT)
    assert node.provenance_note == CANARY_METADATA
    assert node.name == CANARY_FAMILY_ID
    assert CANARY_CLASS_TOKEN in type(node).__qualname__
    # The commitment is computed over that configuration, so the evaluator can
    # prove which generator produced a sealed world without publishing it.
    assert len(registry.commitment(CANARY_FAMILY_ID)) == 64
    assert registry.commitment(CANARY_FAMILY_ID) != _trusted_registry().commitment(PRIVATE_FAMILY_ID)


def test_no_evaluator_private_canary_reaches_the_public_result() -> None:
    report, _observations = _sealed_canary_run()
    text = report.render()
    for canary in CANARIES:
        assert canary not in text
    # The family is redacted from the public view while the evaluator record keeps
    # the real identifier the provenance test above relies on.
    assert report.mechanism_families == (EVALUATOR_PRIVATE_FAMILY,)
    assert CANARY_PLAN_ID not in text
    assert report.replay_package["worlds"][1]["process_family"] == CANARY_FAMILY_ID


def test_every_observation_is_json_for_the_http_boundary() -> None:
    report, observations = _sealed_canary_run()
    assert observations
    for observation in observations:
        assert json.loads(json.dumps(observation)) == observation
    assert report.valid_worlds == report.evaluation_worlds


# --- split classification and the M10.7 export gate --------------------------------


def test_a_sealed_world_is_classified_sealed_and_is_not_trainable() -> None:
    worlds = plan_worlds(plan=_sealed_plan(), registry=_trusted_registry(), count=6, base_seed=99)
    for world in worlds:
        expected = (
            DatasetSplit.SEALED_EVAL
            if world.partition is EvaluationPartition.MECHANISM
            else DatasetSplit.PUBLIC_EVAL
        )
        assert world.split is expected
        assert world.trainable is False
        assert is_trainable(world.split) is False
    sealed = [world for world in worlds if world.sealed]
    assert len(sealed) == 2
    assert all(world.family_visibility is FamilyVisibility.EVALUATOR_PRIVATE for world in sealed)


def test_a_public_family_cannot_be_tagged_sealed_eval() -> None:
    """Since M10.7 the split is declared, so this combination is a plan error."""

    from app.benchmark.plan import InvalidPlanSplitError

    plan = _sealed_plan(family_id=FAMILIAR_FAMILY.value)
    with pytest.raises(InvalidPlanSplitError) as excinfo:
        plan_worlds(plan=plan, registry=default_process_registry(), count=3, base_seed=99)
    assert "SEALED_EVAL" in str(excinfo.value)
    assert "public" in str(excinfo.value)


def test_the_whole_plan_is_split_validated_not_just_a_prefix() -> None:
    """A private family tagged TRAINABLE fails even when it is not in world 0."""

    from app.benchmark.plan import InvalidPlanSplitError

    plan = EvaluationPlan(
        plan_id="late_private_training_plan_v1",
        version="v1",
        worlds=(
            Template(
                family_id=FAMILIAR_FAMILY.value,
                ecology_id=FAMILIAR_ECOLOGY.label,
                split=DatasetSplit.TRAINABLE,
                partition=EvaluationPartition.TRAINING,
            ),
            Template(
                family_id=PRIVATE_FAMILY_ID,
                ecology_id=FAMILIAR_ECOLOGY.label,
                split=DatasetSplit.TRAINABLE,
                partition=EvaluationPartition.TRAINING,
            ),
        ),
    )
    # Even a one-world request must fail: the plan is a whole-campaign statement.
    with pytest.raises(InvalidPlanSplitError):
        plan_worlds(plan=plan, registry=_trusted_registry(), count=1, base_seed=99)


def test_trainable_worlds_cannot_run_evaluation_partitions() -> None:
    from app.benchmark.plan import InvalidPlanSplitError

    for partition in (
        EvaluationPartition.FAMILIAR,
        EvaluationPartition.DISTRIBUTION,
        EvaluationPartition.MECHANISM,
    ):
        plan = EvaluationPlan(
            plan_id="mispartitioned_training_plan_v1",
            version="v1",
            worlds=(
                Template(
                    family_id=FAMILIAR_FAMILY.value,
                    ecology_id=FAMILIAR_ECOLOGY.label,
                    split=DatasetSplit.TRAINABLE,
                    partition=partition,
                ),
            ),
        )
        with pytest.raises(InvalidPlanSplitError) as excinfo:
            plan_worlds(plan=plan, registry=default_process_registry(), count=1, base_seed=1)
        assert "TRAINING partition" in str(excinfo.value)


def test_a_public_family_cannot_be_tagged_trainable_on_an_evaluation_partition() -> None:
    """A private family tagged PUBLIC_EVAL fails the same way."""

    from app.benchmark.plan import InvalidPlanSplitError

    plan = EvaluationPlan(
        plan_id="private_public_eval_plan_v1",
        version="v1",
        worlds=(
            Template(
                family_id=PRIVATE_FAMILY_ID,
                ecology_id=DISTRIBUTION_ECOLOGY.label,
                split=DatasetSplit.PUBLIC_EVAL,
                partition=EvaluationPartition.MECHANISM,
            ),
        ),
    )
    with pytest.raises(InvalidPlanSplitError):
        plan_worlds(plan=plan, registry=_trusted_registry(), count=1, base_seed=1)


def test_the_export_gate_admits_only_trainable_worlds() -> None:
    require_trainable((DatasetSplit.TRAINABLE,))
    assert is_trainable(DatasetSplit.TRAINABLE) is True
    assert is_trainable(DatasetSplit.PUBLIC_EVAL) is False
    assert is_trainable(DatasetSplit.SEALED_EVAL) is False
    with pytest.raises(SealedEvaluationExportError) as excinfo:
        require_trainable((DatasetSplit.TRAINABLE, DatasetSplit.SEALED_EVAL))
    assert excinfo.value.code == SEALED_PLAN_EXPORT_ERROR
    assert SEALED_PLAN_EXPORT_ERROR in str(excinfo.value)


def test_the_export_gate_refuses_a_campaign_that_contains_sealed_worlds() -> None:
    report = _sealed_run()
    worlds = plan_worlds(plan=_sealed_plan(), registry=_trusted_registry(), count=6, base_seed=99)
    with pytest.raises(SealedEvaluationExportError):
        require_trainable(tuple(world.split for world in worlds))
    # The committed campaign says so too: a downstream exporter reads the split
    # from the replay record, not from a re-derived guess.
    assert {entry["split"] for entry in report.replay_package["worlds"]} == {
        DatasetSplit.PUBLIC_EVAL.value,
        DatasetSplit.SEALED_EVAL.value,
    }


# --- plans and ecologies ----------------------------------------------------------


def test_a_template_weight_repeats_a_world_in_the_plan_cycle() -> None:
    plan = EvaluationPlan(
        plan_id="weighted_test_plan_v1",
        version="v1",
        worlds=(
            Template(
                family_id=FAMILIAR_FAMILY.value,
                ecology_id=FAMILIAR_ECOLOGY.label,
                split=DatasetSplit.PUBLIC_EVAL,
                partition=EvaluationPartition.FAMILIAR,
            ),
            Template(
                family_id=FAMILIAR_FAMILY.value,
                ecology_id=DISTRIBUTION_ECOLOGY.label,
                split=DatasetSplit.PUBLIC_EVAL,
                partition=EvaluationPartition.MECHANISM,
                weight=3,
            ),
        ),
    )
    assert [template.partition for template in plan.cycle()] == [
        EvaluationPartition.FAMILIAR,
        EvaluationPartition.MECHANISM,
        EvaluationPartition.MECHANISM,
        EvaluationPartition.MECHANISM,
    ]
    assert plan.template_for(4).partition is EvaluationPartition.FAMILIAR


def test_a_template_rejects_a_non_positive_weight() -> None:
    with pytest.raises(ValueError):
        Template(
            family_id=FAMILIAR_FAMILY.value,
            ecology_id=FAMILIAR_ECOLOGY.label,
            split=DatasetSplit.PUBLIC_EVAL,
            partition=EvaluationPartition.FAMILIAR,
            weight=0,
        )


def test_a_plan_needs_a_template_an_identifier_and_a_version() -> None:
    template = Template(
        family_id=FAMILIAR_FAMILY.value,
        ecology_id=FAMILIAR_ECOLOGY.label,
        split=DatasetSplit.PUBLIC_EVAL,
        partition=EvaluationPartition.FAMILIAR,
    )
    with pytest.raises(ValueError):
        EvaluationPlan(plan_id="empty_v1", version="v1", worlds=())
    with pytest.raises(ValueError):
        EvaluationPlan(plan_id="", version="v1", worlds=(template,))
    with pytest.raises(ValueError):
        EvaluationPlan(plan_id="unversioned_v1", version="", worlds=(template,))


def test_an_unknown_ecology_is_rejected_when_the_plan_is_resolved() -> None:
    plan = EvaluationPlan(
        plan_id="unknown_ecology_test_v1",
        version="v1",
        worlds=(
            Template(
                family_id=FAMILIAR_FAMILY.value,
                ecology_id="not-an-ecology",
                split=DatasetSplit.PUBLIC_EVAL,
                partition=EvaluationPartition.FAMILIAR,
            ),
        ),
    )
    with pytest.raises(KeyError) as excinfo:
        plan_worlds(plan=plan, registry=default_process_registry(), count=1, base_seed=1)
    assert "UNKNOWN_ECOLOGY" in str(excinfo.value)
    assert default_ecology_registry().ecology_ids() == (
        DISTRIBUTION_ECOLOGY.label,
        FAMILIAR_ECOLOGY.label,
    )


def test_the_default_plan_is_versioned_and_open() -> None:
    plan = default_evaluation_plan()
    assert plan.plan_id == DEFAULT_PLAN_ID
    assert plan.version
    assert set(plan.family_ids()) <= set(default_process_registry().public_family_ids())
    worlds = plan_worlds(plan=plan, registry=default_process_registry(), count=6, base_seed=1)
    assert {world.split for world in worlds} == {DatasetSplit.PUBLIC_EVAL}
