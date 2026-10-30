"""微信 iLink 适配器：通过独立 Node 桥接进程接入二维码、消息和图片发送。"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
from pathlib import Path
from typing import Any

from ..scheduler import Scheduler
from ..store import Store
from .base import ReservedPlatform


class WeixinAdapter(ReservedPlatform):
    name = "weixin"

    def __init__(self, store: Store, root: str | Path, *, scheduler: Scheduler | None = None,
                 data_dir: str | Path | None = None):
        super().__init__(self.name)
        self.store = store
        self.root = Path(root).resolve()
        self.directory = Path(data_dir).resolve() / "platforms/weixin" if data_dir else self.root / "var/platforms/weixin"
        self.scheduler = scheduler
        self.package_root = self._find_package()
        self.process: asyncio.subprocess.Process | None = None
        self.reader: asyncio.Task | None = None
        self.stderr_reader: asyncio.Task | None = None
        self.pending: dict[int, asyncio.Future] = {}
        self.counter = 0
        self.status: dict[str, Any] = {"state": "unavailable" if not self.package_root else "idle",
                                       "authenticated": False}
        self.received: list[dict[str, Any]] = []
        self.errors: list[str] = []

    def _find_package(self) -> Path | None:
        candidates = [
            self.root / "runtime/node_modules/dsh-wechat",
            self.root.parent / "data/profiles/desktop/node_modules/dsh-wechat",
        ]
        for candidate in candidates:
            if (candidate / "dist/index.js").exists():
                return candidate
        return None

    async def start(self, *, auto_restore: bool = True) -> None:
        if self.package_root is None:
            self.status = {"state": "unavailable", "authenticated": False,
                           "error": "未找到 dsh-wechat 包"}
            return
        self.directory.mkdir(parents=True, exist_ok=True)
        node = shutil.which("node")
        self.process = await asyncio.create_subprocess_exec(
            node, str(self.root / "runtime/wechat_bridge.mjs"), str(self.directory),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            cwd=str(self.root), env={**os.environ, "DSH_WECHAT_ROOT": str(self.package_root), "WEIXIN_AUTO_RESTORE": "0"})
        self.reader = asyncio.create_task(self._read_stdout())
        self.stderr_reader = asyncio.create_task(self._read_stderr())
        await self.call("status")
        if auto_restore:
            await self.call("restore")

    async def _read_stdout(self) -> None:
        try:
            while line := await self.process.stdout.readline():
                message = json.loads(line.decode("utf-8"))
                if "id" in message:
                    future = self.pending.pop(message["id"], None)
                    if future is not None and not future.done():
                        if "error" in message:
                            future.set_exception(RuntimeError(message["error"]["message"]))
                        else:
                            future.set_result(message["result"])
                elif message.get("event") == "status":
                    self.status = message["data"]
                elif message.get("event") == "message":
                    await self._ingest_message(message["data"])
                elif message.get("event") == "log":
                    self.errors.append(str(message.get("data", {}).get("message", "")))
        finally:
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(RuntimeError("微信消息读取已停止"))
            self.pending.clear()

    async def _read_stderr(self) -> None:
        while self.process is not None and (line := await self.process.stderr.readline()):
            self.errors.append(line.decode("utf-8", errors="replace").strip())

    async def _ingest_message(self, event: dict[str, Any]) -> None:
        settings = self.store.get_config("platforms")[self.name]
        if not (settings["enabled"] and settings.get("messages", False)):
            return
        saved, _, created = self.store.ingest_event(
            self.name, "message", event.get("payload", {}), account_id=str(event.get("account_id") or ""),
            external_id=event.get("external_id"), conversation_id=str(event.get("conversation_id") or ""),
            sender_id=str(event.get("sender_id") or ""), sender_name=event.get("sender_name", ""),
            occurred_at=event.get("occurred_at"))
        if created:
            self.received.append(saved)
            if self.scheduler is not None:
                self.scheduler.notify()

    async def call(self, method: str, **params: Any) -> Any:
        if self.process is None or self.process.returncode is not None:
            raise RuntimeError("微信进程未运行")
        self.counter += 1
        request_id = self.counter
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        try:
            self.process.stdin.write((json.dumps({"id": request_id, "method": method, "params": params},
                                                 ensure_ascii=False) + "\n").encode("utf-8"))
            await self.process.stdin.drain()
            return await asyncio.wait_for(future, timeout=30)
        finally:
            self.pending.pop(request_id, None)

    async def begin_login(self) -> dict[str, Any]:
        return await self.call("login")

    async def login_status(self) -> dict[str, Any]:
        return {"platform": self.name, **await self.call("status")}

    async def check_auth(self) -> dict[str, Any]:
        if self.process is None:
            return {"platform": self.name, **self.status}
        return {"platform": self.name, **await self.call("status")}

    async def poll_messages(self) -> list[dict[str, Any]]:
        events, self.received = self.received, []
        return events

    async def send_message(self, *, conversation_id: str, text: str = "", recipient_id: str = "",
                           account_id: str = "", image_paths: list[str] | None = None,
                           share_urls: list[str] | None = None) -> dict[str, Any]:
        return await self.call("send_message", conversation_id=conversation_id, recipient_id=recipient_id,
                               account_id=account_id, text=text, image_paths=image_paths or [])

    async def send_group_message(self, *, conversation_id: str, text: str = "", account_id: str = "",
                                 image_paths: list[str] | None = None,
                                 share_urls: list[str] | None = None) -> dict[str, Any]:
        return await self.call("send_group_message", conversation_id=conversation_id, account_id=account_id,
                               text=text, image_paths=image_paths or [])

    async def close(self) -> None:
        if self.process is None:
            return
        process = self.process
        try:
            if process.returncode is None:
                try:
                    await self.call("stop")
                except (RuntimeError, BrokenPipeError, ConnectionResetError, TimeoutError):
                    if process.returncode is None:
                        process.terminate()
            await process.wait()
            if self.reader is not None:
                await self.reader
            if self.stderr_reader is not None:
                await self.stderr_reader
        finally:
            self.process = None
