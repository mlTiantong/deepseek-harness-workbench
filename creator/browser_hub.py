"""Open platform login pages without copying cookies or killing other browsers."""
import json
import os
import subprocess
from pathlib import Path
import urllib.request
import psutil
import douyin_login

ROOT=Path(__file__).resolve().parent
URLS={"xiaohongshu":"https://www.xiaohongshu.com/explore","bilibili":"https://www.bilibili.com/","douyin":"https://www.douyin.com/"}
PORT=9334

def open_platform(platform):
    if platform == "douyin":
        code=douyin_login.open_login()
        if code == 0:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                browser=p.chromium.connect_over_cdp(douyin_login.ENDPOINT,timeout=5000)
                context=browser.contexts[0]
                page=next((page for page in context.pages if page.url.startswith(URLS[platform])),None)
                if page is None:
                    page=context.new_page()
                    page.goto(URLS[platform],wait_until="domcontentloaded",timeout=12000)
                page.bring_to_front()
        return {"opened":code==0,"hint":"在抖音专用 Chrome 中完成登录，保持窗口打开，再点工作台的检查登录。" if code==0 else "抖音目录被占用或端口不可用；请手动关闭抖音专用浏览器后重试，不需要关闭所有 Chrome。"}
    if platform not in URLS:
        raise ValueError("不支持的平台")
    profile=(ROOT/"browser").resolve()
    owned=False
    for process in psutil.process_iter(["name","cmdline"]):
        try:
            args=process.info["cmdline"] or []
            if (process.info["name"] or "").lower()!="chrome.exe":
                continue
            if any(arg.startswith("--user-data-dir=") and os.path.normcase(str(Path(arg.split("=",1)[1].strip('"')).resolve()))==os.path.normcase(str(profile)) for arg in args):
                if f"--remote-debugging-port={PORT}" not in args:
                    return {"opened":False,"hint":"创作者浏览器正由旧工具占用。请在 DSH 停止当前浏览器操作、关闭专用登录窗口后再点登录。现有登录状态会保留。"}
                owned=True
                break
        except (psutil.Error,OSError):
            continue
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/version",timeout=1) as response:
            json.load(response)
        if not owned:
            raise RuntimeError("9334 端口属于其他浏览器，拒绝连接；没有操作其他账号。")
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser=p.chromium.connect_over_cdp(f"http://127.0.0.1:{PORT}",timeout=5000)
            context=browser.contexts[0]
            matches=[page for page in context.pages if page.url.startswith(URLS[platform].split('/explore')[0])]
            page=matches[0] if matches else context.new_page()
            if not matches:
                page.goto(URLS[platform],wait_until="domcontentloaded",timeout=20000)
            page.bring_to_front()
        return {"opened":True,"hint":"已复用当前专用浏览器，登录与知游操作共享同一账号目录。"}
    except (OSError,ValueError):
        pass
    # Start only this dedicated profile; never close unrelated browsers.
    subprocess.Popen([str(douyin_login.chrome_exe()),f"--user-data-dir={profile}",
                      "--remote-debugging-address=127.0.0.1",f"--remote-debugging-port={PORT}",
                      "--no-first-run","--no-default-browser-check","--new-window",URLS[platform]],
                     stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    return {"opened":True,"hint":"已打开创作者专用 Chrome；请在官方页面登录。保留窗口，登录后点检查登录核对当前账号。"}


def ensure_browser(platform):
    port=douyin_login.PORT if platform=="douyin" else PORT
    profile=douyin_login.PROFILE if platform=="douyin" else ROOT/"browser"
    owned=False
    for process in psutil.process_iter(["name","cmdline"]):
        try:
            args=process.info["cmdline"] or []
            if (process.info["name"] or "").lower()=="chrome.exe" and any(arg.startswith("--user-data-dir=") and os.path.normcase(str(Path(arg.split("=",1)[1].strip('"')).resolve()))==os.path.normcase(str(profile.resolve())) for arg in args):
                owned=f"--remote-debugging-port={port}" in args
                if not owned:
                    raise RuntimeError("专用登录目录被旧浏览器工具占用。请停止当前工具并手动关闭该专用窗口，再重试。")
                break
        except psutil.Error:
            continue
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version",timeout=1) as response:
            ready=bool(json.load(response).get("webSocketDebuggerUrl"))
        if ready and not owned:
            raise RuntimeError("调试端口属于其他浏览器，拒绝连接。")
        if ready:
            return
    except (OSError,ValueError):
        pass
    result=open_platform(platform)
    if not result.get("opened"):
        raise RuntimeError(result["hint"])
    import time
    for _ in range(40):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version",timeout=.5) as response:
                if json.load(response).get("webSocketDebuggerUrl"):
                    return
        except (OSError,ValueError):
            pass
        time.sleep(.25)
    raise RuntimeError("专用浏览器启动超时，登录状态未改变。")

if __name__=="__main__":
    import sys
    ensure_browser(sys.argv[1] if len(sys.argv)>1 else "bilibili")
