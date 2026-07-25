"""Headless browser acceptance test for the primary Arena lifecycle.

This is intentionally a runnable script rather than a pytest fixture so the
same path works locally and in GitHub Actions. API calls use Playwright's
browser context and therefore exercise the same cookie jar and authorization
boundary as the visible pages.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from playwright.sync_api import APIResponse, Page, Response, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
CHALLENGE_ID = "trade-the-shock"
CHALLENGE_ROOT = f"/api/arena/execution/challenges/{CHALLENGE_ID}"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_health(base_url: str, process: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + 30
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            output = process.stdout.read() if process.stdout else ""
            raise RuntimeError(f"Arena server exited before browser test:\n{output}")
        try:
            with urllib.request.urlopen(f"{base_url}/api/health", timeout=1) as response:
                if response.status == 200:
                    return
        except Exception as error:  # pragma: no cover - only used during startup polling
            last_error = error
        time.sleep(0.1)
    raise RuntimeError(f"Arena server did not become healthy: {last_error}")


@contextmanager
def _arena_server() -> Iterator[str]:
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory(prefix="quant-arena-e2e-") as temp_dir:
        env = {
            **os.environ,
            "ARENA_DB_PATH": str(Path(temp_dir) / "arena.sqlite3"),
            "ARENA_DEMO_AUTH": "1",
            "ARENA_DEMO_INSTRUCTOR_CODE": "browser-e2e-instructor-code",
            "ARENA_SESSION_SECRET": "browser-e2e-secret-not-for-deployment",
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
            _wait_for_health(base_url, process)
            yield base_url
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def _body(response: APIResponse | Response) -> str:
    try:
        return response.text()
    except Exception:
        return "<unreadable response>"


def _expect(response: APIResponse | Response, status: int, label: str) -> Any:
    assert response.status == status, f"{label}: expected {status}, got {response.status}: {_body(response)}"
    return response.json()


def _assert_same_public_world(run: dict[str, Any]) -> None:
    comparison = run["comparison"]
    primary_world = run["world"]
    comparison_world = comparison["world"]
    assert primary_world["variant"] == comparison_world["variant"] == "normal"
    assert primary_world["seed"] == comparison_world["seed"] == 42
    assert primary_world["environment_hash"]
    assert primary_world["environment_hash"] == comparison_world["environment_hash"]


def _collect_console(page: Page, errors: list[str]) -> None:
    page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
    page.on("pageerror", lambda error: errors.append(str(error)))


def _assert_stress_lab_decision_states(browser: Any, base_url: str) -> None:
    """Keep optional decision evidence from breaking the operator workflow."""

    def route_handler(response_status: int, body: dict[str, Any]) -> Any:
        def fulfill(route: Any, _request: Any) -> None:
            route.fulfill(
                status=response_status,
                content_type="application/json",
                body=json.dumps(body),
            )

        return fulfill

    cases = (
        (404, {}, "Decision evidence unavailable"),
        (500, {}, "Decision evidence could not be loaded"),
        (200, {"decision_changed": True, "public_winner": {}}, "Decision evidence incomplete"),
        (None, None, "Decision changed under stress"),
    )
    for status, response_body, expected_heading in cases:
        console_errors: list[str] = []
        context = browser.new_context(base_url=base_url)
        page = context.new_page()
        _collect_console(page, console_errors)
        if status is not None:
            page.route(
                "**/api/enterprise/decision-benchmark",
                route_handler(status, response_body),
            )
        try:
            page.goto("/strategy-stress-lab", wait_until="networkidle")
            page.locator("#readiness-grid .readiness-item").first.wait_for(timeout=30_000)
            page.get_by_role("heading", name=expected_heading, exact=True).wait_for(timeout=30_000)
            assert "deterministic demo fixture" in page.locator("#decision-benchmark").inner_text().lower()
            assert page.locator("#readiness-grid .readiness-item").count() == 4
            unexpected_errors = [
                error
                for error in console_errors
                if not (status is not None and status >= 400 and error.startswith("Failed to load resource"))
            ]
            assert not unexpected_errors, "stress lab browser console errors:\n" + "\n".join(
                unexpected_errors
            )
        finally:
            context.close()


def main() -> None:
    with _arena_server() as base_url, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        console_errors: list[str] = []
        student = browser.new_context(base_url=base_url)
        instructor = browser.new_context(base_url=base_url)
        try:
            student_page = student.new_page()
            _collect_console(student_page, console_errors)
            student_page.goto("/arena", wait_until="networkidle")
            assert "Quant Challenge Arena" in student_page.title()
            student_page.get_by_role("heading", name="Can your strategy survive", exact=False).wait_for()
            student_page.get_by_role("link", name="Advanced Market Fuzzer", exact=False).wait_for()
            with student_page.expect_response(
                lambda response: response.url.endswith("/api/arena/demo-session"), timeout=30_000
            ) as student_session_response:
                student_page.get_by_role("button", name="Student demo", exact=True).click()
            _expect(student_session_response.value, 200, "create visible student session")
            student_page.get_by_text("student demo session active", exact=False).wait_for()

            public_brief = _expect(student.request.get(CHALLENGE_ROOT), 200, "load public challenge")
            assert public_brief["challenge_id"] == CHALLENGE_ID
            public_text = str(public_brief)
            public_html_response = student.request.get("/arena")
            public_js_response = student.request.get("/static/arena.js")
            assert public_html_response.status == 200
            assert public_js_response.status == 200
            public_static_text = "\n".join(
                [public_html_response.text(), public_js_response.text(), student_page.content()]
            )
            protected_names = (
                "liquidity_withdrawal",
                "crowded_unwind",
                "earnings_shock",
                "latency_shock",
            )
            for protected_name in protected_names:
                assert protected_name not in public_text
                assert protected_name not in public_static_text

            hidden_before_release = student.request.get(f"{CHALLENGE_ROOT}/leaderboard/hidden")
            assert hidden_before_release.status in {403, 409}

            aggressive_card = student_page.get_by_role("button", name="Aggressive POV", exact=False)
            assert aggressive_card.get_attribute("aria-pressed") == "true"
            comparison_selector = student_page.get_by_label("Public comparison benchmark", exact=True)
            assert comparison_selector.input_value() == "guarded_pov"
            assert not student_page.locator("#challenge-designer").is_visible()

            with student_page.expect_response(
                lambda response: response.url.endswith(f"{CHALLENGE_ROOT}/practice"),
                timeout=180_000,
            ) as practice_response:
                student_page.get_by_role("button", name="Run public practice", exact=True).click()
            first_practice = _expect(practice_response.value, 200, "visible default paired practice")
            _assert_same_public_world(first_practice)
            assert first_practice["comparison"]["policy_id"] == "guarded_pov"
            student_page.get_by_text("Public practice complete.", exact=False).wait_for(timeout=30_000)
            student_page.locator("#evidence-table table").wait_for(timeout=30_000)
            student_page.get_by_text("Same public world verified", exact=False).wait_for(timeout=30_000)
            student_page.get_by_role("table", name="Paired public decision evidence").wait_for()
            assert "seed 42" in student_page.locator("#comparison-status").inner_text().lower()
            assert (
                first_practice["world"]["environment_hash"][:10].lower()
                in student_page.locator("#comparison-status").inner_text().lower()
            )
            student_page.locator('[data-evidence-marker="order"]').first.wait_for(timeout=30_000)
            student_page.locator('[data-evidence-marker="fill"]').first.wait_for(timeout=30_000)
            assert student_page.locator("#evidence-table tbody tr").count() > 1

            max_spread = student_page.get_by_label("Maximum spread (bps)", exact=True)
            original_max_spread = int(max_spread.input_value())
            max_spread.fill(str(original_max_spread - 1))
            student_page.get_by_text("Policy changed. Run public practice", exact=False).wait_for()
            assert aggressive_card.get_attribute("aria-pressed") == "false"
            assert student_page.locator('[data-evidence-marker="order"]').count() == 0
            assert student_page.locator("#evidence-table table").count() == 0
            assert (
                "no current replay evidence" in student_page.locator("#evidence-table").inner_text().lower()
            )

            with student_page.expect_response(
                lambda response: response.url.endswith(f"{CHALLENGE_ROOT}/practice"),
                timeout=180_000,
            ) as custom_practice_response:
                student_page.get_by_role("button", name="Run public practice", exact=True).click()
            custom_practice = _expect(custom_practice_response.value, 200, "visible custom paired practice")
            _assert_same_public_world(custom_practice)
            assert custom_practice["comparison"]["policy_id"] == "guarded_pov"
            student_page.get_by_role("heading", name="Your custom configured policy", exact=True).wait_for(
                timeout=30_000
            )
            student_page.get_by_role("table", name="Paired public decision evidence").wait_for()
            decision_text = student_page.get_by_role(
                "table", name="Paired public decision evidence"
            ).inner_text()
            for label in ("Submitted orders", "Cancelled orders", "Fill records"):
                assert label in decision_text
            student_page.locator('[data-evidence-marker="order"]').first.wait_for(timeout=30_000)
            student_page.locator('[data-evidence-marker="fill"]').first.wait_for(timeout=30_000)

            with student_page.expect_response(
                lambda response: response.url.endswith(f"{CHALLENGE_ROOT}/submissions"),
                timeout=120_000,
            ) as submission_response:
                student_page.get_by_role("button", name="Submit final policy", exact=True).click()
            submission = _expect(submission_response.value, 200, "save visible final declarative policy")
            submission_id = submission["submission_id"]
            student_page.locator("#submission-status").get_by_text("Final submission", exact=False).wait_for()
            _expect(
                student.request.get(f"{CHALLENGE_ROOT}/leaderboard/public", timeout=120_000),
                200,
                "public leaderboard",
            )

            instructor_page = instructor.new_page()
            _collect_console(instructor_page, console_errors)
            instructor_page.goto("/arena", wait_until="networkidle")
            instructor_page.locator("#instructor-code").fill("browser-e2e-instructor-code")
            with instructor_page.expect_response(
                lambda response: response.url.endswith("/api/arena/demo-session"), timeout=30_000
            ) as instructor_session_response:
                instructor_page.get_by_role("button", name="Instructor demo", exact=True).click()
            _expect(instructor_session_response.value, 200, "create visible instructor session")
            instructor_page.get_by_text("instructor demo session active", exact=False).wait_for()
            instructor_page.get_by_role(
                "heading", name="Draft a qualitative challenge with bounded authority."
            ).wait_for()
            instructor_page.get_by_text("Authorized instructor allow-lists loaded", exact=False).wait_for(
                timeout=30_000
            )
            assert instructor_page.locator('input[name="design-intervention"]').count() >= 1
            with instructor_page.expect_response(
                lambda response: response.url.endswith("/api/arena/execution/challenge-designs"),
                timeout=30_000,
            ) as design_response:
                instructor_page.get_by_role("button", name="Draft with GPT-5.6", exact=True).click()
            design = _expect(design_response.value, 200, "visible instructor challenge-design draft")
            assert design["mode"] == "deterministic_fallback"
            assert design["gpt_design_available"] is False
            assert design["approval_status"] == "draft"
            assert design["numeric_worlds_created"] is False
            assert design["world_construction_authority"] == "deterministic_application_code"
            design_result = instructor_page.locator("#challenge-design-result")
            design_result.get_by_text("draft only", exact=False).wait_for()
            assert "numeric worlds created = false" in design_result.inner_text().lower()

            with instructor_page.expect_response(
                lambda response: response.url.endswith(f"{CHALLENGE_ROOT}/lock"), timeout=30_000
            ) as lock_response:
                instructor_page.get_by_role("button", name="Lock submissions", exact=True).click()
            _expect(lock_response.value, 200, "visible submission lock")

            with instructor_page.expect_response(
                lambda response: response.url.endswith(f"{CHALLENGE_ROOT}/evaluate"),
                timeout=300_000,
            ) as evaluation_response:
                instructor_page.get_by_role("button", name="Evaluate protected matrix", exact=True).click()
            evaluation = _expect(evaluation_response.value, 200, "visible hidden evaluation")
            assert evaluation["policy_count"] >= 5
            hidden = _expect(
                instructor.request.get(f"{CHALLENGE_ROOT}/leaderboard/hidden"),
                200,
                "instructor hidden leaderboard",
            )
            rows = {row["policy_id"]: row for row in hidden["rows"]}
            assert rows["aggressive_pov"]["public_rank"] == 1
            assert rows["guarded_pov"]["robustness_rank"] == 1
            assert rows["aggressive_pov"]["robustness_rank"] > 1
            assert submission_id in rows
            assert rows[submission_id]["submission_id"] == submission_id

            measured_quality = instructor_page.locator("#measured-quality")
            measured_quality.locator(".quality-check").first.wait_for(timeout=30_000)
            assert measured_quality.locator(".quality-check").count() >= 5
            quality_text = measured_quality.inner_text()
            assert "liquidity_reduces_displayed_depth" in quality_text
            assert "public mean depth" in quality_text.lower()
            assert "PASS" in quality_text or "FAIL" in quality_text

            instructor_page.get_by_role("button", name="Refresh rankings", exact=True).click()
            instructor_rankings = instructor_page.locator("#benchmark-result")
            instructor_rankings.get_by_text("Aggressive POV", exact=True).wait_for(timeout=30_000)
            instructor_rankings.get_by_text("Guarded adaptive POV", exact=True).wait_for(timeout=30_000)

            with instructor_page.expect_response(
                lambda response: response.url.endswith(f"{CHALLENGE_ROOT}/release"), timeout=30_000
            ) as release_response:
                instructor_page.get_by_role("button", name="Release allowed results", exact=True).click()
            release = _expect(release_response.value, 200, "visible release")
            assert release["evaluation_unchanged"] is True
            heatmap = instructor_page.locator("#world-heatmap")
            heatmap.locator("table").wait_for(timeout=30_000)
            heatmap_text = heatmap.inner_text()
            assert "Robustness score" in heatmap_text
            assert "Worst-world status" in heatmap_text
            assert "worst world" in heatmap_text.lower()
            assert "development-fixture outcomes" in heatmap_text.lower()
            assert "not sealed primary evaluation" in heatmap_text.lower()
            assert "% complete" in heatmap_text
            assert "Student policy" in heatmap_text
            assert measured_quality.locator(".quality-check").count() >= 5
            released = _expect(
                student.request.get(f"{CHALLENGE_ROOT}/leaderboard/hidden"),
                200,
                "released student leaderboard",
            )
            assert released["rows"]
            assert submission_id in {row["policy_id"] for row in released["rows"]}
            assert all("world_results" not in row for row in released["rows"])
            submission_view = _expect(
                student.request.get(f"/api/arena/execution/submissions/{submission_id}"),
                200,
                "released submission",
            )
            assert submission_view["submission_id"] == submission_id
            student_page.get_by_role("button", name="Refresh rankings", exact=True).click()
            student_rankings = student_page.locator("#benchmark-result")
            student_rankings.get_by_text("Aggressive POV", exact=True).wait_for(timeout=30_000)
            student_rankings.get_by_text("Guarded adaptive POV", exact=True).wait_for(timeout=30_000)
            student_page.locator("#benchmark-result table").wait_for(timeout=30_000)
            assert "Student policy" in student_page.locator("#benchmark-result").inner_text()
            assert student_page.locator("#world-heatmap table").count() == 0
            assert (
                "world-level evidence remains instructor-only"
                in student_page.locator("#world-heatmap").inner_text().lower()
            )
            with student_page.expect_response(
                lambda response: response.url.endswith(
                    f"/api/arena/execution/submissions/{submission_id}/feedback"
                ),
                timeout=30_000,
            ) as feedback_response:
                student_page.get_by_role("button", name="Explain evidence", exact=True).click()
            feedback = _expect(feedback_response.value, 200, "visible deterministic no-key feedback")
            assert feedback["mode"] == "deterministic_fallback"
            assert feedback["generated_by"] == "deterministic_template"
            assert feedback["gpt_analysis_available"] is False
            assert feedback["reason"] == "missing_api_key"
            grounded_statements = [
                *feedback["feedback"].get("statements", []),
                *feedback["feedback"].get("public_strengths", []),
                *feedback["feedback"].get("hidden_failures", []),
            ]
            assert grounded_statements
            assert all(statement["evidence_ids"] for statement in grounded_statements)
            student_page.get_by_text("Deterministic explanation", exact=False).wait_for()
            feedback_source = student_page.locator("#feedback-source")
            assert "source: deterministic fallback" in feedback_source.inner_text().lower()
            assert "no openai api key" in feedback_source.inner_text().lower()
            explanation_text = student_page.locator("#explanation").inner_text()
            for statement in grounded_statements:
                for evidence_id in statement["evidence_ids"]:
                    assert evidence_id in explanation_text

            student_page.set_viewport_size({"width": 390, "height": 844})
            student_page.goto("/arena", wait_until="networkidle")
            student_page.get_by_role("heading", name="Can your strategy survive", exact=False).wait_for()
            student_page.locator("#policy-section").scroll_into_view_if_needed()
            comparison_selector.wait_for()
            assert student_page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 2"), (
                "Arena overflowed the mobile viewport"
            )
            assert (
                student_page.locator("#policy-section").bounding_box()["width"]
                <= student_page.viewport_size["width"]
            )

            student_page.goto("/market-fuzzer", wait_until="networkidle")
            assert "Market Fuzzer" in student_page.title()
            student_page.get_by_role("heading", name="Find the market conditions", exact=False).wait_for()
            student_page.get_by_role("button", name="Start with POV example", exact=False).click()
            student_page.get_by_role("heading", name="What are we testing?", exact=True).wait_for()

            _assert_stress_lab_decision_states(browser, base_url)

            assert not console_errors, "browser console errors:\n" + "\n".join(console_errors)
            print(
                "browser e2e: independent-pair+stale-clear+replay+custom-submission+designer+"
                "lock+evaluate+quality+reversal+release+heatmap+student-aggregate+responsive+"
                "feedback+market-fuzzer+stress-lab-decision-states=pass console=clean"
            )
        finally:
            student.close()
            instructor.close()
            browser.close()


if __name__ == "__main__":
    main()
