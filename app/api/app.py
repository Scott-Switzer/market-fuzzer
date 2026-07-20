from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import sqlite3
import time
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from functools import lru_cache
from ipaddress import ip_address
from pathlib import Path
from typing import Any, Literal, cast
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

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
from app.calibration import (
    CalibrationPackV1,
    build_demo_calibration_pack,
    calibrate_bootstrap,
    compile_canonical_csv_bytes,
)
from app.compiler import compile_world
from app.decision_benchmark import build_decision_change_benchmark
from app.evaluation import development_fixture_evidence
from app.execution_arena import (
    CHALLENGE_ID,
    HIDDEN_VARIANTS,
    ExecutionPolicySubmission,
    benchmark_matrix,
    challenge_overview,
    public_leaderboard_matrix,
    run_execution_challenge,
    run_policy_submission,
)
from app.execution_arena import (
    POLICIES as EXECUTION_POLICIES,
)
from app.execution_challenge_designer import (
    ALLOWED_INTERVENTION_IDS,
    ALLOWED_POLICY_PARAMETER_IDS,
    ExecutionChallengeDesignInput,
    generate_execution_challenge_design,
)
from app.execution_feedback import build_execution_evidence, generate_execution_feedback
from app.execution_store import ArenaPhaseError, ArenaQuotaError, ArenaStore
from app.experiments import run_batch, run_single, run_validation_campaign
from app.external_adapter import adapter_provenance, execute_registered_strategy
from app.governance import build_enterprise_validation_report
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
from app.scenario_studio import compile_scenario_pack
from app.schemas import WorldSpec
from app.simulation import run_simulation
from app.strategy_lab import StrategyCreate, StressExperimentCreate
from app.strategy_language import StrategyBriefRequest, compile_strategy_brief
from app.strategy_protocol import StrategyActionV1, StrategyObservationV1
from app.synthetic_market import (
    SCENARIO_SCHEMA_VERSION,
    WORLD_SCHEMA_VERSION,
    RegressionSuiteCreate,
    ScenarioPackCreate,
    SyntheticWorldCreate,
    new_registry_id,
)
from app.synthetic_market import (
    utc_now as synthetic_utc_now,
)

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_ROOT = Path(os.getenv("MARKET_FUZZER_EXPERIMENT_ROOT", "artifacts")).expanduser().resolve()
app = FastAPI(title="Quant Challenge Arena", version="0.3.0")
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
JOBS: dict[str, dict] = {}
PRODUCT_PROJECTS: dict[str, dict] = {}


def _development_stress_evidence(result: dict[str, Any]) -> dict[str, Any]:
    """State the boundary for legacy deterministic Stress Lab result artifacts."""
    return development_fixture_evidence(
        payload=result,
        limitation=(
            "Stress Lab currently uses declared deterministic scenario packs and fixed seeds; "
            "it is a development fixture until it runs a sealed V2 campaign."
        ),
    ).to_dict()


PRODUCT_FAILURES: dict[str, dict] = {}
_LOCAL_DEMO_SESSION_SECRET = os.urandom(32)
_EXECUTION_STORE_CACHE_SIZE = 8
_MAX_CALIBRATION_UPLOAD_BYTES = 20 * 1024 * 1024


@app.middleware("http")
async def enterprise_api_key_guard(request: Request, call_next):
    """Protect enterprise APIs when a single-tenant deployment key is configured."""

    configured = os.getenv("ARENA_ENTERPRISE_API_KEY")
    if configured and request.url.path.startswith("/api/enterprise"):
        supplied = request.headers.get("x-api-key", "")
        authorization = request.headers.get("authorization", "")
        if authorization.lower().startswith("bearer "):
            supplied = authorization[7:].strip()
        if not hmac.compare_digest(supplied, configured):
            return JSONResponse(
                {"detail": "enterprise API key required"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
    return await call_next(request)


DESIGN_INTERVENTION_LABELS = {
    "liquidity_withdrawal": "Liquidity withdrawal",
    "crowded_unwind": "Crowded unwind",
    "earnings_shock": "Scheduled event shock",
    "latency_shock": "Message latency shock",
}
DESIGN_PARAMETER_LABELS = {
    "strategy_type": "Strategy type",
    "target_participation": "Target participation",
    "max_participation": "Maximum participation",
    "max_spread_bps": "Maximum spread",
    "urgency_curve": "Urgency curve",
    "feed_latency_tolerance_ms": "Feed-latency tolerance",
    "order_entry_latency_ms": "Order-entry latency",
    "cancel_after_ms": "Cancel-after interval",
    "completion_buffer_steps": "Completion buffer",
    "pause_during_halt": "Pause during halt",
    "pause_above_spread_limit": "Pause above spread limit",
    "include_pending_in_budget": "Include pending orders in budget",
}


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


class ExecutionChallengeRunRequest(BaseModel):
    """Legacy practice request. Hidden variants are intentionally absent."""

    model_config = ConfigDict(extra="forbid")

    policy_id: str = Field(
        default="aggressive_pov", pattern=r"^(twap|aggressive_pov|guarded_pov|completion_first)$"
    )
    seed: Literal[42] = 42


class ExecutionSubmissionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy: ExecutionPolicySubmission


class ExecutionPracticeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy_id: str | None = Field(
        default=None, pattern=r"^(twap|aggressive_pov|guarded_pov|completion_first)$"
    )
    policy: ExecutionPolicySubmission | None = None
    comparison_policy_id: str | None = Field(
        default=None, pattern=r"^(twap|aggressive_pov|guarded_pov|completion_first)$"
    )
    seed: Literal[42] = 42

    @model_validator(mode="after")
    def exactly_one_policy(self) -> ExecutionPracticeRequest:
        if (self.policy_id is None) == (self.policy is None):
            raise ValueError("provide exactly one of policy_id or policy")
        return self


class ExecutionPhaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(default="Instructor advanced the demo challenge.", min_length=3, max_length=500)


class DemoSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str = Field(pattern="^(student|instructor)$")
    instructor_code: str | None = Field(default=None, min_length=8, max_length=256)


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
        "enterprise_product": "Synthetic Market World",
        "enterprise_registry": "v1",
        "enterprise_authentication": "api_key_when_configured",
        "external_adapter_protocol": "http_json_v1",
        "calibration_import": "aggregate_only",
    }


@app.get("/api/ready")
def readiness() -> dict[str, Any]:
    """Report whether the SQLite registry and artifact volume are usable."""

    try:
        store = _execution_store()
        with store.connection() as connection:
            database_probe = connection.execute("SELECT 1").fetchone()
        artifact_root_ready = ARTIFACT_ROOT.is_dir() and os.access(ARTIFACT_ROOT, os.W_OK)
    except (OSError, sqlite3.Error) as exc:
        raise HTTPException(503, f"service is not ready: {exc}") from exc
    if database_probe is None or not artifact_root_ready:
        raise HTTPException(503, "service is not ready: registry or artifact volume unavailable")
    return {
        "status": "ready",
        "database": "ok",
        "artifact_store": "ok",
        "deployment_mode": "single_tenant_research_appliance",
        "database_file": store.path.name,
        "artifact_root": str(ARTIFACT_ROOT),
    }


@app.get("/synthetic-market-world")
def synthetic_market_world_landing() -> FileResponse:
    """Enterprise product entry point; the existing Arena remains at /."""
    return FileResponse(ROOT / "static" / "synthetic-market-world.html")


@app.get("/strategy-stress-lab")
def strategy_stress_lab_landing() -> FileResponse:
    return FileResponse(ROOT / "static" / "stress-lab.html")


@app.get("/api/enterprise/worlds")
def enterprise_worlds() -> dict[str, Any]:
    return {"worlds": _execution_store().synthetic_worlds()}


@app.post("/api/enterprise/worlds")
def enterprise_create_world(payload: SyntheticWorldCreate, request: Request) -> dict[str, Any]:
    actor = _enterprise_actor(request)
    manifest = payload.model_dump(mode="json")
    manifest["schema_version"] = WORLD_SCHEMA_VERSION
    manifest["created_at"] = synthetic_utc_now()
    manifest_hash = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    world_id = new_registry_id("world")
    return _execution_store().create_synthetic_world(world_id, manifest, actor, manifest_hash)


@app.get("/api/enterprise/worlds/{world_id}")
def enterprise_world(world_id: str) -> dict[str, Any]:
    try:
        return _execution_store().synthetic_world(world_id)
    except KeyError as exc:
        raise HTTPException(404, "synthetic world not found") from exc


@app.post("/api/enterprise/worlds/{world_id}/calibration")
def enterprise_attach_calibration(world_id: str, pack: CalibrationPackV1, request: Request) -> dict[str, Any]:
    actor = _enterprise_actor(request)
    calibration = calibrate_bootstrap(pack, mode="quick")
    calibration_run_id = new_registry_id("calibration-run")
    try:
        return _execution_store().attach_calibration_pack(
            world_id,
            pack.model_dump(mode="json"),
            actor,
            calibration.model_dump(mode="json"),
            calibration_run_id,
        )
    except KeyError as exc:
        raise HTTPException(404, "synthetic world not found") from exc


@app.post("/api/enterprise/worlds/{world_id}/calibration/import")
async def enterprise_import_calibration(
    world_id: str,
    request: Request,
    pack_id: str = Query("customer-csv-v1", min_length=3, max_length=100),
    source_url: str = Query("user-provided://canonical-csv", min_length=3, max_length=500),
    usage_basis: str = Query(
        "Customer-authorized data for aggregate calibration", min_length=3, max_length=300
    ),
    instrument: str = Query("customer-instrument", min_length=1, max_length=80),
    venue: str = Query("customer-venue", min_length=1, max_length=80),
    session: str = Query("customer-session", min_length=1, max_length=80),
    retrieval_date: str | None = Query(None),
) -> dict[str, Any]:
    """Import a canonical CSV transiently and persist aggregate calibration evidence only."""

    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type not in {"text/csv", "application/csv", "application/octet-stream"}:
        raise HTTPException(415, "calibration import requires a raw CSV request body")
    payload = await request.body()
    if not payload:
        raise HTTPException(400, "calibration CSV is empty")
    if len(payload) > _MAX_CALIBRATION_UPLOAD_BYTES:
        raise HTTPException(413, "calibration CSV exceeds the 20 MB single-tenant limit")
    try:
        parsed_date = date.fromisoformat(retrieval_date) if retrieval_date else date.today()
    except ValueError as exc:
        raise HTTPException(422, "retrieval_date must be ISO 8601 (YYYY-MM-DD)") from exc
    try:
        pack = compile_canonical_csv_bytes(
            payload,
            pack_id=pack_id,
            source_url=source_url,
            retrieval_date=parsed_date,
            usage_basis=usage_basis,
            instrument=instrument,
            venue=venue,
            session=session,
        )
        calibration = calibrate_bootstrap(pack, mode="quick")
        calibration_run_id = new_registry_id("calibration-run")
        result = _execution_store().attach_calibration_pack(
            world_id,
            pack.model_dump(mode="json"),
            _enterprise_actor(request),
            calibration.model_dump(mode="json"),
            calibration_run_id,
        )
        return result | {
            "import_evidence": {
                "source_bytes": len(payload),
                "raw_rows_retained": False,
                "source_checksum": pack.checksum,
                "calibration_run_id": calibration_run_id,
            }
        }
    except KeyError as exc:
        raise HTTPException(404, "synthetic world not found") from exc
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, f"invalid canonical calibration CSV: {exc}") from exc


