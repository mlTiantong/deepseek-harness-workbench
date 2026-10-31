"""项目独立的 SQLite 存储：事件、定时器、任务、角色、记忆、动作和配置。"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from contextlib import closing, contextmanager
from typing import Any, Iterator

from .billing import DEFAULT_PRICING, estimate_cny, selected_profile

ACTORS = {
    "main": ("", "main"),
    "bili_chat": ("bilibili", "chat"),
    "bili_group": ("bilibili", "group"),
    "bili_comment": ("bilibili", "comment"),
    "douyin_chat": ("douyin", "chat"),
    "douyin_group": ("douyin", "group"),
    "douyin_comment": ("douyin", "comment"),
    "weixin_chat": ("weixin", "chat"),
    "xhs_chat": ("xiaohongshu", "chat"),
    "xhs_comment": ("xiaohongshu", "comment"),
    "media": ("", "media"),
    "creator": ("", "creator"),
    "publisher": ("", "publisher"),
}

ACTOR_PRIORITIES = {
    "main": 40, "bili_chat": 10, "douyin_chat": 10, "weixin_chat": 10, "xhs_chat": 10,
    "bili_group": 20, "douyin_group": 20,
    "bili_comment": 15, "douyin_comment": 15, "xhs_comment": 15,
    "media": 60, "creator": 70, "publisher": 80,
}


DEFAULT_CONFIG = {
    "paused": False,
    "desktop_pet": {"enabled": False, "scale": 0.55},
    "admin_ids": {"bilibili": [], "douyin": [], "xiaohongshu": [], "weixin": []},
    "platforms": {
        "bilibili": {"enabled": False, "messages": True, "comments": True, "poll_seconds": 30,
                     "groups": False, "group_reply_interval_seconds": 300},
        "douyin": {"enabled": False, "messages": True, "comments": True, "poll_seconds": 30,
                   "groups": False, "group_reply_interval_seconds": 300, "read_message_images": False},
        "xiaohongshu": {"enabled": False, "messages": True, "comments": True, "poll_seconds": 30},
        "weixin": {"enabled": False, "messages": True, "comments": False, "poll_seconds": 30,
                   "groups": False, "group_reply_interval_seconds": 300},
    },
    "autonomy": {"enabled": True, "direction": "", "main_interval_seconds": 3600,
                 "memory_interval_seconds": 21600, "background_scan_seconds": 60},
    "publishing": {"enabled": False, "daily_limits": {"bilibili": 3, "douyin": 3, "xiaohongshu": 3}},
    "image_messaging": {"enabled": True, "daily_limits": {"bilibili": 0, "douyin": 3, "xiaohongshu": 0, "weixin": 3}},
    "chat_media": {"read_inbound_images": True, "save_inbound_images": True,
                   "folder": "message-media-library"},
    "creation_folder": "content",
    "dsh": {"provider": "deepseek-official", "model": "deepseek-v4-flash",
            "profile": "sdk", "base_url": "", "reasoning_effort": "low", "max_tokens": 8192,
            "context_length": 200000, "api_format": "openai-completions", "compatibility": "deepseek"},
    "media": {"enabled": False, "audio_enabled": True, "daily_limit": 20, "asr_model": "base",
              "comments_enabled": True, "comment_limit": 30},
    "image_generation": {"url": "", "model": ""},
    "session_compaction": {actor_id: {"enabled": False, "threshold_tokens": 100000} for actor_id in ACTORS},
    "pricing": DEFAULT_PRICING,
}


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _id() -> str:
    return uuid.uuid4().hex


def _now(now: float | None) -> float:
    return time.time() if now is None else float(now)


def _row(row: sqlite3.Row | None, *json_fields: str) -> dict[str, Any] | None:
    if row is None:
        return None
    result = dict(row)
    for name in json_fields:
        if result[name] is not None:
            result[name] = json.loads(result[name])
    return result


def event_actor(platform: str, event_type: str) -> tuple[str, int]:
    prefix = {"bilibili": "bili", "douyin": "douyin", "xiaohongshu": "xhs", "weixin": "weixin"}[platform]
    if event_type in ("message", "private_message", "dm"):
        return f"{prefix}_chat", 10
    if event_type in ("comment", "mention", "reply"):
        return f"{prefix}_comment", 15
    return "main", 20


class Store:
    def __init__(self, database: str | Path):
        self.path = Path(database).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(self.path, timeout=30)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys = ON")
            with db:
                yield db

    def _initialize(self) -> None:
        now = time.time()
        with self.connect() as db:
            db.execute("PRAGMA journal_mode = WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS events (
                    id TEXT PRIMARY KEY,
                    platform TEXT NOT NULL,
                    account_id TEXT NOT NULL DEFAULT '',
                    event_type TEXT NOT NULL,
                    external_id TEXT,
                    conversation_id TEXT NOT NULL DEFAULT '',
                    sender_id TEXT NOT NULL DEFAULT '',
                    sender_name TEXT NOT NULL DEFAULT '',
                    occurred_at REAL,
                    discovered_at REAL NOT NULL,
                    payload TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    UNIQUE(platform, account_id, event_type, external_id)
                );
                CREATE INDEX IF NOT EXISTS events_status ON events(status, discovered_at);
                CREATE TABLE IF NOT EXISTS timers (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    priority INTEGER NOT NULL,
                    next_at REAL NOT NULL,
                    interval_seconds REAL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    autonomous INTEGER NOT NULL DEFAULT 1,
                    last_triggered_at REAL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS timers_due ON timers(enabled, next_at);
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    event_id TEXT REFERENCES events(id),
                    timer_id TEXT REFERENCES timers(id),
                    actor_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    priority INTEGER NOT NULL,
                    payload TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'ready',
                    autonomous INTEGER NOT NULL DEFAULT 0,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    result TEXT,
                    error TEXT,
                    discovered_at REAL,
                    queued_at REAL NOT NULL,
                    available_at REAL NOT NULL,
                    started_at REAL,
                    finished_at REAL,
                    updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS jobs_queue ON jobs(status, actor_id, available_at, queued_at);
                CREATE INDEX IF NOT EXISTS jobs_priority ON jobs(status, priority, queued_at);
                CREATE TABLE IF NOT EXISTS actors (
                    id TEXT PRIMARY KEY,
                    platform TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    session_id TEXT,
                    status TEXT NOT NULL DEFAULT 'idle',
                    last_active_at REAL,
                    metadata TEXT NOT NULL DEFAULT '{}',
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    content TEXT NOT NULL,
                    platform TEXT NOT NULL DEFAULT '',
                    subject_id TEXT NOT NULL DEFAULT '',
                    tags TEXT NOT NULL DEFAULT '[]',
                    metadata TEXT NOT NULL DEFAULT '{}',
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS memories_kind ON memories(kind, status, updated_at);
                CREATE INDEX IF NOT EXISTS memories_subject ON memories(platform, subject_id, kind);
                CREATE TABLE IF NOT EXISTS actions (
                    id TEXT PRIMARY KEY,
                    job_id TEXT REFERENCES jobs(id),
                    platform TEXT NOT NULL,
                    account_id TEXT NOT NULL DEFAULT '',
                    kind TEXT NOT NULL,
                    target TEXT NOT NULL DEFAULT '',
                    payload TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'running',
                    result TEXT,
                    error TEXT,
                    created_at REAL NOT NULL,
                    finished_at REAL
                );
                CREATE INDEX IF NOT EXISTS actions_platform_kind ON actions(platform, kind, created_at);
                CREATE TABLE IF NOT EXISTS saved_media (
                    id TEXT PRIMARY KEY,
                    platform TEXT NOT NULL,
                    account_id TEXT NOT NULL DEFAULT '',
                    conversation_id TEXT NOT NULL DEFAULT '',
                    sender_id TEXT NOT NULL DEFAULT '',
                    sender_name TEXT NOT NULL DEFAULT '',
                    event_id TEXT NOT NULL DEFAULT '',
                    external_id TEXT,
                    media_index INTEGER NOT NULL DEFAULT 1,
                    media_kind TEXT NOT NULL DEFAULT 'image',
                    path TEXT NOT NULL,
                    mime_type TEXT NOT NULL DEFAULT '',
                    sha256 TEXT NOT NULL DEFAULT '',
                    source_urls TEXT NOT NULL DEFAULT '[]',
                    metadata TEXT NOT NULL DEFAULT '{}',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    UNIQUE(event_id, media_index)
                );
                CREATE INDEX IF NOT EXISTS saved_media_time ON saved_media(created_at DESC);
                CREATE INDEX IF NOT EXISTS saved_media_conversation ON saved_media(platform, conversation_id, created_at DESC);
                CREATE TABLE IF NOT EXISTS model_usage (
                    source_id TEXT PRIMARY KEY, source TEXT NOT NULL, session_id TEXT NOT NULL,
                    actor_id TEXT NOT NULL, job_id TEXT NOT NULL DEFAULT '', platform TEXT NOT NULL DEFAULT '',
                    provider TEXT NOT NULL DEFAULT '', model TEXT NOT NULL DEFAULT '', recorded_at REAL NOT NULL,
                    input_tokens INTEGER, cached_tokens INTEGER, cache_write_tokens INTEGER,
                    output_tokens INTEGER, total_tokens INTEGER, reasoning_tokens INTEGER,
                    cache_reported INTEGER NOT NULL DEFAULT 0, price_profile TEXT, price_snapshot TEXT,
                    estimated_cost_cny REAL
                );
                CREATE INDEX IF NOT EXISTS model_usage_time ON model_usage(recorded_at);
                CREATE TABLE IF NOT EXISTS group_state (
                    platform TEXT NOT NULL, account_id TEXT NOT NULL DEFAULT '',
                    conversation_id TEXT NOT NULL, last_sent_at REAL,
                    PRIMARY KEY(platform, account_id, conversation_id)
                );
                CREATE TABLE IF NOT EXISTS config (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at REAL NOT NULL
                );
            """)
            for actor_id, (platform, channel) in ACTORS.items():
                db.execute("INSERT OR IGNORE INTO actors(id,platform,channel,updated_at) VALUES(?,?,?,?)",
                           (actor_id, platform, channel, now))
            for key, value in DEFAULT_CONFIG.items():
                db.execute("INSERT OR IGNORE INTO config(key,value,updated_at) VALUES(?,?,?)", (key, _json(value), now))
            # 已有项目升级时只补充新设置，不改变用户的登录、开关和频率。
            platforms = json.loads(db.execute("SELECT value FROM config WHERE key='platforms'").fetchone()[0])
            for name in ("bilibili", "douyin", "weixin"):
                defaults = DEFAULT_CONFIG["platforms"][name]
                platforms.setdefault(name, dict(defaults))
                for key in ("groups", "group_reply_interval_seconds"):
                    platforms[name].setdefault(key, defaults[key])
            platforms["douyin"].setdefault("read_message_images", False)
            db.execute("UPDATE config SET value=? WHERE key='platforms'", (_json(platforms),))
            admin_ids = json.loads(db.execute("SELECT value FROM config WHERE key='admin_ids'").fetchone()[0])
            admin_ids.setdefault("weixin", [])
            db.execute("UPDATE config SET value=? WHERE key='admin_ids'", (_json(admin_ids),))
            image_messaging = json.loads(db.execute("SELECT value FROM config WHERE key='image_messaging'").fetchone()[0])
            image_messaging.setdefault("enabled", DEFAULT_CONFIG["image_messaging"]["enabled"])
            image_messaging.setdefault("daily_limits", {})
            for platform, limit in DEFAULT_CONFIG["image_messaging"]["daily_limits"].items():
                image_messaging["daily_limits"].setdefault(platform, limit)
            db.execute("UPDATE config SET value=? WHERE key='image_messaging'", (_json(image_messaging),))
            chat_media_row = db.execute("SELECT value FROM config WHERE key='chat_media'").fetchone()
            chat_media = json.loads(chat_media_row[0]) if chat_media_row else {}
            for field, value in DEFAULT_CONFIG["chat_media"].items():
                chat_media.setdefault(field, value)
            db.execute("""INSERT INTO config(key,value,updated_at) VALUES('chat_media',?,?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at""",
                       (_json(chat_media), now))
            autonomy = json.loads(db.execute("SELECT value FROM config WHERE key='autonomy'").fetchone()[0])
            autonomy.setdefault("direction", "")
            db.execute("UPDATE config SET value=? WHERE key='autonomy'", (_json(autonomy),))
            video_media = json.loads(db.execute("SELECT value FROM config WHERE key='media'").fetchone()[0])
            for field, value in DEFAULT_CONFIG["media"].items():
                video_media.setdefault(field, value)
            db.execute("UPDATE config SET value=? WHERE key='media'", (_json(video_media),))
            dsh = json.loads(db.execute("SELECT value FROM config WHERE key='dsh'").fetchone()[0])
            for field in ("context_length", "api_format", "compatibility"):
                dsh.setdefault(field, DEFAULT_CONFIG["dsh"][field])
            db.execute("UPDATE config SET value=? WHERE key='dsh'", (_json(dsh),))
            compaction = json.loads(db.execute("SELECT value FROM config WHERE key='session_compaction'").fetchone()[0])
            if "enabled" in compaction:
                compaction = {actor_id: {"enabled": False, "threshold_tokens": compaction["threshold_tokens"]}
                              for actor_id in ACTORS}
            for actor_id in ACTORS:
                compaction.setdefault(actor_id, {"enabled": False, "threshold_tokens": 100000})
            db.execute("UPDATE config SET value=? WHERE key='session_compaction'", (_json(compaction),))
            for key, kind in (("persona", "persona"), ("active_context", "context")):
                db.execute("""INSERT OR IGNORE INTO memories(id,kind,content,created_at,updated_at)
                              VALUES(?,?,?,?,?)""", (key, kind, "", now, now))

    # 配置：每个顶层配置项独立保存，控制台可以直接修改。
    def get_config(self, key: str | None = None) -> Any:
        with self.connect() as db:
            if key is not None:
                row = db.execute("SELECT value FROM config WHERE key=?", (key,)).fetchone()
                return None if row is None else json.loads(row["value"])
            return {row["key"]: json.loads(row["value"]) for row in db.execute("SELECT key,value FROM config")}

    def set_config(self, key: str, value: Any, *, now: float | None = None) -> Any:
        with self.connect() as db:
            db.execute("""INSERT INTO config(key,value,updated_at) VALUES(?,?,?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at""",
                (key, _json(value), _now(now)))
        return value

    def update_config(self, updates: dict[str, Any], *, now: float | None = None) -> dict[str, Any]:
        current = _now(now)
        with self.connect() as db:
            for key, value in updates.items():
                db.execute("""INSERT INTO config(key,value,updated_at) VALUES(?,?,?)
                    ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at""",
                    (key, _json(value), current))
        return self.get_config()

    def record_model_usage(self, *, source_id: str, source: str, session_id: str, actor_id: str,
                           job_id: str = "", platform: str = "", provider: str = "", model: str = "",
                           usage: dict[str, Any] | None = None, profile: dict[str, Any] | None = None,
                           recorded_at: float | None = None) -> None:
        usage = usage or {}
        valid = "inputTokens" in usage and "outputTokens" in usage
        def number(key: str) -> int | None:
            value = usage.get(key)
            return int(value) if value is not None else None
        input_tokens, output_tokens = number("inputTokens"), number("outputTokens")
        cached, write = number("cacheReadTokens"), number("cacheWriteTokens")
        total = number("totalTokens")
        if total is None and valid:
            total = input_tokens + output_tokens + (cached or 0) + (write or 0)
        with self.connect() as db:
            db.execute("""INSERT OR IGNORE INTO model_usage(source_id,source,session_id,actor_id,job_id,platform,
                provider,model,recorded_at,input_tokens,cached_tokens,cache_write_tokens,output_tokens,total_tokens,
                reasoning_tokens,cache_reported,price_profile,price_snapshot,estimated_cost_cny)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (source_id, source, session_id, actor_id, job_id, platform, provider, model, _now(recorded_at),
                 input_tokens, cached, write, output_tokens, total, number("reasoningTokens"),
                 int(cached is not None or write is not None), profile.get("name") if profile else None,
                 _json(profile) if profile else None, estimate_cny(usage, profile)))

    def session_has_model_usage(self, session_id: str) -> bool:
        with self.connect() as db:
            return db.execute("SELECT 1 FROM model_usage WHERE session_id=? LIMIT 1", (session_id,)).fetchone() is not None

    def model_usage_summary(self) -> dict[str, Any]:
        with self.connect() as db:
            row = db.execute("""SELECT COUNT(*) AS records,
                COALESCE(SUM(CASE WHEN source='historical_snapshot' THEN 1 ELSE 0 END),0) AS historical_sessions,
                COALESCE(SUM(CASE WHEN source='model_call' THEN 1 ELSE 0 END),0) AS model_calls,
                COALESCE(SUM(CASE WHEN input_tokens IS NULL OR output_tokens IS NULL THEN 1 ELSE 0 END),0) AS unreported_calls,
                COALESCE(SUM(CASE WHEN estimated_cost_cny IS NULL AND input_tokens IS NOT NULL THEN 1 ELSE 0 END),0) AS unpriced_calls,
                COALESCE(SUM(CASE WHEN source='model_call' AND cache_reported=0 AND input_tokens IS NOT NULL THEN 1 ELSE 0 END),0) AS cache_unspecified_calls,
                COALESCE(SUM(input_tokens),0) AS uncached_input_tokens,
                COALESCE(SUM(cached_tokens),0) AS cached_input_tokens,
                COALESCE(SUM(cache_write_tokens),0) AS cache_write_tokens,
                COALESCE(SUM(output_tokens),0) AS output_tokens, COALESCE(SUM(total_tokens),0) AS total_tokens,
                COALESCE(SUM(reasoning_tokens),0) AS reasoning_tokens,
                COALESCE(SUM(estimated_cost_cny),0) AS estimated_cost_cny
                FROM model_usage""").fetchone()
            by_actor = db.execute("""SELECT actor_id, SUM(total_tokens) AS total_tokens,
                SUM(estimated_cost_cny) AS estimated_cost_cny FROM model_usage GROUP BY actor_id ORDER BY total_tokens DESC""").fetchall()
        return {**dict(row), "by_actor": [dict(item) for item in by_actor]}

    # 事件与其任务在同一业务操作中入库。平台事件 ID 用于简单去重。
    def ingest_event(self, platform: str, event_type: str, payload: dict[str, Any], *,
                     account_id: str = "", external_id: str | None = None,
                     conversation_id: str = "", sender_id: str = "", sender_name: str = "",
                     occurred_at: float | None = None, discovered_at: float | None = None,
                     actor_id: str | None = None, priority: int | None = None,
                     now: float | None = None) -> tuple[dict[str, Any], dict[str, Any] | None, bool]:
        current = _now(now)
        discovered = current if discovered_at is None else float(discovered_at)
        if actor_id is None:
            actor_id, default_priority = event_actor(platform, event_type)
            priority = default_priority if priority is None else priority
        else:
            priority = 20 if priority is None else priority
        with self.connect() as db:
            if external_id is not None:
                existing = db.execute("""SELECT * FROM events WHERE platform=? AND account_id=?
                    AND event_type=? AND external_id=?""", (platform, account_id, event_type, external_id)).fetchone()
                if existing:
                    job = db.execute("SELECT * FROM jobs WHERE event_id=? ORDER BY queued_at LIMIT 1",
                                     (existing["id"],)).fetchone()
                    return _row(existing, "payload"), _row(job, "payload", "result"), False
            event_id, job_id = _id(), _id()
            db.execute("""INSERT INTO events(id,platform,account_id,event_type,external_id,conversation_id,
                sender_id,sender_name,occurred_at,discovered_at,payload,status,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,'queued',?,?)""",
                (event_id, platform, account_id, event_type, external_id, conversation_id, sender_id,
                 sender_name, occurred_at, discovered, _json(payload), current, current))
            db.execute("""INSERT INTO jobs(id,event_id,actor_id,kind,priority,payload,status,autonomous,
                discovered_at,queued_at,available_at,updated_at) VALUES(?,?,?,?,?,?,'ready',0,?,?,?,?)""",
                (job_id, event_id, actor_id, event_type, priority, _json({"event_id": event_id}), discovered,
                 current, current, current))
            event = db.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
            job = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            return _row(event, "payload"), _row(job, "payload", "result"), True

    def ingest_douyin_private_event(self, payload: dict[str, Any], *, account_id: str,
                                    conversation_id: str, external_id: str | None, sender_id: str,
                                    sender_name: str = "", occurred_at: float | None = None,
                                    now: float | None = None) -> tuple[dict[str, Any], dict[str, Any] | None, bool]:
        """一个私信会话从首条消息起等待 10 秒，开始执行前的消息合并为一项任务。"""
        current = _now(now)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if external_id is not None:
                existing = db.execute("""SELECT * FROM events WHERE platform='douyin' AND account_id=?
                    AND event_type='message' AND external_id=?""", (account_id, external_id)).fetchone()
                if existing:
                    return _row(existing, "payload"), None, False
            event_id = _id()
            db.execute("""INSERT INTO events(id,platform,account_id,event_type,external_id,conversation_id,
                sender_id,sender_name,occurred_at,discovered_at,payload,status,created_at,updated_at)
                VALUES(?,'douyin',?,'message',?,?,?,?,?,?,?,'queued',?,?)""",
                (event_id, account_id, external_id, conversation_id, sender_id, sender_name,
                 occurred_at, current, _json(payload), current, current))
            job = db.execute("""SELECT * FROM jobs WHERE actor_id='douyin_chat' AND kind='private_message_batch'
                AND status='ready' AND json_extract(payload,'$.account_id')=?
                AND json_extract(payload,'$.conversation_id')=? ORDER BY queued_at DESC,rowid DESC LIMIT 1""",
                (account_id, conversation_id)).fetchone()
            if job is None:
                job_id = _id()
                job_payload = {"platform": "douyin", "account_id": account_id,
                               "conversation_id": conversation_id, "event_ids": [event_id]}
                db.execute("""INSERT INTO jobs(id,event_id,actor_id,kind,priority,payload,status,autonomous,
                    discovered_at,queued_at,available_at,updated_at)
                    VALUES(?,?,'douyin_chat','private_message_batch',?,?,'ready',0,?,?,?,?)""",
                    (job_id, event_id, ACTOR_PRIORITIES['douyin_chat'], _json(job_payload),
                     current, current, current + 10, current))
            else:
                job_id = job['id']
                job_payload = json.loads(job['payload'])
                job_payload['event_ids'].append(event_id)
                db.execute("UPDATE jobs SET payload=?,updated_at=? WHERE id=?", (_json(job_payload), current, job_id))
            event = db.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
            job = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            return _row(event, "payload"), _row(job, "payload", "result"), True

    def ingest_group_event(self, platform: str, payload: dict[str, Any], *, account_id: str,
                           conversation_id: str, external_id: str | None, sender_id: str,
                           sender_name: str = "", occurred_at: float | None = None,
                           now: float | None = None) -> tuple[dict[str, Any], dict[str, Any] | None, bool]:
        """每条群消息入库，但每群只排一个待处理任务；运行中的批次不吞掉新消息。"""
        current = _now(now)
        actor_id = {"bilibili": "bili_group", "douyin": "douyin_group"}[platform]
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if external_id is not None:
                existing = db.execute("SELECT * FROM events WHERE platform=? AND account_id=? AND event_type='group_message' AND external_id=?",
                                      (platform, account_id, external_id)).fetchone()
                if existing:
                    return _row(existing, "payload"), None, False
            event_id = _id()
            db.execute("""INSERT INTO events(id,platform,account_id,event_type,external_id,conversation_id,
                sender_id,sender_name,occurred_at,discovered_at,payload,status,created_at,updated_at)
                VALUES(?,?,?,'group_message',?,?,?,?,?,?,?,'queued',?,?)""",
                (event_id, platform, account_id, external_id, conversation_id, sender_id, sender_name,
                 occurred_at, current, _json(payload), current, current))
            job = db.execute("""SELECT * FROM jobs WHERE actor_id=? AND kind='group_interaction' AND status='ready' AND autonomous=0
                AND json_extract(payload,'$.account_id')=? AND json_extract(payload,'$.conversation_id')=?
                ORDER BY queued_at LIMIT 1""", (actor_id, account_id, conversation_id)).fetchone()
            if job is None:
                job_id = _id()
                job_payload = {"platform": platform, "account_id": account_id, "conversation_id": conversation_id}
                db.execute("""INSERT INTO jobs(id,actor_id,kind,priority,payload,status,autonomous,discovered_at,
                    queued_at,available_at,updated_at) VALUES(?,?,'group_interaction',?,?,'ready',0,?,?,?,?)""",
                    (job_id, actor_id, ACTOR_PRIORITIES[actor_id], _json(job_payload), current, current, current, current))
                job = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            event = db.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
            return _row(event, "payload"), _row(job, "payload", "result"), True

    def create_group_activity(self, platform: str, conversation_id: str, instruction: str, *,
                              account_id: str = "", available_at: float | None = None) -> dict[str, Any]:
        actor = {"bilibili": "bili_group", "douyin": "douyin_group"}[platform]
        return self.create_job(actor, "group_interaction", {"platform": platform, "account_id": account_id,
                               "conversation_id": conversation_id, "instruction": instruction},
                               autonomous=True, available_at=available_at)

    def group_last_sent(self, platform: str, account_id: str, conversation_id: str) -> float | None:
        with self.connect() as db:
            row = db.execute("SELECT last_sent_at FROM group_state WHERE platform=? AND account_id=? AND conversation_id=?",
                             (platform, account_id, conversation_id)).fetchone()
            action = db.execute("""SELECT MAX(finished_at) FROM actions WHERE platform=? AND account_id=?
                AND target=? AND kind='send_group_message' AND status='succeeded'""",
                (platform, account_id, conversation_id)).fetchone()[0]
            timestamps = [value for value in ((row["last_sent_at"] if row else None), action) if value is not None]
            return max(timestamps) if timestamps else None

    def record_group_sent(self, platform: str, account_id: str, conversation_id: str, *, now: float | None = None) -> None:
        with self.connect() as db:
            db.execute("""INSERT INTO group_state(platform,account_id,conversation_id,last_sent_at) VALUES(?,?,?,?)
                ON CONFLICT(platform,account_id,conversation_id) DO UPDATE SET last_sent_at=excluded.last_sent_at""",
                (platform, account_id, conversation_id, _now(now)))

    def get_event(self, event_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            return _row(db.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone(), "payload")

    def list_events(self, *, platform: str | None = None, status: str | None = None,
                    limit: int = 100) -> list[dict[str, Any]]:
        query, args = "SELECT * FROM events WHERE 1=1", []
        if platform is not None:
            query += " AND platform=?"
            args.append(platform)
        if status is not None:
            query += " AND status=?"
            args.append(status)
        query += " ORDER BY discovered_at DESC,rowid DESC LIMIT ?"
        args.append(limit)
        with self.connect() as db:
            return [_row(row, "payload") for row in db.execute(query, args)]

    def set_event_status(self, event_id: str, status: str, *, now: float | None = None) -> None:
        with self.connect() as db:
            db.execute("UPDATE events SET status=?,updated_at=? WHERE id=?", (status, _now(now), event_id))

    # 入站媒体库：文件放在 data_dir 下，SQLite 只保存可复用的路径和来源索引。
    def save_media(self, *, platform: str, path: str, event_id: str = "", account_id: str = "",
                   conversation_id: str = "", sender_id: str = "", sender_name: str = "",
                   external_id: str | None = None, media_index: int = 1, media_kind: str = "image",
                   mime_type: str = "", sha256: str = "", source_urls: list[str] | None = None,
                   metadata: dict[str, Any] | None = None, now: float | None = None) -> dict[str, Any]:
        current = _now(now)
        with self.connect() as db:
            existing = db.execute("SELECT id FROM saved_media WHERE event_id=? AND media_index=?",
                                 (event_id, media_index)).fetchone() if event_id else None
            media_id = existing["id"] if existing else _id()
            if existing:
                db.execute("""UPDATE saved_media SET platform=?,account_id=?,conversation_id=?,sender_id=?,
                    sender_name=?,external_id=?,media_kind=?,path=?,mime_type=?,sha256=?,source_urls=?,metadata=?,updated_at=?
                    WHERE id=?""", (platform, account_id, conversation_id, sender_id, sender_name, external_id,
                    media_kind, path, mime_type, sha256, _json(source_urls or []), _json(metadata or {}), current, media_id))
            else:
                db.execute("""INSERT INTO saved_media(id,platform,account_id,conversation_id,sender_id,sender_name,
                    event_id,external_id,media_index,media_kind,path,mime_type,sha256,source_urls,metadata,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (media_id, platform, account_id, conversation_id,
                    sender_id, sender_name, event_id, external_id, media_index, media_kind, path, mime_type, sha256,
                    _json(source_urls or []), _json(metadata or {}), current, current))
            return _row(db.execute("SELECT * FROM saved_media WHERE id=?", (media_id,)).fetchone(),
                        "source_urls", "metadata")

    def get_media(self, media_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            return _row(db.execute("SELECT * FROM saved_media WHERE id=?", (media_id,)).fetchone(),
                        "source_urls", "metadata")

    def get_media_for_event(self, event_id: str, media_index: int = 1) -> dict[str, Any] | None:
        with self.connect() as db:
            return _row(db.execute("SELECT * FROM saved_media WHERE event_id=? AND media_index=?",
                                  (event_id, media_index)).fetchone(), "source_urls", "metadata")

    def list_media(self, *, platform: str | None = None, conversation_id: str | None = None,
                   sender_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        query, args = "SELECT * FROM saved_media WHERE 1=1", []
        for key, value in (("platform", platform), ("conversation_id", conversation_id), ("sender_id", sender_id)):
            if value:
                query += f" AND {key}=?"
                args.append(value)
        query += " ORDER BY created_at DESC,rowid DESC LIMIT ?"
        args.append(limit)
        with self.connect() as db:
            return [_row(row, "source_urls", "metadata") for row in db.execute(query, args)]

    def delete_media(self, media_id: str) -> bool:
        with self.connect() as db:
            return bool(db.execute("DELETE FROM saved_media WHERE id=?", (media_id,)).rowcount)

    # 任务 API：每个工作者一条顺序队列；不同工作者由调度器并发运行。
    def create_job(self, actor_id: str, kind: str, payload: dict[str, Any], *, priority: int | None = None,
                   event_id: str | None = None, timer_id: str | None = None,
                   autonomous: bool = False, discovered_at: float | None = None,
                   available_at: float | None = None, now: float | None = None) -> dict[str, Any]:
        current = _now(now)
        job_id = _id()
        priority = ACTOR_PRIORITIES[actor_id] if priority is None else priority
        with self.connect() as db:
            db.execute("""INSERT INTO jobs(id,event_id,timer_id,actor_id,kind,priority,payload,status,autonomous,
                discovered_at,queued_at,available_at,updated_at) VALUES(?,?,?,?,?,?,?,'ready',?,?,?,?,?)""",
                (job_id, event_id, timer_id, actor_id, kind, priority, _json(payload), int(autonomous),
                 discovered_at, current, current if available_at is None else available_at, current))
            return _row(db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone(), "payload", "result")

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            return _row(db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone(), "payload", "result")

    def list_jobs(self, *, status: str | None = None, actor_id: str | None = None,
                  limit: int = 100) -> list[dict[str, Any]]:
        query, args = "SELECT * FROM jobs WHERE 1=1", []
        if status is not None:
            query += " AND status=?"
            args.append(status)
        if actor_id is not None:
            query += " AND actor_id=?"
            args.append(actor_id)
        query += " ORDER BY queued_at DESC,rowid DESC LIMIT ?"
        args.append(limit)
        with self.connect() as db:
            return [_row(row, "payload", "result") for row in db.execute(query, args)]

    def ready_by_actor(self, *, now: float | None = None, autonomous_paused: bool = False) -> list[dict[str, Any]]:
        current = _now(now)
        selected: dict[str, dict[str, Any]] = {}
        with self.connect() as db:
            settings_row = db.execute("SELECT value FROM config WHERE key='platforms'").fetchone()
            platforms = json.loads(settings_row["value"])
            rows = db.execute("""SELECT * FROM jobs WHERE status='ready' AND available_at<=?
                AND (?=0 OR autonomous=0) ORDER BY queued_at,rowid""", (current, int(autonomous_paused)))
            for row in rows:
                actor = row["actor_id"]
                if actor in selected:
                    continue
                job = _row(row, "payload", "result")
                if job["kind"] == "group_interaction":
                    payload = job["payload"]
                    settings = platforms[payload["platform"]]
                    if not (settings["enabled"] and settings.get("groups", False)):
                        continue
                    state = db.execute("SELECT last_sent_at FROM group_state WHERE platform=? AND account_id=? AND conversation_id=?",
                                       (payload["platform"], payload.get("account_id", ""), payload["conversation_id"])).fetchone()
                    action = db.execute("""SELECT MAX(finished_at) FROM actions WHERE platform=? AND account_id=?
                        AND target=? AND kind='send_group_message' AND status='succeeded'""",
                        (payload["platform"], payload.get("account_id", ""), payload["conversation_id"])).fetchone()[0]
                    last = max([value for value in ((state["last_sent_at"] if state else None), action) if value is not None], default=None)
                    if last is not None and current < last + settings["group_reply_interval_seconds"]:
                        continue
                selected[actor] = job
        return sorted(selected.values(), key=lambda job: (job["priority"], job["queued_at"], job["actor_id"]))

    def start_job(self, job_id: str, *, now: float | None = None) -> dict[str, Any] | None:
        current = _now(now)
        with self.connect() as db:
            updated = db.execute("""UPDATE jobs SET status='running',attempts=attempts+1,started_at=?,
                finished_at=NULL,error=NULL,updated_at=? WHERE id=? AND status='ready'""", (current, current, job_id))
            if not updated.rowcount:
                return None
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            db.execute("UPDATE actors SET status='running',last_active_at=?,updated_at=? WHERE id=?",
                       (current, current, row["actor_id"]))
            if row["event_id"]:
                db.execute("UPDATE events SET status='running',updated_at=? WHERE id=?", (current, row["event_id"]))
            if row["kind"] == "private_message_batch":
                payload = json.loads(row["payload"])
                pending = db.execute("""SELECT * FROM events WHERE id IN (SELECT value FROM json_each(?))
                    ORDER BY discovered_at,rowid""", (_json(payload["event_ids"]),)).fetchall()
                payload["messages"] = [_row(item, "payload") for item in pending]
                for item in pending:
                    db.execute("UPDATE events SET status='running',updated_at=? WHERE id=?", (current, item["id"]))
                db.execute("UPDATE jobs SET payload=? WHERE id=?", (_json(payload), job_id))
                row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row["kind"] == "group_interaction":
                payload = json.loads(row["payload"])
                pending = db.execute("""SELECT * FROM events WHERE event_type='group_message' AND status='queued'
                    AND platform=? AND account_id=? AND conversation_id=? ORDER BY COALESCE(occurred_at,discovered_at),rowid""",
                    (payload["platform"], payload.get("account_id", ""), payload["conversation_id"])).fetchall()
                payload["messages"] = [_row(item, "payload") for item in pending]
                for item in pending:
                    db.execute("UPDATE events SET status='running',updated_at=? WHERE id=?", (current, item["id"]))
                db.execute("UPDATE jobs SET payload=? WHERE id=?", (_json(payload), job_id))
                row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            return _row(row, "payload", "result")

    def finish_job(self, job_id: str, *, result: Any = None, error: str | None = None,
                   now: float | None = None) -> dict[str, Any] | None:
        current = _now(now)
        state = "failed" if error is not None else "succeeded"
        with self.connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                return None
            if row["status"] == "cancelled":
                state = "cancelled"
            db.execute("UPDATE jobs SET status=?,result=?,error=?,finished_at=?,updated_at=? WHERE id=?",
                       (state, _json(result), error, current, current, job_id))
            db.execute("UPDATE actors SET status='idle',last_active_at=?,updated_at=? WHERE id=?",
                       (current, current, row["actor_id"]))
            if row["event_id"]:
                db.execute("UPDATE events SET status=?,updated_at=? WHERE id=?", (state, current, row["event_id"]))
            if row["kind"] in ("group_interaction", "private_message_batch"):
                for item in json.loads(row["payload"]).get("messages", []):
                    db.execute("UPDATE events SET status=?,updated_at=? WHERE id=?", (state, current, item["id"]))
            return _row(db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone(), "payload", "result")

    def retry_job(self, job_id: str, *, delay_seconds: float = 0, now: float | None = None) -> bool:
        current = _now(now)
        with self.connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None or row["status"] not in ("failed", "cancelled"):
                return False
            db.execute("""UPDATE jobs SET status='ready',error=NULL,result=NULL,available_at=?,started_at=NULL,
                finished_at=NULL,updated_at=? WHERE id=?""", (current + delay_seconds, current, job_id))
            if row["event_id"]:
                db.execute("UPDATE events SET status='queued',updated_at=? WHERE id=?", (current, row["event_id"]))
            if row["kind"] == "group_interaction":
                for item in json.loads(row["payload"]).get("messages", []):
                    db.execute("UPDATE events SET status='queued',updated_at=? WHERE id=?", (current, item["id"]))
            if row["kind"] == "private_message_batch":
                for event_id in json.loads(row["payload"])["event_ids"]:
                    db.execute("UPDATE events SET status='queued',updated_at=? WHERE id=?", (current, event_id))
            return True

    def cancel_job(self, job_id: str, *, now: float | None = None) -> bool:
        current = _now(now)
        with self.connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None or row["status"] not in ("ready", "running"):
                return False
            db.execute("UPDATE jobs SET status='cancelled',finished_at=?,updated_at=? WHERE id=?",
                       (current, current, job_id))
            if row["event_id"]:
                db.execute("UPDATE events SET status='cancelled',updated_at=? WHERE id=?", (current, row["event_id"]))
            if row["kind"] == "private_message_batch":
                for event_id in json.loads(row["payload"])["event_ids"]:
                    db.execute("UPDATE events SET status='cancelled',updated_at=? WHERE id=?", (current, event_id))
            return True

    def recover_running(self, *, now: float | None = None) -> int:
        current = _now(now)
        with self.connect() as db:
            db.execute("UPDATE events SET status='queued',updated_at=? WHERE id IN "
                       "(SELECT event_id FROM jobs WHERE status='running' AND event_id IS NOT NULL)", (current,))
            for row in db.execute("SELECT payload FROM jobs WHERE status='running' AND kind IN ('group_interaction','private_message_batch')").fetchall():
                for item in json.loads(row["payload"]).get("messages", []):
                    db.execute("UPDATE events SET status='queued',updated_at=? WHERE id=?", (current, item["id"]))
            count = db.execute("UPDATE jobs SET status='ready',started_at=NULL,updated_at=? WHERE status='running'",
                               (current,)).rowcount
            db.execute("""UPDATE actions SET status='failed',error='服务重启，平台发送结果未确认',finished_at=?
                WHERE status='running'""", (current,))
            db.execute("UPDATE actors SET status='idle',updated_at=? WHERE status='running'", (current,))
            return count

    def job_metrics(self, *, actor_id: str | None = None, limit: int = 1000) -> dict[str, Any]:
        query, args = "SELECT j.*,e.occurred_at FROM jobs j LEFT JOIN events e ON e.id=j.event_id WHERE j.finished_at IS NOT NULL", []
        if actor_id:
            query += " AND j.actor_id=?"
            args.append(actor_id)
        query += " ORDER BY j.finished_at DESC LIMIT ?"
        args.append(limit)
        with self.connect() as db:
            rows = db.execute(query, args).fetchall()
        durations = sorted(row["finished_at"] - (row["occurred_at"] or row["discovered_at"] or row["queued_at"]) for row in rows)
        execution = sorted(row["finished_at"] - row["started_at"] for row in rows if row["started_at"] is not None)
        def stats(values: list[float]) -> dict[str, Any]:
            if not values:
                return {"count": 0, "p50": None, "p95": None, "max": None}
            from math import ceil
            return {"count": len(values), "p50": values[ceil(len(values) * .50) - 1],
                    "p95": values[ceil(len(values) * .95) - 1], "max": values[-1]}
        with self.connect() as db:
            sql = """SELECT a.finished_at, e.occurred_at, j.discovered_at, j.queued_at
                FROM actions a JOIN jobs j ON j.id=a.job_id LEFT JOIN events e ON e.id=j.event_id
                WHERE a.kind='send_message' AND a.status='succeeded'
                  AND j.kind IN ('message','private_message','dm','private_message_batch')"""
            args = []
            if actor_id:
                sql += " AND j.actor_id=?"
                args.append(actor_id)
            sql += " ORDER BY a.finished_at DESC LIMIT ?"
            args.append(limit)
            replies = db.execute(sql, args).fetchall()
        reply_durations = sorted(row["finished_at"] - (row["occurred_at"] or row["discovered_at"] or row["queued_at"])
                                 for row in replies)
        reply_stats = stats(reply_durations)
        reply_stats["within_120_seconds"] = sum(duration <= 120 for duration in reply_durations)
        return {"response_seconds": stats(durations), "execution_seconds": stats(execution),
                "message_response_seconds": reply_stats,
                "succeeded": sum(row["status"] == "succeeded" for row in rows),
                "failed": sum(row["status"] == "failed" for row in rows)}

    # 一次性和固定间隔定时器。
    def create_timer(self, title: str, actor_id: str, kind: str, payload: dict[str, Any], *,
                     next_at: float, interval_seconds: float | None = None, priority: int | None = None,
                     enabled: bool = True, autonomous: bool = True, now: float | None = None) -> dict[str, Any]:
        current, timer_id = _now(now), _id()
        priority = ACTOR_PRIORITIES[actor_id] if priority is None else priority
        with self.connect() as db:
            db.execute("""INSERT INTO timers(id,title,actor_id,kind,payload,priority,next_at,interval_seconds,
                enabled,autonomous,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (timer_id, title, actor_id, kind, _json(payload), priority, float(next_at), interval_seconds,
                 int(enabled), int(autonomous), current, current))
            return _row(db.execute("SELECT * FROM timers WHERE id=?", (timer_id,)).fetchone(), "payload")

    def get_timer(self, timer_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            return _row(db.execute("SELECT * FROM timers WHERE id=?", (timer_id,)).fetchone(), "payload")

    def list_timers(self, *, enabled: bool | None = None) -> list[dict[str, Any]]:
        query, args = "SELECT * FROM timers", []
        if enabled is not None:
            query += " WHERE enabled=?"
            args.append(int(enabled))
        query += " ORDER BY next_at,id"
        with self.connect() as db:
            return [_row(row, "payload") for row in db.execute(query, args)]

    def update_timer(self, timer_id: str, updates: dict[str, Any], *, now: float | None = None) -> dict[str, Any] | None:
        keys = {"title", "actor_id", "kind", "payload", "priority", "next_at", "interval_seconds", "enabled", "autonomous"}
        changes = {key: (_json(value) if key == "payload" else int(value) if key in {"enabled", "autonomous"} else value)
                   for key, value in updates.items() if key in keys}
        if changes:
            changes["updated_at"] = _now(now)
            sql = ",".join(f"{key}=?" for key in changes)
            with self.connect() as db:
                db.execute(f"UPDATE timers SET {sql} WHERE id=?", [*changes.values(), timer_id])
        return self.get_timer(timer_id)

    def delete_timer(self, timer_id: str) -> bool:
        # 历史任务保留，移除与已删除规则的关联。
        with self.connect() as db:
            db.execute("UPDATE jobs SET timer_id=NULL WHERE timer_id=?", (timer_id,))
            return bool(db.execute("DELETE FROM timers WHERE id=?", (timer_id,)).rowcount)

    def materialize_timers(self, *, now: float | None = None, autonomous_paused: bool = False) -> list[dict[str, Any]]:
        current = _now(now)
        created = []
        with self.connect() as db:
            timers = db.execute("SELECT * FROM timers WHERE enabled=1 AND next_at<=? AND (?=0 OR autonomous=0) ORDER BY next_at,id", (current, int(autonomous_paused))).fetchall()
            for timer in timers:
                job_id = _id()
                db.execute("""INSERT INTO jobs(id,timer_id,actor_id,kind,priority,payload,status,autonomous,
                    queued_at,available_at,updated_at) VALUES(?,?,?,?,?,?,'ready',?,?,?,?)""",
                    (job_id, timer["id"], timer["actor_id"], timer["kind"], timer["priority"], timer["payload"],
                     timer["autonomous"], current, current, current))
                if timer["interval_seconds"] is None:
                    db.execute("UPDATE timers SET enabled=0,last_triggered_at=?,updated_at=? WHERE id=?",
                               (current, current, timer["id"]))
                else:
                    db.execute("UPDATE timers SET next_at=?,last_triggered_at=?,updated_at=? WHERE id=?",
                               (current + timer["interval_seconds"], current, current, timer["id"]))
                created.append(_row(db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone(), "payload", "result"))
        return created

    # 角色与 DSH 会话映射。
    def has_pending_session_compaction(self, actor_id: str) -> bool:
        with self.connect() as db:
            return db.execute("""SELECT 1 FROM jobs WHERE actor_id=? AND kind='session_compact'
                AND status IN ('ready','running') LIMIT 1""", (actor_id,)).fetchone() is not None

    def get_actor(self, actor_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            return _row(db.execute("SELECT * FROM actors WHERE id=?", (actor_id,)).fetchone(), "metadata")

    def list_actors(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            return [_row(row, "metadata") for row in db.execute("SELECT * FROM actors ORDER BY id")]

    def upsert_actor(self, actor_id: str, *, platform: str = "", channel: str = "", session_id: str | None = None,
                     status: str = "idle", metadata: dict[str, Any] | None = None,
                     now: float | None = None) -> dict[str, Any]:
        current = _now(now)
        with self.connect() as db:
            db.execute("""INSERT INTO actors(id,platform,channel,session_id,status,metadata,updated_at)
                VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET platform=excluded.platform,
                channel=excluded.channel,session_id=excluded.session_id,status=excluded.status,
                metadata=excluded.metadata,updated_at=excluded.updated_at""",
                (actor_id, platform, channel, session_id, status, _json(metadata or {}), current))
            return _row(db.execute("SELECT * FROM actors WHERE id=?", (actor_id,)).fetchone(), "metadata")

    def update_actor(self, actor_id: str, updates: dict[str, Any], *, now: float | None = None) -> dict[str, Any] | None:
        keys = {"platform", "channel", "session_id", "status", "last_active_at", "metadata"}
        changes = {k: (_json(v) if k == "metadata" else v) for k, v in updates.items() if k in keys}
        if changes:
            changes["updated_at"] = _now(now)
            with self.connect() as db:
                db.execute("UPDATE actors SET " + ",".join(f"{key}=?" for key in changes) + " WHERE id=?",
                           [*changes.values(), actor_id])
        return self.get_actor(actor_id)

    # L1/L2/L3/Scratchpad 的完整基本读写、检索和归档接口。
    def put_memory(self, kind: str, content: str, *, memory_id: str | None = None, title: str = "",
                   platform: str = "", subject_id: str = "", tags: list[str] | None = None,
                   metadata: dict[str, Any] | None = None, status: str = "active",
                   now: float | None = None) -> dict[str, Any]:
        current = _now(now)
        if memory_id == "active_context" and len(content) > 2000:
            raise ValueError("近期背景看板请整理到 2000 字以内")
        existing = None
        if not memory_id and kind == "acquaintance" and platform and subject_id:
            matches = self.list_memories(kind=kind, platform=platform, subject_id=subject_id, status=None, limit=1)
            existing = matches[0] if matches else None
            memory_id = existing["id"] if existing else None
        elif memory_id:
            existing = self.get_memory(memory_id)
        if existing:
            tags = existing["tags"] if tags is None else tags
            metadata = existing["metadata"] if metadata is None else metadata
        memory_id = memory_id or _id()
        with self.connect() as db:
            db.execute("""INSERT INTO memories(id,kind,title,content,platform,subject_id,tags,metadata,status,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET kind=excluded.kind,title=excluded.title,
                content=excluded.content,platform=excluded.platform,subject_id=excluded.subject_id,tags=excluded.tags,
                metadata=excluded.metadata,status=excluded.status,updated_at=excluded.updated_at""",
                (memory_id, kind, title, content, platform, subject_id, _json(tags or []), _json(metadata or {}),
                 status, current, current))
            return _row(db.execute("SELECT * FROM memories WHERE id=?", (memory_id,)).fetchone(), "tags", "metadata")

    def get_memory(self, memory_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            return _row(db.execute("SELECT * FROM memories WHERE id=?", (memory_id,)).fetchone(), "tags", "metadata")

    def list_memories(self, *, kind: str | None = None, platform: str | None = None,
                      subject_id: str | None = None, status: str | None = "active", query: str = "",
                      limit: int = 100) -> list[dict[str, Any]]:
        sql, args = "SELECT * FROM memories WHERE 1=1", []
        for key, value in (("kind", kind), ("platform", platform), ("subject_id", subject_id), ("status", status)):
            if value is not None:
                sql += f" AND {key}=?"
                args.append(value)
        if query:
            sql += " AND (title LIKE ? OR content LIKE ? OR tags LIKE ?)"
            args.extend([f"%{query}%"] * 3)
        sql += " ORDER BY updated_at DESC,rowid DESC LIMIT ?"
        args.append(limit)
        with self.connect() as db:
            return [_row(row, "tags", "metadata") for row in db.execute(sql, args)]

    def set_memory_status(self, memory_id: str, status: str, *, now: float | None = None) -> bool:
        with self.connect() as db:
            return bool(db.execute("UPDATE memories SET status=?,updated_at=? WHERE id=?",
                                   (status, _now(now), memory_id)).rowcount)

    def delete_memory(self, memory_id: str) -> bool:
        with self.connect() as db:
            return bool(db.execute("DELETE FROM memories WHERE id=?", (memory_id,)).rowcount)

    # 对外动作记录和发布额度统计。
    def create_action(self, platform: str, kind: str, payload: dict[str, Any], *, account_id: str = "",
                      target: str = "", job_id: str | None = None, now: float | None = None) -> dict[str, Any]:
        action_id, current = _id(), _now(now)
        with self.connect() as db:
            db.execute("""INSERT INTO actions(id,job_id,platform,account_id,kind,target,payload,status,created_at)
                VALUES(?,?,?,?,?,?,?,'running',?)""", (action_id, job_id, platform, account_id, kind, target, _json(payload), current))
            return _row(db.execute("SELECT * FROM actions WHERE id=?", (action_id,)).fetchone(), "payload", "result")

    def finish_action(self, action_id: str, *, result: Any = None, error: str | None = None,
                      now: float | None = None) -> dict[str, Any] | None:
        with self.connect() as db:
            db.execute("UPDATE actions SET status=?,result=?,error=?,finished_at=? WHERE id=?",
                       ("failed" if error is not None else "succeeded", _json(result), error, _now(now), action_id))
            return _row(db.execute("SELECT * FROM actions WHERE id=?", (action_id,)).fetchone(), "payload", "result")

    def get_action(self, action_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            return _row(db.execute("SELECT * FROM actions WHERE id=?", (action_id,)).fetchone(), "payload", "result")

    def list_actions(self, *, platform: str | None = None, kind: str | None = None,
                     limit: int = 100) -> list[dict[str, Any]]:
        sql, args = "SELECT * FROM actions WHERE 1=1", []
        for key, value in (("platform", platform), ("kind", kind)):
            if value is not None:
                sql += f" AND {key}=?"
                args.append(value)
        sql += " ORDER BY created_at DESC,rowid DESC LIMIT ?"
        args.append(limit)
        with self.connect() as db:
            return [_row(row, "payload", "result") for row in db.execute(sql, args)]

    def count_actions(self, platform: str, kind: str, since: float, until: float | None = None,
                      *, status: str = "succeeded") -> int:
        sql, args = "SELECT COUNT(*) FROM actions WHERE platform=? AND kind=? AND status=? AND created_at>=?", [platform, kind, status, since]
        if until is not None:
            sql += " AND created_at<?"
            args.append(until)
        with self.connect() as db:
            return db.execute(sql, args).fetchone()[0]

    def count_action_images(self, platform: str, since: float, until: float | None = None) -> int:
        sql, args = "SELECT payload FROM actions WHERE platform=? AND kind IN ('send_message','send_group_message') AND status='succeeded' AND created_at>=?", [platform, since]
        if until is not None:
            sql += " AND created_at<?"
            args.append(until)
        with self.connect() as db:
            return sum(len(json.loads(row["payload"]).get("image_paths") or [])
                       for row in db.execute(sql, args))
