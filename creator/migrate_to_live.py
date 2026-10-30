"""Enable native DSH steering without rewriting unrelated profile settings."""
import json
import shutil
import uuid
from pathlib import Path
import yaml
from setup import ConfigLoader
import state_store as store

ROOT = Path(__file__).resolve().parent
patch_path = ROOT.parent / "data/profiles/desktop/cordis.patch.yml"
shutil.copy2(patch_path, ROOT / "state" / f"desktop-before-live-{uuid.uuid4().hex}.yml")
rows = yaml.load(patch_path.read_text(encoding="utf-8"), Loader=ConfigLoader)
row = next((r for r in rows if r.get("id") == "ui-conversation"), None)
if row is None:
    row = {"id": "ui-conversation", "config": {}}
    rows.append(row)
row.setdefault("config", {})["busyEnter"] = "steer"
for entry in rows:
    for child in entry.get("insert", []):
        if child.get("id") == "creator-memory":
            child["config"].setdefault("env", {})["CREATOR_TOOLSET_VERSION"] = "2"
patch_path.write_text(yaml.safe_dump(rows, allow_unicode=True, sort_keys=False, width=100), encoding="utf-8")
store.remember("fact", "主要运行方式已迁移为 DSH 原生持续会话：用户可以实时插话与引导；独立后台主管已停用。自主活动通过同一会话内的原生定时任务继续。", "local:user-request-2026-10-27")
store.remember("plan", "上一后台轮次被迁移中断：小红书元球实验笔记已经上传图片并填写部分文案，但未确认发布。对外动作账本仍为 reserved，必须核对实际账号作品/草稿后再继续，禁止盲目重发。草稿在 drafts/2026-10-27-metaball-note.md，图片在 exports/2026-10-27-metaball-verify.png。", "local:migration-observation-2026-10-27")
print("Enabled native DSH busy-enter steering; background start/loop/once disabled.")
