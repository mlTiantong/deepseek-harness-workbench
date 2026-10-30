"""Read-only MCP handshake checks; browser smoke opens only example.com."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import yaml
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parent


async def check(row: dict, browser_smoke: bool) -> dict:
    cfg = row["config"]
    env = {**os.environ, **cfg.get("env", {}), "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    params = StdioServerParameters(command=cfg["command"], args=cfg["args"], env=env, cwd=cfg["cwd"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = (await session.list_tools()).tools
            result = {"server": cfg["serverName"], "ready": True, "tool_count": len(tools), "tools": [t.name for t in tools]}
            if cfg["serverName"] == "creator":
                context = await session.call_tool("creator_context", {})
                if context.isError:
                    raise RuntimeError(str(context.content))
                result["context_ok"] = True
            if browser_smoke and cfg["serverName"] == "creator_browser":
                navigation = await session.call_tool("browser_navigate", {"url": "https://example.com"})
                if navigation.isError:
                    raise RuntimeError(str(navigation.content))
                result["browser_navigation_ok"] = "Example Domain" in str(navigation.content)
                await session.call_tool("browser_close", {})
            return result


async def main(browser_smoke: bool) -> None:
    entries = yaml.safe_load((ROOT / "mcp-entries.yml").read_text(encoding="utf-8"))
    results = []
    for row in entries:
        try:
            result = await asyncio.wait_for(check(row, browser_smoke), timeout=100)
        except Exception as exc:
            result = {"server": row["config"]["serverName"], "ready": False, "error": repr(exc)}
        print(json.dumps(result, ensure_ascii=False), flush=True)
        results.append(result)
    (ROOT / "state/mcp-check.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    if not all(r["ready"] for r in results):
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--browser-smoke", action="store_true")
    asyncio.run(main(parser.parse_args().browser_smoke))
