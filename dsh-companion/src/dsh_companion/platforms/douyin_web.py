"""抖音网页插件：独立浏览器配置、评论和作品发布。"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
import time
from contextlib import AsyncExitStack
from datetime import timedelta
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from ..store import Store
from ..video_media import extract_frame, probe_duration, sample_times, transcribe_audio


class DouyinWeb:
    def __init__(self, store: Store, root: str | Path, *, directory: str | Path | None = None):
        self.store = store
        self.root = Path(root).resolve()
        self.directory = Path(directory).resolve() if directory else self.root / "var/platforms/douyin-web"
        self.stack: AsyncExitStack | None = None
        self.session: ClientSession | None = None

    async def start(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        script = self.root / "src/dsh_companion/platforms/douyin_web_server.py"
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            self.debug_port = sock.getsockname()[1]
        env = {**os.environ, "DSH_DOUYIN_HOME": str(self.directory), "DOUYIN_CDP_URL": "",
               "DOUYIN_PROFILE_PORT": str(self.debug_port), "DOUYIN_DETACHED": "0",
               "DOUYIN_ALLOW_WRITES": "1", "DOUYIN_PROXY_URL": "", "DOUYIN_MCP_TOOLS": "",
               "DOUYIN_HEADLESS": "1", "PYTHONUTF8": "1"}
        self.stack = AsyncExitStack()
        read, write = await self.stack.enter_async_context(stdio_client(
            StdioServerParameters(command=sys.executable, args=[str(script)], env=env, cwd=str(self.root))))
        self.session = await self.stack.enter_async_context(ClientSession(read, write, read_timeout_seconds=timedelta(seconds=1800)))
        await self.session.initialize()

    async def close(self) -> None:
        if self.stack is not None:
            await self.stack.aclose()
            self.stack = None
            self.session = None

    async def call(self, name: str, **arguments: Any) -> dict[str, Any]:
        if self.session is None:
            raise RuntimeError("抖音网页插件未启动")
        response = await self.session.call_tool(name, arguments)
        if response.isError:
            raise RuntimeError(str(response.content))
        result = json.loads(response.content[0].text)
        if not result["ok"]:
            raise RuntimeError(result.get("error") or result.get("hint") or str(result))
        return result

    async def check_auth(self) -> dict[str, Any]:
        result = await self.call("douyin_login_poll")
        if not result["loggedIn"]:
            return {"platform": "douyin", "authenticated": False, "reason": result.get("hint", "")}
        profile = await self.call("companion_account_info")
        return {"platform": "douyin", "authenticated": True, "account_id": profile["uid"],
                "nickname": profile["nickname"], "reason": result.get("hint", "")}

    async def begin_login(self) -> dict[str, Any]:
        result = await self.call("douyin_login_qr")
        if result["loggedIn"]:
            return {"platform": "douyin", "state": "done"}
        if not result["png"]:
            return {"platform": "douyin", "state": "error", "error": result.get("hint", "无法获取二维码")}
        return {"platform": "douyin", "state": "scan", "qr_image": result["png"]}

    async def login_status(self) -> dict[str, Any]:
        result = await self.call("douyin_login_poll")
        state = "done" if result["loggedIn"] else {
            "waiting_scan": "scan", "scanned_need_confirm": "confirm", "no_qr": "verification"
        }[result["state"]]
        hint = "二维码已不在页面上，可能需要完成身份验证或重新扫码" if state == "verification" else result.get("hint", "")
        return {"platform": "douyin", "state": state, "nickname": result.get("nickname", ""), "hint": hint}

    async def poll_comments(self) -> list[dict[str, Any]]:
        auth = await self.check_auth()
        if not auth["authenticated"]:
            raise RuntimeError("抖音网页账号未登录")
        staged: list[tuple[str, dict[str, Any]]] = []
        checkpoints: dict[str, float] = {}
        for group in ("2", "601"):
            key = f"douyin_notice_since:{auth['account_id']}:{group}"
            previous = self.store.get_config(key)
            since = float(previous if previous is not None else int(time.time()))
            newest, min_time, max_time = since, "0", "0"
            while True:
                page = await self.call("companion_notice_page", group=group,
                                       min_time=min_time, max_time=max_time)
                notices = page["notices"]
                for notice in notices:
                    occurred = float(notice["create_time"])
                    if occurred >= since:
                        staged.append((group, notice))
                        newest = max(newest, occurred)
                if not page["has_more"] or not notices or min(float(n["create_time"]) for n in notices) < since:
                    break
                next_cursor = str(page["min_time"]), str(page["max_time"])
                if next_cursor == (min_time, max_time):
                    raise RuntimeError("抖音通知分页游标未前进")
                min_time, max_time = next_cursor
            checkpoints[key] = newest

        events = []
        for group, notice in sorted(staged, key=lambda pair: float(pair[1]["create_time"])):
            body = notice.get("comment" if group == "2" else "at") or {}
            sender = body.get("from_user") or body.get("user") or notice.get("user") or {}
            candidates = [item for item in (body.get("comment"), body.get("reply_comment"),
                                             body.get("replyComment"), body) if isinstance(item, dict)]
            sender_id = str(sender.get("uid") or sender.get("sec_uid") or "")
            comment = next((item for item in candidates if sender_id and
                            str((item.get("user") or {}).get("uid") or "") == sender_id), candidates[0])
            if not sender:
                sender = comment.get("user") or {}
                sender_id = str(sender.get("uid") or sender.get("sec_uid") or "")
            if sender_id and sender_id == auth["account_id"]:
                continue
            work = body.get("aweme") or comment.get("aweme") or {}
            root_comment = body.get("level1_comment") or body.get("level1Comment") or {}
            payload = {"text": comment.get("text") or body.get("text") or notice.get("description") or "",
                       "comment_id": str(comment.get("cid") or body.get("comment_id") or ""),
                       "root_comment_id": str(root_comment.get("cid") or ""),
                       "content_id": str(work.get("aweme_id") or comment.get("aweme_id") or body.get("aweme_id") or ""),
                       "work": work, "notice": notice}
            event, _, created = self.store.ingest_event(
                "douyin", "comment" if group == "2" else "mention", payload,
                account_id=auth["account_id"], external_id=str(notice["nid"]), sender_id=sender_id,
                sender_name=sender.get("nickname") or "", occurred_at=float(notice["create_time"]))
            if created:
                events.append(event)
        for key, newest in checkpoints.items():
            self.store.set_config(key, newest)
        return events

    async def reply_comment(self, *, comment_id: str, text: str, content_id: str = "",
                            resource_type: int = 1, root_comment_id: str = "") -> dict[str, Any]:
        return await self.call("douyin_comment", aweme_id=content_id, text=text, reply_cid=comment_id)

    async def publish_content(self, *, title: str, content: str = "", files: list[str] | None = None,
                              tags: list[str] | None = None, content_type: str = "video",
                              metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        metadata = metadata or {}
        before = None
        try:
            before = await self.my_posts(limit=50)
        except Exception:
            pass
        started = time.time()
        if content_type in ("image", "image_text", "graphic"):
            result = await self.call("companion_publish_images", files=files or [], title=title,
                                     content=content, tags=tags or [], cover_path=metadata.get("cover", ""),
                                     ai_generated=bool(metadata.get("ai_generated", False)),
                                     music=str(metadata.get("music") or ""))
        elif content_type == "video":
            if not files:
                raise ValueError("抖音视频发布需要视频文件")
            result = await self.call("companion_publish_video", file=files[0], title=title,
                                     content=content, tags=tags or [], cover_path=metadata.get("cover", ""),
                                     ai_generated=bool(metadata.get("ai_generated", False)),
                                     music=str(metadata.get("music") or ""))
        else:
            raise ValueError(f"不支持的抖音内容类型：{content_type}")
        result.update({"share_url": None, "share_status": "pending_verification"})
        if before is not None:
            try:
                after = await self.my_posts(limit=50)
                previous = {item["content_id"] for item in before["items"]}
                candidates = [item for item in after["items"] if item["content_id"] not in previous
                              and item.get("share_url") and (item.get("created_at") or 0) >= started - 120
                              and (item["title"] == title or item["title"].startswith(title + "\n"))]
                if before["account_id"] == after["account_id"] and len(candidates) == 1:
                    result.update({"content_id": candidates[0]["content_id"],
                                   "share_url": candidates[0]["share_url"],
                                   "share_status": "identified_visibility_unknown"})
            except Exception:
                pass
        return result

    async def my_posts(self, *, limit: int = 20, cursor: str = "0") -> dict[str, Any]:
        return await self.call("companion_my_posts", limit=limit, cursor=cursor)

    async def fetch_media_frames(self, *, content_id: str, timestamps: list[float] | None = None,
                                 frame_count: int = 8, include_audio: bool = False,
                                 asr_model: str = "base") -> dict[str, Any]:
        downloaded_path = None
        try:
            try:
                result = await self.call("companion_video_frames", content_id=content_id,
                                         timestamps=timestamps, frame_count=frame_count)
            except RuntimeError as exc:
                if "视频定位超时" not in str(exc):
                    raise
                downloaded = await self.call("companion_download_video", content_id=content_id)
                downloaded_path = Path(downloaded["path"])
                duration = await asyncio.to_thread(probe_duration, str(downloaded_path))
                times = timestamps if timestamps is not None else sample_times(duration, frame_count)
                if not times or len(times) > 16 or any(t < 0 or t >= duration for t in times):
                    raise ValueError("请选择视频时长内的 1 至 16 个时间点")
                frames = [await asyncio.to_thread(extract_frame, str(downloaded_path), t) for t in times]
                result = {"content_id": content_id, "duration": duration, "frames": frames,
                          "platform": "douyin", "frame_source": "download"}
            result.update({"audio_status": "disabled", "transcript": "", "audio_understood": False})
            if include_audio and result.get("content_type") != "image":
                try:
                    if downloaded_path is None:
                        downloaded = await self.call("companion_download_video", content_id=content_id)
                        downloaded_path = Path(downloaded["path"])
                    result.update(await asyncio.to_thread(transcribe_audio, str(downloaded_path), model_name=asr_model))
                except Exception as exc:
                    result.update({"audio_status": "error", "audio_error": str(exc)})
            return result
        finally:
            if downloaded_path is not None:
                downloaded_path.unlink(missing_ok=True)
