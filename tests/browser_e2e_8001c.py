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

        def on_resp(resp):
            if "/api/" in resp.url:
                print("RESP", resp.url, resp.status)

        page.on("response", on_resp)
        page.on("request", lambda req: print("REQ", req.method, req.url) if "/api/" in req.url else None)
        page.on("console", lambda msg: print("CONSOLE", msg.type, msg.text))

        page.goto(BASE_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(500)
        page.wait_for_selector("#config-form", timeout=10000)
        page.wait_for_selector("#run-btn", timeout=10000)
        results.append(("load_page_and_form_visible", "PASS", save(page, "01_page_loaded.png")))

        with page.expect_response(lambda r: True, timeout=120000) as any_resp:
            page.click("#run-btn")
        print("ANY_RESP", any_resp.value.url, any_resp.value.status, any_resp.value.text()[:200])
        results.append(("strategy_form_submits", "PASS", save(page, "02_after_run.png")))

        browser.close()

    print("BROWSER_E2E_RESULTS")
    for item in results:
        print(" | ".join(str(x) for x in item))
    return results


if __name__ == "__main__":
    run()