@app.get("/api/enterprise/calibration-packs/{pack_id}")
def enterprise_calibration_pack(pack_id: str) -> dict[str, Any]:
    try:
        return _execution_store().calibration_pack(pack_id)
    except KeyError as exc:
        raise HTTPException(404, "calibration pack not found") from exc


@app.get("/api/enterprise/calibration-runs/{calibration_run_id}")
def enterprise_calibration_run(calibration_run_id: str) -> dict[str, Any]:
    try:
        return _execution_store().calibration_run(calibration_run_id)
    except KeyError as exc:
        raise HTTPException(404, "calibration run not found") from exc


@app.get("/api/enterprise/scenario-packs")
def enterprise_scenario_packs() -> dict[str, Any]:
    return {"scenario_packs": _execution_store().scenario_packs()}


@app.post("/api/enterprise/scenario-packs")
def enterprise_create_scenario_pack(payload: ScenarioPackCreate, request: Request) -> dict[str, Any]:
    actor = _enterprise_actor(request)
    manifest = payload.model_dump(mode="json")
    manifest["schema_version"] = SCENARIO_SCHEMA_VERSION
    manifest["created_at"] = synthetic_utc_now()
    manifest_hash = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    scenario_pack_id = new_registry_id("scenario")
    try:
        return _execution_store().create_scenario_pack(scenario_pack_id, manifest, actor, manifest_hash)
    except KeyError as exc:
        raise HTTPException(404, "base synthetic world not found") from exc


@app.get("/api/enterprise/scenario-packs/{scenario_pack_id}")
def enterprise_scenario_pack(scenario_pack_id: str) -> dict[str, Any]:
    try:
        return _execution_store().scenario_pack(scenario_pack_id)
    except KeyError as exc:
        raise HTTPException(404, "scenario pack not found") from exc


@app.post("/api/enterprise/scenario-packs/{scenario_pack_id}/compile")
def enterprise_compile_scenario_pack(scenario_pack_id: str) -> dict[str, Any]:
    try:
        pack = _execution_store().scenario_pack(scenario_pack_id)
        base_world = _execution_store().synthetic_world(str(pack["base_world_id"]))
    except KeyError as exc:
        raise HTTPException(404, "scenario pack or base world not found") from exc
    try:
        calibration_run = None
        calibration_run_id = base_world["manifest"].get("calibration_run_id")
        if calibration_run_id:
            calibration_run = _execution_store().calibration_run(str(calibration_run_id))["result"]
        return compile_scenario_pack(
            {**pack["manifest"], "scenario_pack_id": scenario_pack_id},
            base_world_manifest=base_world,
            calibration_result=calibration_run,
        )
    except (KeyError, ValueError) as exc:
        raise HTTPException(422, f"scenario pack cannot be compiled: {exc}") from exc


@app.get("/api/enterprise/regression-suites")
def enterprise_regression_suites() -> dict[str, Any]:
    return {"regression_suites": _execution_store().regression_suites()}


@app.post("/api/enterprise/regression-suites")
def enterprise_create_regression_suite(payload: RegressionSuiteCreate, request: Request) -> dict[str, Any]:
    store = _execution_store()
    try:
        store.scenario_pack(payload.scenario_pack_id)
    except KeyError as exc:
        raise HTTPException(404, "scenario pack not found") from exc
    return store.create_regression_suite(
        new_registry_id("regression-suite"), payload.model_dump(mode="json"), _enterprise_actor(request)
    )


@app.get("/api/enterprise/regression-suites/{suite_id}")
def enterprise_regression_suite(suite_id: str) -> dict[str, Any]:
    try:
        return _execution_store().regression_suite(suite_id)
    except KeyError as exc:
        raise HTTPException(404, "regression suite not found") from exc


@app.post("/api/enterprise/regression-suites/{suite_id}/run")
def enterprise_run_regression_suite(suite_id: str, request: Request) -> dict[str, Any]:
    store = _execution_store()
    try:
        suite = store.regression_suite(suite_id)
        pack = store.scenario_pack(suite["scenario_pack_id"])
        base_world = store.synthetic_world(str(pack["base_world_id"]))
    except KeyError as exc:
        raise HTTPException(404, "regression suite or scenario pack not found") from exc
    calibration_result = (
        store.calibration_run(str(base_world["manifest"]["calibration_run_id"]))["result"]
        if base_world["manifest"].get("calibration_run_id")
        else None
    )
    try:
        first = compile_scenario_pack(
            {**pack["manifest"], "scenario_pack_id": suite["scenario_pack_id"]},
            base_world_manifest=base_world,
            calibration_result=calibration_result,
        )
        second = compile_scenario_pack(
            {**pack["manifest"], "scenario_pack_id": suite["scenario_pack_id"]},
            base_world_manifest=base_world,
            calibration_result=calibration_result,
        )
    except (KeyError, ValueError) as exc:
        raise HTTPException(422, f"scenario pack cannot be compiled: {exc}") from exc

    protected_worlds = first.get("protected_worlds", [])
    world_hashes = [str(world["world_hash"]) for world in protected_worlds]
    cases: dict[str, dict[str, Any]] = {
        "protected_worlds_present": {
            "passed": bool(protected_worlds),
            "detail": f"{len(protected_worlds)} protected world(s) compiled.",
        },
        "world_hashes_stable": {
            "passed": first.get("compile_hash") == second.get("compile_hash")
            and [item["world_hash"] for item in first.get("protected_worlds", [])]
            == [item["world_hash"] for item in second.get("protected_worlds", [])],
            "detail": "Repeated compilation produced identical compile and world hashes.",
        },
        "intervention_steps_preserved": {
            "passed": all(
                bool(world["world"].get("events"))
                and all(
                    event.get("simulation_step") == world["intent"]["start_step"]
                    for event in world["world"].get("events", [])
                )
                for world in protected_worlds
            ),
            "detail": "Every compiled world contains events at its declared intervention start step.",
        },
        "base_manifest_stable": {
            "passed": first.get("base_world_manifest_hash") == base_world.get("manifest_hash"),
            "detail": "The compiled pack remains tied to the registered base-world manifest.",
        },
    }
    evidence = {
        "suite_id": suite_id,
        "scenario_pack_id": suite["scenario_pack_id"],
        "compile_hash": first.get("compile_hash"),
        "world_hashes": world_hashes,
        "cases": [
            {"case": case, **cases[case], "required": case in suite["required_cases"]} for case in cases
        ],
    }
    required_results = [cases[case]["passed"] for case in suite["required_cases"]]
    status = "passed" if all(required_results) else "failed"
    return store.save_regression_run(
        new_registry_id("regression-run"),
        suite_id,
        status,
        sum(required_results),
        len(required_results),
        evidence,
    )


