"""Shared console state; no credentials or private message bodies in public diagnostics."""
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
import state_store as store

PLATFORMS = {"xiaohongshu": "小红书", "bilibili": "B站", "douyin": "抖音", "qq": "QQ", "wechat": "微信"}
DEFAULTS = {"reply_enabled": True, "reply_platforms": list(PLATFORMS),
            "reply_daily_limit": 0, "reply_per_contact_daily_limit": 0,
            "reply_check_minutes": 30,
            "reply_quiet_start": 23, "reply_quiet_end": 9,
            "video_enabled": True, "video_auto_publish": True,
            "publish_daily_limit": 2, "owner_contact_enabled": False}

def settings():
    path = store.ROOT / "CONSOLE_SETTINGS.json"
    saved = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    return {key: saved.get(key, default) for key, default in DEFAULTS.items()}

def atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(data, out, ensure_ascii=False, indent=2)
            out.flush()
            os.fsync(out.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)

def save_settings(patch):
    if not isinstance(patch, dict) or set(patch) - set(DEFAULTS):
        raise ValueError("包含未知设置")
    value = settings()
    for key, val in patch.items():
        default = DEFAULTS[key]
        if isinstance(default, bool):
            if type(val) is not bool:
                raise ValueError(key + " 必须为开关")
        elif isinstance(default, int):
            upper = 23 if "quiet" in key else 1440 if "minutes" in key else None if key in {"reply_daily_limit", "reply_per_contact_daily_limit"} else 100
            lower = 5 if "minutes" in key else 0
            if type(val) is not int or (val < lower or (upper is not None and val > upper)):
                raise ValueError(key + " 超出允许范围")
        elif key == "reply_platforms":
            if not isinstance(val, list) or any(x not in PLATFORMS for x in val) or len(set(val)) != len(val):
                raise ValueError("无效平台")
        value[key] = val
    if value["owner_contact_enabled"]:
        contact = json.loads((store.ROOT / "OWNER_CONTACT.json").read_text(encoding="utf-8"))
        if not contact.get("routes") or contact.get("status") != "verified":
            raise ValueError("主动私聊尚未核对发送账号、接收账号和私信入口")
    contact_path=store.ROOT/"OWNER_CONTACT.json"
    if contact_path.exists():
        contact=json.loads(contact_path.read_text(encoding="utf-8"))
        contact["enabled"]=value["owner_contact_enabled"]
        atomic_json(contact_path,contact)
    atomic_json(store.ROOT / "CONSOLE_SETTINGS.json", value)
    return value

def init_db(db):
    db.executescript("""
    CREATE TABLE IF NOT EXISTS inbox (
      platform TEXT NOT NULL, message_id TEXT NOT NULL, conversation_id TEXT NOT NULL,
      sender TEXT NOT NULL, preview TEXT NOT NULL, evidence TEXT NOT NULL,
      received_at TEXT NOT NULL, updated_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
      action_id TEXT NOT NULL DEFAULT '', PRIMARY KEY(platform,message_id));
    CREATE TABLE IF NOT EXISTS platform_checks (
      platform TEXT PRIMARY KEY, status TEXT NOT NULL, account TEXT NOT NULL,
      evidence TEXT NOT NULL, checked_at TEXT NOT NULL);
    """)
    columns={row[1] for row in db.execute("PRAGMA table_info(inbox)")}
    if "account" not in columns:
        db.execute("ALTER TABLE inbox ADD COLUMN account TEXT NOT NULL DEFAULT ''")

def report_platform(platform, status, account, evidence):
    if platform not in PLATFORMS or status not in {"verified", "logged_out", "needs_verification", "unavailable", "unknown"}:
        raise ValueError("无效平台或登录状态")
    if not evidence.strip() or len(evidence)>2000 or len(account)>200:
        raise ValueError("必须提供不含凭据的页面证据")
    if status == "verified" and not account.strip():
        raise ValueError("已登录状态必须核对账号")
    with store.connect() as db:
        init_db(db)
        db.execute("INSERT INTO platform_checks VALUES(?,?,?,?,?) ON CONFLICT(platform) DO UPDATE SET status=excluded.status,account=excluded.account,evidence=excluded.evidence,checked_at=excluded.checked_at", (platform,status,account,evidence,store.now()))
    return {"recorded": True}

def ingest(platform, message_id, conversation_id, sender, preview, evidence):
    if platform not in PLATFORMS or not all(isinstance(x,str) and x.strip() for x in (message_id,conversation_id,sender,evidence)):
        raise ValueError("必须有真实平台、稳定消息ID、会话ID、发送人和页面证据")
    if max(map(len,(message_id,conversation_id,sender,evidence)))>2000 or len(preview)>500:
        raise ValueError("私信摘要过长；不要保存完整私聊记录")
    with store.connect() as db:
        init_db(db)
        check=db.execute("SELECT * FROM platform_checks WHERE platform=?",(platform,)).fetchone()
        account=check["account"] if check and check["status"]=="verified" else ""
        old=db.execute("SELECT account FROM inbox WHERE platform=? AND message_id=?",(platform,message_id)).fetchone()
        if old and old["account"] and old["account"]!=account:
            raise ValueError("私信所属账号与当前核验账号不同；禁止跨账号复用收件记录")
        db.execute("INSERT OR IGNORE INTO inbox(platform,message_id,conversation_id,sender,preview,evidence,received_at,updated_at,account) VALUES(?,?,?,?,?,?,?,?,?)",(platform,message_id,conversation_id,sender,preview,evidence,store.now(),store.now(),account))
        if old and not old["account"] and account:
            db.execute("UPDATE inbox SET account=?,updated_at=? WHERE platform=? AND message_id=?",(account,store.now(),platform,message_id))
    return {"recorded":True,"message_id":message_id}

