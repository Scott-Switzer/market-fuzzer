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


def run():
    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1400, "height": 900})
        page = context.new_page()

        def on_console(msg):
            text = msg.text
            print('CONSOLE', msg.type, text)
            lowered = text.lower()
            if 'polygon' in lowered or 'points:' in lowered:
                return
            console_errors.append(text)

        page.on('console', on_console)
        page.goto(BASE_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(500)
        page.wait_for_selector("#config-form", timeout=10000)
        page.wait_for_selector("#run-btn", timeout=10000)
        results.append(("load_page_and_form_visible", "PASS", save(page, "01_page_loaded.png")))

        page.select_option("#data-source", "demo")
        page.select_option("#strategy-type", "sma_crossover")
        page.fill('input[data-key="fast"]', '10')
        page.fill('input[data-key="slow"]', '30')
        with page.expect_response(lambda r: r.url.endswith("/api/break-test/run") and r.status == 200, timeout=180000) as resp:
            page.click("#run-btn")
        run_json = resp.value.json()
        assert isinstance(run_json, dict) and any(k in run_json for k in ("historical", "equity_curve", "forward_test")), run_json.keys()
        page.wait_for_timeout(500)
        results.append(("strategy_form_submits", "PASS", save(page, "02_after_run.png")))

        step2 = page.locator("#step-2")
        step3 = page.locator("#step-3")
        assert step2.count() == 1 and step3.count() == 1
        assert page.locator(".metric").count() > 0
        assert page.locator("#forward-rows tr").count() > 0
        results.append(("results_render", "PASS", save(page, "03_results_rendered.png")))
        # 4. Verify /api/quant/oos via direct HTTP from the same process (proves backend; browser fetch link is separately testable)
        import httpx
        oos_r = httpx.post('http://127.0.0.1:8001/api/quant/oos', json={
            'closes': [100 + i*0.5 for i in range(200)],
            'strategy_type': 'sma_crossover',
            'params': {'fast': 10, 'slow': 30},
            'mode': 'walk_forward',
            'train_window': 60,
            'test_window': 40,
            'step': 40,
            'embargo': 5
        }, timeout=60)
        print('oos_status', oos_r.status_code, flush=True)
        assert oos_r.status_code == 200
        results.append(("quant_oos_callable", "PASS", None))
    for item in results:
        print(" | ".join(str(x) for x in item))
    return results


if __name__ == "__main__":
    run()
