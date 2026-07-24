#!/usr/bin/env python3
"""Record a demo of the /submission page for the Fenrix judge submission.

Produces (under artifacts/submission/<sha>/demo/):
    demo.webm                  Playwright screen recording of the full run.
    screenshots/               11 required PNG screenshots (see SHOTS below).
    narration.md               Timestamped narration script tied to the shots.
    run_manifest.json          What was recorded, git SHA, hashes, asserts.

Records the *verified judge demo* flow (deterministic, offline, cached evidence)
so the recording is reproducible and never depends on live data fetches.

Usage:
    python scripts/record_submission_demo.py
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]

# 11 required screenshots: 8 stages + overview + charts closeup + evidence closeup.
SHOTS = [
    ("01_overview", "Landing view: header badges, controls, empty stage bar."),
    ("02_stage1_strategy", "Stage 1 — Strategy: description + universe."),
    ("03_stage2_clauses", "Stage 2 — Clause review: resolved clause ledger."),
    ("04_stage3_approval", "Stage 3 — Approval: immutable strategy hash."),
    ("05_stage4_datasource", "Stage 4 — Data source: tier + provenance watermark."),
    ("06_stage5_historical", "Stage 5 — Historical: equity/benchmark, drawdown, exposure charts."),
    ("07_stage5_charts", "Stage 5 closeup — cost attribution + holdings bar charts."),
    ("08_stage6_stress", "Stage 6 — Sealed stress: regime matrix, failure rate."),
    ("09_stage7_replay", "Stage 7 — Failure replay: minimized + adjacent pass."),
    ("10_stage8_evidence", "Stage 8 — Evidence export: signed manifest + download."),
    ("11_complete", "Completed run: all 8 stage chips done, full page."),
]


def _git_head() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=ROOT
        ).stdout.strip()
    except Exception:
        return "unknown"


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


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def main() -> int:
    head = _git_head()
    sha16 = head[:16]
    demo_dir = ROOT / "artifacts" / "submission" / sha16 / "demo"
    shots_dir = demo_dir / "screenshots"
    shots_dir.mkdir(parents=True, exist_ok=True)
    video_dir = demo_dir / "_video_raw"
    video_dir.mkdir(parents=True, exist_ok=True)

    port = _free_port()
    env = {
        **os.environ,
        "ARENA_DB_PATH": str(demo_dir / "_demo.sqlite3"),
        "PYTHONUNBUFFERED": "1",
        "PYTHONNOUSERSITE": "1",
    }
    env.pop("PYTHONPATH", None)
    env.pop("OPENAI_API_KEY", None)

    narration: list[tuple[float, str, str]] = []
    t0 = time.monotonic()

    def note(shot: str, text: str) -> None:
        narration.append((round(time.monotonic() - t0, 1), shot, text))

    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    manifest: dict = {}
    try:
        _wait_health(port, proc)
        base = f"http://127.0.0.1:{port}"
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            context = browser.new_context(
                viewport={"width": 1440, "height": 900},
                record_video_dir=str(video_dir),
                record_video_size={"width": 1440, "height": 900},
            )
            page = context.new_page()
            page.goto(f"{base}/submission", wait_until="domcontentloaded")
            page.wait_for_timeout(800)

            def shot(idx: int) -> None:
                key, desc = SHOTS[idx]
                page.screenshot(path=str(shots_dir / f"{key}.png"))
                note(key, desc)

            # 01 overview
            shot(0)

            # kick off the verified judge demo
            page.click("#demo")
            # stages animate ~90ms apart; wait for completion but grab stage shots as they appear
            page.wait_for_selector("#stage-0", timeout=20_000)
            page.wait_for_function(
                "() => document.getElementById('status').textContent.includes('verified judge demo complete')",
                timeout=30_000,
            )
            page.wait_for_timeout(400)

            # 02..06 stages 1-5 (scroll each into view)
            def scroll_shot(stage_idx: int, shot_idx: int) -> None:
                page.eval_on_selector(f"#stage-{stage_idx}", "el => el.scrollIntoView({block:'center'})")
                page.wait_for_timeout(350)
                shot(shot_idx)

            scroll_shot(0, 1)   # strategy
            scroll_shot(1, 2)   # clauses
            scroll_shot(2, 3)   # approval
            scroll_shot(3, 4)   # data source
            scroll_shot(4, 5)   # historical (top)

            # 07 charts closeup: scroll to bottom of historical stage (cost/holdings)
            page.eval_on_selector("#stage-4", "el => el.scrollIntoView({block:'end'})")
            page.wait_for_timeout(350)
            shot(6)

            scroll_shot(5, 7)   # stress
            scroll_shot(6, 8)   # replay
            scroll_shot(7, 9)   # evidence

            # 11 complete: scroll to top to show all done chips
            page.eval_on_selector(".stagebar", "el => el.scrollIntoView({block:'start'})")
            page.wait_for_timeout(300)
            page.screenshot(path=str(shots_dir / f"{SHOTS[10][0]}.png"), full_page=True)
            note(SHOTS[10][0], SHOTS[10][1])

            # capture verifiable state for the manifest
            state = page.evaluate("""() => ({
                sha: document.getElementById('sha-badge').textContent,
                hash: document.getElementById('hash-badge').textContent,
                source: document.getElementById('src-badge').textContent,
                svg_charts: document.querySelectorAll('svg path').length ? document.querySelectorAll('svg').length : 0,
                stages_done: document.querySelectorAll('.chip.done').length,
                watermarks: document.querySelectorAll('.watermark').length,
                status: document.getElementById('status').textContent,
            })""")

            context.close()  # finalizes the video
            browser.close()

        # move/rename the recorded video to demo.webm
        webms = sorted(video_dir.glob("*.webm"), key=lambda p: p.stat().st_mtime, reverse=True)
        final_webm = demo_dir / "demo.webm"
        if webms:
            if final_webm.exists():
                final_webm.unlink()
            webms[0].rename(final_webm)
        for leftover in video_dir.glob("*.webm"):
            leftover.unlink()
        try:
            video_dir.rmdir()
        except OSError:
            pass

        shot_files = sorted(shots_dir.glob("*.png"))
        manifest = {
            "schema": "fenrix-submission-demo-recording/1.0",
            "generated_at": datetime.now(UTC).isoformat(),
            "git_sha": head,
            "git_sha16": sha16,
            "flow": "verified_judge_demo (deterministic, offline cached evidence)",
            "page_state": state,
            "video": {
                "path": str(final_webm.relative_to(ROOT)) if final_webm.exists() else None,
                "sha256": _sha256(final_webm) if final_webm.exists() else None,
                "bytes": final_webm.stat().st_size if final_webm.exists() else 0,
            },
            "screenshots": [
                {
                    "name": p.name,
                    "path": str(p.relative_to(ROOT)),
                    "sha256": _sha256(p),
                    "bytes": p.stat().st_size,
                }
                for p in shot_files
            ],
            "screenshot_count": len(shot_files),
            "required_screenshot_count": len(SHOTS),
            "asserts": {
                "screenshots_complete": len(shot_files) == len(SHOTS),
                "video_present": final_webm.exists(),
                "stages_done": state.get("stages_done") == 8,
                "charts_present": (state.get("svg_charts") or 0) >= 3,
                "watermarks_present": (state.get("watermarks") or 0) >= 8,
            },
        }
        (demo_dir / "run_manifest.json").write_text(json.dumps(manifest, indent=2))

        # narration.md
        lines = [
            "# Fenrix Submission — Demo Narration",
            "",
            f"- **Git SHA:** `{head}`",
            "- **Flow:** verified judge demo (deterministic, offline cached evidence)",
            f"- **Recorded:** {manifest['generated_at']}",
            f"- **Page state:** {state.get('source','')} · {state.get('hash','')} · "
            f"{state.get('stages_done')}/8 stages · {state.get('watermarks')} watermarks",
            "",
            "## Walkthrough",
            "",
            "> The submission page tells one verifiable story in eight stages. Every number",
            "> traces to a persisted artifact; the source watermark and git SHA are always visible.",
            "",
        ]
        for ts, key, desc in narration:
            lines.append(f"- **[{ts:>5.1f}s] {key}** — {desc}")
        lines += [
            "",
            "## Screenshots",
            "",
        ]
        for p in shot_files:
            lines.append(f"- `screenshots/{p.name}`")
        lines += [
            "",
            "## Verification",
            "",
            "All figures rendered by hand-rolled inline SVG (no external chart library).",
            "The evidence manifest binds the strategy hash → backtest id → campaign id → replay id,",
            "and is downloadable directly from Stage 8. Run `scripts/submission_e2e.py` to re-verify.",
        ]
        (demo_dir / "narration.md").write_text("\n".join(lines) + "\n")

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    ok = all(manifest.get("asserts", {}).values()) if manifest else False
    print(f"demo recorded → {demo_dir}")
    if manifest:
        print(f"  screenshots: {manifest['screenshot_count']}/{manifest['required_screenshot_count']}")
        print(f"  video: {manifest['video']['path']} ({manifest['video']['bytes']} bytes)")
        print(f"  asserts: {manifest['asserts']}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
