from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, cast

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from app.analyst import analyze_failure
from app.arena import (
    ARENA_SCHEMA_VERSION,
    ChallengeGeneration,
    ChallengeSpec,
    build_challenge,
    deterministic_generation,
    evaluate_submission,
    example_submission,
    generate_challenge_content,
    generate_feedback,
    instructor_dataset,
    public_challenge,
    public_dataset,
    validate_submission_csv,
)
from app.calibration import build_demo_calibration_pack, calibrate_bootstrap
from app.compiler import compile_world
from app.experiments import run_batch, run_single, run_validation_campaign
from app.product import (
    DEFAULT_PROPERTIES,
    STORE,
    STRATEGIES,
    evaluate,
    export_fixture,
    run_search,
    scenario_hash,
    stable_id,
)
from app.schemas import WorldSpec

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_ROOT = Path(os.getenv("MARKET_FUZZER_EXPERIMENT_ROOT", "artifacts")).expanduser().resolve()
app = FastAPI(title="Quant Challenge Arena", version="0.3.0")
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
JOBS: dict[str, dict] = {}
PRODUCT_PROJECTS: dict[str, dict] = {}
PRODUCT_FAILURES: dict[str, dict] = {}


def _seed_arena_challenge() -> ChallengeSpec:
    challenge = build_challenge()
    return challenge.model_copy(update={"approved_at": challenge.created_at})


ARENA_CHALLENGES: dict[str, ChallengeSpec] = {"momentum-regime-reversal": _seed_arena_challenge()}
ARENA_SUBMISSIONS: dict[str, dict[str, Any]] = {}


class CompileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=3, max_length=2_000)
    seed: int = Field(default=42, ge=0, le=2_147_483_647)
    mode: str = Field(default="offline", pattern="^(offline|gpt)$")


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spec: WorldSpec


class CampaignRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spec: WorldSpec
    mode: str = Field(default="quick", pattern="^(quick|audit)$")


class ProductRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy_id: str = "pov_fragile"
    parameters: dict = Field(default_factory=dict, max_length=32)
    properties: list[dict] = Field(default_factory=lambda: DEFAULT_PROPERTIES.copy(), max_length=16)
    scenario: dict = Field(default_factory=dict, max_length=32)
    mode: str = Field(default="quick", pattern="^(quick|deep)$")


class FailureAnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str | None = Field(default=None, max_length=120, pattern=r"^gpt-[A-Za-z0-9._-]{1,100}$")


class ArenaChallengeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    challenge_id: str = Field(default="arena-challenge", pattern=r"^[a-z0-9][a-z0-9_-]{2,80}$")
    prompt: str = Field(
        default="Create an advanced synthetic quant challenge.", min_length=3, max_length=2_000
    )
    title: str | None = Field(default=None, max_length=160)
    course_level: str = Field(default="advanced undergraduate / MFE", min_length=3, max_length=120)
    learning_objective: str | None = Field(default=None, max_length=500)
    dataset_seed: int = Field(default=20260715, ge=0, le=2_147_483_647)
    mode: str = Field(default="offline", pattern="^(offline|gpt)$")


class ArenaSubmissionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    student_name: str = Field(default="Anonymous student", min_length=1, max_length=120)
    explanation: str = Field(default="", max_length=4_000)
    csv_text: str = Field(min_length=1, max_length=1_000_000)


class ArenaFeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str | None = Field(default=None, max_length=120, pattern=r"^gpt-[A-Za-z0-9._-]{1,100}$")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(ROOT / "static" / "arena.html")


@app.get("/market-fuzzer")
def legacy_market_fuzzer() -> FileResponse:
    """Keep the verified Market Fuzzer milestone available as a secondary tool."""
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "product": "Quant Challenge Arena",
        "secondary_product": "Market Fuzzer",
        # Preserve the legacy health contract for existing Market Fuzzer clients.
        "engine": "compact_deterministic_pov_harness",
        "arena_engine": "deterministic_synthetic_regime_engine",
        "arena_schema_version": ARENA_SCHEMA_VERSION,
    }


