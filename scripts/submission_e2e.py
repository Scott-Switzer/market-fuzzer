#!/usr/bin/env python3
"""Playwright end-to-end verification for the /submission page.

Runs the *verified judge demo* (deterministic, offline, cached evidence) which
exercises all 8 stages with zero external dependencies, and asserts:

  1. Source watermark present on every stage.
  2. Current Git SHA present (and matches the repo HEAD prefix).
  3. Strategy-hash continuity: the same hash appears in the approval, stress,
     and evidence stages + header badge.
  4. No console errors / uncaught JS exceptions.
  5. All charts render (>=3 <svg> chart elements exist with path data).
  6. No failed API calls (no network response with status >= 400).
  7. Evidence download works (button exists, click produces a JSON download).
  8. No stale loading state (no spinner / "…"-only status left hanging).

Exit code 0 == all assertions passed.

Usage:
    python scripts/submission_e2e.py
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


def _git_head() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=ROOT
        ).stdout.strip()
    except Exception:
        return ""


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait_health(port: int, proc: subprocess.Popen) -> None:
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            out = proc.stdout.read() if proc.stdout else ""
            raise RuntimeError(f"server exited early:\n{out}")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return
        except OSError:
            time.sleep(0.2)
    raise RuntimeError("server did not become healthy")


def main() -> int:
    port = _free_port()
    head = _git_head()
    failures: list[str] = []

    with tempfile.TemporaryDirectory(prefix="fenrix-submission-e2e-") as tmp:
        env = {
            **os.environ,
            "ARENA_DB_PATH": str(Path(tmp) / "e2e.sqlite3"),
            "PYTHONUNBUFFERED": "1",
            "PYTHONNOUSERSITE": "1",
        }
        env.pop("PYTHONPATH", None)
        env.pop("OPENAI_API_KEY", None)
        proc = subprocess.Popen(
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
            _wait_health(port, proc)
            base = f"http://127.0.0.1:{port}"

            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1360, "height": 2400})

                console_errors: list[str] = []
                page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
                page.on("pageerror", lambda e: console_errors.append(str(e)))
                bad_responses: list[str] = []
                page.on(
                    "response",
                    lambda r: bad_responses.append(f"{r.status} {r.url}") if r.status >= 400 else None,
                )

                page.goto(f"{base}/submission", wait_until="domcontentloaded")
                assert "Fenrix Submission" in page.title(), f"bad title {page.title()!r}"

                # ---- run the verified judge demo ----
                page.click("#demo")
                page.wait_for_function(
                    "() => document.getElementById('status').textContent.includes('verified judge demo complete')",
                    timeout=30_000,
                )

                # (1) watermark on every stage
                # 8 pipeline stages + the "Live log" stage (no watermark) -> expect >=8 watermarks
                wms = page.query_selector_all(".watermark")
                if len(wms) < 8:
                    failures.append(f"[watermark] expected >=8 stage watermarks, got {len(wms)}")
                wm_texts = {w.inner_text() for w in wms}
                if not any("VERIFIED" in t or "cached" in t for t in wm_texts):
                    failures.append(f"[watermark] no source watermark text found: {wm_texts}")

                # (2) git SHA present + matches HEAD prefix
                sha_badge = page.inner_text("#sha-badge")
                sha_val = sha_badge.replace("git:", "").strip()
                if not sha_val or sha_val == "—":
                    failures.append("[git-sha] git SHA badge empty")
                elif head and not head.startswith(sha_val) and not sha_val.startswith(head[:12]):
                    failures.append(f"[git-sha] badge {sha_val!r} does not match HEAD {head[:12]!r}")

                # (3) strategy-hash continuity across steps
                hash_badge = page.inner_text("#hash-badge").replace("hash:", "").strip()
                if not hash_badge or hash_badge == "—":
                    failures.append("[hash] header hash badge empty")
                body_text = page.inner_text("body")
                # hash appears in approval, stress, evidence stages (short form)
                occurrences = body_text.count(hash_badge)
                if occurrences < 3:
                    failures.append(
                        f"[hash-continuity] hash {hash_badge!r} appeared {occurrences}x (<3 stages)"
                    )

                # (5) charts render: >=3 svg with path data + bar charts
                svgs = page.query_selector_all("svg")
                svg_with_paths = [s for s in svgs if s.query_selector("path")]
                if len(svg_with_paths) < 3:
                    failures.append(f"[charts] expected >=3 SVG charts with paths, got {len(svg_with_paths)}")
                bars = page.query_selector_all(".bar .fl")
                if len(bars) < 3:
                    failures.append(f"[charts] expected >=3 bar-chart bars (cost/holdings), got {len(bars)}")

                # (8) no stale loading state: no spinner element left, status not spinner-only
                if page.query_selector(".spin"):
                    failures.append("[loading] spinner still present after completion")
                status_txt = page.inner_text("#status")
                if status_txt.strip() in ("", "…") or status_txt.strip().endswith("…"):
                    failures.append(f"[loading] stale status text: {status_txt!r}")

                # (7) evidence download works
                dl_btn = page.query_selector("#evidence-download")
                if not dl_btn:
                    failures.append("[download] evidence download button missing")
                else:
                    try:
                        with page.expect_download(timeout=8000) as dl_info:
                            dl_btn.click()
                        dl = dl_info.value
                        path = dl.path()
                        size = os.path.getsize(path) if path else 0
                        if size < 100:
                            failures.append(f"[download] downloaded file too small ({size} bytes)")
                        if not dl.suggested_filename.endswith(".json"):
                            failures.append(f"[download] unexpected filename {dl.suggested_filename!r}")
                    except Exception as exc:
                        failures.append(f"[download] download failed: {exc}")

                # (4) no console errors
                if console_errors:
                    failures.append(f"[console] {len(console_errors)} console errors: {console_errors[:3]}")

                # (6) no failed API/network calls
                # (favicon 404 is browser-generated noise; ignore it)
                real_bad = [b for b in bad_responses if "favicon" not in b]
                if real_bad:
                    failures.append(f"[network] {len(real_bad)} failed responses: {real_bad[:5]}")

                browser.close()
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()

    if failures:
        print("SUBMISSION E2E: FAIL")
        for f in failures:
            print("  ✗ " + f)
        return 1
    print(
        "SUBMISSION E2E: PASS — watermark, git SHA, hash continuity, charts, "
        "no console errors, no failed API calls, evidence download, no stale loading."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
