from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.compiler import compile_world
from app.experiments import run_batch, run_single
from app.schemas import WorldSpec

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_ROOT = Path("artifacts").resolve()
app = FastAPI(title="Synthetic Market World Engine", version="0.1.0")
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
JOBS: dict[str, dict] = {}


class CompileRequest(BaseModel):
    prompt: str = Field(min_length=3, max_length=2_000)
    seed: int = Field(default=42, ge=0, le=2_147_483_647)
    mode: str = Field(default="offline", pattern="^(offline|gpt)$")


class RunRequest(BaseModel):
    spec: WorldSpec


@app.get("/")
def index() -> FileResponse:
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "product": "Synthetic Market World Engine", "engine": "internal_exact_clob"}


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
    return {
        "manifest": json.loads((directory / "manifest.json").read_text()),
        "metrics": json.loads((directory / "metrics.json").read_text()),
        "realism_report": json.loads((directory / "realism_report.json").read_text()),
        "failure_surface": json.loads((directory / "failure_surface.json").read_text()),
    }


@app.get("/api/artifacts/{experiment_id}/{filename}")
def artifact_download(experiment_id: str, filename: str) -> FileResponse:
    if Path(filename).name != filename:
        raise HTTPException(400, "invalid filename")
    path = (ARTIFACT_ROOT / experiment_id / filename).resolve()
    if ARTIFACT_ROOT not in path.parents or not path.is_file():
        raise HTTPException(404, "artifact not found")
    return FileResponse(path, filename=filename)