@app.get("/api/enterprise/regression-suites/{suite_id}/release-check")
def enterprise_regression_release_check(suite_id: str) -> dict[str, Any]:
    try:
        suite = _execution_store().regression_suite(suite_id)
    except KeyError as exc:
        raise HTTPException(404, "regression suite not found") from exc
    latest = suite.get("latest_run")
    if latest is None or latest["status"] != "passed":
        raise HTTPException(
            409,
            "governed release blocked: the latest required regression run did not pass",
        )
    return {
        "suite_id": suite_id,
        "scenario_pack_id": suite["scenario_pack_id"],
        "release_status": "eligible",
        "run_id": latest["run_id"],
        "run_hash": latest["run_hash"],
    }


@app.post("/api/enterprise/scenario-packs/{scenario_pack_id}/release")
def enterprise_release_scenario_pack(scenario_pack_id: str, request: Request) -> dict[str, Any]:
    store = _execution_store()
    try:
        store.scenario_pack(scenario_pack_id)
    except KeyError as exc:
        raise HTTPException(404, "scenario pack not found") from exc
    try:
        return store.approve_scenario_pack(
            new_registry_id("release"), scenario_pack_id, _enterprise_actor(request)
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.get("/api/enterprise/scenario-packs/{scenario_pack_id}/release-manifest")
def enterprise_scenario_pack_release_manifest(scenario_pack_id: str) -> dict[str, Any]:
    try:
        return _execution_store().scenario_pack_release(scenario_pack_id)
    except KeyError as exc:
        raise HTTPException(404, "scenario pack release manifest not found") from exc


@app.get("/api/enterprise/strategies")
def enterprise_strategies() -> dict[str, Any]:
    return {"strategies": _execution_store().strategies()}


@app.post("/api/enterprise/strategies/compile-brief")
def enterprise_compile_strategy_brief(payload: StrategyBriefRequest) -> dict[str, Any]:
    """Turn plain English into a reviewable allow-listed strategy proposal."""

    return compile_strategy_brief(payload.brief)


@app.post("/api/enterprise/adapter-reference/guarded-pov")
def enterprise_reference_guarded_pov(observation: StrategyObservationV1) -> dict[str, Any]:
    """Reference executable adapter for local boundary and demo verification."""

    if observation.remaining_quantity <= 0 or (
        observation.intervention_active and observation.spread_bps > 35.0
    ):
        return StrategyActionV1(action_type="hold", rationale_code="guarded_pov_pause").model_dump(
            mode="json"
        )
    quantity = max(1, min(observation.remaining_quantity, round(max(observation.observed_volume, 1) * 0.08)))
    return StrategyActionV1(
        action_type="market",
        side=observation.side,
        quantity=quantity,
        rationale_code="guarded_pov_reference",
    ).model_dump(mode="json")


@app.post("/api/enterprise/strategies")
def enterprise_create_strategy(payload: StrategyCreate, request: Request) -> dict[str, Any]:
    actor = _enterprise_actor(request)
    if payload.strategy_type == "arena_policy" and payload.builtin_policy_id is None:
        raise HTTPException(422, "arena_policy strategies require a registered built-in policy ID")
    if payload.strategy_type == "external_adapter" and payload.external_adapter is None:
        raise HTTPException(422, "external_adapter strategies require a bounded adapter contract")
    strategy_id = new_registry_id("strategy")
    record = payload.model_dump(mode="json")
    if payload.external_adapter is not None:
        record["builtin_policy_id"] = payload.external_adapter.policy_id
    return _execution_store().create_strategy(strategy_id, record, actor)


@app.get("/api/enterprise/strategies/{strategy_id}")
def enterprise_strategy(strategy_id: str) -> dict[str, Any]:
    try:
        return _execution_store().strategy(strategy_id)
    except KeyError as exc:
        raise HTTPException(404, "strategy not found") from exc


@app.post("/api/enterprise/experiments")
def enterprise_run_experiment(payload: StressExperimentCreate, request: Request) -> dict[str, Any]:
    actor = _enterprise_actor(request)
    store = _execution_store()
    try:
        pack = store.scenario_pack(payload.scenario_pack_id)
        base_world = store.synthetic_world(str(pack["base_world_id"]))
        strategies = [store.strategy(strategy_id) for strategy_id in payload.strategy_ids]
    except KeyError as exc:
        raise HTTPException(404, "scenario pack or strategy not found") from exc
    calibration_result = (
        store.calibration_run(str(base_world["manifest"]["calibration_run_id"]))["result"]
        if base_world["manifest"].get("calibration_run_id")
        else None
    )
    compiled_by_seed = {
        seed: compile_scenario_pack(
            {**pack["manifest"], "scenario_pack_id": payload.scenario_pack_id},
            base_world_manifest=base_world,
            calibration_result=calibration_result,
            seed=seed,
        )
        for seed in sorted(payload.seeds)
    }
    compiled = compiled_by_seed[sorted(compiled_by_seed)[0]]
    ensemble_runs: list[dict[str, Any]] = []
    if calibration_result is not None:
        for parameter_set in calibration_result.get("accepted_parameter_sets", []):
            ensemble_compiled = compile_scenario_pack(
                {**pack["manifest"], "scenario_pack_id": payload.scenario_pack_id},
                base_world_manifest=base_world,
                calibration_result={"accepted_parameter_sets": [parameter_set]},
            )

            def run_cell(protected: dict[str, Any]) -> dict[str, Any]:
                simulation = run_simulation(WorldSpec.model_validate(protected["world"]))
                return {
                    "world_hash": protected["world_hash"],
                    "filled_quantity": simulation.summary["filled_quantity"],
                    "implementation_shortfall_bps": simulation.summary["implementation_shortfall_bps"],
                    "completion_pct": simulation.summary["completion_pct"],
                }

            # Keep execution single-threaded until the simulator has an explicit
            # worker boundary; this preserves restart safety and avoids sharing
            # mutable engine state across request threads.
            world_results = [
                run_cell(protected)
                for protected in sorted(
                    ensemble_compiled["protected_worlds"], key=lambda item: item["world_hash"]
                )
            ]
            ensemble_runs.append(
                {
                    "parameter_set_id": parameter_set["parameter_set_id"],
                    "validation_distance": parameter_set["validation_distance"],
                    "heldout_distance": parameter_set["heldout_distance"],
                    "world_results": world_results,
                }
            )
    selected: list[dict[str, Any]] = []
    for strategy in sorted(strategies, key=lambda item: str(item["strategy_id"])):
        strategy_id = str(strategy["strategy_id"])
        for seed in sorted(payload.seeds):
            compiled_for_seed = compiled_by_seed[seed]
            for protected in sorted(
                compiled_for_seed["protected_worlds"], key=lambda item: item["world_hash"]
            ):
                row = execute_registered_strategy(
                    strategy,
                    WorldSpec.model_validate(protected["world"]),
                    source_world_hash=str(protected["world_hash"]),
                    scenario_pack_id=payload.scenario_pack_id,
                    response_recorder=store.record_strategy_response,
                    response_lookup=store.find_strategy_response,
                )
                selected.append(
                    {
                        **row,
                        "strategy_id": strategy_id,
                        "seed": seed,
                        "adapter_provenance": adapter_provenance(strategy, row["adapter_runtime"]),
                    }
                )
    selected.sort(key=lambda row: (str(row["strategy_id"]), int(row["seed"]), str(row["world_hash"])))
    baseline = benchmark_matrix(
        seeds=tuple(sorted(payload.seeds)),
        variants=("latency_shock",),
        student_submissions=None,
        policy_ids=tuple(str(strategy["builtin_policy_id"]) for strategy in strategies),
    )
    result = {
        "experiment_type": "baseline_vs_protected_benchmark",
        "compile_hash": compiled["compile_hash"],
        "scenario_pack_id": payload.scenario_pack_id,
        "strategy_results": selected,
        "arena_baseline_comparator": baseline,
        "cell_provenance": [
            {
                "strategy_id": row["strategy_id"],
                "scenario_hash": compiled_by_seed[int(row["seed"])]["compile_hash"],
                "world_hash": row["world_hash"],
                "seed": row["seed"],
                "result_hash": row["result_hash"],
            }
            for row in selected
        ],
        "claim_boundary": "Results are deterministic measurements inside the declared synthetic benchmark worlds.",
        "calibration_ensemble": compiled.get("calibration_ensemble", []),
        "calibration_ensemble_runs": ensemble_runs,
    }
    result["evaluation_evidence"] = _development_stress_evidence(result)
    experiment_id = new_registry_id("experiment")
    return store.save_stress_experiment(experiment_id, payload.model_dump(mode="json"), actor, result)


@app.get("/api/enterprise/experiments")
def enterprise_experiments(
    limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)
) -> dict[str, Any]:
    return {
        "experiments": _execution_store().stress_experiments(limit=limit, offset=offset),
        "limit": limit,
        "offset": offset,
    }


