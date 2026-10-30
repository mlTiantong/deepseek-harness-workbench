"""Local, transactional memory and outbound-action ledger for the creator."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
STATE = Path(os.environ.get("CREATOR_STATE_DIR", str(ROOT / "state")))


def config() -> dict:
    return json.loads((ROOT / "config.json").read_text(encoding="utf-8"))


def now() -> str:
    return datetime.now(ZoneInfo(config()["timezone"])).isoformat(timespec="seconds")


def day() -> str:
    return now()[:10]


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    STATE.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(STATE / "creator.sqlite3", timeout=15)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript("""
      CREATE TABLE IF NOT EXISTS memory (
        id INTEGER PRIMARY KEY, at TEXT NOT NULL, kind TEXT NOT NULL,
        text TEXT NOT NULL, source TEXT NOT NULL, confidence REAL NOT NULL,
        UNIQUE(kind,text,source));
      CREATE TABLE IF NOT EXISTS actions (
        id TEXT PRIMARY KEY, at TEXT NOT NULL, day TEXT NOT NULL,
        platform TEXT NOT NULL, kind TEXT NOT NULL, fingerprint TEXT NOT NULL,
        target TEXT NOT NULL, summary TEXT NOT NULL, status TEXT NOT NULL,
        evidence TEXT NOT NULL DEFAULT '', UNIQUE(platform,kind,fingerprint));
      CREATE TABLE IF NOT EXISTS rounds (
        id TEXT PRIMARY KEY, at TEXT NOT NULL, day TEXT NOT NULL,
        status TEXT NOT NULL, details TEXT NOT NULL DEFAULT '');
    """)
    try:
        with db:
            yield db
    finally:
        db.close()


def remember(kind: str, text: str, source: str = "", confidence: float = 1.0) -> dict:
    if kind not in {"fact", "interest", "relationship", "lesson", "episode", "plan"}:
        raise ValueError("kind must be fact/interest/relationship/lesson/episode/plan")
    if not text.strip() or len(text) > 8000 or len(source) > 2000:
        raise ValueError("Memory text must contain 1–8000 characters; source <=2000")
    if not 0 <= confidence <= 1:
        raise ValueError("confidence must be between 0 and 1")
    with connect() as db:
        db.execute("INSERT OR IGNORE INTO memory(at,kind,text,source,confidence) VALUES(?,?,?,?,?)",
                   (now(), kind, text.strip(), source, confidence))
        row = db.execute("SELECT * FROM memory WHERE kind=? AND text=? AND source=?",
                         (kind, text.strip(), source)).fetchone()
    return dict(row)


def recall(query: str = "", limit: int = 12) -> list[dict]:
    if not 1 <= limit <= 50 or len(query) > 500:
        raise ValueError("limit must be 1–50; query <=500 characters")
    with connect() as db:
        rows = db.execute("SELECT * FROM memory ORDER BY id DESC LIMIT 3000").fetchall()
    terms = list(dict.fromkeys(query.lower().split()))
    # Add Chinese bigrams for lexical recall when the user types an unsegmented sentence.
    chinese = "".join(c if '\u4e00' <= c <= '\u9fff' else ' ' for c in query)
    for chunk in chinese.split():
        terms.extend(chunk[i:i + 2] for i in range(len(chunk) - 1))
    if not terms:
        return [dict(r) for r in rows[:limit]]
    ranked = [(sum(t in (r["text"] + " " + r["source"]).lower() for t in set(terms)), r) for r in rows]
    ranked.sort(key=lambda x: (x[0], x[1]["id"]), reverse=True)
    return [dict(r) for score, r in ranked[:limit] if score]


def status() -> dict:
    with connect() as db:
        actions = [dict(r) for r in db.execute("SELECT * FROM actions ORDER BY at DESC LIMIT 15")]
        counts = dict(db.execute("SELECT kind,count(*) FROM actions WHERE day=? AND status!='failed' GROUP BY kind", (day(),)))
        rounds = db.execute("SELECT count(*) FROM rounds WHERE day=?", (day(),)).fetchone()[0]
    return {"time": now(), "paused": (ROOT / "PAUSED").exists(), "config": config(),
            "today_actions": counts, "today_rounds": rounds, "recent_actions": actions}


def action_begin(platform: str, kind: str, target: str, summary: str, content_key: str) -> dict:
    """Reserve one operation, counting unknown outcomes to prevent blind retries."""
    cfg = config()
    if platform not in cfg["platforms"] or kind not in cfg["daily_limits"]:
        raise ValueError("Unsupported platform or action kind")
    if not all(x.strip() for x in (target, summary, content_key)) or max(map(len, (target, summary, content_key))) > 12000:
        raise ValueError("target, summary, content_key are required and must be <=12000 characters")
    if (ROOT / "PAUSED").exists():
        return {"allowed": False, "reason": "paused"}
    if (kind == "post" and not cfg["autonomous_publish"]) or (kind in {"message", "comment"} and not cfg["autonomous_chat"]):
        return {"allowed": False, "reason": "disabled_in_config"}
    if kind == "post" and (ROOT / "CONSOLE_SETTINGS.json").exists():
        from console_store import settings
        console_cfg = settings()
        if not console_cfg["video_auto_publish"]:
            return {"allowed": False, "reason": "console_publish_disabled"}
        cfg["daily_limits"]["post"] = min(cfg["daily_limits"]["post"], console_cfg["publish_daily_limit"])
    fingerprint = hashlib.sha256((target.strip() + '\n' + content_key.strip()).encode()).hexdigest()
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        old = db.execute("SELECT * FROM actions WHERE platform=? AND kind=? AND fingerprint=?",
                         (platform, kind, fingerprint)).fetchone()
        if old:
            return {"allowed": False, "reason": "duplicate_or_unresolved", "existing": dict(old)}
        count = db.execute("SELECT count(*) FROM actions WHERE day=? AND kind=? AND status!='failed'", (day(), kind)).fetchone()[0]
        if count >= cfg["daily_limits"][kind]:
            return {"allowed": False, "reason": "daily_limit"}
        identity = uuid.uuid4().hex
        db.execute("INSERT INTO actions(id,at,day,platform,kind,fingerprint,target,summary,status) VALUES(?,?,?,?,?,?,?,?,?)",
                   (identity, now(), day(), platform, kind, fingerprint, target, summary, "reserved"))
    return {"allowed": True, "action_id": identity, "remaining_today": cfg["daily_limits"][kind] - count - 1}


def action_finish(action_id: str, outcome: str, evidence: str) -> dict:
    if outcome not in {"published", "failed", "uncertain"} or not evidence.strip() or len(evidence) > 8000:
        raise ValueError("outcome=published/failed/uncertain; evidence is required, <=8000 characters")
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM actions WHERE id=?", (action_id,)).fetchone()
        if not row:
            raise ValueError("Unknown action_id")
        if row["status"] in {"published", "failed"}:
            if row["status"] == outcome and row["evidence"] == evidence:
                return dict(row)
            raise ValueError("Final result is immutable; use memory to record later corrections")
        db.execute("UPDATE actions SET status=?,evidence=? WHERE id=?", (outcome, evidence, action_id))
        row = db.execute("SELECT * FROM actions WHERE id=?", (action_id,)).fetchone()
    return dict(row)


def round_begin() -> str | None:
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        count = db.execute("SELECT count(*) FROM rounds WHERE day=?", (day(),)).fetchone()[0]
        if count >= config()["max_rounds_per_day"]:
            return None
        identity = uuid.uuid4().hex
        db.execute("INSERT INTO rounds(id,at,day,status) VALUES(?,?,?,?)", (identity, now(), day(), "running"))
    return identity


def round_finish(identity: str, outcome: str, details: str) -> None:
    with connect() as db:
        db.execute("UPDATE rounds SET status=?,details=? WHERE id=?", (outcome, details, identity))
