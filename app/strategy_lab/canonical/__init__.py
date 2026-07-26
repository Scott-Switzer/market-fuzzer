"""Canonical product-path application services (Phase 2.5 cutover).

Dependency direction:
    FastAPI router (canonical/router.py)
      -> canonical application service (compilation/approval/backtest/campaign/evidence)
      -> domain / repository / strategy pipeline

The legacy planner / DSL / approval-service flow is NOT used on this path.
"""

from __future__ import annotations

from app.strategy_lab.canonical.router import router

__all__ = ["router"]
