from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .compiler import compile_prompt
from .engine import run_scenario_battery, run_world
from .models import WorldSpec

ROOT = Path(__file__).parent
app = FastAPI(title="Counterfactual Markets", version="0.1.0")
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


class PromptRequest(BaseModel):
    prompt: str = Field(min_length=3, max_length=1000)
    seed: int = Field(default=42, ge=0, le=2_147_483_647)


class RunRequest(BaseModel):
    spec: WorldSpec


@app.get("/")
def index() -> FileResponse:
    return FileResponse(ROOT / "static" / "index.html")


@app.post("/api/compile")
def compile_world(request: PromptRequest) -> dict:
    return compile_prompt(request.prompt, request.seed).to_dict()


@app.post("/api/run")
def run(request: RunRequest) -> dict:
    return run_world(request.spec).to_dict()


@app.post("/api/battery")
def battery(request: RunRequest) -> dict:
    return run_scenario_battery(request.spec)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "product": "Counterfactual Markets"}