def product_strategy(request: ProductRunRequest) -> dict:
    if request.strategy_id not in STRATEGIES:
        raise HTTPException(422, "unknown built-in strategy")
    base: Any = STRATEGIES[request.strategy_id]
    return {
        "id": request.strategy_id,
        **base,
        "parameters": {**base["defaults"], **request.parameters},
    }


@app.get("/api/strategies")
def strategies() -> dict:
    return {"strategies": [{"id": key, **value} for key, value in STRATEGIES.items()]}


@app.post("/api/projects")
def create_project(payload: dict) -> dict:
    project_id = stable_id("project", {"payload": payload, "count": len(PRODUCT_PROJECTS)})
    project = {
        "id": project_id,
        "name": payload.get("name", "Untitled safety test"),
        "strategy_id": payload.get("strategy_id", "pov_fragile"),
    }
    PRODUCT_PROJECTS[project_id] = project
    return project


@app.get("/api/projects/{project_id}")
def get_project(project_id: str) -> dict:
    if project_id not in PRODUCT_PROJECTS:
        raise HTTPException(404, "project not found")
    return PRODUCT_PROJECTS[project_id]


@app.post("/api/baselines")
def baseline(request: ProductRunRequest) -> dict:
    strategy = product_strategy(request)
    result = evaluate(
        strategy,
        {
            "liquidity": 1,
            "volatility": 1,
            "latency_ms": strategy["parameters"].get("latency_ms", 10),
            "forced_seller": 0,
            "spread": 1,
            "replenishment": 1,
        },
        request.properties,
        42,
    )
    return {
        "id": stable_id("baseline", result),
        "strategy": strategy,
        **result,
        "warning": "A pass validates this bounded synthetic test only; it is not a live-trading or capacity claim.",
    }


@app.post("/api/searches")
def search(request: ProductRunRequest) -> dict:
    strategy = product_strategy(request)
    result = run_search(strategy, request.properties, request.mode)
    if result.get("found"):
        result["strategy"] = strategy
        result["properties"] = request.properties
        PRODUCT_FAILURES[result["id"]] = result
    return result


@app.get("/api/searches/{search_id}")
def search_status(search_id: str) -> dict:
    if search_id not in PRODUCT_FAILURES:
        raise HTTPException(404, "search not found")
    return PRODUCT_FAILURES[search_id]


@app.post("/api/searches/{search_id}/cancel")
def cancel_search(search_id: str) -> dict:
    return {"id": search_id, "status": "cancelled"}


@app.get("/api/failures/{failure_id}")
def failure(failure_id: str) -> dict:
    if failure_id not in PRODUCT_FAILURES:
        raise HTTPException(404, "failure not found")
    return PRODUCT_FAILURES[failure_id]


@app.post("/api/failures/{failure_id}/minimize")
def minimize(failure_id: str) -> dict:
    return failure(failure_id)


@app.get("/api/failures/{failure_id}/replay")
def replay(failure_id: str) -> dict:
    value = failure(failure_id)
    fragile = {
        "id": "pov_fragile",
        **STRATEGIES["pov_fragile"],
        "parameters": STRATEGIES["pov_fragile"]["defaults"],
    }
    fragile_parameters = cast(dict[str, Any], STRATEGIES["pov_fragile"]["defaults"])
    corrected = {
        "id": "pov",
        **STRATEGIES["pov"],
        "parameters": dict(fragile_parameters),
    }
    seed = value["reproduction"]["seeds_tested"][0]
    corrected_result = evaluate(corrected, value["minimized"], value["properties"], seed)
    return {
        "failure_id": failure_id,
        "scenario_hash": value["scenario_hash"],
        "baseline": evaluate(
            fragile,
            {"liquidity": 1, "volatility": 1, "latency_ms": 10, "forced_seller": 0, "spread": 1},
            value["properties"],
            42,
        )["timeline"],
        "failure": value["runs"][0]["timeline"],
        "corrected": corrected_result["timeline"],
        "annotation": "Forced flow reaches a thin book; stale participation accounting violates the cap.",
    }


