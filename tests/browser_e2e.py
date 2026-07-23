from pathlib import Path

from playwright.sync_api import sync_playwright

BASE_URL = "http://127.0.0.1:8000/break-test"
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

        # 2. Submit strategy form - click and observe status/result
        page.click("#run-btn")
        page.wait_for_timeout(2000)
        status_text = page.evaluate("() => document.getElementById('config-status')?.textContent || ''")
        last_result = page.evaluate("() => window.lastResult")
        run_ok = isinstance(last_result, dict) and bool(last_result)
        if run_ok:
            results.append(("strategy_form_submits", "PASS", save(page, "02_after_run.png")))
        else:
            results.append(
                (
                    "strategy_form_submits",
                    "FAIL",
                    f"status={status_text!r} lastResult={last_result!r}",
                    save(page, "02_after_run.png"),
                )
            )

        # 3. Verify results render in the UI if the run succeeded
        metric_count = page.evaluate("() => document.querySelectorAll('.metric').length")
        forward_rows = page.evaluate("() => document.querySelectorAll('#forward-rows tr').length")
        if run_ok and metric_count > 0 and forward_rows > 0:
            results.append(("results_render", "PASS", save(page, "03_results_rendered.png")))
        else:
            results.append(
                (
                    "results_render",
                    "FAIL",
                    f"run_ok={run_ok} metrics={metric_count} rows={forward_rows}",
                    save(page, "03_results_rendered.png"),
                )
            )

        # 4. Verify /api/quant/oos is callable via browser-side fetch if present.
        oos_result = page.evaluate("""async () => {
          try {
            const r = await fetch('/api/quant/oos', { method: 'GET', headers: { 'Accept': 'application/json' } });
            const text = await r.text();
            let body = {};
            try { body = JSON.parse(text); } catch (_) {}
            return { status: r.status, ok: r.ok, contentType: r.headers.get('content-type') || '', body };
          } catch (e) {
            return { status: null, ok: false, error: String(e) };
          }
        }""")
        status = oos_result.get("status")
        ok = oos_result.get("ok")
        body = oos_result.get("body")
        if status is None:
            results.append(("quant_oos_callable", "FAIL", f"fetch threw: {oos_result.get('error')}"))
        else:
            results.append(
                (
                    "quant_oos_callable",
                    "PASS" if ok else "INFO",
                    f"status={status} body={str(body)[:300]}",
                    save(page, "04_quant_oos_check.png"),
                )
            )

        browser.close()

    print("BROWSER_E2E_RESULTS")
    for item in results:
        print(" | ".join(str(x) for x in item))
    return results


if __name__ == "__main__":
    run()
