"""DSH 进程、角色会话与每轮共享上下文。"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import yaml
from deepseek_harness import DeepSeekHarness

from .billing import selected_profile
from .memory import MemoryCenter
from .message_media import cache_image, image_block_from_path, persist_image_block, source_urls_for_content, normalize_message_content, visual_blocks_from_path
from .store import Store

SKILLS = {
    "main": "companion-main",
    "bili_chat": "companion-private-chat",
    "bili_group": "companion-group-chat",
    "douyin_chat": "companion-private-chat",
    "douyin_group": "companion-group-chat",
    "weixin_chat": "companion-private-chat",
    "xhs_chat": "companion-private-chat",
    "bili_comment": "companion-comment",
    "douyin_comment": "companion-comment",
    "xhs_comment": "companion-comment",
    "creator": "companion-creator",
    "publisher": "companion-publisher",
    "media": "companion-media",
}


class DshRuntime:
    """一个独立 DSH 宿主，角色在宿主内各自保持持久会话。"""

    def __init__(self, store: Store, root: str | Path, *, api_url: str = "http://127.0.0.1:8765",
                 data_dir: str | Path | None = None):
        self.store = store
        self.memory = MemoryCenter(store)
        self.root = Path(root).resolve()
        self.var = Path(data_dir).resolve() if data_dir else self.root / "var"
        self.home = self.var / "dsh"
        self.contexts = self.var / "contexts"
        self.controls = self.var / "controls"
        self.key_file = self.var / "model-api-key.txt"
        self.api_url = api_url
        self.harness: DeepSeekHarness | None = None
        self.live_sessions: set[str] = set()
        self.activity: dict[str, dict[str, Any]] = {}

    def configure_model(self, *, model: str, base_url: str, api_key: str,
                        provider: str = "companion-model", context_length: int | None = None,
                        max_tokens: int | None = None, reasoning_effort: str | None = None,
                        api_format: str | None = None, compatibility: str | None = None) -> dict[str, Any]:
        """将模型路由和密钥分别写入项目本身的配置与运行目录。"""
        self.var.mkdir(parents=True, exist_ok=True)
        self.key_file.write_text(api_key, encoding="utf-8")
        config = self.store.get_config("dsh")
        config.update({"model": model, "provider": provider, "base_url": base_url})
        for key, value in (("context_length", context_length), ("max_tokens", max_tokens),
                           ("reasoning_effort", reasoning_effort), ("api_format", api_format),
                           ("compatibility", compatibility)):
            if value is not None:
                config[key] = value
        self.store.set_config("dsh", config)
        return config

    def import_model(self, path: str | Path, index: int = 0) -> dict[str, Any]:
        """从 URL、密钥、模型三行一组的文件迁移一个路由，不修改原文件。"""
        lines = [line.strip() for line in Path(path).read_text(encoding="utf-8-sig").splitlines() if line.strip()]
        url, key, model = lines[index * 3:index * 3 + 3]
        return self.configure_model(model=model, base_url=url, api_key=key)

    def _patch(self, config: dict[str, Any]) -> Path:
        node_plugin = (self.root / "src/dsh_companion/context_plugin.mjs").as_posix()
        compaction_plugin = (self.root / "src/dsh_companion/compaction_plugin.mjs").as_posix()
        patch: list[dict[str, Any]] = [
            {"id": "system-prompt", "config": {
                "personaPrefix": ""}},
            {"id": "skill-filesystem", "config": {
                "includeDefaultRoots": False, "customSkillDirs": [str(self.root / ".agents/skills")]}},
            # 自动压缩由控制台的绝对 Token 阈值统一调度；手动压缩仍使用 DSH 引擎。
            {"id": "compaction-basic", "disabled": True},
        ]
        if config["provider"] == "companion-model":
            model = config["model"]
            api_format = config.get("api_format", "openai-completions")
            provider = {"displayName": "Companion model", "api": api_format,
                "apiKeyEnv": "COMPANION_MODEL_API_KEY", "baseURL": config["base_url"],
                "reasoning": "low",
                "models": [{"id": model, "name": model, "contextWindow": int(config.get("context_length", 200000)),
                    "maxTokens": int(config.get("max_tokens", 8192)), "input": ["text", "image"],
                    "reasoningEfforts": {"off": None, "low": "low", "high": "high", "max": "max"}}]}
            if api_format == "openai-completions" and config.get("compatibility", "deepseek") == "deepseek":
                provider["compat"] = {"thinkingFormat": "deepseek", "supportsReasoningEffort": True,
                                      "requiresReasoningContentOnAssistantMessages": True, "maxTokensField": "max_tokens"}
            patch.append({"id": "llm-pi-ai", "config": {"providers": {"companion-model": provider}}})
        patch.append({"insert": [
            {"id": "companion-compaction", "name": compaction_plugin, "config": {"auto": False}},
            {"id": "companion-context", "name": node_plugin},
            {"id": "companion-mcp", "name": "@deepseek-ai/dsh-mcp-client", "config": {
                "serverName": "companion", "transport": "stdio", "command": sys.executable,
                "args": ["-m", "dsh_companion.mcp_tools"], "cwd": str(self.root),
                "env": {"COMPANION_DATABASE": str(self.store.path), "COMPANION_API_URL": self.api_url,
                        "PYTHONPATH": str(self.root / "src"), "PYTHONUTF8": "1"},
                "failOnStartupError": True, "toolCallTimeoutMs": 1800000,
            }},
        ]})
        path = self.var / "dsh-patch.yml"
        path.write_text(yaml.safe_dump(patch, allow_unicode=True, sort_keys=False), encoding="utf-8")
        return path

    def import_historical_usage(self) -> None:
        """首次启用统计时读取已有会话的用量快照；历史价格仅是当前费率估算。"""
        projections = self.home / "storages/session_projcache/sessions"
        model = self.store.get_config("dsh")
        profile = selected_profile(self.store.get_config("pricing"))
        for actor in self.store.list_actors():
            session_id = actor.get("session_id")
            if not session_id or self.store.session_has_model_usage(session_id):
                continue
            path = projections / (session_id + ".json")
            if not path.exists():
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
            totals = data.get("record", {}).get("rows", {}).get("tokenUsage", {}).get("val", {}).get("totals")
            if not totals or not any(totals.values()):
                continue
            usage = {"inputTokens": totals.get("uncachedInputTokens", 0),
                     "outputTokens": totals.get("outputTokens", 0),
                     "cacheReadTokens": totals.get("cacheReadTokens", 0),
                     "cacheWriteTokens": totals.get("cacheWriteTokens", 0)}
            self.store.record_model_usage(source_id="historical:" + session_id, source="historical_snapshot",
                session_id=session_id, actor_id=actor["id"], platform=actor["platform"],
                provider=model["provider"], model=model["model"], usage=usage, profile=profile,
                recorded_at=path.stat().st_mtime)

    def start(self) -> None:
        self.var.mkdir(parents=True, exist_ok=True)
        self.contexts.mkdir(parents=True, exist_ok=True)
        self.controls.mkdir(parents=True, exist_ok=True)
        for path in self.controls.glob("*.context.json"):
            path.unlink()
        self.import_historical_usage()
        config = self.store.get_config("dsh")
        patch = self._patch(config)
        key = (self.key_file.read_text(encoding="utf-8").strip() if self.key_file.exists()
               else os.environ.get("COMPANION_MODEL_API_KEY", ""))
        # npm 版提供真实原生模块。Windows 官方独立 exe 版本会在会话执行时加载 koffi 失败。
        node = shutil.which("node")
        cli = self.root / "runtime/node_modules/@deepseek-ai/dsh/lib/bin.js"
        if node is None or not cli.exists():
            raise RuntimeError("缺少项目内 DSH Node 运行时；请在 runtime 目录运行 npm install")
        profile = config["profile"]
        env = {"DSH_HOME": str(self.home), "DSH_AGENTS_HOME": str(self.var / "agents"),
               "COMPANION_CONTEXT_DIR": str(self.contexts), "COMPANION_CONTROL_DIR": str(self.controls),
               "COMPANION_MODEL_API_KEY": key,
               "DEEPSEEK_API_KEY": key, "DSH_PERMISSION_MODE": "danger-full-access", "PYTHONUTF8": "1"}
        if config["base_url"] and config["provider"] != "companion-model":
            env["DEEPSEEK_BASE_URL"] = config["base_url"]
        self.harness = DeepSeekHarness(
            _launch_args=(node, str(cli), "--profile", profile, "--patch", str(patch)),
            provider=config["provider"], model=config["model"], reasoning_effort=config["reasoning_effort"],
            max_tokens=config["max_tokens"], cwd=str(self.root), dsh_home=str(self.home), env=env,
            initialize_timeout_seconds=60, request_timeout_seconds=180, shutdown_timeout_seconds=5,
        )
        self.harness.start()
        self.live_sessions.clear()

    def close(self) -> None:
        if self.harness is not None:
            self.harness.close()
            self.harness = None
            self.live_sessions.clear()

    def prepare_session(self, actor_id: str) -> str:
        """创建或恢复角色会话，并将会话 ID 持久保存到角色映射。"""
        actor = self.store.get_actor(actor_id)
        if actor is None:
            raise ValueError(f"未知角色：{actor_id}")
        if self.harness is None:
            raise RuntimeError("DSH 尚未启动")
        session_id = actor["session_id"]
        if session_id and session_id not in self.live_sessions:
            self.harness.resume_session(session_id)
        elif not session_id:
            session_id = "session-" + uuid.uuid4().hex
            self.harness.start_session(session_id).get_working_directory()
            self.store.update_actor(actor_id, {"session_id": session_id})
        self.live_sessions.add(session_id)
        return session_id

    def cancel_turn(self, session_id: str) -> None:
        """请求 DSH 中指定角色停止当前轮，不影响其他角色会话。"""
        (self.controls / (session_id + ".cancel")).write_text("cancel", encoding="utf-8")

    def session_contexts(self) -> list[dict[str, Any]]:
        """读取 DSH 的上下文压力投影，而不是累计账单 Token。"""
        default_window = int(self.store.get_config("dsh")["context_length"])
        projections = self.home / "storages/session_projcache/sessions"
        result = []
        for actor in self.store.list_actors():
            session_id = actor["session_id"]
            tokens, source, revision, window = None, None, None, default_window
            if session_id:
                live_path = self.controls / (session_id + ".context.json")
                path = projections / (session_id + ".json")
                if live_path.exists():
                    pressure = json.loads(live_path.read_text(encoding="utf-8"))
                    tokens = pressure.get("projectedTokens", pressure.get("pressureTokens"))
                    revision = pressure.get("revision")
                    window = pressure.get("contextWindow", default_window)
                    source = "projected" if tokens is not None else None
                elif path.exists():
                    data = json.loads(path.read_text(encoding="utf-8"))
                    row = data["record"]["rows"].get("contextPressure", {})
                    pressure = row.get("val", {})
                    revision = row.get("seq")
                    window = pressure.get("contextWindow", default_window)
                    if "pressureTokens" in pressure:
                        tokens = max(0, pressure["pressureTokens"] + pressure.get("surfaceTokens", 0)
                                     - pressure.get("sampledSurfaceTokens", 0))
                        source = "projected"
                    elif "surfaceTokens" in pressure:
                        tokens, source = pressure["surfaceTokens"], "estimated"
            result.append({**actor, "context_tokens": tokens, "context_window": window,
                           "context_source": source, "context_revision": revision})
        return result

    def reset_session(self, actor_id: str) -> dict[str, Any]:
        actor = self.store.get_actor(actor_id)
        old_session = actor["session_id"]
        metadata = dict(actor["metadata"])
        metadata["session_reset_at"] = time.time()
        self.store.update_actor(actor_id, {"session_id": None, "metadata": metadata})
        if old_session:
            self.live_sessions.discard(old_session)
        return {"actor_id": actor_id, "previous_session_id": old_session, "reset": True}

    def mark_auto_compaction(self, actor_id: str, threshold: int) -> None:
        actor = next(item for item in self.session_contexts() if item["id"] == actor_id)
        metadata = dict(actor["metadata"])
        metadata["last_auto_compaction"] = {"session_id": actor["session_id"],
            "revision": actor["context_revision"], "threshold_tokens": threshold}
        self.store.update_actor(actor_id, {"metadata": metadata})

    def _usage_recorder(self, actor_id: str, session_id: str, platform: str, job_id: str):
        model_config = self.store.get_config("dsh")
        profile = selected_profile(self.store.get_config("pricing"))

        def record_usage(notification: Any) -> None:
            if notification.method != "session.event":
                return
            event = notification.payload.get("event") or {}
            kind = event.get("type")
            data = event.get("data") or {}
            if kind == "tool/call":
                self.activity[actor_id] = {"job_id": job_id, "tool": data["name"], "call_id": data["callId"]}
            elif kind in ("tool/result", "turn/end"):
                self.activity.pop(actor_id, None)
            if kind not in ("assistant/message", "assistant/attempt", "compaction/summary"):
                return
            data = event.get("data") or {}
            if kind == "compaction/summary" and not data.get("llmStreamCall") and not data.get("usage"):
                return
            usage = data.get("usage")
            if usage is None:
                for item in data.get("stream") or []:
                    chunk = item.get("chunk") or {}
                    if chunk.get("type") == "usage":
                        usage = chunk.get("usage")
            source_session = notification.payload.get("sessionId") or session_id
            seq = event.get("seq", f"{data.get('turn')}:{data.get('step')}:{kind}")
            timestamp = event.get("time")
            self.store.record_model_usage(source_id=f"{source_session}:{seq}", source="model_call",
                session_id=source_session, actor_id=actor_id, job_id=job_id, platform=platform,
                provider=model_config["provider"], model=model_config["model"], usage=usage, profile=profile,
                recorded_at=float(timestamp) / 1000 if timestamp else None)
        return record_usage

    def compact_session(self, actor_id: str, *, job_id: str = "") -> dict[str, Any]:
        actor = self.store.get_actor(actor_id)
        if not actor["session_id"]:
            return {"actor_id": actor_id, "compacted": False, "message": "会话尚未创建"}
        session_id = self.prepare_session(actor_id)
        request = self.controls / (session_id + ".compact")
        response = self.controls / (session_id + ".compact-result.json")
        response.unlink(missing_ok=True)
        record = self._usage_recorder(actor_id, session_id, actor["platform"], job_id)
        client = self.harness.client
        with client.subscribe_session_notifications(session_id) as subscription:
            request.write_text("compact", encoding="utf-8")
            deadline = time.monotonic() + 600
            while not response.exists():
                subscription.drain(record)
                if time.monotonic() >= deadline:
                    raise TimeoutError("等待 DSH 会话压缩超时")
                time.sleep(0.2)
            result = json.loads(response.read_text(encoding="utf-8"))
            response.unlink()
            # session/wait 的回复在该会话先前通知之后到达，确保压缩模型用量被计入。
            client._request_raw("session/wait", {"sessionId": session_id}, on_notification=record,
                                notification_subscription=subscription)
        if not result["ok"]:
            raise RuntimeError(result["error"])
        return {"actor_id": actor_id, "session_id": session_id, **result}

    def run_turn(self, actor_id: str, prompt: str | list[dict[str, Any]], *, platform: str = "",
                 sender_id: str = "", job_id: str = "") -> dict[str, Any]:
        """装配上下文并调用一个角色。"""
        session_id = self.prepare_session(actor_id)
        context = self.memory.assemble_context(actor_id=actor_id, platform=platform, sender_id=sender_id)
        (self.contexts / (session_id + ".json")).write_text(
            json.dumps(context, ensure_ascii=False), encoding="utf-8")
        record_usage = self._usage_recorder(actor_id, session_id, platform, job_id)
        for attempt in range(2):
            result = self.harness.run(prompt, session_id=session_id, on_notification=record_usage)
            if result.finish_reason == "completed":
                break
            end = next((event for event in reversed(result.events) if event.get("type") == "turn/end"), {})
            reason = end.get("data", {}).get("reason", result.finish_reason)
            if attempt or "CONTEXT_WINDOW_EXCEEDED" not in str(reason):
                raise RuntimeError(f"DSH 会话未完成：{reason}")
            had_tools = any(event.get("type") == "tool/call" for event in result.events)
            try:
                compacted = self.compact_session(actor_id, job_id=job_id)
            except Exception as exc:
                raise RuntimeError(f"DSH 上下文溢出，自动压缩失败：{exc}") from exc
            if not compacted.get("compacted"):
                raise RuntimeError("DSH 上下文溢出，但没有可压缩的会话历史")
            if had_tools:
                raise RuntimeError("DSH 上下文溢出，已压缩；本轮曾调用工具，为避免重复执行未自动重试，请先核对动作结果")
        self.store.update_actor(actor_id, {"session_id": result.session_id})
        return {"actor_id": actor_id, "session_id": result.session_id,
                "response": result.final_response, "finish_reason": result.finish_reason,
                "tool_events": [event for event in result.events if "tool" in event.get("type", "")]}

    def execute_job_sync(self, job: dict[str, Any]) -> dict[str, Any]:
        actor_id = job["actor_id"]
        if job["kind"] == "session_compact":
            try:
                return self.compact_session(actor_id, job_id=job["id"])
            finally:
                if job["payload"].get("automatic"):
                    self.mark_auto_compaction(actor_id, job["payload"]["threshold_tokens"])
        event = self.store.get_event(job["event_id"]) if job["event_id"] else None
        if job["kind"] == "private_message_batch":
            event = job["payload"]["messages"][-1]
        data = {"job_id": job["id"], "kind": job["kind"], "payload": job["payload"], "event": event}
        skill = "companion-memory" if job["kind"] in ("memory_compaction", "compact_memory") else SKILLS[actor_id]
        prompt = (f"请使用 skill 工具加载 {skill}，按该角色处理下面这项工作。"
                  "平台消息使用事件中当前的接收目标和发送者；平台外发请直接调用对应 companion 工具。"
                  "主会话对用户的回复直接作为最终回复。工作内容：\n" + json.dumps(data, ensure_ascii=False))
        if actor_id == "main" and job["kind"] == "autonomous_main":
            prompt += (
                "\n这是一次自主决策机会，不是固定巡检。结合下面的当前状态，自己选择最值得推进的一件事："
                "优先推进用户方向和未完成目标；也可研究、浏览、创作、交流或整理有价值的记忆。"
                "能直接完成的小事自己做，需要并行或专长的工作委派对应角色并写清预期成果。"
                "不必询问用户下一步做什么，不必等待新指令，也不强制刷推荐或例行汇报。"
                "先看已有任务和日程，继续已有工作，不重复派单；结果值得保留时记下结论和下一步。"
                "需要稍后跟进就安排一次性任务；没值得做的事可以结束本轮。\n当前决策上下文：\n"
                + json.dumps(self.memory.decision_context(), ensure_ascii=False))
        if job["kind"] == "desktop_task":
            prompt += ("\n这是用户从桌宠派来的一次性任务，直接执行 payload.text，不只是回答能不能做。"
                       "已启用平台、会话对象、当前方向与记忆见下方；完成后给用户一句简短结果。\n"
                       + json.dumps(self.memory.decision_context(), ensure_ascii=False))
        turn_platform = event["platform"] if event else job["payload"].get("platform", "")
        messages = []
        if job["kind"] in ("group_interaction", "private_message_batch") and job["payload"].get("platform"):
            messages.extend(job["payload"].get("messages", []))
        elif event:
            messages.append(event)
        settings = self.store.get_config("platforms").get(turn_platform, {})
        chat_media = self.store.get_config("chat_media") or {}
        media = [(index, item, normalize_message_content(item.get("payload", {})))
                 for index, item in enumerate(messages, 1)
                 if (item.get("payload", {}).get("content") or {}).get("kind") in ("image", "emoji")]
        read_media = chat_media.get("read_inbound_images", settings.get("read_message_images", False))
        if media and read_media:
            cache_root = self.var / "message-media-cache"
            cache_root.mkdir(parents=True, exist_ok=True)
            library_folder = Path(chat_media.get("folder") or "message-media-library")
            if not library_folder.is_absolute():
                library_folder = self.var / library_folder
            save_media = bool(chat_media.get("save_inbound_images", True))
            with tempfile.TemporaryDirectory(prefix="turn-", dir=cache_root) as temporary:
                blocks: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
                for index, item, content in media:
                    label = f"第 {index} 条消息的{'表情' if content['kind'] == 'emoji' else '图片'}："
                    try:
                        event_id = str(item.get("id") or f"job:{job['id']}")
                        saved = self.store.get_media_for_event(event_id, index)
                        image = None
                        if saved and Path(saved["path"]).exists():
                            image = image_block_from_path(saved["path"])
                        elif content.get("path") and Path(content["path"]).exists():
                            image = image_block_from_path(content["path"])
                        else:
                            image = cache_image(content, Path(temporary), index=index)
                        if image and save_media and not saved:
                            persisted = persist_image_block(image, library_folder,
                                                            stem=f"{turn_platform or 'message'}-{job['id']}-{index}")
                            if persisted:
                                saved = self.store.save_media(
                                    platform=turn_platform or item.get("platform", ""),
                                    account_id=str(item.get("account_id") or job["payload"].get("account_id", "")),
                                    conversation_id=str(item.get("conversation_id") or job["payload"].get("conversation_id", "")),
                                    sender_id=str(item.get("sender_id") or ""),
                                    sender_name=str(item.get("sender_name") or ""),
                                    event_id=event_id, external_id=item.get("external_id"), media_index=index,
                                    media_kind=content.get("kind", "image"), path=persisted["path"],
                                    mime_type=persisted["mime_type"], sha256=persisted["sha256"],
                                    source_urls=source_urls_for_content(content),
                                    metadata={"job_id": job["id"]})
                                image = image_block_from_path(persisted["path"])
                        if image:
                            path_note = f" 已保存到：{saved['path']}" if saved else ""
                            blocks.append({"type": "text", "text": label + path_note})
                            blocks.extend(visual_blocks_from_path(image["path"]) if image.get("path") else
                                          [{key: image[key] for key in ("type", "mimeType", "data") if key in image}])
                        else:
                            blocks.append({"type": "text", "text": label + "平台未提供图片地址，画面不可见。"})
                    except Exception as exc:
                        blocks.append({"type": "text", "text": label + f"读取失败（{type(exc).__name__}），画面不可见。"})
                return self.run_turn(actor_id, blocks, platform=turn_platform,
                                     sender_id=event["sender_id"] if event else "", job_id=job["id"])
        return self.run_turn(actor_id, prompt, platform=turn_platform,
                             sender_id=event["sender_id"] if event else "", job_id=job["id"])

    async def execute_job(self, job: dict[str, Any]) -> dict[str, Any]:
        if job["kind"] == "session_compact" and job["payload"].get("automatic"):
            settings = self.store.get_config("session_compaction")[job["actor_id"]]
            context = next(item for item in self.session_contexts() if item["id"] == job["actor_id"])
            if not settings["enabled"] or context["context_tokens"] is None or context["context_tokens"] < settings["threshold_tokens"]:
                return {"actor_id": job["actor_id"], "compacted": False, "message": "自动压缩未启用或未达到阈值"}
            job["payload"]["threshold_tokens"] = settings["threshold_tokens"]
        if job["kind"] == "session_reset":
            return await asyncio.to_thread(self.reset_session, job["actor_id"])
        if job["kind"] == "session_compact" and not self.store.get_actor(job["actor_id"])["session_id"]:
            return {"actor_id": job["actor_id"], "compacted": False, "message": "会话尚未创建"}
        session_id = await asyncio.to_thread(self.prepare_session, job["actor_id"])
        cancel_file = self.controls / (session_id + ".cancel")
        cancel_file.unlink(missing_ok=True)
        task = asyncio.create_task(asyncio.to_thread(self.execute_job_sync, job))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            self.cancel_turn(session_id)
            try:
                await task
            except Exception:
                # 被用户取消的模型轮以 cancelled 结束；调度器记录任务状态。
                pass
            raise
        finally:
            cancel_file.unlink(missing_ok=True)