@app.post("/api/failures/{failure_id}/analysis")
def failure_analysis(failure_id: str, request: FailureAnalysisRequest) -> dict:
    value = failure(failure_id)
    fragile_parameters = cast(dict[str, Any], STRATEGIES["pov_fragile"]["defaults"])
    corrected = {
        "id": "pov",
        **STRATEGIES["pov"],
        "parameters": dict(fragile_parameters),
    }
    evidence_value = {
        **value,
        "corrected_runs": [
            evaluate(corrected, value["minimized"], value["properties"], seed)
            for seed in value["reproduction"]["seeds_tested"]
        ],
    }
    try:
        return analyze_failure(evidence_value, model=request.model)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/comparisons")
def comparison(request: ProductRunRequest) -> dict:
    strategy: dict[str, Any] = product_strategy(request)
    failure_key = request.scenario.get("failure_id")
    original = PRODUCT_FAILURES.get(str(failure_key), {}) if failure_key is not None else {}
    scenario = original.get("minimized", request.scenario)
    seeds = original.get("reproduction", {}).get("seeds_tested", [42])
    fragile = {
        "id": "pov_fragile",
        **STRATEGIES["pov_fragile"],
        "parameters": STRATEGIES["pov_fragile"]["defaults"],
    }
    # The corrected implementation receives the identical parent-order configuration;
    # only its strategy logic changes.
    fragile_parameters = cast(dict[str, Any], STRATEGIES["pov_fragile"]["defaults"])
    comparison_strategy: dict[str, Any] = {**strategy, "parameters": dict(fragile_parameters)}
    old_runs = [evaluate(fragile, scenario, request.properties, seed) for seed in seeds]
    new_runs = [evaluate(comparison_strategy, scenario, request.properties, seed) for seed in seeds]
    scenario_id = scenario_hash(scenario)
    return {
        "scenario": scenario,
        "scenario_hash": scenario_id,
        "seeds": seeds,
        "original": old_runs[0],
        "modified": new_runs[0],
        "original_runs": old_runs,
        "modified_runs": new_runs,
        "same_scenario_and_seeds": True,
        "same_properties": True,
        "same_parent_order": fragile_parameters == comparison_strategy["parameters"],
        "comparison_contract": {
            "scenario_hash": scenario_id,
            "seed_list": seeds,
            "safety_properties": request.properties,
            "original_strategy": {
                "id": fragile["id"],
                "version": fragile["version"],
                "parameters": fragile["parameters"],
            },
            "modified_strategy": {
                "id": comparison_strategy["id"],
                "version": comparison_strategy["version"],
                "parameters": comparison_strategy["parameters"],
            },
        },
    }


@app.post("/api/regression-fixtures")
def regression_fixture(payload: dict) -> dict:
    failure_id = payload.get("failure_id")
    if failure_id not in PRODUCT_FAILURES:
        raise HTTPException(404, "failure not found")
    value = PRODUCT_FAILURES[failure_id]
    return export_fixture(value, value["strategy"], value["properties"])


@app.get("/api/regression-fixtures")
def regression_fixtures() -> dict:
    return {"fixtures": [str(path) for path in STORE.glob("*.yaml")]}


@app.post("/api/regression-suites/run")
def regression_suite() -> dict:
    from app.cli import _run_fixture_data

    rows = []
    for path in sorted(STORE.glob("*.yaml")):
        try:
            rows.append(_run_fixture_data(path))
        except Exception as exc:
            rows.append({"path": str(path), "result": "invalid", "error": str(exc)})
    invalid = sum(x.get("result") == "invalid" for x in rows)
    failing = sum(x.get("result") != "invalid" and x.get("matches_expected_outcome") is False for x in rows)
    return {
        "total": len(rows),
        "passing": sum(x.get("matches_expected_outcome") is True for x in rows),
        "failing": failing,
        "invalid": invalid,
        "newly_failing": 0,
        "fixed": 0,
        "status": "complete" if invalid == 0 else "complete_with_invalid_fixtures",
        "fixtures": rows,
    }


