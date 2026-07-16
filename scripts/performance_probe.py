"""Collect local development timings without enforcing an unstable SLA."""

from __future__ import annotations

import json
import os
import platform
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from statistics import median
from typing import Any

from playwright.sync_api import sync_playwright

from app.execution_arena import (
    CHALLENGE_ID,
    HIDDEN_VARIANTS,
    SEEDS,
    benchmark_matrix,
    run_execution_challenge,
)
from app.execution_feedback import build_execution_evidence, generate_execution_feedback
from app.execution_store import ArenaStore

ROOT = Path(__file__).resolve().parents[1]


def _milliseconds(operation: Callable[[], Any], repeats: int = 1) -> tuple[float, Any]:
    measurements: list[float] = []
    result: Any = None
    for _ in range(repeats):
        started = time.perf_counter()
        result = operation()
        measurements.append((time.perf_counter() - started) * 1_000)
    return median(measurements), result


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _startup_and_browser_load(database: Path) -> tuple[float, float]:
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    env = {**os.environ, "ARENA_DB_PATH": str(database), "ARENA_DEMO_AUTH": "1"}
    env.pop("OPENAI_API_KEY", None)
    started = time.perf_counter()
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(f"{base_url}/api/health", timeout=1) as response:
                    if response.status == 200:
                        break
            except Exception:  # pragma: no cover - startup polling
                time.sleep(0.05)
        else:
            raise RuntimeError("application startup probe timed out")
        startup_ms = (time.perf_counter() - started) * 1_000
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            loaded = time.perf_counter()
            page.goto(base_url, wait_until="networkidle")
            page.get_by_role("heading", name="Can your strategy survive", exact=False).wait_for()
            browser_load_ms = (time.perf_counter() - loaded) * 1_000
            browser.close()
        return startup_ms, browser_load_ms
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def main() -> None:
    public_ms, public_result = _milliseconds(
        lambda: run_execution_challenge("aggressive_pov", "normal", 42), repeats=3
    )

    def one_policy_hidden() -> list[dict[str, Any]]:
        return [
            run_execution_challenge("aggressive_pov", world, seed)
            for world in HIDDEN_VARIANTS
            for seed in SEEDS
        ]

    one_policy_ms, hidden_runs = _milliseconds(one_policy_hidden)
    matrix_ms, matrix = _milliseconds(benchmark_matrix)

    with tempfile.TemporaryDirectory(prefix="quant-arena-performance-") as temp_dir:
        database = Path(temp_dir) / "arena.sqlite3"
        store = ArenaStore(database)
        store.ensure_default_challenge(CHALLENGE_ID, list(HIDDEN_VARIANTS))

        def sqlite_round_trip() -> dict[str, Any]:
            run_id = f"performance-{time.time_ns()}"
            store.save_practice(
                run_id,
                CHALLENGE_ID,
                "performance-user",
                "performance-policy-hash",
                42,
                float(public_result["public_score"]),
                public_result,
            )
            store.audit(CHALLENGE_ID, "performance-user", "performance_probe", {"run_id": run_id})
            return store.challenge(CHALLENGE_ID)

        sqlite_ms, _ = _milliseconds(sqlite_round_trip, repeats=5)
        startup_ms, browser_load_ms = _startup_and_browser_load(database)

    evidence = build_execution_evidence(matrix, "aggressive_pov", released=True)
    prior_key = os.environ.pop("OPENAI_API_KEY", None)
    try:
        feedback_ms, feedback = _milliseconds(lambda: generate_execution_feedback(evidence), repeats=5)
    finally:
        if prior_key is not None:
            os.environ["OPENAI_API_KEY"] = prior_key

    report = {
        "measured_at": datetime.now(UTC).isoformat(),
        "environment": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "processor": platform.processor() or "not reported",
            "code_commit": matrix["provenance"]["code_commit"],
        },
        "measurements_ms": {
            "application_startup_to_healthy": round(startup_ms, 2),
            "public_practice_median_of_3": round(public_ms, 2),
            "one_policy_hidden_matrix": round(one_policy_ms, 2),
            "full_benchmark_matrix": round(matrix_ms, 2),
            "sqlite_practice_write_audit_read_median_of_5": round(sqlite_ms, 2),
            "gpt_no_key_fallback_median_of_5": round(feedback_ms, 2),
            "browser_initial_load_network_idle": round(browser_load_ms, 2),
        },
        "workload": {
            "public_seed": 42,
            "hidden_world_count": len(HIDDEN_VARIANTS),
            "hidden_seeds": list(SEEDS),
            "one_policy_hidden_runs": len(hidden_runs),
            "full_matrix_policy_count": len(matrix["rows"]),
            "matrix_hash": matrix["provenance"]["matrix_hash"],
            "feedback_mode": feedback["mode"],
        },
        "claim_boundary": "Local development evidence only; not a production benchmark or SLA.",
    }
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