@app.get("/api/enterprise/decision-benchmark")
def enterprise_decision_benchmark(
    seeds: str = Query("41,42", description="Comma-separated deterministic benchmark seeds"),
) -> dict[str, Any]:
    """Expose the canonical decision-change evidence for the operator UI."""
    try:
        parsed_seeds = tuple(sorted({int(value.strip()) for value in seeds.split(",") if value.strip()}))
    except ValueError as exc:
        raise HTTPException(422, "seeds must be comma-separated integers") from exc
    if not parsed_seeds or len(parsed_seeds) > 8:
        raise HTTPException(422, "seeds must contain between 1 and 8 values")
    return build_decision_change_benchmark(parsed_seeds)


@app.post("/api/enterprise/experiment-jobs")
def enterprise_create_experiment_job(payload: StressExperimentCreate, request: Request) -> dict[str, Any]:
    return _execution_store().create_experiment_job(
        new_registry_id("job"), payload.model_dump(mode="json"), _enterprise_actor(request)
    )


@app.get("/api/enterprise/experiment-jobs/{job_id}")
def enterprise_experiment_job(job_id: str) -> dict[str, Any]:
    try:
        return _execution_store().experiment_job(job_id)
    except KeyError as exc:
        raise HTTPException(404, "experiment job not found") from exc


@app.get("/api/enterprise/experiment-jobs")
def enterprise_experiment_jobs(limit: int = Query(50, ge=1, le=200)) -> dict[str, Any]:
    return {"jobs": _execution_store().experiment_jobs(limit=limit), "limit": limit}


@app.post("/api/enterprise/experiment-jobs/{job_id}/resume")
def enterprise_resume_experiment_job(job_id: str, request: Request) -> dict[str, Any]:
    store = _execution_store()
    try:
        job = store.experiment_job(job_id)
    except KeyError as exc:
        raise HTTPException(404, "experiment job not found") from exc
    if job["status"] == "completed":
        try:
            return job | {"artifact": store.experiment_artifact(job_id, "experiment-result")}
        except KeyError:
            return job
    if job["status"] == "running":
        raise HTTPException(409, "experiment job is already running")
    payload = StressExperimentCreate.model_validate(job["payload"])
    try:
        pack = store.scenario_pack(payload.scenario_pack_id)
        base_world = store.synthetic_world(str(pack["base_world_id"]))
        strategies = [store.strategy(strategy_id) for strategy_id in payload.strategy_ids]
    except KeyError as exc:
        raise HTTPException(404, "scenario pack or strategy not found") from exc
    if any(strategy["builtin_policy_id"] is None for strategy in strategies):
        raise HTTPException(422, "only built-in policy adapters are executable in this milestone")
    calibration_result = (
        store.calibration_run(str(base_world["manifest"]["calibration_run_id"]))["result"]
        if base_world["manifest"].get("calibration_run_id")
        else None
    )
    compiled_by_seed = {
        seed: compile_scenario_pack(
            {**pack["manifest"], "scenario_pack_id": payload.scenario_pack_id},
            base_world_manifest=base_world,
            calibration_result=calibration_result,
            seed=seed,
        )
        for seed in sorted(payload.seeds)
    }
    cell_total = sum(
        len(payload.strategy_ids) * len(compiled["protected_worlds"])
        for compiled in compiled_by_seed.values()
    )
    completed_before = sum(cell["status"] == "completed" for cell in job["cells"])
    initial_progress = {
        "completed_cells": completed_before,
        "total_cells": cell_total,
        "percent": round(100 * completed_before / cell_total) if cell_total else 0,
    }
    if not store.claim_experiment_job(job_id, initial_progress):
        raise HTTPException(409, "experiment job is already running")
    if cell_total == 0:
        store.update_experiment_job(job_id, status="failed", progress=initial_progress)
        raise HTTPException(422, "scenario pack compiled to no protected worlds")
    job = store.experiment_job(job_id)
    try:
        completed_rows: list[dict[str, Any]] = []
        for strategy in sorted(strategies, key=lambda item: str(item["strategy_id"])):
            strategy_id = str(strategy["strategy_id"])
            for seed in sorted(payload.seeds):
                compiled = compiled_by_seed[seed]
                for protected in compiled["protected_worlds"]:
                    world_hash = str(protected["world_hash"])
                    existing = next(
                        (
                            cell
                            for cell in job["cells"]
                            if cell["strategy_id"] == strategy_id
                            and cell["scenario_hash"] == compiled["compile_hash"]
                            and cell["world_hash"] == world_hash
                            and cell["seed"] == seed
                            and cell["status"] == "completed"
                            and cell["result"] is not None
                            and cell["result"].get("execution_source") == "compiled_scenario_pack"
                        ),
                        None,
                    )
                    if existing is not None:
                        completed_rows.append(existing["result"])
                        continue
                    try:
                        row = execute_registered_strategy(
                            strategy,
                            WorldSpec.model_validate(protected["world"]),
                            source_world_hash=world_hash,
                            scenario_pack_id=payload.scenario_pack_id,
                            response_recorder=store.record_strategy_response,
                            response_lookup=store.find_strategy_response,
                        )
                        store.upsert_experiment_cell(
                            new_registry_id("cell"),
                            job_id,
                            strategy_id=strategy_id,
                            scenario_hash=str(compiled["compile_hash"]),
                            world_hash=world_hash,
                            seed=seed,
                            status="completed",
                            result={
                                **row,
                                "strategy_id": strategy_id,
                                "seed": seed,
                                "adapter_provenance": adapter_provenance(strategy, row["adapter_runtime"]),
                            },
                        )
                        completed_rows.append(
                            {
                                **row,
                                "strategy_id": strategy_id,
                                "seed": seed,
                                "adapter_provenance": adapter_provenance(strategy, row["adapter_runtime"]),
                            }
                        )
                    except Exception as exc:
                        store.upsert_experiment_cell(
                            new_registry_id("cell"),
                            job_id,
                            strategy_id=strategy_id,
                            scenario_hash=str(compiled["compile_hash"]),
                            world_hash=world_hash,
                            seed=seed,
                            status="failed",
                            error=str(exc),
                        )
                        raise
                    cells = store.experiment_cells(job_id)
                    done = sum(cell["status"] == "completed" for cell in cells)
                    store.update_experiment_job(
                        job_id,
                        status="running",
                        progress={
                            "completed_cells": done,
                            "total_cells": cell_total,
                            "percent": round(100 * done / cell_total),
                        },
                    )
        completed_rows.sort(key=lambda row: (str(row["strategy_id"]), int(row["seed"])))
        compiled = compiled_by_seed[sorted(compiled_by_seed)[0]]
        baseline = benchmark_matrix(
            seeds=tuple(sorted(payload.seeds)),
            variants=("latency_shock",),
            student_submissions=None,
            policy_ids=tuple(str(strategy["builtin_policy_id"]) for strategy in strategies),
        )
        result = {
            "experiment_type": "baseline_vs_protected_benchmark",
            "compile_hash": compiled["compile_hash"],
            "scenario_pack_id": payload.scenario_pack_id,
            "strategy_results": completed_rows,
            "arena_baseline_comparator": baseline,
            "cell_provenance": [
                {
                    "cell_id": cell["cell_id"],
                    "strategy_id": cell["strategy_id"],
                    "scenario_hash": cell["scenario_hash"],
                    "world_hash": cell["world_hash"],
                    "seed": cell["seed"],
                    "result_hash": cell["result_hash"],
                }
                for cell in store.experiment_cells(job_id)
                if cell["status"] == "completed"
            ],
            "claim_boundary": "Results are deterministic measurements inside the declared synthetic benchmark worlds.",
            "calibration_ensemble": next(iter(compiled_by_seed.values())).get("calibration_ensemble", []),
        }
        result["evaluation_evidence"] = _development_stress_evidence(result)
        experiment = store.save_stress_experiment(
            new_registry_id("experiment"),
            payload.model_dump(mode="json"),
            job["created_by"],
            result,
        )
        artifact = store.save_experiment_artifact(
            new_registry_id("artifact"),
            job_id,
            "experiment-result",
            experiment,
            manifest={
                "experiment_id": experiment["experiment_id"],
                "scenario_pack_id": payload.scenario_pack_id,
                "scenario_hashes": sorted(
                    {
                        cell["scenario_hash"]
                        for cell in store.experiment_cells(job_id)
                        if cell["status"] == "completed"
                    }
                ),
                "world_hashes": sorted(
                    {
                        cell["world_hash"]
                        for cell in store.experiment_cells(job_id)
                        if cell["status"] == "completed"
                    }
                ),
                "seeds": sorted({cell["seed"] for cell in store.experiment_cells(job_id)}),
                "strategy_ids": sorted({cell["strategy_id"] for cell in store.experiment_cells(job_id)}),
                "creator": job["created_by"],
            },
        )
        return store.update_experiment_job(
            job_id,
            status="completed",
            progress={"completed_cells": cell_total, "total_cells": cell_total, "percent": 100},
            experiment_id=experiment["experiment_id"],
        ) | {"artifact": artifact}
    except Exception:
        cells = store.experiment_cells(job_id)
        done = sum(cell["status"] == "completed" for cell in cells)
        store.update_experiment_job(
            job_id,
            status="failed",
            progress={
                "completed_cells": done,
                "total_cells": cell_total,
                "percent": round(100 * done / cell_total),
            },
        )
        raise


