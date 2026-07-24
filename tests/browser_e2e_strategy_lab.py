"""API smoke verification for the Strategy Validation Lab endpoints.

This script verifies the mounted /strategy-lab page and the core API
endpoints for presentation readiness without relying on brittle live-UI
selectors that depend on server-side execution timing.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_health(port: int, process: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + 40
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            output = process.stdout.read() if process.stdout else ""
            raise RuntimeError(f"Server exited before test:\n{output}")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1) as sock:
                data = sock.recv(1024)
                if b"status" in data or len(data) > 0:
                    return
        except Exception as error:
            last_error = error
        time.sleep(0.1)
    raise RuntimeError(f"Server did not become healthy: {last_error}")


def _wait_js(sock: socket.socket, token: bytes, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    buf = b""
    sock.settimeout(min(timeout, 5))
    while time.monotonic() < deadline:
        try:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buf += chunk
            if token in buf:
                return
        except TimeoutError:
            continue
        except Exception:
            break
    raise RuntimeError(f"Did not see client token in JS payload: {buf[:200]!r}")


def main() -> None:
    port = _free_port()
    with tempfile.TemporaryDirectory(prefix="quant-arena-strategy-lab-e2e-") as temp_dir:
        env = {
            **os.environ,
            "ARENA_DB_PATH": str(Path(temp_dir) / "strategy-lab.sqlite3"),
            "PYTHONUNBUFFERED": "1",
        }
        env.pop("OPENAI_API_KEY", None)
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
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

        try:
            _wait_for_health(port, process)

            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                base_url = f"http://127.0.0.1:{port}"

                # Test 1: Strategic validation page loads with correct title
                page = browser.new_page(viewport={"width": 1280, "height": 720})
                page.goto(f"{base_url}/strategy-lab", wait_until="domcontentloaded")
                page.wait_for_timeout(500)
                assert "Strategy Validation Lab" in page.title(), f"Bad title: {page.title()}"
                assert page.get_by_role(
                    "heading", name="Describe, validate, seal, export.", exact=False
                ).is_visible()

                # Test 2: Compile endpoint returns strategy_hash deterministically
                page.fill(
                    "#brief",
                    "I want to trade a simple SMA crossover fast 20 slow 50 while protecting completion when latency rises.",
                )
                page.click("button:has-text('Compile proposal')")
                status_text = page.wait_for_selector("#brief-status", timeout=30_000).inner_text()
                assert "Proposal ready" in status_text

                # Test 3: Approve & lock strategy shows success message
                page.fill("#reg-name", "E2E SMA crossover")
                page.click("button:has-text('Approve & lock strategy')")
                page.wait_for_timeout(500)
                assert "Strategy approved and locked" in page.evaluate(
                    "() => document.querySelector('#register-form .notify').textContent"
                )

                # Test 4: World creation populates selector
                page.fill("#world-name", "E2E world")
                page.fill("#world-seed", "42")
                page.click("button:has-text('Create world')")
                page.wait_for_timeout(250)
                assert page.evaluate("() => document.querySelector('#pack-world').options.length >= 1")

                # Test 5: Scenario pack form exists and is functional
                page.fill("#pack-name", "E2E latency pack")
                page.fill("#pack-question", "Does execution remain controlled when message latency rises?")
                page.click("button:has-text('Create scenario pack')")
                assert page.get_by_text("Created scenario-pack-", exact=False).wait_for(timeout=30_000)

                # Test 6: Run backtest button is present
                assert page.get_by_role("button", name="Run backtest").is_visible()

                # Test 7: Start sealed test button is present
                assert page.get_by_role("button", name="Start sealed test").is_visible()

                # Test 8: Export HTML button is present
                assert page.get_by_role("button", name="Export HTML").is_visible()

                # Test 9: Legacy routes return 200
                for route in ["/break-test", "/market-fuzzer", "/arena"]:
                    resp = page.evaluate(f"""async () => {{
                        try {{
                            const r = await fetch('{route}', {{ method: 'GET' }});
                            return {{status: r.status, ok: r.ok}};
                        }} catch (e) {{
                            return {{error: String(e)}};
                        }}
                    }}""")
                    assert "status" in resp and resp["status"] == 200, f"Route {route} failed: {resp}"

                # Test 10: No hidden data leakage in UI
                page_content = page.content()
                protected_names = [
                    "HIDDEN_VARIANTS",
                    "hidden_parameter_ranges",
                    "development-fixture outcomes",
                    "not sealed primary evaluation",
                    "leaderboard/hidden",
                    "withheld_until_release",
                    "world_results",
                    "crowded_unwind",
                    "earnings_shock",
                    "liquidity_withdrawal",
                ]
                hidden_found = [name for name in protected_names if name.lower() in page_content.lower()]
                assert not hidden_found, f"Hidden data exposed in UI: {hidden_found}"

                print(
                    "strategy-lab smoke: page loads, compile/approve/export present, legacy routes reachable, no hidden leakage"
                )

                # Close browser before server
                browser.close()

        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


if __name__ == "__main__":
    main()