@app.get("/api/model-quality")
def model_quality() -> dict:
    return {
        "mechanical_validity": "supported by deterministic exchange tests",
        "calibration_stability": "demo calibration evidence available",
        "permitted_claim": "software regression within configured synthetic bounds",
        "blocked_claims": ["production capacity estimate", "future profitability", "live-trading safety"],
    }


@app.get("/api/schema")
def schema() -> dict:
    return WorldSpec.model_json_schema()


@app.post("/api/compile")
def compile_endpoint(request: CompileRequest) -> dict:
    try:
        result = compile_world(request.prompt, request.seed, request.mode)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(422, str(exc)) from exc
    return {
        "spec": result.spec.model_dump(mode="json"),
        "spec_yaml": result.spec.to_yaml(),
        "spec_hash": result.spec_hash,
        "validation": {"valid": True, "errors": [], "warnings": result.warnings},
        "assumptions": result.assumptions,
        "compiler": {"mode": result.compiler_mode, "model": result.model},
    }


@app.post("/api/validate")
def validate_endpoint(payload: dict) -> dict:
    try:
        spec = WorldSpec.model_validate(payload)
        return {"valid": True, "errors": [], "spec_hash": spec.specification_hash()}
    except Exception as exc:
        return {"valid": False, "errors": [str(exc)], "spec_hash": None}


@app.post("/api/run")
def run_endpoint(request: RunRequest) -> dict:
    return run_single(request.spec).to_dict()


@app.post("/api/batch")
def batch_endpoint(request: RunRequest) -> dict:
    result = run_batch(request.spec, ARTIFACT_ROOT, quick=True)
    value = result.to_dict()
    JOBS[result.experiment_id] = value
    return value


@app.get("/api/calibration/demo")
def demo_calibration() -> dict:
    pack = build_demo_calibration_pack()
    calibration = calibrate_bootstrap(pack, mode="quick")
    return {
        "calibration_pack": {
            "pack": pack.model_dump(mode="json"),
            "calibration": calibration.model_dump(mode="json"),
        }
    }


@app.post("/api/validation-campaign")
def validation_campaign(request: CampaignRequest) -> dict:
    result = run_validation_campaign(request.spec, ARTIFACT_ROOT, request.mode)
    value = result.to_dict()
    JOBS[result.experiment_id] = value
    return value


@app.get("/api/experiments/{experiment_id}")
def experiment_status(experiment_id: str) -> dict:
    if experiment_id in JOBS:
        return {"experiment_id": experiment_id, "status": "complete", "result": JOBS[experiment_id]}
    manifest = ARTIFACT_ROOT / experiment_id / "manifest.json"
    if manifest.exists() and ARTIFACT_ROOT in manifest.resolve().parents:
        return {
            "experiment_id": experiment_id,
            "status": "complete",
            "manifest": json.loads(manifest.read_text()),
        }
    raise HTTPException(404, "experiment not found")


@app.get("/api/results/{experiment_id}")
def results(experiment_id: str) -> dict:
    directory = (ARTIFACT_ROOT / experiment_id).resolve()
    if ARTIFACT_ROOT not in directory.parents or not directory.exists():
        raise HTTPException(404, "experiment not found")
    response = {
        "manifest": json.loads((directory / "manifest.json").read_text()),
        "metrics": json.loads((directory / "metrics.json").read_text()),
        "realism_report": json.loads((directory / "realism_report.json").read_text()),
        "failure_surface": json.loads((directory / "failure_surface.json").read_text()),
    }
    for name in (
        "calibration_pack",
        "intervention_results",
        "simulator_validation_report",
        "synthetic_release_validation_report",
        "synthetic_market_package_manifest",
    ):
        path = directory / f"{name}.json"
        if path.exists():
            response[name] = json.loads(path.read_text())
    return response


