"""Validate the externally visible contract of the container image.

The Docker ``HEALTHCHECK`` proves that the process answers. This host-side
probe also catches packaging mistakes where static pages or the primary Arena
route were omitted from the image.
"""

from __future__ import annotations

import json
import os
import urllib.request

BASE_URL = os.getenv("ARENA_BASE_URL", "http://127.0.0.1:18080").rstrip("/")


def _get(path: str) -> tuple[int, str, str]:
    with urllib.request.urlopen(f"{BASE_URL}{path}", timeout=10) as response:
        return response.status, response.headers.get_content_type(), response.read().decode()


def main() -> None:
    status, content_type, body = _get("/api/health")
    health = json.loads(body)
    assert status == 200
    assert content_type == "application/json"
    assert health["status"] == "ok"
    assert health["product"] == "Quant Challenge Arena"

    status, content_type, body = _get("/api/ready")
    ready = json.loads(body)
    assert status == 200
    assert content_type == "application/json"
    assert ready["status"] == "ready"
    assert ready["database"] == "ok"
    assert ready["artifact_store"] == "ok"

    status, _, home = _get("/")
    assert status == 200
    assert "Strategy Break Test" in home or "Synthetic Market World" in home

    status, _, arena = _get("/arena")
    assert status == 200
    assert "Quant Challenge Arena" in arena

    status, _, fuzzer = _get("/market-fuzzer")
    assert status == 200
    assert "Market Fuzzer" in fuzzer

    status, _, sealed = _get("/sealed-campaign")
    assert status == 200
    assert "Campaign lifecycle" in sealed

    status, content_type, body = _get("/api/arena/execution/challenges/trade-the-shock")
    public_challenge = json.loads(body)
    assert status == 200
    assert content_type == "application/json"
    assert public_challenge["challenge_id"] == "trade-the-shock"
    assert "liquidity_withdrawal" not in body
    assert "crowded_unwind" not in body
    assert "earnings_shock" not in body
    assert "latency_shock" not in body
    print(f"docker health smoke: {BASE_URL} primary+advanced+public-contract=pass")


if __name__ == "__main__":
    main()
