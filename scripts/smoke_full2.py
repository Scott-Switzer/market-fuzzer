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
cases = [
    {"strategy_type": "sma_crossover"},
    {"strategy_type": "breakout"},
    {"strategy_type": "rsi_reversion"},
]
for i, case in enumerate(cases, 1):
    body = {**base, **case}
    r = client.post("/api/break-test/run", json=body)
    print(f"CASE {i}: {case['strategy_type']} => {r.status_code}")
    if r.status_code != 200:
        print(r.text[:400])
        continue
    data = r.json()
    print("  historical:", data["historical"])
    print(
        "  forward_test overall_loss:",
        data["forward_test"]["overall_loss_rate_pct"],
        "worlds:",
        data["forward_test"]["total_worlds"],
    )
    print("  stats:", data["forward_test"]["stats"])
    print("  failure:", data["failure_summary"])
    print("  suggestion:", data["correction_suggestion"]["rationale"])
    print("  failure_analysis keys:", sorted(data.get("failure_analysis", {}).keys()))
    print(
        "  segment labels:",
        sorted((data.get("failure_analysis") or {}).get("segmentation_insights", {}).keys())[:2],
    )

# custom python strategy smoke test
custom = {
    "closes": [100.0 + i * 0.1 for i in range(260)],
    "strategy_type": "python",
    "params": {},
    "worlds_per_regime": 10,
    "forward_mode": "gbm",
    "strategy_code": 'def strategy(obs, params):\n    return [{"action_type":"market","side":"buy","quantity":1} for _ in obs]\n',
}
r = client.post("/api/break-test/run", json=custom)
print("CUSTOM PYTHON:", r.status_code)
print(r.text[:500])
