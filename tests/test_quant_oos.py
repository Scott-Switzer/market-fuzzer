from __future__ import annotations

import os

from fastapi.testclient import TestClient

from app.api.app import app
from app.break_test.oos_validation import (
    combinatorial_purged_cross_validation,
    walk_forward_validation,
)

_client = TestClient(app)
_live_base_url = os.environ.get("BREAK_TEST_LIVE_URL", "http://127.0.0.1:8000")


def test_walk_forward_validation_returns_summary():
    closes = [100 + i * 0.25 for i in range(300)]
    result = walk_forward_validation(closes, "sma_crossover", {"fast": 10, "slow": 30}, train_window=60, test_window=20, step=20, embargo=2)
    assert "folds" in result
    assert "oos_sharpe" in result
    assert "note" in result


def test_cpcv_validation_returns_summary():
    closes = [100 + i * 0.3 for i in range(300)]
    result = combinatorial_purged_cross_validation(closes, "sma_crossover", {"fast": 10, "slow": 30}, embargo=2, nested=False)
    assert "folds" in result
    assert "blocks" in result


def test_oos_endpoint_returns_summary_via_test_client():
    payload = {
        "closes": [100 + i * 0.25 for i in range(300)],
        "strategy_type": "sma_crossover",
        "params": {"fast": 10, "slow": 30},
        "mode": "walk_forward",
        "train_window": 60,
        "test_window": 20,
        "step": 20,
        "embargo": 2,
        "anchored": False,
        "regime_aware": False,
        "adversarial": False,
        "adversarial_seed": 42,
        "worlds_per_regime": 10,
    }
    r = _client.post("/api/quant/oos", json=payload)
    assert r.status_code == 200, r.text
    data = r.json()
    assert "folds" in data
    assert "oos_sharpe" in data
    assert "note" in data


def test_live_oos_endpoint_returns_summary_or_skip():
    try:
        import httpx
    except ImportError:
        import pytest
        pytest.skip("httpx not available")

    client = httpx.Client(base_url=_live_base_url, timeout=30)
    try:
        payload = {
            "closes": [100 + i * 0.25 for i in range(300)],
            "strategy_type": "sma_crossover",
            "params": {"fast": 10, "slow": 30},
            "mode": "walk_forward",
            "train_window": 60,
            "test_window": 20,
            "step": 20,
            "embargo": 2,
            "anchored": False,
            "regime_aware": False,
            "adversarial": False,
            "adversarial_seed": 42,
            "worlds_per_regime": 10,
        }
        r = client.post("/api/quant/oos", json=payload)
        if r.status_code == 404:
            import pytest
            pytest.skip("live server not running this app")
        assert r.status_code == 200, r.text
        data = r.json()
        assert "folds" in data
        assert "oos_sharpe" in data
        assert "note" in data
    finally:
        client.close()
