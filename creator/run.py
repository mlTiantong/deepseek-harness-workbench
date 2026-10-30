"""Start/pause/status for bounded autonomous rounds through the installed DSH CLI."""
from __future__ import annotations
import argparse
import json
import msvcrt
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import psutil
import state_store as store

ROOT = store.ROOT
BASE = ROOT.parent
PROMPT = """执行一次自主创作者日常。先调用 creator_context，回顾长期记忆与兴趣，自行决定本轮探索或创作的一个具体主题。普通浏览、自然聊天、原创发布已授权，按 POLICY.md 和账本次数执行。只操作此环境已由用户登录的账号；没有登录就记录接入需求，不反复弹窗，不编造身份或操作结果。不需要为了活跃而发帖。最多探索几个实际内容，做一个有价值的小循环；保存真实经历和下一步，然后结束。若暂停，立即结束。"""


def save_process(process: subprocess.Popen | None) -> None:
    if process is None:
        (store.STATE / "child.json").unlink(missing_ok=True)
    else:
        (store.STATE / "child.json").write_text(json.dumps({"pid": process.pid, "created": psutil.Process(process.pid).create_time()}), encoding="utf-8")


def stop_record(path: Path) -> None:
    if not path.exists():
        return
    data = json.loads(path.read_text(encoding="utf-8"))
    try:
        process = psutil.Process(data["pid"])
        if abs(process.create_time() - data["created"]) > 0.01:
            raise RuntimeError("PID was reused; refusing to stop an unrelated process")
        descendants = process.children(recursive=True)
        for child in reversed(descendants):
            try:
                child.terminate()
            except psutil.NoSuchProcess:
                pass
        process.terminate()
        _, alive = psutil.wait_procs(descendants + [process], timeout=4)
        for child in alive:
            try:
                child.kill()
            except psutil.NoSuchProcess:
                pass
    except psutil.NoSuchProcess:
        pass
    path.unlink(missing_ok=True)


def stop_child() -> None:
    stop_record(store.STATE / "child.json")