@app.get("/api/enterprise/experiment-jobs/{job_id}/artifacts/{kind}")
def enterprise_experiment_artifact(job_id: str, kind: str) -> dict[str, Any]:
    try:
        return _execution_store().experiment_artifact(job_id, kind)
    except KeyError as exc:
        raise HTTPException(404, "experiment artifact not found") from exc


@app.get("/api/enterprise/experiments/{experiment_id}/artifacts/{kind}")
def enterprise_experiment_artifact_by_experiment(experiment_id: str, kind: str) -> dict[str, Any]:
    try:
        return _execution_store().experiment_artifact_for_experiment(experiment_id, kind)
    except KeyError as exc:
        raise HTTPException(404, "experiment artifact not found") from exc


@app.get("/api/enterprise/experiments/{experiment_id}/artifacts/{kind}/download")
def enterprise_experiment_artifact_download(experiment_id: str, kind: str) -> Response:
    try:
        artifact = _execution_store().experiment_artifact_for_experiment(experiment_id, kind)
    except KeyError as exc:
        raise HTTPException(404, "experiment artifact not found") from exc
    return Response(
        content=json.dumps(artifact["content"], indent=2, sort_keys=True),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{experiment_id}-{kind}.json"'},
    )


@app.get("/api/enterprise/experiments/{experiment_id}")
def enterprise_experiment(experiment_id: str) -> dict[str, Any]:
    try:
        return _execution_store().stress_experiment(experiment_id)
    except KeyError as exc:
        raise HTTPException(404, "experiment not found") from exc


@app.post("/api/enterprise/experiments/{experiment_id}/validate")
def enterprise_validate_experiment(experiment_id: str, request: Request) -> dict[str, Any]:
    actor = _enterprise_actor(request)
    store = _execution_store()
    try:
        experiment = store.stress_experiment(experiment_id)
    except KeyError as exc:
        raise HTTPException(404, "experiment not found") from exc
    try:
        report = build_enterprise_validation_report(experiment)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return store.save_validation_report(
        f"validation-{experiment_id}", experiment_id, report.model_dump(mode="json"), actor
    )


@app.get("/api/enterprise/experiments/{experiment_id}/validation")
def enterprise_validation_report(experiment_id: str) -> dict[str, Any]:
    try:
        return _execution_store().validation_report(experiment_id)
    except KeyError as exc:
        raise HTTPException(404, "validation report not found") from exc


@app.get("/api/enterprise/experiments/{experiment_id}/validation/export")
def enterprise_validation_export(experiment_id: str) -> Response:
    try:
        record = _execution_store().validation_report(experiment_id)
    except KeyError as exc:
        raise HTTPException(404, "validation report not found") from exc
    payload = json.dumps(record["report"], indent=2, sort_keys=True)
    return Response(
        content=payload,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{experiment_id}-validation.json"'},
    )


@app.get("/api/execution-challenge")
def execution_challenge() -> dict[str, Any]:
    """The public brief. Hidden-world parameters never appear on this route."""
    return challenge_overview()


@app.post("/api/execution-challenge/run")
def execution_challenge_run(request: ExecutionChallengeRunRequest) -> dict[str, Any]:
    """Compatibility alias: always runs the public world."""
    try:
        return run_execution_challenge(request.policy_id, "normal", request.seed)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@app.get("/api/execution-challenge/benchmarks")
def execution_challenge_benchmarks(request: Request) -> dict[str, Any]:
    """Compatibility read: hidden evaluation must first run through the audited lifecycle."""
    if _arena_role(request) != "instructor":
        raise HTTPException(403, "hidden benchmark matrix is instructor-only")
    try:
        return _execution_store().evaluation(CHALLENGE_ID)["matrix"]
    except KeyError as exc:
        raise HTTPException(409, "lock and evaluate the challenge before reading the matrix") from exc


@app.get("/api/execution-challenge/policies")
def execution_challenge_policies() -> dict[str, Any]:
    return {"policies": [policy.__dict__ for policy in EXECUTION_POLICIES.values()]}


@app.post("/api/arena/demo-session")
def arena_demo_session(payload: DemoSessionRequest, request: Request, response: Response) -> dict[str, str]:
    """Issue a deliberately scoped demo cookie; this is not institutional auth."""
    if os.getenv("ARENA_DEMO_AUTH") != "1":
        raise HTTPException(404, "demo sessions are disabled")
    secure_cookie = _arena_cookie_secure(request)
    if payload.role == "instructor":
        expected_code = os.getenv("ARENA_DEMO_INSTRUCTOR_CODE")
        if not expected_code:
            raise HTTPException(403, "instructor demo sessions are disabled")
        if payload.instructor_code is None or not hmac.compare_digest(payload.instructor_code, expected_code):
            raise HTTPException(403, "invalid instructor demo code")
    role_cookie = f"arena_{payload.role}_session"
    token = request.cookies.get(role_cookie, "")
    existing = _arena_identity_from_token(token)
    if existing is not None and existing[0] == payload.role:
        user_id = existing[1]
        action = "demo_session_resumed"
    else:
        issued = datetime.now(UTC)
        issued_at = str(int(issued.timestamp()))
        expires_at = str(int((issued + timedelta(hours=12)).timestamp()))
        user_id = f"demo-{payload.role}-{uuid4().hex[:16]}"
        value = f"{payload.role}|{user_id}|{issued_at}|{expires_at}"
        signature = hmac.new(_arena_session_secret(), value.encode(), hashlib.sha256).hexdigest()
        token = base64.urlsafe_b64encode(f"{value}|{signature}".encode()).decode()
        _execution_store().save_session(
            hashlib.sha256(token.encode()).hexdigest(),
            user_id,
            payload.role,
            issued.isoformat(),
            (issued + timedelta(hours=12)).isoformat(),
        )
        action = "demo_session_issued"
    _execution_store().audit(CHALLENGE_ID, user_id, action, {"role": payload.role})
    response.set_cookie(
        "arena_demo_session",
        token,
        httponly=True,
        samesite="lax",
        secure=secure_cookie,
        max_age=43_200,
    )
    response.set_cookie(
        role_cookie,
        token,
        httponly=True,
        samesite="lax",
        secure=secure_cookie,
        max_age=43_200,
    )
    return {
        "status": "ok",
        "role": payload.role,
        "user_id": user_id,
        "authentication": "demo_session",
    }


