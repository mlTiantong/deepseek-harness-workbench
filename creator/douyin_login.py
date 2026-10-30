"""Visible, manual login using the SAME profile as dsh-douyin-life.
Never reads passwords/OTP values, copies cookies, or changes browser security flags.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.request
import psutil

ROOT = Path(__file__).resolve().parent.parent
PROFILE = ROOT / "data/douyin-duty/browser"
PORT = 9333
ENDPOINT = f"http://127.0.0.1:{PORT}"


def debug_ready() -> bool:
    try:
        with urllib.request.urlopen(ENDPOINT + "/json/version", timeout=1) as response:
            return bool(json.load(response).get("webSocketDebuggerUrl"))
    except (OSError, ValueError):
        return False


def profile_processes() -> list[dict]:
    matches = []
    for process in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            if (process.info["name"] or "").lower() != "chrome.exe":
                continue
            args = process.info["cmdline"] or []
            for arg in args:
                if arg.startswith("--user-data-dir="):
                    actual = Path(arg.split("=", 1)[1].strip('"')).resolve()
                    if os.path.normcase(str(actual)) == os.path.normcase(str(PROFILE.resolve())):
                        matches.append({"pid": process.info["pid"], "args": args})
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            continue
    return matches


def chrome_exe() -> Path:
    candidates = [Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Google/Chrome/Application/chrome.exe",
                  Path(os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)")) / "Google/Chrome/Application/chrome.exe",
                  Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe"]
    for path in candidates:
        if path.is_file():
            return path
    raise RuntimeError("未找到 Chrome，请先安装 Chrome。")


def open_login() -> int:
    processes = profile_processes()
    if debug_ready():
        if not any(f"--remote-debugging-port={PORT}" in p["args"] for p in processes):
            print("9333 端口被其他浏览器占用；未连接，也未操作其他账号。")
            return 1
        print("抖音专用浏览器已经运行；请切到该窗口完成登录，不要反复生成二维码。")
        return 0
    if processes:
        print("抖音专用目录正在被占用。请保存工作并手动关闭抖音专用浏览器，再重试；不会关闭其他 Chrome。")
        return 1
    PROFILE.mkdir(parents=True, exist_ok=True)
    args = [str(chrome_exe()), f"--user-data-dir={PROFILE}",
            "--remote-debugging-address=127.0.0.1", f"--remote-debugging-port={PORT}",
            "--no-first-run", "--no-default-browser-check", "--new-window",
            "https://www.douyin.com/"]
    subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    for _ in range(30):
        if debug_ready():
            print("已打开抖音插件的专用可见 Chrome。请直接在网页扫码/输入验证码并完成设备验证。")
            print("登录后保留此窗口，再在 DSH 调用 dy_whoami；不要在 creator 登录窗口重复登录抖音。")
            print("调试端口仅供本机使用；不用时关闭此专用窗口。")
            return 0
        time.sleep(0.5)
    print("未检测到专用调试端口。请检查弹出的浏览器是否有配置占用提示；未删除登录数据。")
    return 1


def check_login() -> int:
    if not debug_ready() or not any(f"--remote-debugging-port={PORT}" in p["args"] for p in profile_processes()):
        print("未检测到使用抖音专用目录的 9333 浏览器。先运行 douyin-login；此检查不会自动启动或登录。")
        return 1
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(ENDPOINT, timeout=8000)
        if not browser.contexts:
            print("浏览器没有可用上下文。")
            return 1
        context = browser.contexts[0]
        # Inspect names/booleans only. Never print credential values or export storage state.
        cookies = context.cookies(["https://www.douyin.com/"])
        names = {c["name"] for c in cookies if c.get("value") and (c.get("expires", -1) == -1 or c.get("expires", 0) > time.time())}
        candidate = bool(names & {"sessionid", "sessionid_ss", "sid_tt"})
        pages = [page for page in context.pages if page.url.startswith("https://www.douyin.com/")]
        print(json.dumps({"profile": str(PROFILE), "cdp": ENDPOINT, "session_cookie_present": candidate,
                          "douyin_tab_open": bool(pages), "verified_logged_in": False,
                          "next_step": "在 DSH 调用 dy_whoami 核对登录态和当前账号；Cookie 存在不代表登录有效。"}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="抖音插件专用可见登录/只读连接检查")
    parser.add_argument("command", choices=["open", "check"], nargs="?", default="open")
    args = parser.parse_args()
    raise SystemExit(open_login() if args.command == "open" else check_login())
