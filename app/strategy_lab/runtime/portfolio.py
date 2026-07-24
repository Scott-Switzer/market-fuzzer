from __future__ import annotations

from typing import Any


class StrategyPortfolioEngine:
    @staticmethod
    def validate_portfolio(portfolio: dict[str, Any]) -> dict[str, Any]:
        required = {"type", "gross_exposure", "net_exposure"}
        missing = required - portfolio.keys()
        if missing:
            raise ValueError(f"missing portfolio fields: {sorted(missing)}")
        if portfolio["gross_exposure"] < abs(portfolio["net_exposure"]) - 1e-9:
            raise ValueError("gross_exposure must be >= abs(net_exposure)")
        return portfolio
