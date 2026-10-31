"""桌宠的项目状态与进程入口，不运行额外的模型或调度器。"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

# Stable action IDs are shared with the desktop animation player.
ACTIVITIES = {
    "watch_video": ("看视频 / 图文", "watch"), "fetch_media_frames": ("看作品", "watch"),
    "get_content_comments": ("读评论", "read"), "browse_douyin_recommended": ("刷抖音推荐", "watch"),
    "browse_trending": ("找热点", "search"), "search_content": ("找作品", "search"),
    "get_content": ("读作品详情", "read"), "generate_image": ("画图", "draw"),
    "publish_content": ("发作品", "organize"), "send_message": ("回私信", "reply"),
    "send_group_message": ("聊群", "reply"), "reply_comment": ("回评论", "reply"),
    "delegate_job": ("安排分工", "organize"), "delegate_group_chat": ("安排群聊", "reply"),
    "read_creation_file": ("读素材", "read"), "list_creation_files": ("挑素材", "search"),
    "get_decision_context": ("琢磨下一步", "think"), "apply_memory_compaction": ("整理记忆", "organize"),
    "create_timer": ("安排后续", "organize"), "shell": ("制作内容", "code"),
    "write": ("写稿", "write"), "edit": ("打磨内容", "write"), "read": ("读资料", "read"),
    "skill": ("选择工作方法", "think"), "add_scratchpad_note": ("记下一笔", "write"),
    "set_recent_context": ("更新进度", "organize"),
}
ROLE_NAMES = {"main": "自主思考", "media": "研究作品", "creator": "创作", "publisher": "发布作品"}
ROLE_ACTIONS = {"main": "think", "media": "watch", "creator": "write", "publisher": "organize"}


def job_label(job: dict[str, Any]) -> str:
    actor = job["actor_id"]
    if "comment" in actor: return "回复评论"
    if "group" in actor: return "群聊互动"
    if "chat" in actor: return "回复私信"
    if job["kind"] in ("memory_compaction", "session_compact"): return "整理记忆"
    if job["kind"] == "manual": return "陪你聊天"
    return ROLE_NAMES.get(actor, "处理任务")


def job_action(job: dict[str, Any]) -> str:
    actor = job["actor_id"]
    if any(kind in actor for kind in ("comment", "group", "chat")) or job["kind"] == "manual":
        return "reply"
    if job["kind"] in ("memory_compaction", "session_compact"):
        return "organize"
    return ROLE_ACTIONS.get(actor, "think")


def desktop_status(companion) -> dict[str, Any]:
    store = companion.store
    start = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    with store.connect() as db:
        running = [dict(row) for row in db.execute(
            "SELECT id,actor_id,kind,status FROM jobs WHERE status='running' ORDER BY started_at")]
        waiting = [dict(row) for row in db.execute(
            "SELECT id,actor_id,kind,status FROM jobs WHERE status='ready' ORDER BY priority,queued_at LIMIT 5")]
        waiting_count = db.execute("SELECT COUNT(*) FROM jobs WHERE status='ready'").fetchone()[0]
        completed = db.execute("SELECT COUNT(*) FROM jobs WHERE status='succeeded' AND finished_at>=?", (start,)).fetchone()[0]
        recent_done = db.execute("SELECT finished_at FROM jobs WHERE status='succeeded' ORDER BY finished_at DESC LIMIT 1").fetchone()
    paused = store.get_config("paused") or not store.get_config("autonomy")["enabled"]
    media = companion.platforms.media_activity
    tools = [activity for activity in companion.runtime.activity.copy().values()
             if any(job["id"] == activity["job_id"] for job in running)]
    if media:
        label, action = {"frames": ("看视频 / 图文", "watch"), "comments": ("读评论", "read")}[media["phase"]]
    elif tools:
        tool = tools[-1]
        name = tool["tool"].split("__")[-1]
        job = next(job for job in running if job["id"] == tool["job_id"])
        label, action = ACTIVITIES.get(name, (job_label(job), job_action(job)))
    elif running:
        # A user conversation stays visible while background roles keep working.
        job = next((job for job in running if job_action(job) == "reply"), running[0])
        label, action = job_label(job), job_action(job)
    else:
        label, action = ("休息", "sleep") if paused else ("自由待命", "idle")
    state = "working" if running or media else "paused" if paused else "idle"
    if state == "idle" and recent_done and time.time() - recent_done["finished_at"] < 20:
        state, label, action = "completed", "刚做完一件事", "celebrate"
    timers = [timer for timer in store.list_timers(enabled=True) if timer["actor_id"] == "main"]
    title = {"idle": "大肥鱼自由待命", "completed": "大肥鱼刚忙完", "paused": "大肥鱼在休息"}.get(state, "大肥鱼在" + label)
    return {"state": state, "action": action, "title": title, "currentTask": label,
            "activeCount": len(running), "waitingCount": waiting_count,
            "completed": completed,
            "direction": store.get_config("autonomy").get("direction", ""),
            "todos": [{"content": job_label(job), "status": "in_progress" if job["status"] == "running" else "pending",
                       "actor_id": job["actor_id"], "job_id": job["id"], "action": job_action(job)} for job in running + waiting[:5]],
            "media": media, "nextWakeAt": min((timer["next_at"] for timer in timers), default=None),
            "paused": bool(paused)}


class DesktopPet:
    def __init__(self, store, data_dir: Path, api_url: str):
        self.store = store
        self.data_dir = data_dir
        self.api_url = api_url
        self.process: subprocess.Popen | None = None

    def status(self) -> dict[str, Any]:
        return {"running": self.process is not None and self.process.poll() is None,
                "enabled": self.store.get_config("desktop_pet")["enabled"]}

    def start(self) -> dict[str, Any]:
        if self.status()["running"]:
            return self.status()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        executable = Path(sys.executable).with_name("pythonw.exe")
        if not executable.exists(): executable = Path(sys.executable)
        env = {**os.environ, "COMPANION_API_URL": self.api_url,
               "COMPANION_PET_STATE": str(self.data_dir / "pet-position.json"),
               "COMPANION_PET_SCALE": str(self.store.get_config("desktop_pet")["scale"])}
        with (self.data_dir / "desktop-pet.log").open("a", encoding="utf-8") as output:
            self.process = subprocess.Popen([str(executable), "-m", "dsh_companion.desktop_pet.window"],
                env=env, stdout=output, stderr=output, creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
        return self.status()

    def stop(self) -> dict[str, Any]:
        if self.status()["running"]:
            self.process.terminate()
            self.process.wait()
        self.process = None
        return self.status()
