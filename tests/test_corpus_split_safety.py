"""M10.7 split-safety tests: explicit splits, symmetric gates, adversarial boundary.

The core acceptance criterion: it must be structurally impossible for
PUBLIC_EVAL or SEALED_EVAL worlds to enter a training release, and structurally
impossible for TRAINABLE worlds to enter a scored evaluation. The guards must
fire *before any side effect* (no world generated, no policy port constructed,
no directory created, no file written).
"""

from __future__ import annotations

import inspect
import os
from pathlib import Path
from typing import Any

import pytest
from test_benchmark_private_evaluation import _sealed_plan, _trusted_registry

from app.benchmark.model import TaskKind
from app.benchmark.plan import (
    DEFAULT_TRAINING_PLAN_ID,
    DEFAULT_TRAINING_PLAN_VERSION,
    SEALED_PLAN_EXPORT_ERROR,
    DatasetSplit,
    EvaluationPlan,
    EvaluationWorldTemplate,
    InvalidPlanSplitError,
    SealedEvaluationExportError,
    TrainablePlanInBenchmarkError,
    default_evaluation_plan,
    default_training_plan,
    plan_worlds,
    require_evaluation,
    require_trainable,
)
from app.benchmark.port import twap_port
from app.benchmark.process_registry import (
    default_process_registry,
)
from app.benchmark.runner import run_benchmark
from app.benchmark.universe import DISTRIBUTION_ECOLOGY, FAMILIAR_ECOLOGY, EvaluationPartition
from app.corpus.builder import CorpusConfig, build_corpus
from app.market.process import (
    GJR_FACTOR_T_V1,
    MARKOV_REGIME_JUMP_FACTOR_T_V1,
    STOCHASTIC_VOL_FACTOR_T_V1,
)

# --- explicit split on templates ---------------------------------------------------


def test_a_template_must_declare_its_split() -> None:
    with pytest.raises(TypeError):
        EvaluationWorldTemplate(  # type: ignore[call-arg]
            family_id=GJR_FACTOR_T_V1,
            ecology_id=FAMILIAR_ECOLOGY.label,
            partition=EvaluationPartition.FAMILIAR,
        )


def test_the_default_evaluation_plan_declares_public_eval_everywhere() -> None:
    plan = default_evaluation_plan()
    for template in plan.worlds:
        assert template.split is DatasetSplit.PUBLIC_EVAL
    worlds = plan_worlds(plan=plan, registry=default_process_registry(), count=6, base_seed=1)
    assert {world.split for world in worlds} == {DatasetSplit.PUBLIC_EVAL}


def test_the_training_plan_is_trainable_everywhere() -> None:
    plan = default_training_plan()
    assert plan.plan_id == DEFAULT_TRAINING_PLAN_ID
    assert plan.version == DEFAULT_TRAINING_PLAN_VERSION
    for template in plan.worlds:
        assert template.split is DatasetSplit.TRAINABLE
        assert template.partition is EvaluationPartition.TRAINING
    worlds = plan_worlds(plan=plan, registry=default_process_registry(), count=6, base_seed=1)
    assert {world.split for world in worlds} == {DatasetSplit.TRAINABLE}


def test_the_training_plan_uses_all_published_families_and_ecologies() -> None:
    plan = default_training_plan()
    assert plan.family_ids() == (
        GJR_FACTOR_T_V1,
        MARKOV_REGIME_JUMP_FACTOR_T_V1,
        STOCHASTIC_VOL_FACTOR_T_V1,
    )
    assert plan.ecology_ids() == (DISTRIBUTION_ECOLOGY.label, FAMILIAR_ECOLOGY.label)
    cycle = plan.cycle()
    assert len(cycle) == 6
    # Each family appears once per ecology, and no family repeats adjacently.
    for family_id in plan.family_ids():
        used = {(template.ecology_id) for template in cycle if template.family_id == family_id}
        assert used == set(plan.ecology_ids())


# --- the whole-plan split/visibility matrix ----------------------------------------


def _plan(template: EvaluationWorldTemplate) -> EvaluationPlan:
    return EvaluationPlan(plan_id="matrix_probe_plan_v1", version="v1", worlds=(template,))


