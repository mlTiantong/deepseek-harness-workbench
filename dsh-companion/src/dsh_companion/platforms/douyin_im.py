"""抖音 IM 常驻进程：用项目内账号登录，推送事件进入调度器。"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
from pathlib import Path
from typing import Any

from ..scheduler import Scheduler
from ..store import Store


IM_CALL_TIMEOUT = 30


class DouyinIM:
    def __init__(self, store: Store, root: str | Path, *, scheduler: Scheduler | None = None,
                 directory: str | Path | None = None):
        self.store = store
        self.root = Path(root).resolve()
        self.directory = Path(directory).resolve() if directory else self.root / "var/platforms/douyin-im"
        self.scheduler = scheduler
        self.process: asyncio.subprocess.Process | None = None
        self.reader: asyncio.Task | None = None
        self.stderr_reader: asyncio.Task | None = None
        self.pending: dict[int, asyncio.Future] = {}
        self.counter = 0
        self.status: dict[str, Any] = {"state": "idle", "authenticated": False}
        self.errors: list[str] = []
        self.received: list[dict[str, Any]] = []

    async def start(self, *, auto_restore: bool = True) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        node = shutil.which("node")
        self.process = await asyncio.create_subprocess_exec(
            node, str(self.root / "runtime/douyin_bridge.mjs"), str(self.directory),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            cwd=str(self.root), env={**os.environ, "PYTHONUTF8": "1", "DOUYIN_AUTO_RESTORE": "1" if auto_restore else "0"})
        self.reader = asyncio.create_task(self._read_stdout())
        self.stderr_reader = asyncio.create_task(self._read_stderr())
        await self.call("status")

    async def _read_stdout(self) -> None:
        buffer = bytearray()
        try:
            while chunk := await self.process.stdout.read(65536):
                buffer.extend(chunk)
                while (end := buffer.find(b"\n")) != -1:
                    line = bytes(buffer[:end])
                    del buffer[:end + 1]
                    message = json.loads(line)
                    if "id" in message:
                        future = self.pending.pop(message["id"], None)
                        if future is not None and not future.done():
                            if "error" in message:
                                future.set_exception(RuntimeError(message["error"]["message"]))
                            else:
                                future.set_result(message["result"])
                    elif message["event"] == "status":
                        self.status = message["data"]
                    elif message["event"] == "message":
                        await self._ingest_message(message["data"])
        finally:
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(RuntimeError("抖音 IM 消息读取已停止"))
            self.pending.clear()

    async def _ingest_message(self, event: dict[str, Any]) -> tuple[dict[str, Any] | None, bool]:
        settings = self.store.get_config("platforms")["douyin"]
        is_group = event["event_type"] == "group_message"
        if not (settings["enabled"] and settings.get("groups" if is_group else "messages", False)):
            return None, False
        args = {"account_id": event["account_id"], "external_id": event.get("external_id"),
                "conversation_id": event["conversation_id"], "sender_id": event["sender_id"],
                "sender_name": event.get("sender_name", ""), "occurred_at": event.get("occurred_at")}
        if is_group:
            saved, _, created = self.store.ingest_group_event("douyin", event["payload"], **args)
        else:
            saved, _, created = self.store.ingest_douyin_private_event(event["payload"], **args)
        if created:
            if not is_group:
                self.received.append(saved)
            if self.scheduler is not None:
                self.scheduler.notify()
        return saved, created

    async def list_joined_groups(self) -> list[dict[str, Any]]:
        return await self.call("list_groups", refresh=True)

    async def get_group_history(self, *, account_id: str, conversation_id: str, limit: int = 50) -> list[dict[str, Any]]:
        return await self.call("recent_group_history", account_id=account_id, conversation_id=conversation_id, limit=limit)

    async def poll_groups(self) -> list[dict[str, Any]]:
        settings = self.store.get_config("platforms")["douyin"]
        since_default = float(settings.get("group_started_at") or time.time())
        cursors = self.store.get_config("douyin_group_cursors") or {}
        result = []
        for group in await self.list_joined_groups():
            key = group["account_id"] + ":" + group["conversation_id"]
            since = max(float(cursors.get(key, since_default)), since_default)
            history = await self.call("group_history", account_id=group["account_id"],
                                      conversation_id=group["conversation_id"], since=since)
            for item in sorted(history, key=lambda event: event.get("occurred_at") or 0):
                saved, created = await self._ingest_message(item)
                if created:
                    result.append(saved)
            cursors[key] = max([since, *[float(item.get("occurred_at") or 0) for item in history]])
            self.store.set_config("douyin_group_cursors", cursors)
        return result

    async def send_group_message(self, *, conversation_id: str, text: str, account_id: str = "",
                                 image_paths: list[str] | None = None,
                                 share_urls: list[str] | None = None) -> dict[str, Any]:
        return await self.call("send_group_message", conversation_id=conversation_id, text=text,
                               account_id=account_id, image_paths=image_paths or [],
                               share_urls=share_urls or [])

    async def _read_stderr(self) -> None:
        while line := await self.process.stderr.readline():
            self.errors.append(line.decode("utf-8", errors="replace").strip())

    async def call(self, method: str, **params: Any) -> Any:
        if self.process is None or self.process.returncode is not None:
            raise RuntimeError("抖音 IM 进程未运行")
        if self.reader is not None and self.reader.done():
            raise RuntimeError("抖音 IM 消息读取已停止")
        self.counter += 1
        request_id = self.counter
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        try:
            self.process.stdin.write((json.dumps({"id": request_id, "method": method, "params": params},
                                                 ensure_ascii=False) + "\n").encode("utf-8"))
            await self.process.stdin.drain()
            try:
                return await asyncio.wait_for(future, timeout=IM_CALL_TIMEOUT)
            except TimeoutError as exc:
                raise TimeoutError(f"抖音 IM 调用 {method} 超时") from exc
        finally:
            self.pending.pop(request_id, None)

    async def begin_login(self) -> dict[str, Any]:
        return await self.call("login")

    async def check_auth(self) -> dict[str, Any]:
        return {"platform": "douyin", **await self.call("status")}

    async def poll_messages(self) -> list[dict[str, Any]]:
        """IM 长连接已实时入队；返回自上次查看以来接收的事件。"""
        events, self.received = self.received, []
        return events

    async def send_message(self, *, conversation_id: str, text: str, recipient_id: str = "",
                           account_id: str = "", image_paths: list[str] | None = None,
                           share_urls: list[str] | None = None) -> dict[str, Any]:
        return await self.call("send_message", conversation_id=conversation_id, text=text,
                               recipient_id=recipient_id, account_id=account_id,
                               image_paths=image_paths or [], share_urls=share_urls or [])

    async def close(self) -> None:
        if self.process is not None:
            process = self.process
            try:
                if process.returncode is None:
                    try:
                        await self.call("stop")
                    except (RuntimeError, BrokenPipeError, ConnectionResetError, TimeoutError):
                        if process.returncode is None:
                            try:
                                process.terminate()
                            except ProcessLookupError:
                                pass
                if process.stdin is not None:
                    process.stdin.close()
                await process.wait()
                if self.reader is not None:
                    await self.reader
                if self.stderr_reader is not None:
                    await self.stderr_reader
            finally:
                self.process = None
