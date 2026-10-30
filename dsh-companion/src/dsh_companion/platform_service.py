"""平台标准调用、动作记录和控制台设定的发布额度。"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .platforms.base import ReservedPlatform
from .platforms.bilibili import BilibiliAdapter
from .platforms.douyin import DouyinAdapter
from .platforms.weixin import WeixinAdapter
from .scheduler import Scheduler
from .store import Store


class PlatformService:
    def __init__(self, store: Store, root: str | Path, scheduler: Scheduler, *, data_dir: str | Path | None = None):
        self.store = store
        self.scheduler = scheduler
        self.group_locks: dict[tuple[str, str, str], asyncio.Lock] = {}
        base = Path(data_dir).resolve() / "platforms" if data_dir else Path(root).resolve() / "var/platforms"
        self.adapters = {"bilibili": BilibiliAdapter(store, root, directory=base / "bilibili"),
                         "douyin": DouyinAdapter(store, root, scheduler=scheduler, data_dir=data_dir),
                         "weixin": WeixinAdapter(store, root, scheduler=scheduler, data_dir=data_dir),
                         "xiaohongshu": ReservedPlatform("xiaohongshu")}

    async def check_auth(self, platform: str) -> dict[str, Any]:
        return await self.adapters[platform].check_auth()

    async def poll_messages(self, platform: str) -> list[dict[str, Any]]:
        events = await self.adapters[platform].poll_messages()
        self.scheduler.notify()
        return events

    async def poll_groups(self, platform: str) -> list[dict[str, Any]]:
        events = await self.adapters[platform].poll_groups()
        self.scheduler.notify()
        return events

    async def list_joined_groups(self, platform: str) -> list[dict[str, Any]]:
        return await self.adapters[platform].list_joined_groups()

    async def get_group_history(self, platform: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
        settings = self.store.get_config("platforms")[platform]
        if not (settings["enabled"] and settings.get("groups", False)):
            raise RuntimeError("控制台尚未启用该平台群聊")
        return await self.adapters[platform].get_group_history(**payload)

    async def poll_comments(self, platform: str) -> list[dict[str, Any]]:
        events = await self.adapters[platform].poll_comments()
        self.scheduler.notify()
        return events

    async def browse(self, platform: str, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.store.get_config("platforms")[platform]["enabled"]:
            raise RuntimeError(f"{platform} 尚未在控制台启用")
        return await getattr(self.adapters[platform], operation)(**payload)

    def publishing_quota(self, platform: str) -> dict[str, Any]:
        config = self.store.get_config("publishing")
        start = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        used = self.store.count_actions(platform, "publish_content", start)
        limit = int(config["daily_limits"][platform])
        return {"platform": platform, "enabled": config["enabled"], "used": used,
                "limit": limit, "remaining": max(0, limit - used)}

    def image_quota(self, platform: str) -> dict[str, Any]:
        config = self.store.get_config("image_messaging")
        start = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        used = self.store.count_action_images(platform, start)
        limit = int(config["daily_limits"][platform])
        return {"platform": platform, "enabled": config["enabled"], "used": used,
                "limit": limit, "remaining": max(0, limit - used)}

    async def action(self, platform: str, kind: str, payload: dict[str, Any], *, job_id: str | None = None) -> dict[str, Any]:
        settings = self.store.get_config("platforms")[platform]
        if not settings["enabled"]:
            raise RuntimeError(f"{platform} 尚未在控制台启用")
        if kind in ("send_message", "send_group_message") and payload.get("image_paths"):
            quota = self.image_quota(platform)
            if not quota["enabled"]:
                raise RuntimeError("控制台尚未启用图片消息")
            count = len(payload["image_paths"])
            if quota["remaining"] < count:
                raise RuntimeError(f"{platform} 今日图片消息额度不足")
        if kind == "send_group_message":
            if not settings.get("groups", False):
                raise RuntimeError("控制台尚未启用群聊")
            account_id = str(payload.get("account_id") or "")
            if not account_id:
                raise ValueError("群聊发送需要已登录账号 ID；可先读取已加入群列表")
            conversation_id = str(payload["conversation_id"])
            key = (platform, account_id, conversation_id)
            async with self.group_locks.setdefault(key, asyncio.Lock()):
                settings = self.store.get_config("platforms")[platform]
                if not (settings["enabled"] and settings.get("groups", False)):
                    raise RuntimeError("控制台尚未启用群聊")
                last = self.store.group_last_sent(*key)
                interval = float(settings["group_reply_interval_seconds"])
                if last is not None:
                    wait_seconds = last + interval - time.time()
                    if wait_seconds > 0:
                        await asyncio.sleep(wait_seconds)
                result = await self._perform_action(platform, kind, payload, job_id=job_id)
                self.store.record_group_sent(*key)
                return result
        if kind == "publish_content":
            quota = self.publishing_quota(platform)
            if not quota["enabled"]:
                raise RuntimeError("控制台尚未启用发布")
            if quota["remaining"] <= 0:
                raise RuntimeError(f"{platform} 今日发布额度已用完")
        return await self._perform_action(platform, kind, payload, job_id=job_id)

    async def _perform_action(self, platform: str, kind: str, payload: dict[str, Any], *, job_id: str | None) -> dict[str, Any]:
        action = self.store.create_action(platform, kind, payload, job_id=job_id,
            account_id=str(payload.get("account_id") or ""),
            target=str(payload.get("conversation_id") or payload.get("comment_id") or ""))
        try:
            result = await getattr(self.adapters[platform], kind)(**payload)
        except Exception as exc:
            self.store.finish_action(action["id"], error=f"{type(exc).__name__}: {exc}")
            raise
        self.store.finish_action(action["id"], result=result)
        return {"action_id": action["id"], "result": result}

    async def fetch_media_frames(self, platform: str, *, content_id: str, timestamps: list[float]) -> dict[str, Any]:
        return await self.adapters[platform].fetch_media_frames(content_id=content_id, timestamps=timestamps)
