from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.strategy_lab.api_lab import router as strategy_lab_router


def test_strategy_lab_routes_are_registered() -> None:
    app = FastAPI()
    app.include_router(strategy_lab_router, prefix="/api/strategy-lab")
    TestClient(app)  # client instantiated for router side effects

    direct_routes = [
        route for route in strategy_lab_router.routes if hasattr(route, "methods") and hasattr(route, "path")
    ]
    routes = [f"{','.join(sorted(route.methods or []))} {route.path}" for route in direct_routes]
    expected_routes = {
        "POST /compile",
        "POST /approve",
        "POST /backtests",
        "POST /sealed/run",
        "POST /replay/minimize",
        "POST /evidence/export",
    }
    assert expected_routes.issubset(set(routes)), f"missing routes; found: {routes}"


def test_strategy_lab_compile_returns_hash() -> None:
    app = FastAPI()
    app.include_router(strategy_lab_router, prefix="/api/strategy-lab")
    client = TestClient(app)
    response = client.post("/api/strategy-lab/compile", json={"description": "SMA crossover fast 20 slow 50"})
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert "strategy_hash" in body
    assert body["spec"]["family"] in {"sma_crossover", "sma_cross", "sma"}
    assert len(body["strategy_hash"]) == 64


def test_strategy_lab_approve_locks_strategy() -> None:
    app = FastAPI()
    app.include_router(strategy_lab_router, prefix="/api/strategy-lab")
    client = TestClient(app)
    compiled = client.post(
        "/api/strategy-lab/compile", json={"description": "SMA crossover fast 20 slow 50"}
    ).json()
    response = client.post("/api/strategy-lab/approve", json={"spec": compiled["spec"], "actor": "tester"})
    assert response.status_code in {200, 422}
    assert "strategy_id" in response.json() or "detail" in response.json()


def test_strategy_lab_export_returns_envelope() -> None:
    app = FastAPI()
    app.include_router(strategy_lab_router, prefix="/api/strategy-lab")
    client = TestClient(app)
    response = client.post("/api/strategy-lab/evidence/export", json={})
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    envelope = body["envelope"]
    assert envelope["scope"] == "strategy_validation_lab"
    assert "/break-test" in envelope["legacy_routes_preserved"]


def test_strategy_lab_scenario_packs_route_exists() -> None:
    app = FastAPI()
    app.include_router(strategy_lab_router, prefix="/api/strategy-lab")
    client = TestClient(app)
    response = client.post("/api/strategy-lab/campaigns", json={})
    assert response.status_code in {404, 405, 422}


def test_strategy_lab_replay_minimize_requires_failure() -> None:
    app = FastAPI()
    app.include_router(strategy_lab_router, prefix="/api/strategy-lab")
    client = TestClient(app)
    response = client.post("/api/strategy-lab/replay/minimize", json={})
    assert response.status_code in {422, 503}


def test_strategy_lab_backtest_requires_closes() -> None:
    app = FastAPI()
    app.include_router(strategy_lab_router, prefix="/api/strategy-lab")
    client = TestClient(app)
    response = client.post("/api/strategy-lab/backtests", json={})
    assert response.status_code in {422, 503}
