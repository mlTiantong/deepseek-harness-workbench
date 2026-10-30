"""Real console UI smoke check; no AI task or outbound social message."""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

ROOT=Path(__file__).resolve().parent
info=json.loads((ROOT/"state/console-host.json").read_text(encoding="utf-8"))
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True)
    page=browser.new_page(viewport={"width":1400,"height":1000})
    errors=[]; requests=[]
    page.on("pageerror",lambda error:errors.append(str(error)))
    page.on("request",lambda request:requests.append(request.url.rsplit("/",1)[-1]) if "/creator-console/api/" in request.url else None)
    page.goto(info["url"]);page.wait_for_load_state("networkidle")
    expect(page.locator('[data-check="douyin"]')).to_be_visible()
    page.evaluate("document.querySelector('#session-id').innerHTML='<option value=\"\">no session</option>'")
    with page.expect_response(lambda response:"/api/login" in response.url):
        page.locator('[data-check="douyin"]').click()
    expect(page.locator('[data-check="douyin"]')).to_be_enabled()
    assert "正在" not in page.locator("#notice").inner_text()
    page.locator("#check-all").click()
    expect(page.locator("#check-all")).to_be_enabled(timeout=30000)
    assert "task" not in requests
    page.locator('[data-view="settings"]').click()
    for name in ("reply_daily_limit","reply_per_contact_daily_limit"):
        field=page.locator(f'[name="{name}"]')
        expect(field).to_be_visible()
        assert field.get_attribute("max") is None
    page.screenshot(path=str(ROOT/"state/console-login-settings-acceptance.png"),full_page=True)
    assert not errors,errors
    browser.close()
print("PASS: direct login/all checks without AI session, visible results, configurable limits, no page errors")