@app.get("/api/arena/execution/challenges/{challenge_id}")
def execution_public_challenge(challenge_id: str) -> dict[str, Any]:
    challenge = _execution_challenge_record(challenge_id)
    overview = challenge_overview()
    return {
        **overview,
        "phase": challenge["phase"],
        "hidden_worlds": {
            "count": len(challenge["hidden_worlds"]),
            "status": "withheld_until_release",
        },
        "practice_policy": {
            "maximum_runs": challenge["max_practice_runs"],
            "score_mode": challenge["practice_score_mode"],
            "best_public_only": challenge["best_public_only"],
        },
        "submission_policy": {
            "maximum_final_submissions": challenge["max_final_submissions"],
            "hidden_results_final_only": challenge["hidden_final_only"],
        },
    }


@app.get("/api/arena/session")
def arena_current_session(request: Request) -> dict[str, str | bool]:
    role, user_id = _arena_identity(request)
    if user_id == "anonymous-student":
        return {"status": "anonymous", "authenticated": False}
    return {
        "status": "ok",
        "role": role,
        "user_id": user_id,
        "authentication": "demo_session",
        "authenticated": True,
    }


@app.post("/api/arena/execution/challenge-designs")
def execution_challenge_design(
    payload: dict[str, Any],
    request: Request,
    model: str | None = None,
) -> dict[str, Any]:
    """Create an instructor-only qualitative draft; deterministic code still owns worlds."""
    _, actor = _require_execution_instructor(request)
    if model is not None and (len(model) > 120 or not model.startswith("gpt-")):
        raise HTTPException(422, "model must be a supported gpt-* identifier")
    try:
        constraints = ExecutionChallengeDesignInput.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(422, "invalid challenge-design constraints") from exc
    result = generate_execution_challenge_design(constraints, model=model)
    store = _execution_store()
    design_id = store.save_challenge_design(
        CHALLENGE_ID,
        actor,
        constraints.model_dump(mode="json"),
        result,
    )
    return {
        **result,
        "design_id": design_id,
        "approval_status": "draft",
        "numeric_worlds_created": False,
    }


@app.get("/api/arena/execution/challenge-design-options")
def execution_challenge_design_options(request: Request) -> dict[str, Any]:
    """Return protected designer allow-lists only to an authenticated instructor."""
    _require_execution_instructor(request)
    return {
        "allowed_world_interventions": [
            {"id": identifier, "label": DESIGN_INTERVENTION_LABELS[identifier]}
            for identifier in DESIGN_INTERVENTION_LABELS
            if identifier in ALLOWED_INTERVENTION_IDS
        ],
        "allowed_policy_parameters": [
            {"id": identifier, "label": DESIGN_PARAMETER_LABELS[identifier]}
            for identifier in DESIGN_PARAMETER_LABELS
            if identifier in ALLOWED_POLICY_PARAMETER_IDS
        ],
        "exchange_capabilities": [
            "price-time-priority order book",
            "explicit feed, decision, order-entry, and cancel latency",
            "partial fills and simplified queue-ahead evidence",
            "deterministic event and replay instrumentation",
        ],
    }


@app.get("/api/arena/execution/challenges/{challenge_id}/policies")
def execution_policies(challenge_id: str) -> dict[str, Any]:
    _execution_challenge_record(challenge_id)
    return execution_challenge_policies()


@app.post("/api/arena/execution/challenges/{challenge_id}/practice")
def execution_practice(
    challenge_id: str, payload: ExecutionPracticeRequest, request: Request
) -> dict[str, Any]:
    challenge = _execution_challenge_record(challenge_id)
    if challenge["phase"] != "public_practice":
        raise HTTPException(409, "public practice is closed")
    role, user_id = _require_execution_student(request)
    store = _execution_store()
    used = store.practice_count(challenge_id, user_id)
    if used >= int(challenge["max_practice_runs"]):
        raise HTTPException(429, "public practice limit reached")
    try:
        result = (
            run_policy_submission(payload.policy, f"practice-{user_id}", payload.seed)
            if payload.policy is not None
            else run_execution_challenge(
                str(payload.policy_id), challenge["public_world_variant"], payload.seed
            )
        )
        if payload.comparison_policy_id is not None:
            comparison = run_execution_challenge(
                payload.comparison_policy_id,
                challenge["public_world_variant"],
                payload.seed,
            )
            result["comparison"] = {
                "policy_id": payload.comparison_policy_id,
                "name": comparison["policy"]["name"],
                "metrics": comparison["metrics"],
                "public_score": comparison["public_score"],
                "replay": comparison["replay"],
                "world": comparison["world"],
                "evidence": comparison["evidence"],
            }
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    policy_hash = hashlib.sha256(
        json.dumps(result["policy"], sort_keys=True, default=str).encode()
    ).hexdigest()
    run_id = f"practice-{uuid4().hex[:16]}"
    try:
        store.save_practice(
            run_id,
            challenge_id,
            user_id,
            policy_hash,
            payload.seed,
            float(result["public_score"]),
            result,
            max_runs=int(challenge["max_practice_runs"]),
        )
    except ArenaPhaseError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ArenaQuotaError as exc:
        raise HTTPException(429, str(exc)) from exc
    return {
        **result,
        "practice_run_id": run_id,
        "practice_runs_remaining": int(challenge["max_practice_runs"]) - used - 1,
        "actor_role": role,
    }


@app.post("/api/arena/execution/challenges/{challenge_id}/submissions")
def execution_submit(
    challenge_id: str, payload: ExecutionSubmissionRequest, request: Request
) -> dict[str, Any]:
    challenge = _execution_challenge_record(challenge_id)
    if challenge["phase"] != "public_practice":
        raise HTTPException(409, "final submissions are closed")
    _, user_id = _require_execution_student(request)
    store = _execution_store()
    if store.submission_count(challenge_id, user_id) >= int(challenge["max_final_submissions"]):
        raise HTTPException(429, "final submission limit reached")
    submission_id = f"execution-submission-{uuid4().hex[:16]}"
    result = run_policy_submission(payload.policy, submission_id)
    policy_json = payload.policy.model_dump(mode="json")
    policy_hash = hashlib.sha256(json.dumps(policy_json, sort_keys=True).encode()).hexdigest()
    try:
        store.save_submission(
            submission_id,
            challenge_id,
            user_id,
            payload.policy.schema_version,
            policy_json,
            policy_hash,
            result,
            max_final_submissions=int(challenge["max_final_submissions"]),
        )
    except ArenaPhaseError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ArenaQuotaError as exc:
        raise HTTPException(429, str(exc)) from exc
    return {
        "submission_id": submission_id,
        "public_score": result["public_score"],
        "public_metrics": result["metrics"],
        "status": "final",
        "hidden_results": "withheld_until_release",
    }


@app.post("/api/arena/execution/challenges/{challenge_id}/drafts")
def execution_save_draft(
    challenge_id: str, payload: ExecutionSubmissionRequest, request: Request
) -> dict[str, Any]:
    challenge = _execution_challenge_record(challenge_id)
    if challenge["phase"] != "public_practice":
        raise HTTPException(409, "draft editing is closed")
    _, user_id = _require_execution_student(request)
    draft_id = f"execution-draft-{uuid4().hex[:16]}"
    result = run_policy_submission(payload.policy, draft_id)
    policy_json = payload.policy.model_dump(mode="json")
    policy_hash = hashlib.sha256(json.dumps(policy_json, sort_keys=True).encode()).hexdigest()
    try:
        _execution_store().save_submission(
            draft_id,
            challenge_id,
            user_id,
            payload.policy.schema_version,
            policy_json,
            policy_hash,
            result,
            status="draft",
        )
    except ArenaPhaseError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"submission_id": draft_id, "status": "draft", "policy_hash": policy_hash}


