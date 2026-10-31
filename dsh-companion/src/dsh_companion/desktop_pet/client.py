"""桌宠只连接本项目，不扫描其他 DSH 会话。"""
from __future__ import annotations
import json
import os
from urllib.request import Request, urlopen
from urllib.error import URLError


def base_url() -> str:
    return os.environ.get("COMPANION_API_URL", "http://127.0.0.1:8765").rstrip("/")


def read_status() -> dict:
    try:
        with urlopen(base_url() + "/api/pet", timeout=3) as response:
            return json.load(response)
    except URLError:
        return {"state": "offline", "title": "大肥鱼还没起床", "todos": [],
                "message": "打开大肥鱼工作台，再来找它。"}


def control(operation: str) -> None:
    request = Request(base_url() + "/api/control/" + operation, data=b"", method="POST")
    with urlopen(request, timeout=3) as response:
        response.read()


def assign_task(action: str, text: str = "") -> dict:
    request = Request(base_url() + "/api/pet/task",
        data=json.dumps({"action": action, "text": text}, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(request, timeout=5) as response:
        return json.load(response)
