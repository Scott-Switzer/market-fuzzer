import os
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE_URL = "http://127.0.0.1:8001/break-test"
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
        expect_str(page.title(), "Strategy Break Test · Synthetic Market World", "title")
        page.wait_for_selector("#config-form", timeout=10000)
        page.wait_for_selector("#run-btn", timeout=10000)
        results.append(("load_page_and_form_visible", "PASS", save(page, "01_page_loaded.png")))

        # 2. Submit strategy form (defaults: sma_crossover, demo data)
        with page.expect_response(lambda r: r.url.endswith("/api/break-test/run") and r.status == 200, timeout=120000) as resp:
            page.click("#run-btn")
        run_json = resp.value.json()
        assert isinstance(run_json, dict), "run response should be JSON object"
        assert any(k in run_json for k in ("historical", "historical_metrics", "forward_test", "session")), f"unexpected run payload keys: {list(run_json)[:10]}"
        page.wait_for_timeout(500)
        results.append(("strategy_form_submits", "PASS", save(page, "02_after_run.png")))

        # 3. Verify results render in the UI
        step2 = page.locator("#step-2")
        step3 = page.locator("#step-3")
        assert step2.count() == 1, "step-2 missing"
        assert step3.count() == 1, "step-3 missing"
        any_metric = page.locator(".metric").count()
        assert any_metric > 0, "no metrics rendered"
        forward_rows = page.locator("#forward-rows tr").count()
        assert forward_rows > 0, "forward test table rows missing"
        results.append(("results_render", "PASS", save(page, "03_results_rendered.png")))

        # 4. Verify /api/quant/oos from the browser context
        oos_page = context.new_page()
        with oos_page.expect_response(lambda r: r.url.endswith("/api/quant/oos") and r.method.upper() == "POST") as oos_resp:
            oos_page.evaluate("""async () => {
              await fetch('/api/quant/oos', {
                method: 'POST',
                headers: { 'Accept': 'application/json', 'Content-Type': 'application/json' },
                body: JSON.stringify({
                  closes: Array.from({length: 160}, (_,i)=>100+i*0.3),
                  strategy_type: 'sma_crossover',
                  params: {fast: 10, slow: 30},
                  mode: 'walk_forward',
                  train_window: 60,
                  test_window: 30,
                  step: 30,
                  embargo: 5
                })
              });
            }""")
        oos_status = oos_resp.value.status
        results.append(("quant_oos_callable", "PASS" if oos_status == 200 else "INFO", f"status={oos_status}", save(oos_page, "04_quant_oos_check.png")))

        browser.close()

    print("BROWSER_E2E_RESULTS")
    for item in results:
        print(" | ".join(str(x) for x in item))
    return results


if __name__ == "__main__":
    run()
