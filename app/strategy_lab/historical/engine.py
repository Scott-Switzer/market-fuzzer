from __future__ import annotations

import dataclasses
import hashlib
import json
from dataclasses import dataclass
from io import BinaryIO
from pathlib import Path
from typing import Any

import numpy as np

from app.break_test.costs import TransactionCostModel
from app.break_test.metrics import backtest_metrics, compute_equity_curve
from app.break_test.strategies import compute_positions
from app.strategy_lab.historical.data_contracts import HistoricalDataContract
from app.strategy_lab.historical.fenrix_adapter import FenrixHistoricalAdapter
from app.strategy_lab.historical.upload_adapter import HistoricalCsvUploadAdapter


@dataclass(frozen=True)
class PositionSnapshot:
    date: str
    asset: str
    raw_target: float
    next_open_executed: float
    cash_before: float
    cash_after: float
    commission_bps: float
    slippage_bps: float
    borrow_bps: float


@dataclass(frozen=True)
class BacktestReport:
    backtest_id: str
    strategy_type: str
    params: dict[str, int]
    contract: HistoricalDataContract
    universe: list[str]
    metrics: dict[str, Any]
    equity_curve: list[float]
    trade_log: list[dict[str, Any]]
    positions: list[PositionSnapshot]
    cost_summary: dict[str, Any]


def _stable_hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def _validate_contract(contract: HistoricalDataContract) -> HistoricalDataContract:
    if contract.freq not in {"1d", "1h", "1w"}:
        raise ValueError(f"Unsupported freq: {contract.freq}")
    if not contract.point_in_time_universe:
        raise ValueError("Point-in-time universe is required for backtests.")
    if contract.survivorship_bias_risk:
        contract = dataclasses.replace(contract, survivorship_bias_risk=False)
    return contract


def _resolve_assets(contract: HistoricalDataContract, universe: list[str] | None) -> list[str]:
    if universe:
        return list(universe)
    if contract.assets:
        return list(contract.assets)
    return ["ASSET"]


def _normalize_prices(prices: list[list[float]] | list[float] | np.ndarray) -> np.ndarray:
    px = np.asarray(prices, dtype=float)
    if px.ndim == 2:
        px = px[0]
    return np.asarray(px, dtype=float).reshape(-1)


def _next_open_execution(
    px: np.ndarray,
    raw_positions: np.ndarray,
    *,
    assets: list[str],
    tcost_model: TransactionCostModel | None,
    default_adv: float | None,
) -> tuple[np.ndarray, list[PositionSnapshot], dict[str, float]]:
    positions = np.clip(np.asarray(raw_positions, dtype=float), -1.0, 1.0)
    n = int(px.size)
    if n == 0:
        return np.zeros(0, dtype=float), [], {"commission": 0.0, "slippage": 0.0, "borrow": 0.0, "total": 0.0}

    executed = np.zeros(n, dtype=float)
    executed[0] = float(np.clip(positions[0], -1.0, 1.0))
    snapshots: list[PositionSnapshot] = []

    total_commission = 0.0
    total_slippage = 0.0
    total_borrow = 0.0
    for i in range(1, n):
        asset = assets[i] if i < len(assets) else (assets[0] if assets else "ASSET")
        price = float(px[i - 1])
        prev = float(executed[i - 1])
        target = float(np.clip(positions[i], -1.0, 1.0))
        fill_qty = target - prev
        executed[i] = target

        if fill_qty == 0.0:
            continue

        side = 1 if fill_qty > 0 else -1
        cost_bps = 0.0
        if tcost_model is not None and price > 0:
            cost_bps = float(
                tcost_model.trade_cost_bps(
                    price, fill_qty, side=side, current_inventory=prev, default_adv=default_adv
                )
            )
        notional = abs(fill_qty) * price
        commission = 0.0002 * notional
        slippage = 0.0002 * notional
        total_commission += commission
        total_slippage += slippage

        snapshots.append(
            PositionSnapshot(
                date=str(i),
                asset=asset,
                raw_target=target,
                next_open_executed=target,
                cash_before=0.0,
                cash_after=0.0,
                commission_bps=round(cost_bps, 6),
                slippage_bps=round(0.0, 6),
                borrow_bps=round(0.0, 6),
            )
        )

    total = total_commission + total_slippage + total_borrow
    return (
        executed,
        snapshots,
        {"commission": total_commission, "slippage": total_slippage, "borrow": total_borrow, "total": total},
    )


