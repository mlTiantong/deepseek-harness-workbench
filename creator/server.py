"""Creator memory and action-ledger MCP. stdio only; diagnostics go to stderr."""
from pathlib import Path
import console_store
from mcp.server.fastmcp import FastMCP
import state_store as store

mcp = FastMCP("creator_mcp", instructions="Each creator round starts with creator_context. Reserve each outbound action, verify its result, and preserve real memories.")
READ = {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False}
WRITE = {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False}


@mcp.tool(annotations=READ)
def creator_context() -> dict:
    """Read the current persona, policy, interests, recent memories and action budget before acting."""
    return {"persona": (store.ROOT / "SOUL.md").read_text(encoding="utf-8"),
            "policy": (store.ROOT / "POLICY.md").read_text(encoding="utf-8"),
            "interests": (store.ROOT / "INTERESTS.md").read_text(encoding="utf-8"),
            "memories": store.recall(limit=12), "runtime": store.status(),
            "console_settings": console_store.settings(), "platform_inbox": console_store.snapshot()["inbox"][:20],
            "owner_contact": __import__("json").loads((store.ROOT / "OWNER_CONTACT.json").read_text(encoding="utf-8"))
            if (store.ROOT / "OWNER_CONTACT.json").exists() else {"enabled": False}}


@mcp.tool(annotations=READ)
def creator_recall(query: str, limit: int = 12) -> list[dict]:
    """Recall local memories with lexical Chinese/English matching; limit 1–50, query <=500 characters."""
    return store.recall(query, limit)


@mcp.tool(annotations=WRITE)
def creator_remember(kind: str, text: str, source: str = "", confidence: float = 1.0) -> dict:
    """Save an actual fact/interest/relationship/lesson/episode/plan; include source and confidence, not invented experience."""
    return store.remember(kind, text, source, confidence)


@mcp.tool(annotations=WRITE)
def creator_evolve(document: str, content: str, reason: str) -> dict:
    """Evolve SOUL or INTERESTS with a reason, retaining a prior version; policy/config cannot be changed here."""
    if document not in {"SOUL", "INTERESTS"} or not 1 <= len(content.strip()) <= 24000 or not reason.strip():
        raise ValueError("document=SOUL/INTERESTS; content 1–24000 characters; reason required")
    path = store.ROOT / (document + ".md")
    versions = store.STATE / "versions"
    versions.mkdir(parents=True, exist_ok=True)
    import uuid
    (versions / f"{document}-{uuid.uuid4().hex}.md").write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    temporary = path.with_suffix("." + uuid.uuid4().hex + ".tmp")
    temporary.write_text(content.rstrip() + "\n", encoding="utf-8")
    temporary.replace(path)
    store.remember("lesson", f"更新 {document}：{reason}", "local:creator_evolve")
    return {"updated": document, "reason": reason}


@mcp.tool(annotations=WRITE)
def creator_action_begin(platform: str, kind: str, target: str, summary: str, content_key: str) -> dict:
    """Before an outbound post/comment/message/like on qq/wechat/xiaohongshu/bilibili/douyin, reserve one action. Execute only if allowed=true. content_key identifies exact text or artifact hash; same content is not retried blindly."""
    return store.action_begin(platform, kind, target, summary, content_key)


@mcp.tool(annotations=WRITE)
def creator_action_finish(action_id: str, outcome: str, evidence: str) -> dict:
    """Record verified published/sent result, definite failed result, or uncertain result. Include URL, object id or observed UI evidence. Never guess success."""
    return store.action_finish(action_id, outcome, evidence)


@mcp.tool(annotations=READ)
def creator_status() -> dict:
    """Read today's action counts, run counts, pause state and recent outbound results."""
    return store.status()


@mcp.tool(annotations=WRITE)
def creator_round_begin() -> dict:
    """Reserve one scheduled autonomous round under the daily budget; ordinary user conversation does not need this. Do not act unless allowed=true."""
    if (store.ROOT / "PAUSED").exists():
        return {"allowed": False, "reason": "paused"}
    identity = store.round_begin()
    return {"allowed": identity is not None, "round_id": identity, "reason": "reserved" if identity else "daily_round_limit"}


@mcp.tool(annotations=WRITE)
def creator_round_finish(round_id: str, summary: str) -> dict:
    """Finish a native DSH scheduled round with a concise factual summary; preserve detailed memories separately."""
    with store.connect() as db:
        row = db.execute("SELECT status FROM rounds WHERE id=?", (round_id,)).fetchone()
    if row is None:
        raise ValueError("Unknown round_id")
    if not summary.strip() or len(summary) > 8000:
        raise ValueError("summary must contain 1–8000 characters")
    store.round_finish(round_id, "completed", summary)
    return {"round_id": round_id, "status": "completed"}


@mcp.tool(annotations=WRITE)
def creator_pause(reason: str) -> dict:
    """Pause subsequent autonomous work immediately when the user asks or the environment needs attention."""
    (store.ROOT / "PAUSED").write_text(reason, encoding="utf-8")
    return {"paused": True, "reason": reason}


@mcp.tool(annotations=WRITE)
def creator_platform_report(platform: str, status: str, account: str, evidence: str) -> dict:
    """Compatibility alias: run programmatic login verification. Model-provided status/account/evidence are not trusted or persisted."""
    from login_check import check_and_record
    return check_and_record(platform)


@mcp.tool(annotations=WRITE)
def creator_login_check(platform: str) -> dict:
    """Direct programmatic login check; no AI judgment, no messages, no login attempts. Returns actual account or unknown/unavailable."""
    from login_check import check_and_record
    return check_and_record(platform)


@mcp.tool(annotations=WRITE)
def creator_inbox_record(platform: str, message_id: str, conversation_id: str, sender: str, preview: str, evidence: str) -> dict:
    """Record one REAL incoming private message using stable observed message+conversation IDs, short necessary preview <=500 chars and source evidence. Not comments/notifications, never invented IDs. Idempotent."""
    return console_store.ingest(platform, message_id, conversation_id, sender, preview, evidence)


@mcp.tool(annotations=WRITE)
def creator_reply_begin(platform: str, message_id: str, reply_text: str) -> dict:
    """Reserve one response to a recorded private message with atomic deduplication and configured quiet hours; configured daily and per-contact limits (0 means unlimited). Only send if allowed=true; then creator_action_finish with verified result. Never blind retry uncertain sends."""
    return console_store.reserve_reply(platform, message_id, reply_text)


if __name__ == "__main__":
    mcp.run(transport="stdio")
