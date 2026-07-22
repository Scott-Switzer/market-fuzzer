from __future__ import annotations
import os, sys
os.environ.pop("PYTHONPATH", None)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
from fastapi.testclient import TestClient
from app.api.app import app
client = TestClient(app)
body = {
    "closes": [100.0 + i * 0.1 for i in range(260)],
    "strategy_type": "sma_crossover",
    "params": {"fast": 20, "slow": 50},
    "worlds_per_regime": 5,
    "forward_mode": "gbm",
}
response = client.post("/api/break-test/run", json=body)
print("status:", response.status_code)
print(response.text)
