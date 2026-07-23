from __future__ import annotations

from typing import Any

from app.strategy_lab.runtime.features import StrategyFeatureEngine
from app.strategy_lab.runtime.orders import StrategyOrderEngine
from app.strategy_lab.runtime.portfolio import StrategyPortfolioEngine
from app.strategy_lab.runtime.signals import StrategySignalEngine


class StrategyRuntime:
    def __init__(self) -> None:
        self.feature_engine = StrategyFeatureEngine()
        self.signal_engine = StrategySignalEngine()
        self.portfolio_engine = StrategyPortfolioEngine()
        self.order_engine = StrategyOrderEngine()

    def validate(self, spec: dict[str, Any]) -> dict[str, Any]:
        for feature in spec.get("features", []):
            self.feature_engine.validate_feature(feature)
        for signal in spec.get("signals", []):
            self.signal_engine.validate_signal(signal)
        self.portfolio_engine.validate_portfolio(spec.get("portfolio", {}))
        self.order_engine.validate_order(spec.get("execution", {}))
        return {"status": "valid", "errors": []}
