"""三层共享记忆、熟人档案、背景同步和转告。"""
from __future__ import annotations

from typing import Any

from .store import Store


class MemoryCenter:
    def __init__(self, store: Store):
        self.store = store

    def get_persona(self) -> str:
        return self.store.get_memory("persona")["content"]

    def set_persona(self, content: str) -> dict[str, Any]:
        return self.store.put_memory("persona", content, memory_id="persona", title="核心人格")

    def get_recent_context(self) -> str:
        return self.store.get_memory("active_context")["content"]

    def set_recent_context(self, content: str) -> dict[str, Any]:
        return self.store.put_memory("context", content, memory_id="active_context", title="近期背景看板")

    def search_memory(self, query: str, *, kind: str | None = None,
                      platform: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        return self.store.list_memories(kind=kind, platform=platform, query=query, limit=limit)

    def add_episode(self, content: str, *, title: str = "", tags: list[str] | None = None,
                    metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.store.put_memory("episode", content, title=title, tags=tags, metadata=metadata)

    def update_episode(self, memory_id: str, content: str, *, title: str | None = None,
                       tags: list[str] | None = None, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        previous = self.store.get_memory(memory_id)
        return self.store.put_memory("episode", content, memory_id=memory_id,
                                     title=previous["title"] if title is None and previous else title or "",
                                     tags=tags, metadata=metadata)

    def get_acquaintance(self, platform: str, subject_id: str) -> dict[str, Any] | None:
        matches = self.store.list_memories(kind="acquaintance", platform=platform, subject_id=str(subject_id), limit=1)
        return matches[0] if matches else None

    def set_acquaintance(self, platform: str, subject_id: str, content: str, *, nickname: str | None = None,
                         tags: list[str] | None = None, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        matches = self.store.list_memories(kind="acquaintance", platform=platform,
                                           subject_id=str(subject_id), status=None, limit=1)
        existing = matches[0] if matches else None
        return self.store.put_memory("acquaintance", content,
                                     memory_id=existing["id"] if existing else None,
                                     title=existing["title"] if nickname is None and existing else nickname or "",
                                     platform=platform, subject_id=str(subject_id),
                                     tags=tags, metadata=metadata)

    def add_scratchpad_note(self, content: str, *, actor_id: str = "", platform: str = "",
                            subject_id: str = "", metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        details = dict(metadata or {})
        details["actor_id"] = actor_id
        return self.store.put_memory("scratchpad", content, platform=platform,
                                     subject_id=str(subject_id), metadata=details)

    def list_scratchpad(self, *, limit: int = 100) -> list[dict[str, Any]]:
        return self.store.list_memories(kind="scratchpad", limit=limit)

    def archive_scratchpad(self, note_ids: list[str]) -> int:
        return sum(self.store.set_memory_status(note_id, "archived") for note_id in note_ids)

    def apply_compaction(self, *, recent_context: str, episodes: list[dict[str, Any]],
                         acquaintances: list[dict[str, Any]], note_ids: list[str]) -> dict[str, Any]:
        """将主会话整理出的内容写入 L2/L3，并归档已处理流水。"""
        self.set_recent_context(recent_context)
        for episode in episodes:
            self.add_episode(episode["content"], title=episode.get("title", ""),
                             tags=episode.get("tags"), metadata=episode.get("metadata"))
        for person in acquaintances:
            self.set_acquaintance(person["platform"], str(person["subject_id"]), person["content"],
                                  nickname=person.get("nickname"), tags=person.get("tags"),
                                  metadata=person.get("metadata"))
        return {"episodes": len(episodes), "acquaintances": len(acquaintances),
                "archived_notes": self.archive_scratchpad(note_ids)}

    def assemble_context(self, *, actor_id: str, platform: str = "", sender_id: str = "") -> dict[str, Any]:
        person = self.get_acquaintance(platform, str(sender_id)) if platform and sender_id else None
        return {"actor_id": actor_id, "persona": self.get_persona(),
                "recent_context": self.get_recent_context(), "acquaintance": person}

    def broadcast_background(self, content: str, *, actor_ids: list[str] | None = None,
                             update_board: bool = True) -> list[dict[str, Any]]:
        """更新共享看板，并把背景通知放入各角色原有队列。"""
        if update_board:
            self.set_recent_context(content)
        if actor_ids is None:
            platforms = self.store.get_config("platforms")
            actor_ids = []
            for platform, prefix in (("bilibili", "bili"), ("douyin", "douyin"), ("xiaohongshu", "xhs")):
                if platforms[platform]["enabled"]:
                    actor_ids.extend([prefix + "_chat", prefix + "_comment"])
        return [self.store.create_job(actor, "background_sync", {"context": content}, priority=40)
                for actor in actor_ids]

    def relay_to_main(self, *, platform: str, sender_id: str, message: str,
                      source_event_id: str = "", account_id: str = "") -> dict[str, Any]:
        """按控制台的管理员 ID 核验后，将转告持久化到主会话队列。"""
        admins = self.store.get_config("admin_ids")
        if str(sender_id) not in [str(item) for item in admins.get(platform, [])]:
            raise PermissionError("发送者不在该平台的管理员 ID 列表中")
        event, job, created = self.store.ingest_event(
            platform, "relay", {"message": message, "source_event_id": source_event_id},
            account_id=account_id, sender_id=str(sender_id),
            external_id="relay:" + source_event_id if source_event_id else None,
            actor_id="main", priority=20)
        return {"event": event, "job": job, "created": created}
