"""按平台和业务角色分队列的持久调度器。"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from .store import Store

log = logging.getLogger(__name__)
Executor = Callable[[dict[str, Any]], Awaitable[Any]]


class Scheduler:
    """同一角色顺序执行，不同角色独立并行；定时器与任务保存在 SQLite。"""

    def __init__(self, store: Store, execute: Executor, *, tick_seconds: float = 1.0):
        self.store = store
        self.execute = execute
        self.tick_seconds = tick_seconds
        self.running: dict[str, tuple[str, asyncio.Task]] = {}
        self._wake = asyncio.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._runner: asyncio.Task | None = None
        self._stopping = False

    def notify(self) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._wake.set)

    async def start(self) -> int:
        self._loop = asyncio.get_running_loop()
        self._stopping = False
        recovered = self.store.recover_running()
        self._runner = asyncio.create_task(self._run(), name="companion-scheduler")
        self.notify()
        return recovered

    async def stop(self) -> None:
        self._stopping = True
        self.notify()
        if self._runner:
            await self._runner
            self._runner = None
        tasks = [task for _, task in self.running.values()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _run(self) -> None:
        while not self._stopping:
            await self.dispatch_due()
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.tick_seconds)
            except TimeoutError:
                pass

    async def dispatch_due(self) -> int:
        paused = bool(self.store.get_config("paused"))
        autonomy = self.store.get_config("autonomy")
        paused = paused or not autonomy["enabled"]
        self.store.materialize_timers(autonomous_paused=paused)
        count = 0
        for job in self.store.ready_by_actor(autonomous_paused=paused):
            actor_id = job["actor_id"]
            if actor_id in self.running:
                continue
            started = self.store.start_job(job["id"])
            if started is None:
                continue
            task = asyncio.create_task(self._execute(started), name=f"{actor_id}:{job['id']}")
            self.running[actor_id] = (job["id"], task)
            count += 1
        return count

    async def _execute(self, job: dict[str, Any]) -> None:
        try:
            result = await self.execute(job)
        except asyncio.CancelledError:
            current = self.store.get_job(job["id"])
            if current and current["status"] == "cancelled":
                self.store.finish_job(job["id"], error="任务已取消")
            # 停止进程时保留 running，启动时按设计恢复为 ready。
            raise
        except Exception as exc:
            log.exception("任务执行失败 actor=%s job=%s", job["actor_id"], job["id"])
            self.store.finish_job(job["id"], error=f"{type(exc).__name__}: {exc}")
        else:
            self.store.finish_job(job["id"], result=result)
        finally:
            self.running.pop(job["actor_id"], None)
            self.notify()

    def enqueue_event(self, platform: str, event_type: str, payload: dict[str, Any], **kwargs: Any):
        result = self.store.ingest_event(platform, event_type, payload, **kwargs)
        self.notify()
        return result

    def enqueue(self, actor_id: str, kind: str, payload: dict[str, Any], **kwargs: Any):
        result = self.store.create_job(actor_id, kind, payload, **kwargs)
        self.notify()
        return result

    def create_timer(self, title: str, actor_id: str, kind: str, payload: dict[str, Any], **kwargs: Any):
        result = self.store.create_timer(title, actor_id, kind, payload, **kwargs)
        self.notify()
        return result

    def pause(self) -> None:
        self.store.set_config("paused", True)
        self.notify()

    def resume(self) -> None:
        self.store.set_config("paused", False)
        autonomy = self.store.get_config("autonomy")
        if not autonomy["enabled"]:
            autonomy["enabled"] = True
            self.store.set_config("autonomy", autonomy)
        self.notify()

    def retry(self, job_id: str) -> bool:
        result = self.store.retry_job(job_id)
        self.notify()
        return result

    def cancel(self, job_id: str) -> bool:
        result = self.store.cancel_job(job_id)
        for current_id, task in list(self.running.values()):
            if current_id == job_id and self._loop is not None:
                self._loop.call_soon_threadsafe(task.cancel)
        self.notify()
        return result

    def status(self) -> dict[str, Any]:
        return {"paused": self.store.get_config("paused"),
                "autonomy_enabled": self.store.get_config("autonomy")["enabled"],
                "running": {actor: job_id for actor, (job_id, _) in self.running.items()},
                "jobs": {state: len(self.store.list_jobs(status=state, limit=100000))
                         for state in ("ready", "running", "succeeded", "failed", "cancelled")}}