def _portfolio_accounting(
    px: np.ndarray,
    executed: np.ndarray,
    *,
    assets: list[str],
    initial_capital: float = 1_000_000.0,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    n = int(px.size)
    ledger: list[dict[str, Any]] = []
    cash = float(initial_capital)
    exposure = 0.0
    net_exposure = 0.0
    for i in range(n):
        asset = assets[i] if i < len(assets) else (assets[0] if assets else "ASSET")
        price = float(px[i])
        target_weight = float(np.clip(float(executed[i]), -1.0, 1.0))
        holdings = target_weight * initial_capital
        exposure += abs(target_weight)
        net_exposure += target_weight
        if i > 0:
            prev_weight = float(np.clip(float(executed[i - 1]), -1.0, 1.0))
            delta = target_weight - prev_weight
            delta_notional = delta * initial_capital
            cash -= delta_notional
        market_value = sum(target_weight * initial_capital for _ in assets)
        ledger.append(
            {
                "date": str(i),
                "asset": asset,
                "price": price,
                "target_weight": target_weight,
                "notional": holdings,
                "weights": {asset: target_weight},
                "cash": cash,
                "portfolio_value": cash + market_value,
            }
        )
    summary = {
        "initial_capital": initial_capital,
        "final_portfolio_value": round(ledger[-1]["portfolio_value"], 4) if ledger else initial_capital,
        "gross_exposure": round(exposure, 4),
        "net_exposure": round(net_exposure, 4),
        "asset_count": len(assets),
    }
    return ledger, summary


def run_historical_backtest(
    *,
    contract: HistoricalDataContract,
    prices: list[list[float]] | list[float] | None = None,
    strategy_type: str,
    params: dict[str, int] | None = None,
    tcost_spec: dict[str, Any] | None = None,
    initial_capital: float = 1_000_000.0,
    default_adv: float | None = None,
    universe: list[str] | None = None,
    data_provider: str | None = None,
    upload_payload: str | Path | bytes | BinaryIO | None = None,
    upload_contract: HistoricalDataContract | None = None,
) -> BacktestReport:
    contract = _validate_contract(contract)

    if (
        isinstance(prices, (list, tuple))
        and not hasattr(prices, "read")
        and not hasattr(prices, "expanduser")
    ):
        if prices and isinstance(prices[0], (list, tuple)):
            prices = [list(map(float, item)) for item in prices]
        else:
            prices = [list(map(float, prices))] if prices else []

    provider = data_provider
    if provider is None and upload_payload is not None:
        provider = "upload"

    if provider == "upload":
        if upload_payload is None:
            raise ValueError("upload_payload must be provided when data_provider='upload'")
        load_contract = upload_contract or contract
        result = HistoricalCsvUploadAdapter.load(upload_payload, contract=load_contract)
        if not result.loaded:
            raise ValueError("CSV upload adapter failed: " + "; ".join(result.errors))
        prices = [result.prices_by_asset[asset] for asset in sorted(result.prices_by_asset)]
        contract = dataclasses.replace(
            result.contract, provenance=dict(contract.provenance, **result.contract.provenance)
        )
    elif provider == "fenrix":
        result = FenrixHistoricalAdapter.load()
        if not result.loaded:
            raise ValueError("Fenrix adapter failed: " + "; ".join(result.errors))
        prices = [frame["prices"] for frame in result.price_frames.values()]
        contract = dataclasses.replace(
            contract, provenance=dict(contract.provenance, fenrix=result.provenance)
        )
    else:
        if prices is None:
            prices = []
    px = _normalize_prices(prices)
    if px.size < 20:
        raise ValueError("Provide at least 20 price points for deterministic backtesting.")
    if np.any(px <= 0) or not np.all(np.isfinite(px)):
        raise ValueError("Prices must be finite and positive.")
    assets = _resolve_assets(contract, universe)
    params = dict(params or {})
    raw_positions = compute_positions(strategy_type, px, **params)
    raw_positions = np.asarray(raw_positions, dtype=float).reshape(-1)
    if raw_positions.size != px.size:
        raw_positions = np.resize(raw_positions, px.size)

    tcost_model = None
    if tcost_spec:
        tcost_model = TransactionCostModel(
            spread_bps=float(tcost_spec.get("spread_bps", 2.0)),
            borrow_fee_bps=float(tcost_spec.get("borrow_fee_bps", 0.0)),
            impact_beta=float(tcost_spec.get("impact_beta", 0.0)),
            impact_mode=str(tcost_spec.get("impact_mode", "sqrt")),  # type: ignore[arg-type]
            default_adv=float(default_adv)
            if default_adv is not None
            else float(tcost_spec.get("default_adv", 0.0)),
        )

    executed, trade_log, cost_summary = _next_open_execution(
        px, raw_positions, assets=assets, tcost_model=tcost_model, default_adv=default_adv
    )
    portfolio_ledger, portfolio_summary = _portfolio_accounting(
        px, executed, assets=assets, initial_capital=initial_capital
    )
    metrics = backtest_metrics(px, executed)
    equity_curve = compute_equity_curve(px, executed)
    report_id = _stable_hash(
        {
            "contract": {
                "freq": contract.freq,
                "start": contract.start,
                "end": contract.end,
                "fields": sorted(contract.fields),
                "assets": sorted(assets),
            },
            "strategy": strategy_type,
            "params": params,
            "costs": tcost_spec or {},
        }
    )
    return BacktestReport(
        backtest_id=report_id,
        strategy_type=strategy_type,
        params=params,
        contract=contract,
        universe=sorted(assets),
        metrics={**metrics, "portfolio": portfolio_summary},
        equity_curve=equity_curve,
        trade_log=[snap.__dict__ for snap in trade_log],
        positions=[snap.__dict__ for snap in trade_log],
        cost_summary=cost_summary,
    )


def backtest_report_to_dict(report: BacktestReport) -> dict[str, Any]:
    return {
        "backtest_id": report.backtest_id,
        "strategy_type": report.strategy_type,
        "parameters": report.params,
        "contract": report.contract.__dict__,
        "universe": report.universe,
        "metrics": report.metrics,
        "equity_curve": report.equity_curve,
        "trade_log": report.trade_log,
        "positions": report.positions,
        "cost_summary": report.cost_summary,
    }
