"""抖音适配器：IM 与网页各自运行，使用统一平台接口。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..scheduler import Scheduler
from ..store import Store
from .douyin_im import DouyinIM
from .douyin_web import DouyinWeb


class DouyinAdapter:
    name = "douyin"

    def __init__(self, store: Store, root: str | Path, *, scheduler: Scheduler | None = None,
                 data_dir: str | Path | None = None):
        base = Path(data_dir).resolve() / "platforms" if data_dir else Path(root).resolve() / "var/platforms"
        self.im = DouyinIM(store, root, scheduler=scheduler, directory=base / "douyin-im")
        self.web = DouyinWeb(store, root, directory=base / "douyin-web")

    async def start(self, *, auto_restore: bool = True) -> None:
        await self.im.start(auto_restore=auto_restore)
        await self.web.start()

    async def close(self) -> None:
        await self.web.close()
        await self.im.close()

    async def check_auth(self) -> dict[str, Any]:
        im = await self.im.check_auth()
        web = await self.web.check_auth()
        return {"platform": self.name, "authenticated": im["authenticated"] and web["authenticated"],
                "channels": {"messages": im, "comments_and_publish": web}}

    async def begin_login(self, channel: str) -> dict[str, Any]:
        return await (self.im.begin_login() if channel == "messages" else self.web.begin_login())

    async def login_status(self, channel: str) -> dict[str, Any]:
        return await (self.im.check_auth() if channel == "messages" else self.web.login_status())

    async def poll_messages(self) -> list[dict[str, Any]]:
        return await self.im.poll_messages()

    async def list_joined_groups(self) -> list[dict[str, Any]]:
        return await self.im.list_joined_groups()

    async def get_group_history(self, *, account_id: str, conversation_id: str, limit: int = 50) -> list[dict[str, Any]]:
        return await self.im.get_group_history(account_id=account_id, conversation_id=conversation_id, limit=limit)

    async def poll_groups(self) -> list[dict[str, Any]]:
        return await self.im.poll_groups()

    async def send_group_message(self, *, conversation_id: str, text: str, account_id: str = "",
                                 image_paths: list[str] | None = None,
                                 share_urls: list[str] | None = None) -> dict[str, Any]:
        return await self.im.send_group_message(conversation_id=conversation_id, text=text,
                                                account_id=account_id, image_paths=image_paths,
                                                share_urls=share_urls)

    async def send_message(self, *, conversation_id: str, text: str, recipient_id: str = "",
                           account_id: str = "", image_paths: list[str] | None = None,
                           share_urls: list[str] | None = None) -> dict[str, Any]:
        return await self.im.send_message(conversation_id=conversation_id, text=text,
                                          recipient_id=recipient_id, account_id=account_id,
                                          image_paths=image_paths, share_urls=share_urls)

    async def poll_comments(self) -> list[dict[str, Any]]:
        return await self.web.poll_comments()

    async def reply_comment(self, *, comment_id: str, text: str, content_id: str = "",
                            resource_type: int = 1, root_comment_id: str = "") -> dict[str, Any]:
        return await self.web.reply_comment(comment_id=comment_id, text=text, content_id=content_id,
                                            resource_type=resource_type, root_comment_id=root_comment_id)

    async def publish_content(self, *, title: str, content: str = "", files: list[str] | None = None,
                              tags: list[str] | None = None, content_type: str = "video",
                              metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self.web.publish_content(title=title, content=content, files=files, tags=tags,
                                              content_type=content_type, metadata=metadata)

    async def browse_trending(self, *, limit: int = 10, trend_id: str = "", keyword: str = "") -> dict[str, Any]:
        if trend_id or keyword:
            return await self.web.call("douyin_hot_videos", sentence_id=trend_id, word=keyword, num=limit)
        return await self.web.call("douyin_hot", limit=limit)

    async def browse_recommended(self, *, limit: int = 10, scrolls: int = 6) -> dict[str, Any]:
        return await self.web.call("douyin_feed", num=max(1, min(limit, 30)),
                                   scrolls=max(1, min(scrolls, 15)), skip_live=True)

    async def list_my_posts(self, *, limit: int = 20, cursor: str = "0") -> dict[str, Any]:
        return await self.web.my_posts(limit=limit, cursor=cursor)

    async def inspect_video(self, *, content_id: str, timestamps: list[float]) -> dict[str, Any]:
        return await self.web.call("companion_video_frames", content_id=content_id, timestamps=timestamps)

    async def search_content(self, *, keyword: str, limit: int = 10, order: str = "general") -> dict[str, Any]:
        return await self.web.call("douyin_search", keyword=keyword, num=limit,
                                   order={"popular": "like"}.get(order, order))

    async def get_content(self, *, content_id: str, resource_type: int = 1) -> dict[str, Any]:
        return await self.web.call("douyin_video", aweme_id=content_id)

    async def get_content_comments(self, *, content_id: str, resource_type: int = 1,
                                   cursor: str = "", limit: int = 20) -> dict[str, Any]:
        if cursor:
            raise ValueError("抖音插件当前按数量读取评论，不提供翻页游标")
        return await self.web.call("douyin_comments", aweme_id=content_id, num=limit)

    async def fetch_media_frames(self, *, content_id: str, timestamps: list[float] | None = None,
                                 frame_count: int = 8, include_audio: bool = False,
                                 asr_model: str = "base") -> dict[str, Any]:
        return await self.web.fetch_media_frames(content_id=content_id, timestamps=timestamps,
                                                 frame_count=frame_count, include_audio=include_audio,
                                                 asr_model=asr_model)
