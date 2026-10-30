"""Contact-scoped memory for fast plugin DM workers; shares creator SQLite."""
import json
import os
from pathlib import Path
from mcp.server.fastmcp import FastMCP
import state_store as store
import console_store


def context(account, contact):
    scope = f"dm:bilibili:{account}:{contact}"
    with store.connect() as db:
        memories = [dict(r) for r in db.execute(
            "SELECT kind,text,source FROM memory WHERE source=? ORDER BY id DESC LIMIT 12", (scope,))]
    return {"persona": (store.ROOT / "SOUL.md").read_text(encoding="utf-8"),
            "policy": (store.ROOT / "POLICY.md").read_text(encoding="utf-8"),
            "memories": memories, "memory_source": scope,
            "settings": console_store.settings(), "paused": (store.ROOT / "PAUSED").exists()}


def remember(account, contact, text):
    return store.remember("relationship", text, f"dm:bilibili:{account}:{contact}")


mcp = FastMCP("creator_dm_memory")


@mcp.tool()
def creator_dm_remember(summary: str) -> dict:
    """Save only necessary facts/preferences about this contact, not full chat logs."""
    return remember(os.environ["CREATOR_DM_ACCOUNT"], os.environ["CREATOR_DM_CONTACT"], summary)


@mcp.tool()
def creator_dm_recall() -> dict:
    """Read persona and this contact's long-term memory only."""
    return context(os.environ["CREATOR_DM_ACCOUNT"], os.environ["CREATOR_DM_CONTACT"])


if __name__ == "__main__":
    import sys
    if len(sys.argv) == 3:
        print(json.dumps(context(sys.argv[1], sys.argv[2]), ensure_ascii=False))
    else:
        mcp.run(transport="stdio")