@app.get("/api/arena/execution/challenges/{challenge_id}/submissions/me")
def execution_current_submission(challenge_id: str, request: Request) -> dict[str, Any]:
    """Recover the signed-in student's persisted submission after reload or restart."""
    _execution_challenge_record(challenge_id)
    _, user_id = _require_execution_student(request)
    store = _execution_store()
    owned = [row for row in store.submissions(challenge_id) if row["user_id"] == user_id]
    finals = [row for row in owned if row["status"] == "final"]
    drafts = [row for row in owned if row["status"] == "draft"]
    final = finals[-1] if finals else None
    draft = drafts[-1] if drafts else None
    challenge = store.challenge(challenge_id)
    practice_used = store.practice_count(challenge_id, user_id)
    return {
        "challenge_id": challenge_id,
        "practice_runs_used": practice_used,
        "practice_runs_remaining": max(0, int(challenge["max_practice_runs"]) - practice_used),
        "final": (
            {
                "submission_id": final["submission_id"],
                "created_at": final["created_at"],
                "public_score": final["public_score"],
                "status": final["status"],
                "policy": final["policy"],
            }
            if final
            else None
        ),
        "latest_draft": (
            {
                "submission_id": draft["submission_id"],
                "created_at": draft["created_at"],
                "status": draft["status"],
                "policy": draft["policy"],
            }
            if draft
            else None
        ),
    }


@app.post("/api/arena/execution/challenges/{challenge_id}/lock")
def execution_lock(challenge_id: str, payload: ExecutionPhaseRequest, request: Request) -> dict[str, Any]:
    _, actor = _require_execution_instructor(request)
    try:
        challenge = _execution_store().transition(challenge_id, actor, "submission_locked", payload.reason)
    except KeyError as exc:
        raise HTTPException(404, "execution challenge not found") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"challenge_id": challenge_id, "phase": challenge["phase"]}


@app.post("/api/arena/execution/challenges/{challenge_id}/evaluate")
def execution_evaluate(challenge_id: str, request: Request) -> dict[str, Any]:
    _, actor = _require_execution_instructor(request)
    challenge = _execution_challenge_record(challenge_id)
    if challenge["phase"] != "submission_locked":
        raise HTTPException(409, "challenge must be submission_locked before evaluation")
    store = _execution_store()
    final_submissions = {
        row["submission_id"]: ExecutionPolicySubmission.model_validate(row["policy"])
        for row in store.submissions(challenge_id)
        if row["status"] == "final"
    }
    matrix = benchmark_matrix(
        variants=tuple(challenge["hidden_worlds"]),
        student_submissions=final_submissions,
    )
    try:
        evaluation = store.save_evaluation_and_transition(
            challenge_id,
            actor,
            matrix,
            "instructor initiated evaluation",
        )
    except KeyError as exc:
        raise HTTPException(404, "execution challenge not found") from exc
    except ArenaPhaseError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {
        "evaluation_id": evaluation["evaluation_id"],
        "matrix_hash": evaluation["matrix_hash"],
        "phase": "hidden_evaluation",
        "policy_count": len(matrix["rows"]),
        "world_result_count": sum(len(row.get("world_results", [])) for row in matrix["rows"]),
    }


@app.post("/api/arena/execution/challenges/{challenge_id}/release")
def execution_release(challenge_id: str, payload: ExecutionPhaseRequest, request: Request) -> dict[str, Any]:
    _, actor = _require_execution_instructor(request)
    store = _execution_store()
    challenge = _execution_challenge_record(challenge_id)
    if challenge["phase"] != "hidden_evaluation":
        raise HTTPException(409, "challenge must be evaluated before release")
    try:
        before = store.evaluation(challenge_id)
    except KeyError as exc:
        raise HTTPException(409, "hidden evaluation is missing") from exc
    try:
        released = store.release_challenge(challenge_id, actor, payload.reason)
    except KeyError as exc:
        raise HTTPException(404, "execution challenge not found") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {
        "challenge_id": challenge_id,
        "phase": "released",
        "matrix_hash": released["matrix_hash"],
        "evaluation_unchanged": before["matrix_hash"] == released["matrix_hash"],
    }


@app.post("/api/arena/execution/challenges/{challenge_id}/archive")
def execution_archive(challenge_id: str, payload: ExecutionPhaseRequest, request: Request) -> dict[str, Any]:
    _, actor = _require_execution_instructor(request)
    try:
        challenge = _execution_store().transition(challenge_id, actor, "archived", payload.reason)
    except KeyError as exc:
        raise HTTPException(404, "execution challenge not found") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"challenge_id": challenge_id, "phase": challenge["phase"]}


@app.get("/api/arena/execution/challenges/{challenge_id}/leaderboard/public")
def execution_public_leaderboard(challenge_id: str) -> dict[str, Any]:
    challenge = _execution_challenge_record(challenge_id)
    try:
        matrix = _execution_store().evaluation(challenge_id)["matrix"]
    except KeyError:
        store = _execution_store()
        final_submissions = {
            row["submission_id"]: ExecutionPolicySubmission.model_validate(row["policy"])
            for row in store.submissions(challenge_id)
            if row["status"] == "final"
        }
        matrix = public_leaderboard_matrix(student_submissions=final_submissions)
    return {
        "challenge_id": challenge_id,
        "phase": challenge["phase"],
        "rows": [
            {
                "policy_id": row["policy_id"],
                "name": row["name"],
                "public_score": row["public_score"],
                "public_rank": (row["public_rank"] if "public_rank" in row else row["public_score_rank"]),
            }
            for row in matrix["rows"]
        ],
    }


@app.get("/api/arena/execution/challenges/{challenge_id}/leaderboard/hidden")
def execution_hidden_leaderboard(challenge_id: str, request: Request) -> dict[str, Any]:
    challenge = _execution_challenge_record(challenge_id)
    role, _ = _arena_identity(request)
    if role != "instructor" and challenge["phase"] != "released":
        raise HTTPException(403, "hidden results are withheld until release")
    try:
        evaluation = _execution_store().evaluation(challenge_id)
    except KeyError as exc:
        raise HTTPException(409, "hidden evaluation has not run") from exc
    if role == "instructor":
        return {**evaluation["matrix"], "released": challenge["phase"] == "released"}
    return {
        "challenge_id": challenge_id,
        "released": True,
        "matrix_hash": evaluation["matrix_hash"],
        "rows": _released_execution_rows(evaluation["matrix"]),
    }


@app.get("/api/arena/execution/challenges/{challenge_id}/evidence")
def execution_raw_evidence(challenge_id: str, request: Request) -> dict[str, Any]:
    _require_execution_instructor(request)
    try:
        evaluation = _execution_store().evaluation(challenge_id)
    except KeyError as exc:
        raise HTTPException(409, "hidden evaluation has not run") from exc
    return {
        "evaluation": evaluation,
        "audit_events": _execution_store().audit_events(challenge_id),
        "raw_evidence_policy": "instructor_only_even_after_release",
    }


@app.get("/api/arena/execution/submissions/{submission_id}")
def execution_submission(submission_id: str, request: Request) -> dict[str, Any]:
    role, user_id = _require_execution_session(request)
    store = _execution_store()
    try:
        submission = store.submission(submission_id)
    except KeyError as exc:
        raise HTTPException(404, "submission not found") from exc
    if role != "instructor" and submission["user_id"] != user_id:
        raise HTTPException(403, "submission belongs to another user")
    challenge = store.challenge(submission["challenge_id"])
    result = {
        "submission_id": submission_id,
        "status": submission["status"],
        "policy": submission["policy"],
        "public_score": submission["public_score"],
        "public_metrics": submission["public_result"]["metrics"],
        "hidden_results": "withheld_until_release",
    }
    if challenge["phase"] == "released":
        result["hidden_results"] = "released"
    return result