@app.get("/api/artifacts/{experiment_id}/{filename}")
def artifact_download(experiment_id: str, filename: str) -> FileResponse:
    if Path(filename).name != filename:
        raise HTTPException(400, "invalid filename")
    path = (ARTIFACT_ROOT / experiment_id / filename).resolve()
    if ARTIFACT_ROOT not in path.parents or not path.is_file():
        raise HTTPException(404, "artifact not found")
    return FileResponse(path, filename=filename)


# ---------------------------------------------------------------------------
# Quant Challenge Arena
# ---------------------------------------------------------------------------


def _arena_role(request: Request) -> str:
    return request.headers.get("X-Role", "student").strip().lower()


def _require_instructor(request: Request) -> None:
    if _arena_role(request) != "instructor":
        raise HTTPException(403, "instructor role is required for this endpoint")


def _arena_challenge(challenge_id: str) -> ChallengeSpec:
    challenge = ARENA_CHALLENGES.get(challenge_id)
    if challenge is None:
        raise HTTPException(404, "challenge not found")
    return challenge


def _challenge_is_released(challenge: ChallengeSpec) -> bool:
    return challenge.release_policy.get("hidden_results") == "released"


def _public_submission_view(record: dict[str, Any], *, released: bool = False) -> dict[str, Any]:
    evaluation = record["evaluation"]
    result = {
        "submission_id": record["submission_id"],
        "student_name": record["student_name"],
        "explanation": record["explanation"],
        "valid": evaluation["valid"],
        "public_metrics": evaluation.get("public_metrics"),
        "public_score": evaluation.get("public_score"),
        "integrity_tests": evaluation.get("integrity_tests", []),
        "validation": {
            "row_count": evaluation.get("validation", {}).get("row_count", 0),
            "warnings": evaluation.get("validation", {}).get("warnings", []),
        },
        "hidden_results": "released" if released else "withheld_until_instructor_release",
    }
    if released:
        result.update(
            {
                "hidden_metrics": evaluation.get("hidden_metrics"),
                "robustness_score": evaluation.get("robustness_score"),
            }
        )
    return result


def _instructor_submission_view(record: dict[str, Any]) -> dict[str, Any]:
    evaluation = record["evaluation"]
    return {
        **_public_submission_view(record, released=True),
        "csv_text": record["csv_text"],
        "evaluation": evaluation,
        "feedback": record.get("feedback"),
    }


