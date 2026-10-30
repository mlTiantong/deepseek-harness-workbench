"""Local UI acceptance only: no site login, incoming DM or outbound send is exercised."""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parent
info=json.loads((ROOT/"state/console-host.json").read_text(encoding="utf-8"))
with sync_playwright() as p:
    browser=p.chromium.launch(channel="chrome",headless=True)
    context=browser.new_context(viewport={"width":1400,"height":1000})
    page=context.new_page();errors=[]
    page.on("pageerror",lambda e:errors.append(str(e)))
    page.goto(info["url"].split("#")[0]);page.wait_for_load_state("networkidle")
    page.goto(info["origin"]+"/creator-console/");page.wait_for_load_state("networkidle")
    page.get_by_text("本机已连接",exact=True).wait_for()
    assert page.locator(".platform-grid .card").count()==5
    assert page.locator("#session-id option").count()>=1
    assert not page.locator("#session-id").input_value()==""
    page.screenshot(path=str(ROOT/"state/console-accounts.png"),full_page=True)
    page.get_by_role("button",name="私信中心",exact=True).click()
    page.get_by_role("heading",name="各站私信 · 统一处理",exact=True).wait_for()
    assert page.get_by_text("还没有同步到真实私信",exact=True).is_visible()
    page.screenshot(path=str(ROOT/"state/console-inbox.png"),full_page=True)
    page.get_by_role("button",name="创作与设置",exact=True).click()
    page.get_by_role("heading",name="跨站私信回复",exact=True).wait_for()
    initial=page.locator('[name="reply_daily_limit"]').input_value()
    page.locator('[name="reply_daily_limit"]').fill(initial)
    page.get_by_role("button",name="保存设置",exact=True).click()
    page.get_by_text("设置已保存。私信回复规则由统一账本执行，账号未核验时不发送。",exact=True).wait_for()
    page.screenshot(path=str(ROOT/"state/console-settings.png"),full_page=True)
    page.get_by_role("button",name="与知游对话",exact=True).click()
    frame=page.frame_locator("#native-chat")
    frame.locator("#root").wait_for()
    page.wait_for_timeout(1500)
    assert page.locator("#native-chat").get_attribute("src")=="/"
    # Exercise only the existing local onboarding UI; do not send a model prompt.
    proceed=frame.get_by_role("button",name="继续",exact=True)
    if proceed.is_visible():
        proceed.click()
    frame.get_by_text("知游 · 自主生活与实时引导",exact=False).first.click()
    page.wait_for_timeout(1000)
    page.screenshot(path=str(ROOT/"state/console-chat.png"),full_page=True)
    assert not errors,errors
    request=context.request
    invalid=request.post(info["origin"]+"/creator-console/api/settings",data={"bad":"value"},headers={"X-Creator-Console":"1"})
    assert invalid.status==400
    missing=request.post(info["origin"]+"/creator-console/api/settings",data={"reply_enabled":False})
    assert missing.status==403
    anonymous=browser.new_context()
    assert anonymous.request.get(info["origin"]+"/creator-console/").status==401
    anonymous.close()
    page.get_by_role("button",name="账号连接",exact=True).click()
    page.set_viewport_size({"width":540,"height":980})
    assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
    page.screenshot(path=str(ROOT/"state/console-mobile.png"),full_page=True)
    browser.close()
print("PASS: 5 account cards; real session list; inbox; settings save; native chat iframe; auth/CSRF checks; mobile layout; no JS errors.")
