"""Regression coverage for Strategy Validation Lab lookup routes.

Historical backtest GET used to answer HTTP 501. Campaign reports used to
answer 200 with status ``not_implemented`` for every id, including missing ones.
"""

from __future__ import annotations

import numpy as np
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.strategy_lab.api import campaigns as campaigns_module
from app.strategy_lab.api.backtests import clear_backtest_store
from app.strategy_lab.api.backtests import router as backtests_router
from app.strategy_lab.api.reports import router as reports_router


def _prices(length: int = 80) -> list[float]:
    rng = np.random.default_rng(7)
    return (100 * np.exp(np.cumsum(rng.normal(0.0002, 0.01, length)))).tolist()


def test_backtest_lookup_returns_stored_report_and_404_when_missing() -> None:
    clear_backtest_store()
    app = FastAPI()
    app.include_router(backtests_router)
    client = TestClient(app)
    missing = client.get("/backtests/does-not-exist")
    assert missing.status_code == 404
    assert missing.json()["detail"] == "backtest not found"

    created = client.post(
        "/backtests",
        json={
            "strategy_type": "sma_crossover",
            "prices": _prices(),
            "contract": {"freq": "1d", "start": "2024-01-01", "end": "2024-12-31"},
        },
    )
    assert created.status_code == 200
    body = created.json()
    backtest_id = body["backtest_id"]
    fetched = client.get(f"/backtests/{backtest_id}")
    assert fetched.status_code == 200
    assert fetched.json() == body
    assert "501" not in str(fetched.status_code)


def test_campaign_report_uses_public_registry_or_404() -> None:
    app = FastAPI()
    app.include_router(reports_router)
    client = TestClient(app)
    missing = client.get("/campaigns/missing-campaign/report")
    assert missing.status_code == 404

    campaign_id = "campaign-report-regression"
    campaigns_module._campaign_registry[campaign_id] = {
        "campaign_id": campaign_id,
        "state": "baseline",
        "public_document": {"commitment_digest": "public-only"},
        "baseline_result": {"evaluated": 1, "failures": 0},
        "broad_search_evidence": None,
        "targeted_search_evidence": None,
        "failure_confirmation": None,
        "evaluation_evidence": None,
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
        "world_counts": {"same_family": 1, "holdout": 1},
        "strategy_backend": "deterministic_product_fixture",
    }
    try:
        found = client.get(f"/campaigns/{campaign_id}/report")
        assert found.status_code == 200
        payload = found.json()
        assert payload["status"] == "available"
        assert payload["state"] == "baseline"
        assert payload["baseline_result"]["evaluated"] == 1
        assert payload["status"] != "not_implemented"
        assert "secret_seed_material_hex" not in payload
    finally:
        campaigns_module._campaign_registry.pop(campaign_id, None)
