"""作品评论的统一阅读格式和优先级。"""
from __future__ import annotations

from typing import Any

PRIORITY = "置顶 → 作者补充 → 高赞 → 平台原始顺序"


def normalize_comment(platform: str, item: dict[str, Any], *, pinned: bool = False,
                      author_id: str = "", parent_id: str = "") -> dict[str, Any]:
    if platform == "bilibili":
        user = item.get("member") or {}
        user_id = str(user.get("mid") or item.get("mid") or "")
        return {"id": str(item["rpid"]), "text": (item.get("content") or {}).get("message", ""),
                "author": user.get("uname", ""), "author_id": user_id,
                "likes": int(item.get("like") or 0), "created_at": item.get("ctime"),
                "pinned": pinned or bool(item.get("is_top")),
                "is_author": bool(author_id and user_id == author_id), "parent_id": parent_id}
    user = item.get("user") or {}
    user_id = str(user.get("uid") or "")
    return {"id": str(item["cid"]), "text": item.get("text", ""),
            "author": user.get("nickname", ""), "author_id": user_id,
            "likes": int(item.get("digg_count") or 0), "created_at": item.get("create_time"),
            "pinned": bool(item.get("is_top") or item.get("author_pin")),
            "is_author": bool(item.get("is_author") or (author_id and user_id == author_id)),
            "parent_id": str(item.get("reply_id") or parent_id or "")}


def prioritize_comments(items: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    unique: dict[str, dict[str, Any]] = {}
    for item in items:
        if not item["text"].strip():
            continue
        previous = unique.get(item["id"])
        if previous:
            previous["pinned"] = previous["pinned"] or item["pinned"]
            previous["is_author"] = previous["is_author"] or item["is_author"]
        else:
            unique[item["id"]] = item
    return sorted(unique.values(), key=lambda item: (
        -int(item["pinned"]), -int(item["is_author"]), -item["likes"]))[:limit]


def comments_result(platform: str, content_id: str, items: list[dict[str, Any]], limit: int,
                    *, next_cursor: str = "", has_more: bool = False) -> dict[str, Any]:
    selected = prioritize_comments(items, limit)
    return {"platform": platform, "content_id": content_id, "items": selected,
            "count": len(selected), "requested_limit": limit, "priority": PRIORITY,
            "next_cursor": next_cursor, "has_more": has_more}