def reserve_reply(platform, message_id, reply_text):
    if not reply_text.strip() or len(reply_text)>4000:
        raise ValueError("回复内容不能为空或超过4000字")
    cfg=settings()
    if not cfg["reply_enabled"] or platform not in cfg["reply_platforms"]:
        return {"allowed":False,"reason":"reply_disabled"}
    hour=datetime.fromisoformat(store.now()).hour
    start,end=cfg["reply_quiet_start"],cfg["reply_quiet_end"]
    quiet=(start <= hour < end) if start<end else (hour>=start or hour<end) if start>end else False
    if quiet:
        return {"allowed":False,"reason":"quiet_hours"}
    with store.connect() as db:
        init_db(db)
        db.execute("BEGIN IMMEDIATE")
        row=db.execute("SELECT * FROM inbox WHERE platform=? AND message_id=?",(platform,message_id)).fetchone()
        if row is None:
            raise ValueError("先登记实际收到的私信")
        if row["status"] != "pending":
            return {"allowed":False,"reason":"already_reserved_or_replied"}
        # Serialize with the same transactional ledger used by normal outbound actions.
        from hashlib import sha256
        import uuid
        if (store.ROOT / "PAUSED").exists() or not store.config()["autonomous_chat"]:
            return {"allowed":False,"reason":"paused_or_chat_disabled"}
        check=db.execute("SELECT * FROM platform_checks WHERE platform=?",(platform,)).fetchone()
        if not check or check["status"] != "verified" or (datetime.fromisoformat(store.now())-datetime.fromisoformat(check["checked_at"])).total_seconds()>900:
            return {"allowed":False,"reason":"account_not_recently_verified"}
        if not row["account"] or row["account"]!=check["account"]:
            return {"allowed":False,"reason":"inbox_account_mismatch_reobserve_required"}
        target=row["conversation_id"]
        daily_limit=cfg["reply_daily_limit"]
        contact_limit=cfg["reply_per_contact_daily_limit"]
        if daily_limit:
            used=db.execute("SELECT COUNT(*) FROM actions WHERE day=? AND kind='message' AND status IN ('reserved','published','uncertain')",(store.day(),)).fetchone()[0]
            if used >= daily_limit:
                return {"allowed":False,"reason":"daily_reply_limit"}
        if contact_limit:
            used=db.execute("SELECT COUNT(*) FROM actions WHERE day=? AND platform=? AND kind='message' AND target=? AND status IN ('reserved','published','uncertain')",(store.day(),platform,target)).fetchone()[0]
            if used >= contact_limit:
                return {"allowed":False,"reason":"per_contact_reply_limit"}
        key="reply:"+message_id
        fingerprint=sha256((target.strip()+"\n"+key).encode()).hexdigest()
        if db.execute("SELECT 1 FROM actions WHERE platform=? AND kind='message' AND fingerprint=?",(platform,fingerprint)).fetchone():
            return {"allowed":False,"reason":"duplicate_or_unresolved"}
        identity=uuid.uuid4().hex
        db.execute("INSERT INTO actions(id,at,day,platform,kind,fingerprint,target,summary,status) VALUES(?,?,?,?,?,?,?,?,?)",(identity,store.now(),store.day(),platform,"message",fingerprint,target,"回复私信 "+message_id,"reserved"))
        db.execute("UPDATE inbox SET status='reserved',action_id=?,updated_at=? WHERE platform=? AND message_id=?",(identity,store.now(),platform,message_id))
    return {"allowed":True,"action_id":identity,"target":target,"message_id":message_id}

def snapshot():
    with store.connect() as db:
        init_db(db)
        rows=[dict(x) for x in db.execute("SELECT i.*,a.status AS action_status FROM inbox i LEFT JOIN actions a ON i.action_id=a.id ORDER BY received_at DESC LIMIT 100")]
        checks={x["platform"]:dict(x) for x in db.execute("SELECT * FROM platform_checks")}
    for row in rows:
        row["status"]={"published":"replied","failed":"failed","uncertain":"uncertain","reserved":"reserved"}.get(row.pop("action_status"),row["status"])
    workspace_path=store.ROOT/"state/live-workspace.json"
    preferred=None
    if workspace_path.exists():
        ids=json.loads(workspace_path.read_text(encoding="utf-8")).get("sessionIds",[])
        preferred=ids[0] if ids else None
    return {"preferred_session":preferred,"platforms":[{"id":p,"name":n,**checks.get(p,{"status":"unknown","account":"","evidence":"尚未核验；点击检查登录，由本地程序检查当前账号。","checked_at":None})} for p,n in PLATFORMS.items()],"inbox":rows,"settings":settings(),"runtime":store.status(),"time":store.now()}