def _arena_rankings(challenge_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    records = [record for record in ARENA_SUBMISSIONS.values() if record["challenge_id"] == challenge_id]
    public_order = sorted(
        records, key=lambda item: (-item["evaluation"].get("public_score", -1), item["submission_id"])
    )
    robust_order = sorted(
        records, key=lambda item: (-item["evaluation"].get("robustness_score", -1), item["submission_id"])
    )
    public_ranks = {record["submission_id"]: index for index, record in enumerate(public_order, start=1)}
    robust_ranks = {record["submission_id"]: index for index, record in enumerate(robust_order, start=1)}
    public_rows = [
        {
            "submission_id": record["submission_id"],
            "student_name": record["student_name"],
            "public_score": record["evaluation"].get("public_score"),
            "public_rank": public_ranks[record["submission_id"]],
            "status": "valid" if record["evaluation"].get("valid") else "invalid",
        }
        for record in public_order
    ]
    instructor_rows = [
        {
            **row,
            "robustness_score": record["evaluation"].get("robustness_score"),
            "robustness_rank": robust_ranks[record["submission_id"]],
            "rank_movement": public_ranks[record["submission_id"]] - robust_ranks[record["submission_id"]],
            "hidden_sharpe": record["evaluation"].get("hidden_metrics", {}).get("hidden_sharpe"),
        }
        for row, record in zip(public_rows, public_order, strict=True)
    ]
    instructor_rows.sort(key=lambda item: (item["robustness_rank"], item["submission_id"]))
    return public_rows, instructor_rows


@app.get("/api/arena/challenges")
def arena_challenges() -> dict[str, Any]:
    return {
        "challenges": [
            {
                **public_challenge(challenge),
                "approved": challenge.approved_at is not None,
                "released": _challenge_is_released(challenge),
            }
            for challenge in ARENA_CHALLENGES.values()
        ]
    }


@app.get("/api/arena/challenges/{challenge_id}")
def arena_challenge(challenge_id: str) -> dict[str, Any]:
    challenge = _arena_challenge(challenge_id)
    return {
        **public_challenge(challenge),
        "approved": challenge.approved_at is not None,
        "released": _challenge_is_released(challenge),
    }


@app.get("/api/arena/challenges/{challenge_id}/dataset")
def arena_public_dataset(challenge_id: str) -> dict[str, Any]:
    challenge = _arena_challenge(challenge_id)
    if challenge.approved_at is None:
        raise HTTPException(409, "challenge is not approved")
    return public_dataset(challenge)


@app.post("/api/arena/challenges")
def arena_create_challenge(payload: ArenaChallengeRequest, request: Request) -> dict[str, Any]:
    _require_instructor(request)
    if payload.challenge_id in ARENA_CHALLENGES:
        raise HTTPException(409, "challenge_id already exists")
    try:
        generated_result = (
            generate_challenge_content(payload.prompt, model=None)
            if payload.mode == "gpt"
            else {
                "status": "unavailable",
                "mode": "deterministic_fallback",
                "model": None,
                "content": deterministic_generation().model_dump(mode="json"),
            }
        )
        generation = generated_result["content"]
        challenge = build_challenge(
            challenge_id=payload.challenge_id,
            title=payload.title,
            course_level=payload.course_level,
            learning_objective=payload.learning_objective,
            dataset_seed=payload.dataset_seed,
            generated=ChallengeGeneration.model_validate(generation),
        )
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc
    ARENA_CHALLENGES[payload.challenge_id] = challenge
    return {
        **public_challenge(challenge),
        "approved": False,
        "released": False,
        "generation_status": generated_result["status"],
        "generation_mode": generated_result["mode"],
    }


@app.post("/api/arena/challenges/{challenge_id}/approve")
def arena_approve_challenge(challenge_id: str, request: Request) -> dict[str, Any]:
    _require_instructor(request)
    challenge = _arena_challenge(challenge_id)
    approved = challenge.model_copy(update={"approved_at": challenge.created_at})
    ARENA_CHALLENGES[challenge_id] = approved
    return {**public_challenge(approved), "approved": True, "released": _challenge_is_released(approved)}


@app.post("/api/arena/challenges/{challenge_id}/release")
def arena_release_challenge(challenge_id: str, request: Request) -> dict[str, Any]:
    _require_instructor(request)
    challenge = _arena_challenge(challenge_id)
    if challenge.approved_at is None:
        raise HTTPException(409, "approve the challenge before releasing results")
    policy = {**challenge.release_policy, "hidden_results": "released"}
    released = challenge.model_copy(update={"release_policy": policy})
    ARENA_CHALLENGES[challenge_id] = released
    return {**public_challenge(released), "approved": True, "released": True}


@app.get("/api/arena/challenges/{challenge_id}/bundle")
def arena_instructor_bundle(challenge_id: str, request: Request) -> dict[str, Any]:
    _require_instructor(request)
    challenge = _arena_challenge(challenge_id)
    return {
        "challenge_id": challenge_id,
        "specification_hash": challenge.specification_hash(),
        "public": public_dataset(challenge),
        "instructor": instructor_dataset(challenge),
        "hidden_data_policy": "never returned by student endpoints",
    }


@app.post("/api/arena/challenges/{challenge_id}/validate-submission")
def arena_validate_submission(challenge_id: str, payload: ArenaSubmissionRequest) -> dict[str, Any]:
    challenge = _arena_challenge(challenge_id)
    return validate_submission_csv(payload.csv_text, challenge)


@app.post("/api/arena/challenges/{challenge_id}/submissions")
def arena_submit(challenge_id: str, payload: ArenaSubmissionRequest) -> dict[str, Any]:
    challenge = _arena_challenge(challenge_id)
    if challenge.approved_at is None:
        raise HTTPException(409, "challenge is not approved")
    submission_id = f"{challenge_id}-submission-{len(ARENA_SUBMISSIONS) + 1:03d}"
    evaluation = evaluate_submission(challenge, payload.csv_text, submission_id=submission_id)
    if not evaluation["valid"]:
        raise HTTPException(422, detail=evaluation["validation"])
    record = {
        "submission_id": submission_id,
        "challenge_id": challenge_id,
        "student_name": payload.student_name,
        "explanation": payload.explanation,
        "csv_text": payload.csv_text,
        "evaluation": evaluation,
    }
    ARENA_SUBMISSIONS[submission_id] = record
    return _public_submission_view(record, released=_challenge_is_released(challenge))


@app.get("/api/arena/submissions/{submission_id}")
def arena_submission(submission_id: str, request: Request) -> dict[str, Any]:
    record = ARENA_SUBMISSIONS.get(submission_id)
    if record is None:
        raise HTTPException(404, "submission not found")
    challenge = _arena_challenge(record["challenge_id"])
    return (
        _instructor_submission_view(record)
        if _arena_role(request) == "instructor"
        else _public_submission_view(record, released=_challenge_is_released(challenge))
    )


@app.get("/api/arena/challenges/{challenge_id}/leaderboard")
def arena_leaderboard(challenge_id: str, request: Request) -> dict[str, Any]:
    challenge = _arena_challenge(challenge_id)
    public_rows, instructor_rows = _arena_rankings(challenge_id)
    if _arena_role(request) == "instructor":
        return {
            "challenge_id": challenge_id,
            "released": _challenge_is_released(challenge),
            "rows": instructor_rows,
        }
    if _challenge_is_released(challenge):
        return {"challenge_id": challenge_id, "released": True, "rows": instructor_rows}
    return {
        "challenge_id": challenge_id,
        "released": False,
        "rows": public_rows,
        "hidden_results": "withheld_until_instructor_release",
    }


@app.get("/api/arena/challenges/{challenge_id}/instructor-report")
def arena_instructor_report(challenge_id: str, request: Request) -> dict[str, Any]:
    _require_instructor(request)
    challenge = _arena_challenge(challenge_id)
    _, instructor_rows = _arena_rankings(challenge_id)
    return {
        "challenge": public_challenge(challenge),
        "hidden_regime_manifest": [item.model_dump(mode="json") for item in challenge.hidden_regime_manifest],
        "instructor_dataset": instructor_dataset(challenge),
        "leaderboard": instructor_rows,
        "submissions": [
            _instructor_submission_view(record)
            for record in ARENA_SUBMISSIONS.values()
            if record["challenge_id"] == challenge_id
        ],
    }


@app.get("/api/arena/challenges/{challenge_id}/examples")
def arena_examples(challenge_id: str, request: Request) -> dict[str, Any]:
    _require_instructor(request)
    challenge = _arena_challenge(challenge_id)
    examples = []
    for label, name in (
        ("backtest_winner", "Example A · public leaderboard winner"),
        ("robust_generalizer", "Example B · hidden robustness winner"),
    ):
        csv_text = example_submission(challenge, label)
        evaluation = evaluate_submission(challenge, csv_text, submission_id=f"example-{label}")
        examples.append({"label": label, "name": name, "csv_text": csv_text, "evaluation": evaluation})
    return {"challenge_id": challenge_id, "examples": examples}


@app.post("/api/arena/submissions/{submission_id}/feedback")
def arena_feedback(submission_id: str, payload: ArenaFeedbackRequest, request: Request) -> dict[str, Any]:
    record = ARENA_SUBMISSIONS.get(submission_id)
    if record is None:
        raise HTTPException(404, "submission not found")
    challenge = _arena_challenge(record["challenge_id"])
    if _arena_role(request) != "instructor" and not _challenge_is_released(challenge):
        return {
            "status": "withheld",
            "message": "Feedback is released by the instructor after hidden evaluation.",
        }
    try:
        feedback = generate_feedback(record["evaluation"], model=payload.model)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc
    return {
        **feedback,
        "submission_id": submission_id,
        "model_requested": payload.model,
        "grounded_only": True,
    }
