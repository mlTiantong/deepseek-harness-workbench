"""Atomic lightweight reply quota for the Bilibili plugin fast path."""
import json
import sys
import state_store as store
import console_store as console


def call(request):
    account, contact = request["account"], request["contact"]
    if request["method"] == "reserve":
        prefs=console.settings()
        if not prefs["reply_enabled"] or "bilibili" not in prefs["reply_platforms"] or (store.ROOT/"PAUSED").exists():
            return {"allowed":False}
        with store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            daily=prefs["reply_daily_limit"]
            contact_cap=prefs["reply_per_contact_daily_limit"]
            used=db.execute("SELECT COUNT(*) FROM actions WHERE day=? AND kind='message' AND status IN ('reserved','published','uncertain')",(store.day(),)).fetchone()[0]
            if daily and used>=daily:return {"allowed":False}
            used=db.execute("SELECT COUNT(*) FROM actions WHERE day=? AND platform='bilibili' AND kind='message' AND target=? AND status IN ('reserved','published','uncertain')",(store.day(),contact)).fetchone()[0]
            if contact_cap and used>=contact_cap:return {"allowed":False}
            import uuid
            identity=uuid.uuid4().hex
            db.execute("INSERT INTO actions(id,at,day,platform,kind,fingerprint,target,summary,status) VALUES(?,?,?,?,?,?,?,?,?)",(identity,store.now(),store.day(),"bilibili","message","fast-dm:"+account+":"+contact+":"+identity,contact,"B站快速私信回复额度预留", "reserved"))
        return {"allowed":True,"id":identity}
    if request["method"] == "finish":
        status=request["status"]
        if status not in {"reserved","failed","published","uncertain"}:raise ValueError("invalid status")
        with store.connect() as db:
            db.execute("UPDATE actions SET status=?,evidence=? WHERE id=? AND status='reserved'",(status,"B站插件快速回复会话启动",request["id"]))
        return {"ok":True}
    raise ValueError("unknown method")

if __name__ == "__main__":
    try: print(json.dumps(call(json.loads(sys.stdin.read(8192)))))
    except Exception as exc:
        print(str(exc),file=sys.stderr);raise SystemExit(1)
