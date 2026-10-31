"""应用宿主：DSH、调度器、平台监听和控制台 API。"""
from __future__ import annotations

import asyncio
import logging
import math
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .creation import CreationFolder
from .dsh_runtime import DshRuntime
from .image_generation import ImageGeneration
from .memory import MemoryCenter
from .platform_service import PlatformService
from .scheduler import Scheduler
from .store import Store

log = logging.getLogger(__name__)


class Companion:
    def __init__(self, root: str | Path, *, data_dir: str | Path | None = None,
                 api_url: str = "http://127.0.0.1:8765"):
        self.root = Path(root).resolve()
        self.data_dir = Path(data_dir).resolve() if data_dir else self.root / "var"
        self.store = Store(self.data_dir / "companion.sqlite3")
        self.memory = MemoryCenter(self.store)
        self.runtime = DshRuntime(self.store, self.root, api_url=api_url, data_dir=self.data_dir)
        self.scheduler = Scheduler(self.store, self.runtime.execute_job)
        self.platforms = PlatformService(self.store, self.root, self.scheduler, data_dir=self.data_dir)
        self.creation = CreationFolder(self.store, self.scheduler, self.root)
        self.images = ImageGeneration(self.store, self.data_dir)
        self.listeners: list[asyncio.Task] = []
        self.listener_status: dict[str, dict[str, Any]] = {}

    def ensure_default_timers(self) -> None:
        settings = self.store.get_config("autonomy")
        titles = {timer["title"] for timer in self.store.list_timers()}
        for title, kind, interval in (("主会话自主唤醒", "autonomous_main", settings["main_interval_seconds"]),
                                      ("记忆整理", "memory_compaction", settings["memory_interval_seconds"])):
            if title not in titles:
                self.scheduler.create_timer(title, "main", kind, {}, next_at=time.time() + interval,
                                            interval_seconds=interval, priority=40, autonomous=True)

    async def start(self) -> None:
        await asyncio.to_thread(self.runtime.start)
        dy = self.store.get_config("platforms")["douyin"]
        await self.platforms.adapters["douyin"].start(auto_restore=dy["enabled"] and (dy["messages"] or dy.get("groups", False)))
        wx = self.store.get_config("platforms")["weixin"]
        await self.platforms.adapters["weixin"].start(auto_restore=wx["enabled"] and wx.get("messages", False))
        self.ensure_default_timers()
        await self.scheduler.start()
        for platform, channel in (("bilibili", "messages"), ("bilibili", "comments"),
                                  ("bilibili", "groups"), ("douyin", "groups"), ("douyin", "comments"),
                                  ("weixin", "messages")):
            self.listeners.append(asyncio.create_task(self.listen(platform, channel), name=f"{platform}:{channel}"))
        self.listeners.append(asyncio.create_task(self.scan_creation(), name="creation-scan"))
        self.listeners.append(asyncio.create_task(self.auto_compaction(), name="auto-compaction"))

    async def stop(self) -> None:
        for task in self.listeners:
            task.cancel()
        if self.listeners:
            await asyncio.gather(*self.listeners, return_exceptions=True)
        self.listeners.clear()
        await self.scheduler.stop()
        try:
            await self.platforms.adapters["douyin"].close()
        finally:
            try:
                await self.platforms.adapters["weixin"].close()
            finally:
                await asyncio.to_thread(self.runtime.close)

    async def listen(self, platform: str, channel: str) -> None:
        name = f"{platform}:{channel}"
        while True:
            settings = self.store.get_config("platforms")[platform]
            if settings["enabled"] and settings[channel]:
                try:
                    if platform == "bilibili":
                        auth = await self.platforms.check_auth(platform)
                        if not auth["authenticated"]:
                            self.listener_status[name] = {"state": "not_logged_in", "detail": auth}
                            await asyncio.sleep(settings["poll_seconds"])
                            continue
                    events = await getattr(self.platforms, "poll_" + channel)(platform)
                    self.listener_status[name] = {"state": "ok", "last_poll": time.time(), "new_events": len(events)}
                except Exception as exc:
                    log.exception("平台监听出错 %s", name)
                    self.listener_status[name] = {"state": "error", "last_poll": time.time(),
                                                  "error": f"{type(exc).__name__}: {exc}"}
            else:
                self.listener_status[name] = {"state": "disabled"}
            await asyncio.sleep(settings["poll_seconds"])

    def schedule_auto_compactions(self) -> list[dict[str, Any]]:
        settings = self.store.get_config("session_compaction")
        jobs = []
        for actor in self.runtime.session_contexts():
            config = settings[actor["id"]]
            if not config["enabled"]:
                continue
            threshold = int(config["threshold_tokens"])
            tokens, revision = actor["context_tokens"], actor["context_revision"]
            if not actor["session_id"] or actor["status"] != "idle" or tokens is None or revision is None or tokens < threshold:
                continue
            last = actor["metadata"].get("last_auto_compaction", {})
            if (last.get("session_id") == actor["session_id"] and last.get("threshold_tokens") == threshold
                    and last.get("revision") is not None and revision <= last["revision"]):
                continue
            if not self.store.has_pending_session_compaction(actor["id"]):
                jobs.append(self.scheduler.enqueue(actor["id"], "session_compact",
                    {"automatic": True, "threshold_tokens": threshold}, priority=0, autonomous=False))
        return jobs

    async def auto_compaction(self) -> None:
        while True:
            self.schedule_auto_compactions()
            await asyncio.sleep(5)

    async def scan_creation(self) -> None:
        while True:
            settings = self.store.get_config("autonomy")
            if settings["enabled"] and not self.store.get_config("paused"):
                self.creation.scan()
            await asyncio.sleep(settings["background_scan_seconds"])

    async def change_platforms(self, value: dict[str, Any]) -> dict[str, Any]:
        before = self.store.get_config("platforms")
        for name in ("bilibili", "douyin", "weixin"):
            value.setdefault(name, dict(before[name]))
        for name in ("bilibili", "douyin"):
            if value[name].get("groups", False) and not before[name].get("groups", False):
                value[name]["group_started_at"] = time.time()
        self.store.set_config("platforms", value)
        old_messages = before["douyin"]["enabled"] and (before["douyin"]["messages"] or before["douyin"].get("groups", False))
        new_messages = value["douyin"]["enabled"] and (value["douyin"]["messages"] or value["douyin"].get("groups", False))
        if old_messages != new_messages:
            im = self.platforms.adapters["douyin"].im
            if new_messages:
                if not (await im.check_auth())["authenticated"]:
                    await im.call("restore")
            else:
                await im.call("stop")
        old_weixin = before["weixin"]["enabled"] and before["weixin"].get("messages", False)
        new_weixin = value["weixin"]["enabled"] and value["weixin"].get("messages", False)
        wx = self.platforms.adapters["weixin"]
        if old_weixin != new_weixin:
            if new_weixin:
                if not (await wx.check_auth())["authenticated"]:
                    await wx.call("restore")
            else:
                await wx.call("stop")
        self.scheduler.notify()
        return value

    async def change_model(self, model: str, base_url: str, api_key: str,
                           provider: str = "companion-model", context_length: int | None = None,
                           max_tokens: int | None = None, reasoning_effort: str | None = None,
                           api_format: str | None = None, compatibility: str | None = None) -> dict[str, Any]:
        current = self.store.get_config("dsh")
        context = context_length if context_length is not None else current["context_length"]
        output = max_tokens if max_tokens is not None else current["max_tokens"]
        if context <= 0 or output <= 0 or output > context:
            raise ValueError("上下文长度和输出上限须大于零，输出上限不能超过上下文长度")
        if reasoning_effort is not None and reasoning_effort not in ("off", "low", "high", "max"):
            raise ValueError("不支持的思考等级")
        if api_format is not None and api_format not in ("openai-completions", "openai-responses", "anthropic-messages"):
            raise ValueError("不支持的 API 接口格式")
        if compatibility is not None and compatibility not in ("deepseek", "standard"):
            raise ValueError("不支持的接口兼容模式")
        await self.scheduler.stop()
        await asyncio.to_thread(self.runtime.close)
        if not api_key and self.runtime.key_file.exists():
            api_key = self.runtime.key_file.read_text(encoding="utf-8").strip()
        config = self.runtime.configure_model(model=model, base_url=base_url, api_key=api_key, provider=provider,
                                              context_length=context_length, max_tokens=max_tokens,
                                              reasoning_effort=reasoning_effort, api_format=api_format,
                                              compatibility=compatibility)
        await asyncio.to_thread(self.runtime.start)
        await self.scheduler.start()
        return config


