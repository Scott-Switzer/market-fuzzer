from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.strategy_lab.compiler.interface import StrategyCompiler
from app.strategy_lab.runtime.compiler import StrategyRuntime

router = APIRouter()
_compiler = StrategyCompiler()
_runtime = StrategyRuntime()


@router.post("/strategies/compile")
def compile_strategy(body: dict[str, Any]) -> dict[str, Any]:
    raw_text = body.get("description", "")
    resolution_overrides = body.get("resolution_overrides")
    compiled = _compiler.compile(raw_text, resolution_overrides)
    return {"ok": True, **compiled}


@router.post("/strategies/validate")
def validate_spec(spec: dict[str, Any]) -> dict[str, Any]:
    result = _runtime.validate(spec)
    return result