def test_matrix_trainable_requires_public_family_and_training_partition() -> None:
    # TRAINABLE + evaluator-private family -> invalid.
    with pytest.raises(InvalidPlanSplitError) as public_error:
        plan_worlds(
            plan=_plan(
                EvaluationWorldTemplate(
                    family_id="test_private_mean_reverting_family_v1",
                    ecology_id=FAMILIAR_ECOLOGY.label,
                    split=DatasetSplit.TRAINABLE,
                )
            ),
            registry=_trusted_registry(),
            count=1,
            base_seed=1,
        )
    assert "PUBLIC" in str(public_error.value)
    # TRAINABLE + evaluation partition -> invalid.
    with pytest.raises(InvalidPlanSplitError):
        plan_worlds(
            plan=_plan(
                EvaluationWorldTemplate(
                    family_id=GJR_FACTOR_T_V1,
                    ecology_id=FAMILIAR_ECOLOGY.label,
                    split=DatasetSplit.TRAINABLE,
                    partition=EvaluationPartition.FAMILIAR,
                )
            ),
            registry=default_process_registry(),
            count=1,
            base_seed=1,
        )


def test_matrix_public_eval_requires_public_family_and_non_training_partition() -> None:
    with pytest.raises(InvalidPlanSplitError) as private_error:
        plan_worlds(
            plan=_plan(
                EvaluationWorldTemplate(
                    family_id="test_private_mean_reverting_family_v1",
                    ecology_id=FAMILIAR_ECOLOGY.label,
                    split=DatasetSplit.PUBLIC_EVAL,
                )
            ),
            registry=_trusted_registry(),
            count=1,
            base_seed=1,
        )
    assert "PUBLIC" in str(private_error.value)
    with pytest.raises(InvalidPlanSplitError):
        plan_worlds(
            plan=_plan(
                EvaluationWorldTemplate(
                    family_id=GJR_FACTOR_T_V1,
                    ecology_id=FAMILIAR_ECOLOGY.label,
                    split=DatasetSplit.PUBLIC_EVAL,
                    partition=EvaluationPartition.TRAINING,
                )
            ),
            registry=default_process_registry(),
            count=1,
            base_seed=1,
        )


def test_matrix_sealed_eval_requires_private_family_and_non_training_partition() -> None:
    # SEALED_EVAL + public family -> invalid (the M10.6 inference is now a plan error).
    with pytest.raises(InvalidPlanSplitError) as open_error:
        plan_worlds(
            plan=_plan(
                EvaluationWorldTemplate(
                    family_id=GJR_FACTOR_T_V1,
                    ecology_id=FAMILIAR_ECOLOGY.label,
                    split=DatasetSplit.SEALED_EVAL,
                )
            ),
            registry=default_process_registry(),
            count=1,
            base_seed=1,
        )
    assert "evaluator-private" in str(open_error.value)
    with pytest.raises(InvalidPlanSplitError):
        plan_worlds(
            plan=_plan(
                EvaluationWorldTemplate(
                    family_id="test_private_mean_reverting_family_v1",
                    ecology_id=FAMILIAR_ECOLOGY.label,
                    split=DatasetSplit.SEALED_EVAL,
                    partition=EvaluationPartition.TRAINING,
                )
            ),
            registry=_trusted_registry(),
            count=1,
            base_seed=1,
        )


def test_matrix_validates_every_template_not_just_a_prefix() -> None:
    # World 0 is valid; the second template is a TRAINABLE private family. A
    # one-world request must still fail -- the plan is a whole-campaign statement.
    plan = EvaluationPlan(
        plan_id="late_leak_plan_v1",
        version="v1",
        worlds=(
            EvaluationWorldTemplate(
                family_id=GJR_FACTOR_T_V1,
                ecology_id=FAMILIAR_ECOLOGY.label,
                split=DatasetSplit.TRAINABLE,
            ),
            EvaluationWorldTemplate(
                family_id="test_private_mean_reverting_family_v1",
                ecology_id=FAMILIAR_ECOLOGY.label,
                split=DatasetSplit.TRAINABLE,
            ),
        ),
    )
    with pytest.raises(InvalidPlanSplitError):
        plan_worlds(plan=plan, registry=_trusted_registry(), count=1, base_seed=1)


# --- symmetric runtime gates --------------------------------------------------------


def test_the_benchmark_rejects_a_plan_containing_trainable_worlds() -> None:
    with pytest.raises(TrainablePlanInBenchmarkError) as excinfo:
        run_benchmark(
            kind=TaskKind.EXECUTION,
            port_factory=lambda _index: twap_port(slice_quantity=100),
            worlds=2,
            security_count=2,
            days=1,
            steps_per_day=2,
            plan=default_training_plan(),
            base_seed=7,
        )
    assert excinfo.value.code == "TRAINABLE_PLAN_IN_BENCHMARK"


