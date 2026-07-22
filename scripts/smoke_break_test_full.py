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
data = response.json()
print("historical:", data.get('historical'))
print("forward_test keys:", list(data.get('forward_test', {}).keys())[:5])
print("forward_test overall_loss_rate_pct:", data.get('forward_test', {}).get('overall_loss_rate_pct'))
print("forward_test total_worlds:", data.get('forward_test', {}).get('total_worlds'))
print("failure_summary:", data.get('failure_summary'))
print("suggestion keys:", list(data.get('correction_suggestion', {}).keys()))
