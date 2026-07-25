from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.strategy_lab.historical.data_contracts import HistoricalDataContract
from app.strategy_lab.historical.engine import run_historical_backtest

router = APIRouter()


class HistoricalBacktestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    strategy_type: str = Field(min_length=1)
    params: dict[str, int] | None = Field(default=None)
    prices: list[list[float]] | list[float]
    contract: HistoricalDataContract
    tcost_spec: dict[str, Any] | None = Field(default=None)
    initial_capital: float = Field(default=1_000_000.0, ge=1.0)
    default_adv: float | None = Field(default=None, gt=0)
    universe: list[str] | None = Field(default=None)


class HistoricalBacktestResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    backtest_id: str
    strategy_type: str
    parameters: dict[str, int]
    contract: dict[str, Any]
    universe: list[str]
    metrics: dict[str, Any]
    equity_curve: list[float]
    trade_log: list[dict[str, Any]]
    positions: list[dict[str, Any]]
    cost_summary: dict[str, Any]


@router.post("/backtests", response_model=HistoricalBacktestResponse)
def run_backtest(body: dict[str, Any]) -> HistoricalBacktestResponse:
    try:
        payload = HistoricalBacktestRequest.model_validate(body)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc

    contract_payload = payload.contract
    if not isinstance(contract_payload, HistoricalDataContract):
        data = contract_payload.model_dump()
        contract = HistoricalDataContract(**data)
    else:
        contract = contract_payload

    try:
        report = run_historical_backtest(
            contract=contract,
            prices=payload.prices,
            strategy_type=payload.strategy_type,
            params=payload.params,
            tcost_spec=payload.tcost_spec,
            initial_capital=payload.initial_capital,
            default_adv=payload.default_adv,
            universe=payload.universe,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    from app.strategy_lab.historical.engine import backtest_report_to_dict

    return HistoricalBacktestResponse.model_validate(backtest_report_to_dict(report))


@router.get("/backtests/{backtest_id}")
def get_backtest(backtest_id: str) -> dict[str, Any]:
    raise HTTPException(status_code=501, detail="Backtest lookup not implemented for historical engine stub")
