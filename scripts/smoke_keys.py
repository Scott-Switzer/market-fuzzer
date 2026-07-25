from __future__ import annotations

import os
import sys

os.environ.pop("PYTHONPATH", None)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
from fastapi.testclient import TestClient

from app.api.app import app

client = TestClient(app)
base = {
    "closes": [100.0 + i * 0.1 for i in range(260)],
    "worlds_per_regime": 10,
    "forward_mode": "gbm",
}
for case in [
    {"strategy_type": "sma_crossover"},
    {"strategy_type": "breakout"},
    {"strategy_type": "rsi_reversion"},
]:
    body = {**base, **case}
    r = client.post("/api/break-test/run", json=body)
    data = r.json()
    print(case["strategy_type"], r.status_code, sorted(data.keys()))
