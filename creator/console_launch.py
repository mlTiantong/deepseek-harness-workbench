"""Single user entry: reuse the existing DSH host, never fork a second creator."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from datetime import datetime
import urllib.request
import psutil
from douyin_login import chrome_exe

ROOT=Path(__file__).resolve().parent
BASE=ROOT.parent

def discover():
    try:
        data=json.loads((ROOT/"state/console-host.json").read_text(encoding="utf-8"))
        process=psutil.Process(data["pid"])
        if abs(process.create_time()-datetime.fromisoformat(data["startedAt"].replace("Z","+00:00")).timestamp())>300:
            return None
        if "DeepSeek Harness.exe" != process.name():
            return None
        with urllib.request.urlopen(data["origin"]+"/creator-console/",timeout=2):
            pass
        return data
    except urllib.error.HTTPError as exc:
        if exc.code==401:
            return data
    except (OSError,ValueError,KeyError,psutil.Error):
        pass
    return None

def desktop_running():
    for p in psutil.process_iter(["exe","cmdline"]):
        try:
            if p.info["exe"] and os.path.normcase(p.info["exe"])==os.path.normcase(str(BASE/"app/DeepSeek Harness.exe")) and not any(arg.startswith("--type=") or arg=="--expose-internals" for arg in p.info["cmdline"] or []):
                return True
        except psutil.Error:
            continue
    return False

def main():
    data=discover()
    if not data:
        if desktop_running():
            import ctypes
            ctypes.windll.user32.MessageBoxW(0,"知游工作台已安装。当前 DeepSeek Harness 仍运行旧配置，请保存工作后正常退出并重新打开一次，再点此入口。不会强制关闭你的会话或另起后台机器人。","知游工作台",64)
            return 0
        subprocess.Popen([str(BASE/".venv/Scripts/pythonw.exe"),str(BASE/"launcher.py")],cwd=BASE,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
        for _ in range(80):
            data=discover()
            if data:
                break
            time.sleep(.5)
    if not data:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0,"未连接到工作台服务。请打开 DeepSeek Harness 检查启动报错，再重试；不会删除登录数据。","知游工作台",48)
        return 1
    subprocess.Popen([str(chrome_exe()),"--user-data-dir="+str(ROOT/"console-window"),"--app="+data["url"],"--window-size=1360,960","--no-first-run","--no-default-browser-check"],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    return 0

if __name__=="__main__":
    raise SystemExit(main())
