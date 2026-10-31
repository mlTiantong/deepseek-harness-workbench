from __future__ import annotations

import asyncio
import base64
import importlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from dsh_companion.app import create_app
from dsh_companion.comment_reading import comments_result, normalize_comment
from dsh_companion.dsh_runtime import DshRuntime
from dsh_companion.memory import MemoryCenter
from dsh_companion.platform_service import PlatformService
from dsh_companion.platforms.bilibili import BilibiliAdapter
from dsh_companion.platforms.douyin import DouyinAdapter
from dsh_companion.platforms import douyin_web_server as server
from dsh_companion.store import Store

ROOT = Path(__file__).resolve().parents[1]


def bili_item(ident, likes=0, author="reader", **extra):
    return {"rpid": ident, "like": likes, "member": {"mid": author, "uname": author},
            "content": {"message": f"评论 {ident}"}, **extra}


def dy_item(ident, likes=0, author="reader", **extra):
    return {"cid": str(ident), "digg_count": likes, "user": {"uid": author, "nickname": author},
            "text": f"评论 {ident}", **extra}


class PendingNetworkResponse:
    def __init__(self, response): self.response=response
    async def __aenter__(self): return self
    async def __aexit__(self, *_args): pass
    @property
    def value(self):
        async def response(): return self.response
        return response()


class CommentReadingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / "db.sqlite")

    def test_priority_deduplicates_pins_then_author_then_likes(self):
        for platform, make in (("bilibili", bili_item), ("douyin", dy_item)):
            raw = [make(1, 1000), make(2, 1, "owner"), make(3, 0, is_top=True), make(4, 50)]
            items = [normalize_comment(platform, item, author_id="owner") for item in raw]
            items.append(normalize_comment(platform, make(1, 1000)))
            result = comments_result(platform, "123", items, 30)
            self.assertEqual([item["id"] for item in result["items"]], ["3", "2", "1", "4"])
            self.assertEqual(result["count"], 4)

    async def test_bilibili_fetches_hot_pages_to_requested_limit(self):
        pages = [{"replies": [bili_item(i, i) for i in range(start, start + 20)],
                  "upper": {"mid": "owner"}, "cursor": {"is_end": False,
                  "pagination_reply": {"next_offset": str(start + 20)}}} for start in (0, 20, 40)]
        pages[0]["top"] = {"upper": bili_item(500, 0)}
        pages[0]["replies"][0]["replies"] = [bili_item(501, 0, "owner")]
        adapter = BilibiliAdapter(self.store, ROOT, directory=self.root / "bili")
        with patch("dsh_companion.platforms.bilibili.comment.get_comments_lazy", AsyncMock(side_effect=pages)) as fetch:
            result = await adapter.get_content_comments(content_id="123", limit=50)
        self.assertEqual(result["count"], 50)
        self.assertEqual([c["id"] for c in result["items"][:2]], ["500", "501"])
        self.assertEqual(fetch.await_count, 3)
        self.assertEqual(fetch.await_args_list[1].kwargs["offset"], "20")
        self.assertEqual(fetch.await_args.kwargs["order"].name, "LIKE")

    async def test_bilibili_empty_and_short_comment_sections_are_not_padded(self):
        adapter = BilibiliAdapter(self.store, ROOT, directory=self.root / "bili")
        for count in (0, 3):
            with patch("dsh_companion.platforms.bilibili.comment.get_comments_lazy", AsyncMock(return_value={
                    "replies": [bili_item(i) for i in range(count)], "cursor": {"is_end": True}})) as fetch:
                result = await adapter.get_content_comments(content_id="123", limit=30)
            self.assertEqual(result["count"], count)
            self.assertEqual(fetch.await_count, 1)

    async def test_douyin_preserves_creator_and_pin_fields_across_pages(self):
        aid = "1234567890123456789"
        pages = [{"status_code": 0, "comments": [dy_item(i, i) for i in range(start, start+20)],
                  "cursor": start+20, "has_more": True} for start in (0, 20, 40)]
        pages[0]["comments"][0].update(author_pin=True)
        pages[0]["comments"][1] = dy_item(1, 1, "owner")
        responses = [SimpleNamespace(ok=True, json=AsyncMock(return_value=data)) for data in pages]
        def expect_response(matches, **_kwargs):
            self.assertTrue(matches(SimpleNamespace(url=f'https://www.douyin.com/aweme/v1/web/comment/list/?aweme_id={aid}&cursor={pages[3-len(responses)]["cursor"]-20}')))
            return PendingNetworkResponse(responses.pop(0))
        page = SimpleNamespace(url="https://www.douyin.com/note/"+aid, expect_response=expect_response, evaluate=AsyncMock())
        browser = SimpleNamespace(lock=asyncio.Lock(), page=AsyncMock(return_value=page))
        with patch.object(server.upstream, "_browser", browser), patch.object(server.upstream, "_goto", AsyncMock()) as navigate:
            result = json.loads(await server.companion_read_comments(aid, 50, author_id="owner"))
        navigate.assert_not_awaited()
        self.assertEqual(result["count"], 50)
        self.assertEqual([c["id"] for c in result["items"][:3]], ["0", "1", "59"])
        self.assertEqual(page.evaluate.await_args_list[2].args[1]["cursor"], 40)
        self.assertIn('keepalive:true',page.evaluate.await_args.args[0])
        self.assertNotIn('await fetch',page.evaluate.await_args.args[0])
        adapter = DouyinAdapter(self.store, ROOT, data_dir=self.root)
        adapter.web.call = AsyncMock(return_value=result)
        await adapter.get_content_comments(content_id=aid, limit=50, author_id="owner")
        adapter.web.call.assert_awaited_with("companion_read_comments", content_id=aid, limit=50, author_id="owner")

    async def test_douyin_comment_http_error_is_not_an_empty_comment_section(self):
        page=SimpleNamespace(url="about:blank",evaluate=AsyncMock(),expect_response=lambda *_a,**_kw:
                             PendingNetworkResponse(SimpleNamespace(ok=False,status=403)))
        browser=SimpleNamespace(lock=asyncio.Lock(),page=AsyncMock(return_value=page))
        with patch.object(server.upstream,'_browser',browser),patch.object(server.upstream,'_goto',AsyncMock()):
            with self.assertRaisesRegex(RuntimeError,'HTTP 403'):
                await server.companion_read_comments('1234567890123456789',30)

    def media_service(self, platform="douyin", content_type="video"):
        config = self.store.get_config("platforms"); config[platform]["enabled"] = True
        self.store.set_config("platforms", config)
        config = self.store.get_config("media"); config.update(enabled=True, audio_enabled=True)
        self.store.set_config("media", config)
        service = PlatformService(self.store, ROOT, SimpleNamespace(notify=lambda: None), data_dir=self.root)
        adapter = service.adapters[platform]
        adapter.fetch_media_frames = AsyncMock(return_value={"content_id": "123", "author_id": "owner",
            "content_type": content_type, "duration": 10, "frames": [{"time": 5, "mime_type": "image/jpeg",
            "data": base64.b64encode(b"test-image").decode()}], "audio_status": "ok", "transcript": "语音内容"})
        adapter.get_content_comments = AsyncMock(return_value=comments_result(platform, "123", [
            normalize_comment(platform, dy_item(1) if platform == "douyin" else bili_item(1))], 30))
        return service, adapter

    async def test_media_returns_frames_audio_and_comments_with_config_and_override(self):
        for platform, content_type in (("bilibili", "video"), ("douyin", "image")):
            service, adapter = self.media_service(platform, content_type)
            result = await service.fetch_media_frames(platform, content_id="123")
            self.assertEqual(result["transcript"], "语音内容")
            self.assertEqual(len(result["frames"]), 1)
            self.assertEqual(result["comments"]["status"], "ok")
            adapter.get_content_comments.assert_awaited_with(content_id="123", limit=30, author_id="owner")
            await service.fetch_media_frames(platform, content_id="123", comment_limit=50)
            self.assertEqual(adapter.get_content_comments.await_args.kwargs["limit"], 50)
            await service.fetch_media_frames(platform, content_id="123", comment_limit=10)
            self.assertEqual(adapter.get_content_comments.await_args.kwargs["limit"], 10)

    async def test_comment_switch_empty_and_failure_keep_media(self):
        service, adapter = self.media_service()
        result = await service.fetch_media_frames("douyin", content_id="123", include_comments=False)
        self.assertEqual(result["comments"]["status"], "disabled")
        adapter.get_content_comments.assert_not_awaited()
        config = self.store.get_config("media"); config["comments_enabled"] = False
        self.store.set_config("media", config)
        await service.fetch_media_frames("douyin", content_id="123", include_comments=True)
        adapter.get_content_comments.assert_not_awaited()
        config["comments_enabled"] = True; self.store.set_config("media", config)
        adapter.get_content_comments.return_value = comments_result("douyin", "123", [], 30)
        result = await service.fetch_media_frames("douyin", content_id="123")
        self.assertEqual(result["comments"]["status"], "empty")
        adapter.get_content_comments.side_effect = RuntimeError("评论暂时不可用")
        result = await service.fetch_media_frames("douyin", content_id="123")
        self.assertEqual(result["comments"]["status"], "error")
        self.assertEqual(result["transcript"], "语音内容")
        self.assertEqual(len(result["frames"]), 1)

    async def test_watch_video_delivers_comment_text_and_images_to_model(self):
        service, _ = self.media_service()
        result = await service.fetch_media_frames("douyin", content_id="123")
        with patch.dict(os.environ, {"COMPANION_DATABASE": str(self.root / "mcp.sqlite")}):
            tools = importlib.import_module("dsh_companion.mcp_tools")
        for content_type in ("video", "image"):
            result["content_type"] = content_type
            with patch.object(tools, "_api", AsyncMock(return_value=result)) as api:
                blocks = await tools.watch_video("douyin", "123", comment_limit=50)
            self.assertIn('"comments"', blocks[0].text)
            self.assertIn("评论 1", blocks[0].text)
            self.assertEqual(blocks[-1].type, "image")
            self.assertEqual(api.await_args.args[1]["comment_limit"], 50)

    def test_defaults_migrate_and_empty_persona_does_not_replace_custom_persona(self):
        self.assertEqual(MemoryCenter(self.store).get_persona(), "")
        MemoryCenter(self.store).set_persona("用户自己写的性格")
        self.store.set_config("media", {"enabled": True, "audio_enabled": False, "daily_limit": 20, "asr_model": "tiny"})
        self.store.set_config("autonomy", {"enabled": True, "main_interval_seconds": 900,
            "memory_interval_seconds": 3600, "background_scan_seconds": 60})
        reopened = Store(self.store.path)
        self.assertEqual(reopened.get_config("media")["comment_limit"], 30)
        self.assertTrue(reopened.get_config("media")["comments_enabled"])
        self.assertEqual(reopened.get_config("autonomy")["direction"], "")
        self.assertEqual(MemoryCenter(reopened).get_persona(), "用户自己写的性格")
        runtime = DshRuntime(reopened, ROOT, data_dir=self.root)
        runtime.var.mkdir(exist_ok=True)
        import yaml
        self.assertEqual(yaml.safe_load(runtime._patch(reopened.get_config("dsh")).read_text(encoding="utf-8"))[0]["config"]["personaPrefix"], "")

    def test_api_saves_reading_preferences_and_autonomy_direction(self):
        client = TestClient(create_app(ROOT, data_dir=self.root / "api"))
        media = client.get("/api/config").json()["media"]
        media.update(comment_limit=50, comments_enabled=False)
        self.assertEqual(client.put("/api/config/media", json={"value": media}).json()["comment_limit"], 50)
        autonomy = client.get("/api/config").json()["autonomy"]
        autonomy["direction"] = "做一个夜景创作系列"
        client.put("/api/config/autonomy", json={"value": autonomy})
        self.assertEqual(client.get("/api/config").json()["autonomy"]["direction"], "做一个夜景创作系列")
        response = client.post("/api/memories", json={"memory_id": "persona", "kind": "persona", "content": ""})
        self.assertEqual(response.json()["content"], "")


if __name__ == "__main__":
    unittest.main()
