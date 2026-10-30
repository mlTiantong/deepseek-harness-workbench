"""Programmatic, read-only login verification; never invokes an AI session."""
import os
from pathlib import Path
from urllib.parse import urlparse
import psutil
import browser_hub
import douyin_login


def result(status, evidence, account=""):
    return {"status": status, "account": account, "evidence": evidence}


def owned_browser(port, profile):
    for process in psutil.process_iter(["name", "cmdline"]):
        try:
            args = process.info["cmdline"] or []
            if (process.info["name"] or "").lower() != "chrome.exe":
                continue
            if f"--remote-debugging-port={port}" in args and any(
                arg.startswith("--user-data-dir=") and
                os.path.normcase(str(Path(arg.split("=", 1)[1].strip(chr(34))).resolve())) == os.path.normcase(str(profile.resolve()))
                for arg in args):
                return True
        except (psutil.Error, OSError):
            continue
    return False


def inspect_page(platform, page):
    if platform == "bilibili":
        data = page.evaluate("""async()=>{const c=new AbortController();const t=setTimeout(()=>c.abort(),6000);try{const r=await fetch('https://api.bilibili.com/x/web-interface/nav',{credentials:'include',signal:c.signal});return await r.json()}finally{clearTimeout(t)}}""")
        item = data.get("data") or {}
        if data.get("code") == 0 and item.get("isLogin") and item.get("mid"):
            return result("verified", "B站官方 nav 接口确认当前账号：" + str(item.get("uname") or item["mid"]), str(item["mid"]))
        if data.get("code") == -101 or item.get("isLogin") is False:
            return result("logged_out", "B站官方接口返回未登录。")
        return result("unknown", "B站接口未返回可确认的账号状态，请稍后重试。")
    if platform == "xiaohongshu":
        user = page.evaluate("""()=>{const u=window.__INITIAL_STATE__?.user;const i=u?.userInfo?.value||u?.userInfo?._value||u?.userInfo;return i?{id:i.userId||i.user_id||'',name:i.nickname||'',guest:i.guest}:null}""")
        if user and user.get("id") and user.get("guest") is False:
            return result("verified", "小红书页面当前用户状态确认：" + (user.get("name") or user["id"]), str(user["id"]))
    login = page.get_by_text("登录", exact=True)
    if any(login.nth(i).is_visible() for i in range(login.count())):
        return result("logged_out", "平台页面显示可见的登录按钮，请在官方页面完成登录。")
    # Only a current-account profile identity can confirm login, never generic avatars or cookies.
    if platform == "douyin":
        user = page.evaluate(r"""()=>{const h=document.querySelector('header');if(!h)return null;const a=[...h.querySelectorAll('a[href*="/user/"]')].find(a=>!/\/user\/self(?:[?/#]|$)/.test(a.href)&&a.querySelector('img'));return a?{id:new URL(a.href).pathname.split('/').pop(),name:a.innerText.trim()||a.querySelector('img')?.alt||''}:null}""")
        if user and user.get("id"):
            return result("verified", "抖音页面当前账号头像与个人主页确认：" + (user.get("name") or user["id"]), user["id"])
    return result("unknown", "页面未返回可确认的当前账号信息；未将 Cookie、通用头像或个人入口当作已登录。")


def check_platform(platform):
    if platform in {"qq", "wechat"}:
        return result("unavailable", "尚未接入官方桌面客户端的程序化登录核验；未调用 AI，请在客户端确认。")
    if platform not in browser_hub.URLS:
        raise ValueError("无效平台")
    port, profile = (douyin_login.PORT, douyin_login.PROFILE) if platform == "douyin" else (browser_hub.PORT, browser_hub.ROOT / "browser")
    if not owned_browser(port, profile):
        return result("unavailable", "未找到该平台的专用浏览器，请先点击登录 / 打开平台；已有登录数据不会删除。")
    from playwright.sync_api import sync_playwright
    try:
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}", timeout=5000)
            context = browser.contexts[0]
            host = urlparse(browser_hub.URLS[platform]).hostname
            page = next((x for x in context.pages if urlparse(x.url).hostname == host), None)
            if page is None:
                return result("unknown", "专用浏览器已打开，但未找到该平台页面，请先打开平台。")
            return inspect_page(platform, page)
    except Exception as exc:
        return result("unavailable", "程序核验未完成（" + type(exc).__name__ + "）；请确认平台页面已加载后重试。")


def check_and_record(platform):
    import console_store
    value = check_platform(platform)
    console_store.report_platform(platform, value["status"], value["account"], value["evidence"])
    return value
