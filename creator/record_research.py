"""Record local source commits, artifact hashes and observed checks without credentials."""
import hashlib
import importlib.metadata
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BASE = ROOT.parent
sources = []
for folder, url in [
    ("douyin", "https://github.com/kdcadmin/dsh_douyin_tools"),
    ("soul-md", "https://github.com/Scorp1o117/dsh-soul-md"),
    ("windows-mcp", "https://github.com/CursorTouch/Windows-MCP"),
    ("computer-use", "https://github.com/xiaoheizi1212/dsh-computer-use"),
]:
    path = BASE / "output/research" / folder
    commit = subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"], text=True).strip()
    sources.append({"repository": url, "commit": commit, "local_checkout": str(path)})
artifact = ROOT / "vendor/dsh-douyin-life-0.2.1.tgz"
result = {"checked_on": "2026-10-27", "sources": sources,
          "python_versions": {name: importlib.metadata.version(name) for name in ["windows-mcp", "mcp", "fastmcp", "playwright", "tzdata"]},
          "local_douyin_artifact": {"path": str(artifact), "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest()},
          "npm": {"dsh-soul-md": "0.10.3", "@playwright/mcp": "0.0.83", "dsh-douyin-life": "404; installed source archive instead"},
          "checks": {"unit_tests": 7, "mcp_tools": {"creator": 8, "creator_browser": 25, "creator_windows": 20},
                     "browser_navigation": "https://example.com returned Example Domain", "deepseek_readonly_smoke": "completed",
                     "web_profile_boot": "creator-web started on local port 3081; test server stopped", "outbound_social_actions": 0},
          "not_tested": ["QQ/WeChat real messages", "Xiaohongshu/Bilibili/Douyin real posts", "Qzone/Moments", "long-running unattended operation"]}
(ROOT / "state/research-manifest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print("Recorded source revisions, artifact hash and verification scope.")
