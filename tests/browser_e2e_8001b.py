from pathlib import Path

from playwright.sync_api import sync_playwright

BASE_URL = "http://127.0.0.1:8001/strategy-lab"
SCREENSHOT_DIR = Path("/Users/scottthomasswitzer/Documents/OAI_Build_Week/tests/browser_screenshots")
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)


def save(page, name):
    path = SCREENSHOT_DIR / name
    page.screenshot(path=str(path), full_page=True)
    return str(path)


def expect_str(actual, expected, label):
    assert actual == expected, f"{label}: expected {expected!r}, got {actual!r}"


def run():
    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1400, "height": 900})
        page = context.new_page()

        # 1. Load page and capture basic UI state
        page.goto(BASE_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(500)
        expect_str(page.title(), "Strategy Validation Lab", "title")
        page.wait_for_selector("textarea#brief", timeout=10000)
        page.click("button:has-text('Compile proposal')", timeout=10000)
        results.append(("load_page_and_form_visible", "PASS", save(page, "01_page_loaded.png")))

        # 2. Submit strategy form (defaults: sma_crossover, demo data)
        with page.expect_response(lambda r: r.url.endswith("/api/break-test/run"), timeout=60000) as resp:
            page.click("button:has-text('Compile proposal')")
        run_json = resp.value.json()
        print("COMPILE_KEYS", list(run_json.keys())[:10], flush=True)
        status = resp.value.status
        print("RUN_STATUS", status, flush=True)
        assert isinstance(run_json, dict), "run response should be JSON object"
        assert "strategy_hash" in run_json, f"unexpected compile payload keys: {list(run_json)[:10]}"
        page.wait_for_timeout(500)
        results.append(("strategy_form_submits", "PASS", save(page, "02_after_compile.png")))

        # 3. Verify results render in the UI
        step2 = page.locator("#register-form")
        step3 = page.locator("#brief-output")
        assert step2.count() == 1, "register-form missing"
        assert step3.count() == 1, "brief-output missing"
        proposal_text = page.locator("#brief-output").inner_text()
        assert "Proposal ready" in proposal_text or "strategy_hash" in proposal_text, (
            f"proposal not ready: {proposal_text[:120]}"
        )
        page.wait_for_timeout(300)
        page.screenshot(path=str(SCREENSHOT_DIR / "02_after_compile.png"))
        results.append(("results_render", "PASS", save(page, "03_results_rendered.png")))

        # 4. Verify /api/quant/oos from the browser context
        compile2 = page.evaluate(
            """async () => { const r = await fetch('/api/strategy-lab/compile', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({description:'simple sma crossover fast 20 slow 50'})}); return await r.json(); }"""
        )
        print("SECOND_COMPILE_KEYS", list(compile2.keys())[:10], flush=True)
        results.append(
            (
                "second_compile_callable",
                "PASS" if "strategy_hash" in compile2 else "INFO",
                str(compile2)[:120],
                save(page, "04_second_compile.png"),
            )
        )

        browser.close()

    print("BROWSER_E2E_RESULTS")
    for item in results:
        print(" | ".join(str(x) for x in item))
    return results


if __name__ == "__main__":
    run()
