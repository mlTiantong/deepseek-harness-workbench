"""所有平台适配器遵循的接口；预留接口显式报告尚未接入。"""
from __future__ import annotations

from typing import Any, Protocol


class PlatformAdapter(Protocol):
    async def check_auth(self) -> dict[str, Any]: ...
    async def poll_messages(self) -> list[dict[str, Any]]: ...
    async def send_message(self, *, conversation_id: str, text: str, recipient_id: str = "",
                           image_paths: list[str] | None = None) -> dict[str, Any]: ...
    async def poll_comments(self) -> list[dict[str, Any]]: ...
    async def reply_comment(self, *, comment_id: str, text: str, content_id: str = "",
                            resource_type: int = 1, root_comment_id: str = "") -> dict[str, Any]: ...
    async def publish_content(self, *, title: str, content: str = "", files: list[str] | None = None,
                              tags: list[str] | None = None, content_type: str = "video",
                              metadata: dict[str, Any] | None = None) -> dict[str, Any]: ...
    async def browse_trending(self, *, limit: int = 10, trend_id: str = "", keyword: str = "") -> dict[str, Any]: ...
    async def search_content(self, *, keyword: str, limit: int = 10, order: str = "general") -> dict[str, Any]: ...
    async def get_content(self, *, content_id: str, resource_type: int = 1) -> dict[str, Any]: ...
    async def get_content_comments(self, *, content_id: str, resource_type: int = 1,
                                   cursor: str = "", limit: int = 20, author_id: str = "") -> dict[str, Any]: ...
    async def fetch_media_frames(self, *, content_id: str, timestamps: list[float] | None = None,
                                 frame_count: int = 8, include_audio: bool = False,
                                 asr_model: str = "base") -> dict[str, Any]: ...


class ReservedPlatform:
    """小红书和媒体接口保留在统一调用链，不宣称已有平台实现。"""
    def __init__(self, name: str):
        self.name = name

    async def check_auth(self) -> dict[str, Any]:
        return {"platform": self.name, "authenticated": False, "available": False, "reason": "接口预留，尚未接入"}

    async def poll_messages(self) -> list[dict[str, Any]]:
        raise NotImplementedError(f"{self.name} 私信接口尚未接入")

    async def send_message(self, *, conversation_id: str, text: str, recipient_id: str = "") -> dict[str, Any]:
        raise NotImplementedError(f"{self.name} 发信接口尚未接入")

    async def poll_comments(self) -> list[dict[str, Any]]:
        raise NotImplementedError(f"{self.name} 评论接口尚未接入")

    async def reply_comment(self, *, comment_id: str, text: str, content_id: str = "",
                            resource_type: int = 1, root_comment_id: str = "") -> dict[str, Any]:
        raise NotImplementedError(f"{self.name} 评论回复接口尚未接入")

    async def publish_content(self, *, title: str, content: str = "", files: list[str] | None = None,
                              tags: list[str] | None = None, content_type: str = "video",
                              metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        raise NotImplementedError(f"{self.name} 发布接口尚未接入")

    async def browse_trending(self, *, limit: int = 10, trend_id: str = "", keyword: str = "") -> dict[str, Any]:
        raise NotImplementedError(f"{self.name} 热门内容接口尚未接入")

    async def search_content(self, *, keyword: str, limit: int = 10, order: str = "general") -> dict[str, Any]:
        raise NotImplementedError(f"{self.name} 内容搜索接口尚未接入")

    async def get_content(self, *, content_id: str, resource_type: int = 1) -> dict[str, Any]:
        raise NotImplementedError(f"{self.name} 内容详情接口尚未接入")

    async def get_content_comments(self, *, content_id: str, resource_type: int = 1,
                                   cursor: str = "", limit: int = 20, author_id: str = "") -> dict[str, Any]:
        raise NotImplementedError(f"{self.name} 内容评论接口尚未接入")

    async def fetch_media_frames(self, *, content_id: str, timestamps: list[float] | None = None,
                                 frame_count: int = 8, include_audio: bool = False,
                                 asr_model: str = "base") -> dict[str, Any]:
        raise NotImplementedError(f"{self.name} 视频阅读尚未接入")