def round_once(prompt: str = PROMPT) -> bool:
    if (ROOT / "PAUSED").exists():
        print("Paused.")
        return False
    identity = store.round_begin()
    if identity is None:
        print("Daily round limit reached.")
        return False
    log = ROOT / "logs" / (store.now().replace(":", "-") + "-" + identity[:8] + ".log")
    env = os.environ.copy()
    env.update(DSH_WORKDIR=str(ROOT), DSH_PERMISSION_MODE="danger-full-access", PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    command = [str(BASE / ".venv/Scripts/python.exe"), str(BASE / "launcher.py"), "--cli", "--profile", "creator", prompt]
    with log.open("wb") as output:
        process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=output, stderr=subprocess.STDOUT,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        save_process(process)
        started = time.monotonic()
        outcome = "failed"
        try:
            while process.poll() is None:
                if (ROOT / "PAUSED").exists():
                    outcome = "paused"
                    stop_child()
                    break
                if time.monotonic() - started > store.config()["round_timeout_seconds"]:
                    outcome = "timeout"
                    stop_child()
                    break
                time.sleep(0.5)
            else:
                outcome = "completed" if process.returncode == 0 else "failed"
        except BaseException:
            stop_child()
            store.round_finish(identity, "interrupted", str(log))
            raise
        finally:
            process.wait()
            save_process(None)
    store.round_finish(identity, outcome, str(log))
    print(json.dumps({"round": identity, "status": outcome, "log": str(log)}, ensure_ascii=False))
    return outcome == "completed"


def running() -> bool:
    path = store.STATE / "supervisor.json"
    if not path.exists():
        return False
    data = json.loads(path.read_text(encoding="utf-8"))
    try:
        return abs(psutil.Process(data["pid"]).create_time() - data["created"]) < 0.01
    except psutil.NoSuchProcess:
        return False


def locked_run(loop: bool, prompt: str = PROMPT) -> int:
    store.STATE.mkdir(exist_ok=True)
    with (store.STATE / "runner.lock").open("a+b") as lock:
        if lock.tell() == 0:
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            print("Another creator runner is already active.")
            return 1
        supervisor = store.STATE / "supervisor.json"
        if loop:
            supervisor.write_text(json.dumps({"pid": os.getpid(), "created": psutil.Process().create_time()}), encoding="utf-8")
        try:
            while not (ROOT / "PAUSED").exists():
                cfg = store.config()
                hour = datetime.now(ZoneInfo(cfg["timezone"])).hour
                eligible = cfg["active_hours"][0] <= hour < cfg["active_hours"][1] and store.status()["today_rounds"] < cfg["max_rounds_per_day"]
                if not loop or eligible:
                    success = round_once(prompt)
                    if not loop:
                        return 0 if success else 1
                    if not success:
                        # Persistent failures require attention, not repeated login windows or API calls.
                        (ROOT / "PAUSED").write_text("A round failed, timed out or reached its daily limit. Inspect logs then start again.", encoding="utf-8")
                        break
                deadline = time.monotonic() + cfg["interval_seconds"]
                while time.monotonic() < deadline and not (ROOT / "PAUSED").exists():
                    time.sleep(1)
        finally:
            if loop:
                supervisor.unlink(missing_ok=True)
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["start", "loop", "once", "smoke", "pause", "status", "login", "douyin-login", "douyin-check"])
    args = parser.parse_args()
    store.STATE.mkdir(exist_ok=True)
    if store.config().get("interaction_mode") == "dsh-chat" and args.command in {"start", "loop", "once"}:
        print("知游现在运行于 DSH 原生持续会话。请在 DSH 的 creator 工作区内聊天、引导或恢复自主活动。")
        if args.command == "start":
            subprocess.run([str(BASE / ".venv/Scripts/python.exe"), str(BASE / "launcher.py")], check=True)
        return 0
    if args.command == "status":
        print(json.dumps({**store.status(), "supervisor_running": running()}, ensure_ascii=False, indent=2))
    elif args.command == "pause":
        (ROOT / "PAUSED").write_text("User paused creator.", encoding="utf-8")
        stop_child()
        stop_record(store.STATE / "supervisor.json")
        print("Paused. The active creator process tree has been stopped.")
    elif args.command == "start":
        if running():
            print("Already running.")
            return 0
        (ROOT / "PAUSED").unlink(missing_ok=True)
        output = (ROOT / "logs/supervisor.log").open("ab")
        with output:
            process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "loop"], cwd=ROOT,
                                       stdout=output, stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW)
        time.sleep(0.5)
        if process.poll() is not None:
            print("Supervisor exited during startup. Inspect creator/logs/supervisor.log.")
            return 1
        print("Started creator supervisor. Use creator.cmd status or creator.cmd pause.")
    elif args.command in {"douyin-login", "douyin-check"}:
        from douyin_login import open_login, check_login
        return open_login() if args.command == "douyin-login" else check_login()
    elif args.command == "login":
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch_persistent_context(str(ROOT / "browser"), channel="chrome", headless=False)
            for url in ["https://www.bilibili.com/", "https://www.xiaohongshu.com/explore", "https://www.douyin.com/"]:
                browser.new_page().goto(url, wait_until="domcontentloaded")
            print("Log in manually. Close this browser before starting creator. QQ/WeChat use their desktop apps.", flush=True)
            try:
                while browser.pages:
                    browser.pages[0].wait_for_timeout(1000)
            except Exception as exc:
                if "closed" not in str(exc).lower():
                    raise
    else:
        prompt = ("环境只读验收：只调用 mcp__creator__creator_context 与 creator_recall，读取人设和预算，然后用一句中文报出人设名字与平台数量。严禁浏览网页、控制桌面、发送消息、发布、修改任何文件或记忆。" if args.command == "smoke" else PROMPT)
        return locked_run(args.command == "loop", prompt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
