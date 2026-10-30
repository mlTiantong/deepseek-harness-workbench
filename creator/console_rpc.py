"""Allowlisted JSON RPC for the host plugin. Never accepts shell/code/path inputs."""
import contextlib
import io
import json
import sys
from pathlib import Path
import console_store as console
import state_store as store

def call(method, payload):
    if method == "snapshot":
        return console.snapshot()
    if method == "settings":
        return console.save_settings(payload)
    if method == "pause":
        (store.ROOT / "PAUSED").write_text("用户在统一工作台暂停自主活动", encoding="utf-8")
        return {"paused": True, "note": "本地动作闸门已暂停；原生唤醒收到后结束，不自动恢复。"}
    if method == "check_login" or (method == "login" and payload.get("action") == "check"):
        platform=payload.get("platform")
        from login_check import check_and_record
        return check_and_record(platform)
    if method == "login":
        platform=payload.get("platform")
        if platform not in console.PLATFORMS:
            raise ValueError("无效平台")
        if platform in {"qq","wechat"}:
            return {"manual":True,"hint":"请在已安装的官方桌面客户端登录；当前未接入程序化客户端登录核验，不读取账号凭据，也不调用 AI。"}
        from browser_hub import open_platform
        return open_platform(platform)
    raise ValueError("未知 RPC 方法")

if __name__ == "__main__":
    try:
        request=json.loads(sys.stdin.read(65537))
        with contextlib.redirect_stdout(io.StringIO()):
            result=call(request["method"],request.get("payload",{}))
        print(json.dumps({"ok":True,"value":result},ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({"ok":False,"error":str(exc)},ensure_ascii=False))
        raise SystemExit(1)