def create_app(root: str | Path, *, data_dir: str | Path | None = None,
               api_url: str = "http://127.0.0.1:8765") -> FastAPI:
    companion = Companion(root, data_dir=data_dir, api_url=api_url)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await companion.start()
        try:
            yield
        finally:
            await companion.stop()

    app = FastAPI(title="DSH Companion", lifespan=lifespan)
    app.state.companion = companion

    @app.exception_handler(ValueError)
    async def invalid_request(_request, exc: ValueError):
        return JSONResponse(status_code=400, content={"error": str(exc)})

    @app.exception_handler(Exception)
    async def failure(_request, exc: Exception):
        log.exception("接口执行失败", exc_info=exc)
        return JSONResponse(status_code=500, content={"error": f"{type(exc).__name__}: {exc}"})

    @app.get("/api/status")
    async def status():
        return {"scheduler": companion.scheduler.status(), "actors": companion.store.list_actors(),
                "listeners": companion.listener_status, "metrics": companion.store.job_metrics(),
                "douyin_im": companion.platforms.adapters["douyin"].im.status,
                "weixin": companion.platforms.adapters["weixin"].status}

    @app.get("/api/config")
    async def get_config():
        return companion.store.get_config()

    @app.put("/api/config/{key}")
    async def set_config(key: str, body: dict[str, Any]):
        if key == "platforms":
            return await companion.change_platforms(body["value"])
        if key == "pricing":
            pricing = body["value"]
            profiles = pricing.get("profiles", [])
            ids = [item.get("id") for item in profiles]
            if not profiles or len(set(ids)) != len(ids) or pricing.get("selected_profile") not in ids:
                raise HTTPException(status_code=400, detail="请选择有效的计费方案，且方案 ID 不能重复")
            for profile in profiles:
                if not profile.get("id") or not profile.get("name"):
                    raise HTTPException(status_code=400, detail="计费方案需要名称和 ID")
                for field in ("uncached_input", "cached_input", "output"):
                    try:
                        number = float(profile[field])
                    except (KeyError, TypeError, ValueError):
                        raise HTTPException(status_code=400, detail="计费单价必须是非负数字")
                    if not math.isfinite(number) or number < 0:
                        raise HTTPException(status_code=400, detail="计费单价必须是非负数字")
                    profile[field] = number
        previous = companion.store.get_config(key)
        value = companion.store.set_config(key, body["value"])
        if key == "autonomy":
            for timer in companion.store.list_timers():
                field = {"主会话自主唤醒": "main_interval_seconds", "记忆整理": "memory_interval_seconds"}.get(timer["title"])
                if field and previous[field] != value[field]:
                    companion.store.update_timer(timer["id"], {"interval_seconds": value[field], "next_at": time.time() + value[field]})
        companion.scheduler.notify()
        return value

    @app.post("/api/model")
    async def set_model(body: dict[str, Any]):
        return await companion.change_model(body["model"], body["base_url"], body.get("api_key", ""),
                                            body.get("provider", "companion-model"),
                                            context_length=body.get("context_length"), max_tokens=body.get("max_tokens"),
                                            reasoning_effort=body.get("reasoning_effort"),
                                            api_format=body.get("api_format"), compatibility=body.get("compatibility"))

    @app.post("/api/model/import")
    async def import_model(body: dict[str, Any]):
        lines = [line.strip() for line in Path(body["path"]).read_text(encoding="utf-8-sig").splitlines() if line.strip()]
        index = int(body.get("index", 0)) * 3
        base_url, api_key, model = lines[index:index + 3]
        return await companion.change_model(model, base_url, api_key)

    @app.post("/api/control/{operation}")
    async def control(operation: str):
        if operation == "pause":
            companion.scheduler.pause()
        elif operation == "resume":
            companion.scheduler.resume()
        else:
            raise HTTPException(status_code=404, detail="未知操作")
        return companion.scheduler.status()

    @app.post("/api/chat")
    async def chat(body: dict[str, Any]):
        return companion.scheduler.enqueue("main", "manual", {"text": body["text"]}, priority=10)

    @app.get("/api/jobs")
    async def jobs(status: str | None = None, actor_id: str | None = None, limit: int = 100):
        return companion.store.list_jobs(status=status, actor_id=actor_id, limit=limit)

    @app.get("/api/jobs/{job_id}")
    async def job(job_id: str):
        return companion.store.get_job(job_id)

    @app.post("/api/jobs")
    async def new_job(body: dict[str, Any]):
        return companion.scheduler.enqueue(body["actor_id"], body["kind"], body.get("payload", {}),
                                           priority=body.get("priority"), autonomous=body.get("autonomous", False))

    @app.post("/api/jobs/{job_id}/{operation}")
    async def update_job(job_id: str, operation: str):
        if operation == "cancel":
            return {"ok": companion.scheduler.cancel(job_id)}
        if operation == "retry":
            return {"ok": companion.scheduler.retry(job_id)}
        raise HTTPException(status_code=404, detail="未知任务操作")

    @app.get("/api/events")
    async def events(platform: str | None = None, status: str | None = None, limit: int = 100):
        return companion.store.list_events(platform=platform, status=status, limit=limit)

    @app.get("/api/events/{event_id}")
    async def event_detail(event_id: str):
        return companion.store.get_event(event_id)

    @app.get("/api/media")
    async def media(platform: str | None = None, conversation_id: str | None = None,
                    sender_id: str | None = None, limit: int = 100):
        return companion.store.list_media(platform=platform, conversation_id=conversation_id,
                                          sender_id=sender_id, limit=limit)

    @app.get("/api/media/{media_id}")
    async def media_detail(media_id: str):
        result = companion.store.get_media(media_id)
        if result is None:
            raise HTTPException(status_code=404, detail="媒体不存在")
        return result

    @app.delete("/api/media/{media_id}")
    async def delete_media(media_id: str):
        return {"ok": companion.store.delete_media(media_id)}

    @app.post("/api/events")
    async def ingest_event(body: dict[str, Any]):
        data = dict(body)
        platform, event_type, payload = data.pop("platform"), data.pop("event_type"), data.pop("payload")
        event, job, created = companion.scheduler.enqueue_event(platform, event_type, payload, **data)
        return {"event": event, "job": job, "created": created}

    @app.get("/api/actors")
    async def actors():
        return companion.store.list_actors()

    @app.get("/api/sessions")
    async def sessions():
        settings = companion.store.get_config("session_compaction")
        return [{**actor, "auto_compaction": settings[actor["id"]]}
                for actor in companion.runtime.session_contexts()]

    @app.post("/api/sessions/{actor_id}/{operation}")
    async def session_operation(actor_id: str, operation: str):
        if companion.store.get_actor(actor_id) is None:
            raise HTTPException(status_code=404, detail="会话角色不存在")
        if operation not in ("reset", "compact"):
            raise HTTPException(status_code=404, detail="未知会话操作")
        return companion.scheduler.enqueue(actor_id, "session_" + operation, {}, priority=0, autonomous=False)

    @app.put("/api/sessions/{actor_id}/auto-compaction")
    async def set_session_auto_compaction(actor_id: str, body: dict[str, Any]):
        if companion.store.get_actor(actor_id) is None:
            raise HTTPException(status_code=404, detail="会话角色不存在")
        settings = companion.store.get_config("session_compaction")
        settings[actor_id] = {"enabled": body["enabled"], "threshold_tokens": int(body["threshold_tokens"])}
        companion.store.set_config("session_compaction", settings)
        return settings[actor_id]

    @app.get("/api/image-generation/config")
    async def image_generation_config():
        return companion.images.config()

    @app.put("/api/image-generation/config")
    async def set_image_generation_config(body: dict[str, Any]):
        return companion.images.configure(url=body["url"], api_key=body.get("api_key", ""),
                                          model=body.get("model", ""))

    @app.post("/api/image-generation/generate")
    async def generate_image(body: dict[str, Any]):
        return await companion.images.generate(body["prompt"], model=body.get("model", ""),
                                               size=body.get("size", ""), n=body.get("n", 1))

    @app.get("/api/actions")
    async def actions(platform: str | None = None, kind: str | None = None, limit: int = 100):
        return companion.store.list_actions(platform=platform, kind=kind, limit=limit)

    @app.get("/api/usage")
    async def model_usage():
        return companion.store.model_usage_summary()

    @app.get("/api/metrics")
    async def metrics(actor_id: str | None = None):
        return companion.store.job_metrics(actor_id=actor_id)

    @app.get("/api/timers")
    async def timers():
        return companion.store.list_timers()

    @app.post("/api/timers")
    async def create_timer(body: dict[str, Any]):
        data = dict(body)
        title, actor_id, kind, payload = data.pop("title"), data.pop("actor_id"), data.pop("kind"), data.pop("payload", {})
        return companion.scheduler.create_timer(title, actor_id, kind, payload, **data)

    @app.patch("/api/timers/{timer_id}")
    async def update_timer(timer_id: str, body: dict[str, Any]):
        result = companion.store.update_timer(timer_id, body)
        companion.scheduler.notify()
        return result

    @app.delete("/api/timers/{timer_id}")
    async def delete_timer(timer_id: str):
        return {"ok": companion.store.delete_timer(timer_id)}

    @app.get("/api/memories")
    async def memories(kind: str | None = None, platform: str | None = None, query: str = "",
                       status: str = "active", limit: int = 100):
        return companion.store.list_memories(kind=kind, platform=platform, query=query,
                                             status=None if status == "all" else status, limit=limit)

    @app.post("/api/memories")
    async def write_memory(body: dict[str, Any]):
        return companion.store.put_memory(**body)

    @app.post("/api/memory/broadcast")
    async def broadcast_memory(body: dict[str, Any]):
        result = companion.memory.broadcast_background(body["content"], actor_ids=body.get("actor_ids"))
        companion.scheduler.notify()
        return result

    @app.get("/api/memories/{memory_id}")
    async def memory(memory_id: str):
        return companion.store.get_memory(memory_id)

    @app.delete("/api/memories/{memory_id}")
    async def delete_memory(memory_id: str):
        return {"ok": companion.store.delete_memory(memory_id)}

    @app.get("/api/video-reading/quota")
    async def video_quota():
        return companion.platforms.video_quota()

    @app.get("/api/platforms/{platform}/quota")
    async def quota(platform: str):
        return companion.platforms.publishing_quota(platform)

    @app.get("/api/platforms/{platform}/image-quota")
    async def image_quota(platform: str):
        return companion.platforms.image_quota(platform)

    @app.post("/api/platforms/{platform}/login/start")
    async def platform_login(platform: str, body: dict[str, Any] = {}):
        adapter = companion.platforms.adapters[platform]
        if platform == "douyin":
            return await adapter.begin_login(body.get("channel", "messages"))
        if platform == "bilibili":
            return await adapter.begin_login()
        if platform == "weixin":
            return await adapter.begin_login()
        raise HTTPException(status_code=501, detail="该平台接口预留")

    @app.get("/api/platforms/{platform}/login/status")
    async def platform_login_status(platform: str, channel: str = "messages"):
        adapter = companion.platforms.adapters[platform]
        if platform == "douyin":
            return await adapter.login_status(channel)
        if platform == "bilibili":
            return await adapter.login_status()
        if platform == "weixin":
            return await adapter.login_status()
        raise HTTPException(status_code=501, detail="该平台接口预留")

    @app.get("/api/platforms/{platform}/login/qr")
    async def platform_qr(platform: str, channel: str = "messages"):
        base = companion.data_dir / "platforms"
        path = base / ("bilibili/login-qr.png" if platform == "bilibili" else
                       "douyin-im/login-qr.png" if platform == "douyin" and channel == "messages" else
                       "douyin-web/login-qr.png" if platform == "douyin" else "weixin/login-qr.svg")
        return FileResponse(path, media_type="image/svg+xml" if platform == "weixin" else "image/png")

    @app.post("/api/platforms/{platform}/{operation}")
    async def platform_operation(platform: str, operation: str, body: dict[str, Any] = {}):
        if operation == "check_auth":
            return await companion.platforms.check_auth(platform)
        if operation in ("poll_messages", "poll_comments", "poll_groups"):
            return await getattr(companion.platforms, operation)(platform)
        if operation == "list_joined_groups":
            return await companion.platforms.list_joined_groups(platform)
        if operation == "get_group_history":
            return await companion.platforms.get_group_history(platform, body)
        if operation == "inspect_video":
            return await companion.platforms.fetch_media_frames(platform, **body, include_audio=False)
        if operation in ("browse_trending", "browse_recommended", "list_my_posts", "search_content", "get_content", "get_content_comments"):
            return await companion.platforms.browse(platform, operation, body)
        if operation == "fetch_media_frames":
            return await companion.platforms.fetch_media_frames(platform, **body)
        if operation in ("send_message", "send_group_message", "reply_comment", "publish_content"):
            payload = dict(body)
            job_id = payload.pop("job_id", None)
            return await companion.platforms.action(platform, operation, payload, job_id=job_id)
        raise HTTPException(status_code=404, detail="未知平台操作")

    @app.post("/api/creation/files")
    async def creation_files():
        return {"folder": str(companion.creation.path()), "files": companion.creation.list_files()}

    @app.post("/api/creation/open")
    async def creation_open():
        return {"folder": companion.creation.open_folder()}

    @app.post("/api/creation/read")
    async def creation_read(body: dict[str, Any]):
        return companion.creation.read_file(body["path"], binary=body.get("binary", False))

    @app.post("/api/creation/scan")
    async def creation_scan():
        return {"jobs": companion.creation.scan()}

    static = Path(__file__).resolve().parent / "static"
    if static.exists():
        app.mount("/assets", StaticFiles(directory=static), name="assets")

        @app.get("/")
        async def index():
            return FileResponse(static / "index.html")
    return app