@app.post("/api/arena/execution/submissions/{submission_id}/feedback")
def execution_submission_feedback(
    submission_id: str, payload: ArenaFeedbackRequest, request: Request
) -> dict[str, Any]:
    role, user_id = _require_execution_session(request)
    store = _execution_store()
    try:
        submission = store.submission(submission_id)
    except KeyError as exc:
        raise HTTPException(404, "submission not found") from exc
    if role != "instructor" and submission["user_id"] != user_id:
        raise HTTPException(403, "submission belongs to another user")
    challenge = store.challenge(submission["challenge_id"])
    if challenge["phase"] != "released":
        return {
            "status": "withheld",
            "message": "Feedback is withheld until the instructor releases hidden evaluation.",
        }
    existing_report = store.feedback(submission_id)
    if existing_report is not None:
        return {
            **existing_report["report"],
            "report_id": existing_report["report_id"],
            "recovered_from_sqlite": True,
        }
    try:
        matrix = store.evaluation(submission["challenge_id"])["matrix"]
        feedback_matrix = deepcopy(matrix)
        if not challenge["raw_evidence_released"]:
            for row in feedback_matrix["rows"]:
                row.pop("world_results", None)
        public_replay = submission["public_result"].get("replay", {})

        def trace_ids(kind: str, values: list[Any], limit: int) -> list[str]:
            """Return a bounded, stable, de-duplicated public trace allow-list."""
            identifiers: list[str] = []
            seen: set[str] = set()
            for value in values:
                identifier = f"public.{kind}.{hashlib.sha256(str(value).encode()).hexdigest()[:16]}"
                if identifier in seen:
                    continue
                seen.add(identifier)
                identifiers.append(identifier)
                if len(identifiers) == limit:
                    break
            return identifiers

        evidence = build_execution_evidence(
            feedback_matrix,
            submission_id,
            released=True,
            policy_parameters=submission["policy"],
            event_ids=trace_ids(
                "event",
                [row.get("event_id", index) for index, row in enumerate(public_replay.get("events", []))],
                200,
            ),
            trade_ids=trace_ids(
                "trade",
                [row.get("trade_id", index) for index, row in enumerate(public_replay.get("trades", []))],
                500,
            ),
            fill_ids=trace_ids(
                "fill",
                [
                    row.get("trade_id", index)
                    for index, row in enumerate(public_replay.get("strategy_trades", []))
                ],
                500,
            ),
            replay_step_ids=trace_ids(
                "replay",
                [row.get("step", index) for index, row in enumerate(public_replay.get("evidence_rows", []))],
                500,
            ),
        )
        report = generate_execution_feedback(evidence, model=payload.model)
    except (KeyError, ValueError) as exc:
        raise HTTPException(422, f"feedback evidence is unavailable: {exc}") from exc
    response_report = {
        **report,
        "scoring_authority": "deterministic_engine",
        "evidence_scope": (
            "released_world_evidence"
            if challenge["raw_evidence_released"]
            else "released_aggregates_and_public_trace_ids"
        ),
    }
    report_id = store.save_feedback(
        submission_id,
        user_id,
        str(report["status"]),
        report.get("model"),
        response_report,
    )
    return {**response_report, "report_id": report_id, "recovered_from_sqlite": False}


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


def _arena_session_secret() -> bytes:
    configured = os.getenv("ARENA_SESSION_SECRET")
    if configured:
        encoded = configured.encode()
        if len(encoded) < 32:
            raise RuntimeError("ARENA_SESSION_SECRET must contain at least 32 bytes")
        return encoded
    if os.getenv("ARENA_DEMO_AUTH") == "1":
        # Local demo sessions remain usable without shipping a shared fallback
        # secret. They intentionally stop validating after a process restart.
        return _LOCAL_DEMO_SESSION_SECRET
    raise RuntimeError("ARENA_SESSION_SECRET is required outside local demo mode")


def _loopback_host(host: str | None) -> bool:
    if not host:
        return False
    normalized = host.strip().lower().strip("[]")
    if normalized == "localhost":
        return True
    try:
        return ip_address(normalized).is_loopback
    except ValueError:
        return False


def _arena_cookie_secure(request: Request) -> bool:
    """Default to Secure unless this is an explicit or verified local demo."""
    override = os.getenv("ARENA_COOKIE_SECURE")
    if override not in {None, "0", "1"}:
        raise RuntimeError("ARENA_COOKIE_SECURE must be either 0 or 1")
    client_host = request.client.host if request.client is not None else None
    request_host = request.url.hostname
    local_request = (client_host == "testclient" and request_host == "testserver") or (
        _loopback_host(client_host) and _loopback_host(request_host)
    )
    if override == "1":
        return True
    if override == "0" and os.getenv("ARENA_DEMO_AUTH") != "1":
        raise RuntimeError("ARENA_COOKIE_SECURE=0 is allowed only in local demo mode")
    # A false override never weakens the network boundary: non-loopback peers
    # still receive Secure cookies.
    return not local_request


def _resolved_arena_db_path() -> str:
    configured = os.getenv("ARENA_DB_PATH", "artifacts/arena.sqlite3")
    return str(Path(configured).expanduser().resolve())


@lru_cache(maxsize=_EXECUTION_STORE_CACHE_SIZE)
def _cached_execution_store(resolved_path: str) -> ArenaStore:
    store = ArenaStore(resolved_path)
    store.ensure_default_challenge(CHALLENGE_ID, list(HIDDEN_VARIANTS))
    return store


def _execution_store() -> ArenaStore:
    return _cached_execution_store(_resolved_arena_db_path())


def _execution_challenge_record(challenge_id: str) -> dict[str, Any]:
    if challenge_id != CHALLENGE_ID:
        raise HTTPException(404, "execution challenge not found")
    challenge = _execution_store().challenge(challenge_id)
    hidden_worlds = challenge["hidden_worlds"]
    if (
        not hidden_worlds
        or len(set(hidden_worlds)) != len(hidden_worlds)
        or any(world not in HIDDEN_VARIANTS for world in hidden_worlds)
    ):
        raise HTTPException(500, "stored protected-world manifest is invalid")
    return challenge


def _arena_identity_from_token(token: str) -> tuple[str, str] | None:
    if not token:
        return None
    try:
        decoded = base64.urlsafe_b64decode(token.encode()).decode()
        role, user_id, issued_at, expires_at, signature = decoded.split("|", 4)
        value = f"{role}|{user_id}|{issued_at}|{expires_at}"
        expected = hmac.new(_arena_session_secret(), value.encode(), hashlib.sha256).hexdigest()
        session = _execution_store().session(hashlib.sha256(token.encode()).hexdigest())
        if (
            not hmac.compare_digest(signature, expected)
            or role not in {"student", "instructor"}
            or int(expires_at) < int(time.time())
            or session is None
            or session["role"] != role
            or session["user_id"] != user_id
        ):
            return None
        return role, user_id
    except (binascii.Error, RuntimeError, ValueError, UnicodeDecodeError, sqlite3.Error):
        return None


def _enterprise_actor(request: Request) -> str:
    """Resolve a local actor while keeping enterprise writes auditable."""
    role, user_id = _arena_identity(request)
    if user_id != "anonymous-student":
        return user_id
    if os.getenv("ARENA_TEST_AUTH") == "1" and request.client and request.client.host == "testclient":
        return f"test-{role}"
    return "local-enterprise-demo"


def _arena_identity(request: Request) -> tuple[str, str]:
    """Resolve role from a signed cookie, never a normal client header."""
    if (
        os.getenv("ARENA_TEST_AUTH") == "1"
        and request.client is not None
        and request.client.host == "testclient"
    ):
        role = request.headers.get("X-Test-Role", "student").strip().lower()
        user_id = request.headers.get("X-Test-User", f"test-{role}").strip()
        return (role if role in {"student", "instructor"} else "student", user_id)
    identity = _arena_identity_from_token(request.cookies.get("arena_demo_session", ""))
    if identity is None:
        return "student", "anonymous-student"
    return identity


def _arena_role(request: Request) -> str:
    return _arena_identity(request)[0]


def _require_execution_session(request: Request) -> tuple[str, str]:
    identity = _arena_identity(request)
    if identity[1] == "anonymous-student":
        raise HTTPException(401, "a signed demo or institutional session is required")
    return identity


def _require_execution_student(request: Request) -> tuple[str, str]:
    identity = _require_execution_session(request)
    if identity[0] != "student":
        raise HTTPException(403, "student session is required for this endpoint")
    return identity


def _require_execution_instructor(request: Request) -> tuple[str, str]:
    identity = _require_execution_session(request)
    if identity[0] != "instructor":
        raise HTTPException(403, "instructor session is required for this endpoint")
    return identity


def _released_execution_rows(matrix: dict[str, Any]) -> list[dict[str, Any]]:
    allowed = (
        "policy_id",
        "name",
        "public_rank",
        "robustness_rank",
        "rank_movement",
        "public_score",
        "robustness_score",
        "public_shortfall_bps",
        "public_completion_pct",
        "hidden_mean_shortfall_bps",
        "hidden_worst_shortfall_bps",
        "hidden_completion_pct",
        "released_intent_aggregates",
    )
    return [{key: row[key] for key in allowed if key in row} for row in matrix["rows"]]


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
