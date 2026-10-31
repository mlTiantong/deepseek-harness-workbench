"""本地 MCP 工具：DSH 会话通过它访问共享记忆、任务和平台动作。"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from mcp.types import ImageContent, TextContent

import httpx
from mcp.server.fastmcp import FastMCP

from .memory import MemoryCenter
from .message_media import image_block_from_path, persist_image_block
from .store import Store

mcp = FastMCP("companion_mcp")
store = Store(os.environ["COMPANION_DATABASE"])
memory = MemoryCenter(store)
base_url = os.environ.get("COMPANION_API_URL", "http://127.0.0.1:8765")
READ = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
READ_EXTERNAL = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True}
WRITE = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False}
EXTERNAL = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True}


async def _api(path: str, body: dict[str, Any] | None = None, *,
               method: str = "POST", timeout: float = 120) -> Any:
    async with httpx.AsyncClient(base_url=base_url, timeout=timeout) as client:
        response = await client.request(method, path, json=body if method != "GET" else None)
        if response.is_error:
            raise RuntimeError(f"工作台接口 {response.status_code}: {response.text}")
        return response.json()


@mcp.tool(annotations=READ)
def get_persona() -> str:
    """读取所有角色共享的 L1 核心人格。"""
    return memory.get_persona()


@mcp.tool(annotations=WRITE)
def set_persona(content: str) -> dict[str, Any]:
    """主会话或用户更新 L1 核心人格，内容为完整新版。"""
    return memory.set_persona(content)


@mcp.tool(annotations=READ)
def get_recent_context() -> str:
    """读取所有角色共享的 L2 近期背景看板。"""
    return memory.get_recent_context()


@mcp.tool(annotations=WRITE)
def set_recent_context(content: str) -> dict[str, Any]:
    """更新 L2 近期背景看板，内容为完整新版，最长 2000 字。"""
    return memory.set_recent_context(content)


@mcp.tool(annotations=READ)
def search_memory(query: str, kind: str = "", platform: str = "", limit: int = 20) -> list[dict[str, Any]]:
    """按关键字查询共同经历、熟人印象及其他记忆；kind 或 platform 留空表示不限。"""
    return memory.search_memory(query, kind=kind or None, platform=platform or None, limit=limit)


@mcp.tool(annotations=READ)
def list_memories(kind: str = "", platform: str = "", subject_id: str = "",
                  status: str = "active", query: str = "", limit: int = 100) -> list[dict[str, Any]]:
    """列举或搜索指定类型、平台、熟人及状态的记忆；status 留空包含已归档。"""
    return store.list_memories(kind=kind or None, platform=platform or None, subject_id=subject_id or None,
                               status=status or None, query=query, limit=limit)


@mcp.tool(annotations=READ)
def get_memory(memory_id: str) -> dict[str, Any] | None:
    """按 ID 读取一条完整记忆，包括状态、标签和元数据。"""
    return store.get_memory(memory_id)


@mcp.tool(annotations=READ)
def list_saved_media(platform: str = "", conversation_id: str = "", sender_id: str = "",
                     limit: int = 100) -> list[dict[str, Any]]:
    """列出已经保存的入站图片和表情，返回可直接交给发信工具的本机路径。"""
    return store.list_media(platform=platform or None, conversation_id=conversation_id or None,
                            sender_id=sender_id or None, limit=limit)


@mcp.tool(annotations=READ)
def get_saved_media(media_id: str) -> dict[str, Any] | None:
    """读取一条已保存媒体的完整来源和本机路径。"""
    return store.get_media(media_id)


@mcp.tool(annotations=WRITE)
def save_media_file(path: str, platform: str = "", conversation_id: str = "",
                    sender_id: str = "", media_kind: str = "image") -> dict[str, Any]:
    """把本机图片或表情保存到共享媒体库，返回以后可复用的 path。"""
    source = image_block_from_path(path)
    media_config = store.get_config("chat_media") or {}
    folder = Path(media_config.get("folder") or "message-media-library")
    if not folder.is_absolute():
        folder = store.path.parent / folder
    persisted = persist_image_block(source, folder, stem=f"manual-{int(time.time())}")
    if persisted is None:
        raise ValueError("无法保存图片")
    return store.save_media(platform=platform, conversation_id=conversation_id, sender_id=sender_id,
                            event_id=f"manual:{persisted['sha256']}",
                            media_kind=media_kind, path=persisted["path"], mime_type=persisted["mime_type"],
                            sha256=persisted["sha256"], metadata={"source_path": str(Path(path).resolve())})


@mcp.tool(annotations=WRITE)
def update_memory(memory_id: str, content: str, title: str | None = None,
                  tags: list[str] | None = None, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    """修改现有记忆的内容；未提供的标题、标签和元数据保持原样。"""
    item = store.get_memory(memory_id)
    if item is None:
        raise ValueError(f"记忆不存在：{memory_id}")
    return store.put_memory(item["kind"], content, memory_id=memory_id,
                            title=item["title"] if title is None else title,
                            platform=item["platform"], subject_id=item["subject_id"],
                            tags=item["tags"] if tags is None else tags,
                            metadata=item["metadata"] if metadata is None else metadata,
                            status=item["status"])


@mcp.tool(annotations=WRITE)
def set_memory_status(memory_id: str, status: str) -> bool:
    """归档或恢复记忆；status 通常为 archived 或 active。"""
    return store.set_memory_status(memory_id, status)


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True,
                       "idempotentHint": True, "openWorldHint": False})
def delete_memory(memory_id: str) -> bool:
    """删除指定记忆。"""
    return store.delete_memory(memory_id)


@mcp.tool(annotations=WRITE)
def add_episode(content: str, title: str = "", tags: list[str] | None = None) -> dict[str, Any]:
    """向 L3 写入真实共同经历。"""
    return memory.add_episode(content, title=title, tags=tags)


@mcp.tool(annotations=READ)
def get_acquaintance(platform: str, subject_id: str) -> dict[str, Any] | None:
    """按平台和用户 ID 读取 L3 熟人印象卡。"""
    return memory.get_acquaintance(platform, subject_id)


@mcp.tool(annotations=WRITE)
def set_acquaintance(platform: str, subject_id: str, content: str,
                     nickname: str | None = None, tags: list[str] | None = None) -> dict[str, Any]:
    """创建或更新平台用户的 L3 熟人印象卡。"""
    return memory.set_acquaintance(platform, subject_id, content, nickname=nickname, tags=tags)


@mcp.tool(annotations=WRITE)
def add_scratchpad_note(content: str, actor_id: str = "", platform: str = "", subject_id: str = "") -> dict[str, Any]:
    """向待整理流水写入值得保留的互动、作品或经历。"""
    return memory.add_scratchpad_note(content, actor_id=actor_id, platform=platform, subject_id=subject_id)


@mcp.tool(annotations=READ)
def list_scratchpad_notes(limit: int = 100) -> list[dict[str, Any]]:
    """读取等待主会话整理的记忆流水。"""
    return memory.list_scratchpad(limit=limit)


@mcp.tool(annotations=WRITE)
def apply_memory_compaction(recent_context: str, episodes: list[dict[str, Any]],
                            acquaintances: list[dict[str, Any]], note_ids: list[str]) -> dict[str, Any]:
    """提交整理后的 L2 背景、L3 经历与熟人卡，归档指定的已处理流水。"""
    return memory.apply_compaction(recent_context=recent_context, episodes=episodes,
                                   acquaintances=acquaintances, note_ids=note_ids)


@mcp.tool(annotations=WRITE)
def broadcast_background(content: str, actor_ids: list[str] | None = None,
                         update_board: bool = True) -> list[dict[str, Any]]:
    """更新近期背景，并向指定角色或已开启平台的互动角色推送背景通知。"""
    return memory.broadcast_background(content, actor_ids=actor_ids, update_board=update_board)


@mcp.tool(annotations=WRITE)
def notify_main(platform: str, sender_id: str, message: str, source_event_id: str = "",
                account_id: str = "") -> dict[str, Any]:
    """将控制台已登记的管理员转告持久排入主会话；来源使用当前事件的 ID。"""
    return memory.relay_to_main(platform=platform, sender_id=sender_id, message=message,
                                source_event_id=source_event_id, account_id=account_id)


@mcp.tool(annotations=WRITE)
def delegate_task(actor_id: str, kind: str, payload: dict[str, Any], priority: int | None = None,
                  autonomous: bool = True) -> dict[str, Any]:
    """把工作交给 main、平台 chat/comment、media、creator 或 publisher 角色。"""
    return store.create_job(actor_id, kind, payload, priority=priority, autonomous=autonomous)


@mcp.tool(annotations=WRITE)
def schedule_task(title: str, actor_id: str, kind: str, payload: dict[str, Any],
                  after_seconds: float, interval_seconds: float | None = None,
                  priority: int | None = None, autonomous: bool = True) -> dict[str, Any]:
    """创建一次性或固定间隔任务；after_seconds 是从现在起的等待秒数。"""
    return store.create_timer(title, actor_id, kind, payload, next_at=time.time() + after_seconds,
                              interval_seconds=interval_seconds, priority=priority, autonomous=autonomous)


@mcp.tool(annotations=READ)
def list_timers() -> list[dict[str, Any]]:
    """列出当前持久化的一次性与周期性定时规则。"""
    return store.list_timers()


@mcp.tool(annotations=WRITE)
def update_timer(timer_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
    """更新定时规则的时间、间隔、目标角色、内容、优先级或启用状态。"""
    return store.update_timer(timer_id, updates)


@mcp.tool(annotations=WRITE)
def delete_timer(timer_id: str) -> bool:
    """删除一条定时规则；已经产生的任务保留。"""
    return store.delete_timer(timer_id)


@mcp.tool(annotations=READ)
def list_jobs(status: str = "", actor_id: str = "", limit: int = 100) -> list[dict[str, Any]]:
    """查看任务队列与状态，可按状态和角色筛选。"""
    return store.list_jobs(status=status or None, actor_id=actor_id or None, limit=limit)


@mcp.tool(annotations=READ)
def get_job(job_id: str) -> dict[str, Any] | None:
    """读取单项任务的输入、状态、结果与错误。"""
    return store.get_job(job_id)


@mcp.tool(annotations=READ)
def list_events(platform: str = "", status: str = "", limit: int = 100) -> list[dict[str, Any]]:
    """查看平台事件和管理员转告，可按平台或处理状态筛选。"""
    return store.list_events(platform=platform or None, status=status or None, limit=limit)


@mcp.tool(annotations=READ)
def list_actions(platform: str = "", kind: str = "", limit: int = 100) -> list[dict[str, Any]]:
    """查看平台收发与发布动作的执行记录。"""
    return store.list_actions(platform=platform or None, kind=kind or None, limit=limit)


@mcp.tool(annotations=READ)
async def get_publishing_quota(platform: str) -> Any:
    """读取指定平台今天已用和剩余的发布额度。"""
    return await _api(f"/api/platforms/{platform}/quota", method="GET")


@mcp.tool(annotations=READ)
async def get_image_quota(platform: str) -> Any:
    """读取指定平台今天已用和剩余的图片消息额度。"""
    return await _api(f"/api/platforms/{platform}/image-quota", method="GET")


@mcp.tool(annotations=WRITE)
def retry_job(job_id: str) -> bool:
    """将失败或取消的任务重新排入队列。"""
    return store.retry_job(job_id)


@mcp.tool(annotations=WRITE)
async def cancel_job(job_id: str) -> Any:
    """取消等待中或运行中的任务；运行中的角色会收到实际停止指令。"""
    return await _api(f"/api/jobs/{job_id}/cancel")


@mcp.tool(annotations=READ)
def get_decision_context() -> dict[str, Any]:
    """读取自主方向、近期记忆、待办、执行结果、已有日程和启用能力，供自己选择下一步。"""
    return memory.decision_context()


@mcp.tool(annotations=READ)
def get_runtime_config() -> dict[str, Any]:
    """读取控制台中配置的平台、管理员、自主活动和发布额度。"""
    return store.get_config()


@mcp.tool(annotations=READ)
async def check_auth(platform: str) -> Any:
    """检查指定平台在本系统独立环境中的登录状态。"""
    return await _api(f"/api/platforms/{platform}/check_auth")


@mcp.tool(annotations=EXTERNAL)
async def poll_messages(platform: str) -> Any:
    """读取指定平台的新私信并交给调度器。"""
    return await _api(f"/api/platforms/{platform}/poll_messages")


@mcp.tool(annotations=READ_EXTERNAL)
async def list_joined_groups(platform: str) -> Any:
    """读取本账号已加入的群，返回账号 ID、群会话 ID 和群名。"""
    return await _api(f"/api/platforms/{platform}/list_joined_groups", {})


@mcp.tool(annotations=READ_EXTERNAL)
async def get_group_history(platform: str, account_id: str, conversation_id: str, limit: int = 50) -> Any:
    """查看已启用群聊中的最近上下文，包括回复间隔内的其他消息。"""
    return await _api(f"/api/platforms/{platform}/get_group_history",
                      {"account_id": account_id, "conversation_id": conversation_id, "limit": limit})


@mcp.tool(annotations=WRITE)
def delegate_group_chat(platform: str, account_id: str, conversation_id: str, instruction: str,
                        after_seconds: float = 0) -> dict[str, Any]:
    """主会话决定后委派群聊子会话回复或主动聊，自动遵守该群回复间隔。"""
    settings = store.get_config("platforms")[platform]
    if not (settings["enabled"] and settings.get("groups", False)):
        raise RuntimeError("控制台尚未启用该平台群聊")
    return store.create_group_activity(platform, conversation_id, instruction, account_id=account_id,
                                       available_at=time.time() + after_seconds)


@mcp.tool(annotations=EXTERNAL)
async def send_group_message(platform: str, account_id: str, conversation_id: str, text: str,
                             job_id: str = "", image_paths: list[str] | None = None,
                             share_urls: list[str] | None = None) -> Any:
    """向已加入的指定群发送一条消息；抖音图片用 image_paths，链接会直接发送分享卡。"""
    payload = {"account_id": account_id, "conversation_id": conversation_id,
               "text": text, "job_id": job_id or None}
    if platform in ("douyin", "weixin"):
        if image_paths:
            payload["image_paths"] = image_paths
    if platform == "douyin":
        if share_urls:
            payload["share_urls"] = share_urls
    return await _api(f"/api/platforms/{platform}/send_group_message", payload, timeout=600)


@mcp.tool(annotations=EXTERNAL)
async def send_message(platform: str, conversation_id: str, text: str = "",
                       recipient_id: str = "", job_id: str = "", account_id: str = "",
                       image_paths: list[str] | None = None,
                       share_urls: list[str] | None = None) -> Any:
    """向指定平台会话或联系人发送私信；抖音和微信图片用 image_paths，抖音作品链接会直接发送分享卡。"""
    payload = {"conversation_id": conversation_id, "recipient_id": recipient_id,
               "text": text, "job_id": job_id or None}
    if image_paths:
        payload["image_paths"] = image_paths
    if platform == "douyin":
        payload["account_id"] = account_id
        if share_urls:
            payload["share_urls"] = share_urls
    return await _api(f"/api/platforms/{platform}/send_message", payload)


@mcp.tool(annotations=EXTERNAL)
async def poll_comments(platform: str) -> Any:
    """读取指定平台的新评论和提及并交给调度器。"""
    return await _api(f"/api/platforms/{platform}/poll_comments")


@mcp.tool(annotations=EXTERNAL)
async def reply_comment(platform: str, comment_id: str, text: str,
                        content_id: str = "", resource_type: int = 1,
                        root_comment_id: str = "", job_id: str = "") -> Any:
    """在指定平台回复给定评论，结果记录在动作表。"""
    return await _api(f"/api/platforms/{platform}/reply_comment",
                      {"comment_id": comment_id, "content_id": content_id, "resource_type": resource_type,
                       "root_comment_id": root_comment_id, "text": text, "job_id": job_id or None})


@mcp.tool(annotations=EXTERNAL)
async def publish_content(platform: str, title: str, content: str = "",
                          files: list[str] | None = None, tags: list[str] | None = None,
                          content_type: str = "video", metadata: dict[str, Any] | None = None,
                          music: str = "", job_id: str = "") -> Any:
    """按控制台设置向指定平台发布作品；files 是创作文件的绝对路径；music 是抖音曲库中的歌曲名。"""
    metadata = dict(metadata or {})
    if music:
        metadata["music"] = music
    return await _api(f"/api/platforms/{platform}/publish_content",
                      {"title": title, "content": content, "files": files or [], "tags": tags or [],
                       "content_type": content_type, "metadata": metadata, "job_id": job_id or None},
                      timeout=1800)


@mcp.tool(annotations=READ_EXTERNAL)
async def browse_douyin_recommended(limit: int = 10, scrolls: int = 6) -> Any:
    """浏览抖音推荐流，返回平台实际提供的作品标题、作者与链接；不代表看过视频内容。"""
    return await _api("/api/platforms/douyin/browse_recommended", {"limit": limit, "scrolls": scrolls}, timeout=180)


@mcp.tool(annotations=READ_EXTERNAL)
async def list_my_douyin_posts(limit: int = 20, cursor: str = "0") -> Any:
    """列出已登录抖音账号的作品及分享地址。visibility=unknown 表示公开可见性尚未核验。"""
    return await _api("/api/platforms/douyin/list_my_posts", {"limit": limit, "cursor": cursor}, timeout=180)


@mcp.tool(annotations=READ_EXTERNAL, structured_output=False)
async def inspect_douyin_video(content_id: str, timestamps: list[float]) -> Any:
    """查看抖音视频指定时间点的画面（1 至 16 帧）；此工具不转写音频。"""
    result = await _api("/api/platforms/douyin/inspect_video",
                        {"content_id": content_id, "timestamps": timestamps}, timeout=180)
    output = [TextContent(type="text", text=f"作品 {content_id} 的画面；未获取音频。")]
    for frame in result["frames"]:
        output.extend([TextContent(type="text", text=f"时间：{frame['time']:.1f} 秒"),
                       ImageContent(type="image", mimeType=frame["mime_type"], data=frame["data"])])
    return output


@mcp.tool(annotations=READ_EXTERNAL)
async def browse_trending(platform: str, limit: int = 10, trend_id: str = "", keyword: str = "") -> Any:
    """浏览平台热门内容。抖音默认返回话题，传 trend_id 或 keyword 查看话题相关作品。"""
    return await _api(f"/api/platforms/{platform}/browse_trending",
                      {"limit": limit, "trend_id": trend_id, "keyword": keyword})


@mcp.tool(annotations=READ_EXTERNAL)
async def search_content(platform: str, keyword: str, limit: int = 10, order: str = "general") -> Any:
    """在平台搜索作品；order 支持 general、latest 和 popular。"""
    return await _api(f"/api/platforms/{platform}/search_content",
                      {"keyword": keyword, "limit": limit, "order": order})


@mcp.tool(annotations=READ_EXTERNAL)
async def get_content(platform: str, content_id: str, resource_type: int = 1) -> Any:
    """读取作品标题、描述、作者及平台提供的元数据，不代表已看过视频画面。"""
    return await _api(f"/api/platforms/{platform}/get_content",
                      {"content_id": content_id, "resource_type": resource_type})


@mcp.tool(annotations=READ_EXTERNAL)
async def get_content_comments(platform: str, content_id: str, resource_type: int = 1,
                               cursor: str = "", limit: int = 20) -> Any:
    """按置顶、作者补充、高赞优先读取作品评论；返回 items 和 next_cursor。B 站可继续翻页，抖音暂不支持外部游标。"""
    return await _api(f"/api/platforms/{platform}/get_content_comments",
                      {"content_id": content_id, "resource_type": resource_type, "cursor": cursor, "limit": limit})


def _video_content(result: dict[str, Any]) -> list[Any]:
    metadata = {key: value for key, value in result.items() if key not in ("frames", "ok")}
    if result.get("content_type") == "image":
        output = [TextContent(type="text", text="以下是抖音图文作品的图片、正文及优先评论。评论是观众观点，不等于作品事实。\n" + json.dumps(metadata, ensure_ascii=False))]
        for index, frame in enumerate(result["frames"], 1):
            output.extend([TextContent(type="text", text=f"第 {index} 张图片"),
                           ImageContent(type="image", mimeType=frame["mime_type"], data=frame["data"])])
        return output
    output = [TextContent(type="text", text="以下是按时间采样的画面、可用字幕、本地语音转写及优先评论；不是逐帧观看。评论是观众观点，不等于作品事实。"
                          "仅依据实际材料总结，转写可能有误；音频关闭或无语音时不要编造声音内容。\n"
                          + json.dumps(metadata, ensure_ascii=False))]
    for frame in result["frames"]:
        output.extend([TextContent(type="text", text=f"画面时间：{frame['time']:.1f} 秒"),
                       ImageContent(type="image", mimeType=frame["mime_type"], data=frame["data"])])
    return output


@mcp.tool(annotations=READ_EXTERNAL, structured_output=False)
async def fetch_media_frames(platform: str, content_id: str, timestamps: list[float] | None = None,
                             frame_count: int = 8, include_audio: bool | None = None,
                             include_comments: bool | None = None, comment_limit: int | None = None) -> Any:
    """阅读视频抽帧或图文图片，附可用语音和优先评论。comment_limit 为 10–50；include_comments=false 可仅看作品。"""
    result = await _api(f"/api/platforms/{platform}/fetch_media_frames",
                        {"content_id": content_id, "timestamps": timestamps, "frame_count": frame_count,
                         "include_audio": include_audio, "include_comments": include_comments,
                         "comment_limit": comment_limit}, timeout=1800)
    return _video_content(result)


@mcp.tool(annotations=READ_EXTERNAL, structured_output=False)
async def watch_video(platform: str, content_id: str, frame_count: int = 8,
                      include_audio: bool | None = None, include_comments: bool | None = None,
                      comment_limit: int | None = None) -> Any:
    """阅读 B 站/抖音视频或抖音图文：画面 + 可用语音/字幕 + 优先评论。comment_limit 为 10–50，默认使用控制台设置（30）。"""
    return await fetch_media_frames(platform, content_id, frame_count=frame_count, include_audio=include_audio,
                                   include_comments=include_comments, comment_limit=comment_limit)


@mcp.tool(annotations=EXTERNAL)
async def generate_image(prompt: str, model: str = "", size: str = "", n: int = 1) -> Any:
    """使用控制台配置的图像生成 API 创作图片，返回本机图片文件路径；可选指定模型、尺寸和张数。"""
    return await _api("/api/image-generation/generate", {"prompt": prompt, "model": model, "size": size, "n": n},
                      timeout=320)


@mcp.tool(annotations=READ)
async def list_creation_files() -> Any:
    """列出控制台设定的创作文件夹中的素材和作品文件。"""
    return await _api("/api/creation/files")


@mcp.tool(annotations=READ)
async def read_creation_file(path: str, binary: bool = False) -> Any:
    """读取创作文件夹素材；文本按 UTF-8 返回，binary 时返回 base64。"""
    return await _api("/api/creation/read", {"path": path, "binary": binary})


@mcp.tool(annotations=WRITE)
async def scan_creation_folder() -> Any:
    """扫描创作文件夹的新增或改动文件并唤醒创作工作者。"""
    return await _api("/api/creation/scan")


if __name__ == "__main__":
    mcp.run(transport="stdio")
