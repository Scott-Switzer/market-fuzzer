from __future__ import annotations

from fastapi import APIRouter

from app.strategy_lab.api.backtests import router as backtests_router
from app.strategy_lab.api.campaigns import router as campaigns_router
from app.strategy_lab.api.reports import router as reports_router
from app.strategy_lab.api.strategies import router as strategies_router

router = APIRouter()
router.include_router(strategies_router, prefix="/strategies", tags=["strategy-lab"])
router.include_router(backtests_router, prefix="/backtests", tags=["strategy-lab"])
router.include_router(campaigns_router, prefix="/campaigns", tags=["strategy-lab"])
router.include_router(reports_router, prefix="/reports", tags=["strategy-lab"])
try:
    from app.strategy_lab.api_lab import router as api_lab_router

    router.include_router(api_lab_router, prefix="", tags=["strategy-lab"])
except Exception:  # pragma: no cover - optional dependency guard
    pass