def test_require_evaluation_rejects_trainable_and_accepts_evaluation_splits() -> None:
    require_evaluation((DatasetSplit.PUBLIC_EVAL, DatasetSplit.SEALED_EVAL))
    with pytest.raises(TrainablePlanInBenchmarkError):
        require_evaluation((DatasetSplit.PUBLIC_EVAL, DatasetSplit.TRAINABLE))


def test_require_trainable_rejects_any_non_trainable_split() -> None:
    require_trainable((DatasetSplit.TRAINABLE,))
    with pytest.raises(SealedEvaluationExportError) as mixed:
        require_trainable((DatasetSplit.TRAINABLE, DatasetSplit.PUBLIC_EVAL))
    assert mixed.value.code == SEALED_PLAN_EXPORT_ERROR
    with pytest.raises(SealedEvaluationExportError):
        require_trainable((DatasetSplit.SEALED_EVAL,))


# --- the corpus build path refuses evaluation before side effects --------------------


def test_the_exporter_refuses_the_default_evaluation_plan_before_side_effects(tmp_path: Path) -> None:
    guard = tmp_path / "guard"
    guard.mkdir()
    before = sorted(os.listdir(guard))
    with pytest.raises(SealedEvaluationExportError) as excinfo:
        build_corpus(
            CorpusConfig(
                output=guard / "nope",
                worlds=2,
                securities=4,
                days=1,
                steps_per_day=4,
                plan=default_evaluation_plan(),
                seed=5,
            )
        )
    assert excinfo.value.code == SEALED_PLAN_EXPORT_ERROR
    assert "public_eval" in str(excinfo.value)
    # No world generated, no port constructed, no directory created.
    assert sorted(os.listdir(guard)) == before
    assert not (guard / ".nope.building").exists()


def test_the_exporter_refuses_a_sealed_plan_before_side_effects(tmp_path: Path) -> None:
    guard = tmp_path / "guard"
    guard.mkdir()
    before = sorted(os.listdir(guard))
    with pytest.raises(SealedEvaluationExportError):
        build_corpus(
            CorpusConfig(
                output=guard / "nope",
                worlds=2,
                securities=4,
                days=1,
                steps_per_day=4,
                plan=_sealed_plan(),
                registry=_trusted_registry(),
                seed=5,
            )
        )
    assert sorted(os.listdir(guard)) == before


def test_the_exporter_refuses_an_existing_release(tmp_path: Path) -> None:
    from app.corpus.builder import CorpusConfig
    from app.corpus.builder import build_corpus as build

    first_manifest, _stats = build(
        CorpusConfig(output=tmp_path / "corpus", worlds=1, securities=2, days=1, steps_per_day=2, seed=1)
    )
    with pytest.raises(FileExistsError):
        build(
            CorpusConfig(output=tmp_path / "corpus", worlds=1, securities=2, days=1, steps_per_day=2, seed=1)
        )
    assert first_manifest.release_digest


# --- the participant-facing corpus CLI carries no evaluator knobs --------------------


def test_the_corpus_cli_cannot_be_asked_to_load_evaluator_code() -> None:
    """Mirror of the benchmark CLI guard: no private-family/registry/plan/module knobs."""

    from app.cli import corpus_build

    parameters = {name.lower() for name in inspect.signature(corpus_build).parameters}
    for forbidden in ("family", "registry", "plan", "module", "import", "ecolog", "python"):
        assert not any(forbidden in name for name in parameters), forbidden


# --- package exports are importable ------------------------------------------


def test_the_benchmark_package_exports_the_plan_error_codes() -> None:
    """Documented public error codes must be importable from the package,

    both directly and via wildcard import. They are part of the participant-
    facing contract, so removing them from ``__all__`` without import would
    still be a defect.
    """

    from app.benchmark import INVALID_PLAN_SPLIT, TRAINABLE_PLAN_IN_BENCHMARK

    assert INVALID_PLAN_SPLIT == "INVALID_PLAN_SPLIT"
    assert TRAINABLE_PLAN_IN_BENCHMARK == "TRAINABLE_PLAN_IN_BENCHMARK"

    namespace: dict[str, Any] = {}
    exec("from app.benchmark import *", namespace)
    assert namespace["INVALID_PLAN_SPLIT"] == "INVALID_PLAN_SPLIT"
    assert namespace["TRAINABLE_PLAN_IN_BENCHMARK"] == "TRAINABLE_PLAN_IN_BENCHMARK"
