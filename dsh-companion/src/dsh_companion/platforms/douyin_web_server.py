"""项目内对开源抖音 MCP 的补充：通知收件箱及图文、视频发布。"""
from __future__ import annotations

import asyncio
import base64
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import TimeoutError as PlaywrightTimeoutError

PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT / "vendor/douyin-tools/vendor/douyin-mcp"))
import mcp_server as upstream  # noqa: E402


def _own_post(item: dict, account_id: str) -> dict | None:
    """Normalize only posts returned from the logged-in account's own feed."""
    post_id = str(item.get("aweme_id") or "")
    author_id = str((item.get("author") or {}).get("uid") or "")
    if not post_id.isdigit() or (author_id and author_id != account_id):
        return None
    kind = item.get("aweme_type")
    path_kind = "note" if kind in (68, 150, 211) else "video" if kind in (0, 55) else None
    candidate = str((item.get("share_info") or {}).get("share_url") or "")
    parsed = urlparse(candidate)
    pieces = parsed.path.strip("/").split("/")
    if (parsed.scheme == "https" and parsed.hostname in ("www.douyin.com", "douyin.com")
            and len(pieces) >= 2 and pieces[0] in ("note", "video") and pieces[1] == post_id):
        path_kind = pieces[0]
    url = f"https://www.douyin.com/{path_kind}/{post_id}" if path_kind else None
    return {"content_id": post_id, "title": str(item.get("desc") or ""),
            "created_at": item.get("create_time"), "content_type": path_kind,
            "share_url": url, "visibility": "unknown"}


@upstream.mcp.tool()
async def companion_my_posts(limit: int = 20, cursor: str = "0") -> str:
    """Read a page of the signed-in account's posts and candidate share URLs."""
    if not str(cursor).isdigit():
        raise ValueError("作品分页游标必须是数字")
    count = max(1, min(int(limit), 50))
    async with upstream._browser.lock:
        ctx = await upstream._browser.context()
        page = await upstream._browser.page()
        await upstream._goto(page, "https://www.douyin.com/", settle=2)
        if not (await upstream._login_state(page, ctx))["loggedIn"]:
            return json.dumps({"ok": False, "error": "读取本人作品需要登录"}, ensure_ascii=False)
        profile_raw = await page.evaluate("""async () => {
            const r = await fetch('/aweme/v1/web/user/profile/self/?device_platform=webapp&aid=6383&channel=channel_pc_web', {credentials:'include'});
            if (!r.ok) throw new Error('本人信息 HTTP ' + r.status);
            return await r.text();
        }""")
        profile = json.loads(profile_raw)
        user = profile.get("user") or {}
        account_id, sec_uid = str(user.get("uid") or ""), str(user.get("sec_uid") or "")
        if profile.get("status_code", 0) != 0 or not account_id or not sec_uid:
            raise RuntimeError("未能核实抖音本人账号，无法查询作品")
        raw = await page.evaluate("""async ({uid, cursor, count}) => {
            const q = new URLSearchParams({device_platform:'webapp', aid:'6383', channel:'channel_pc_web',
                sec_user_id:uid, max_cursor:cursor, count:String(count)});
            const r = await fetch('/aweme/v1/web/aweme/post/?' + q, {credentials:'include'});
            if (!r.ok) throw new Error('本人作品 HTTP ' + r.status);
            return await r.text();
        }""", {"uid": sec_uid, "cursor": str(cursor), "count": count})
        data = json.loads(raw)
        if data.get("status_code", 0) != 0:
            raise RuntimeError(f"本人作品接口返回错误：{data.get('status_code')}")
        items = [post for item in data.get("aweme_list") or [] if (post := _own_post(item, account_id))]
        return json.dumps({"ok": True, "account_id": account_id, "items": items,
                           "next_cursor": str(data.get("max_cursor") or "0"),
                           "has_more": bool(data.get("has_more"))}, ensure_ascii=False)


@upstream.mcp.tool()
async def companion_video_frames(content_id: str, timestamps: list[float]) -> str:
    """Capture visible video frames; no claim about unheard audio is made."""
    if not content_id.isdigit() or not timestamps or len(timestamps) > 3:
        raise ValueError("需要数字作品 ID 和 1 至 3 个时间点")
    if any(not isinstance(t, (int, float)) or not 0 <= t <= 600 for t in timestamps):
        raise ValueError("视频时间点必须在 0 到 600 秒之间")
    async with upstream._browser.lock:
        page = await upstream._browser.page()
        await page.goto(f"https://www.douyin.com/video/{content_id}", wait_until="domcontentloaded", timeout=45000)
        video = page.locator("video:visible").first
        await video.wait_for(state="visible", timeout=30000)
        await video.scroll_into_view_if_needed()
        frames = []
        for timestamp in timestamps:
            actual = await video.evaluate("""async (v, requested) => {
                v.muted = true;
                if (v.readyState < 1) await new Promise((resolve, reject) => {
                    const timer = setTimeout(() => reject(new Error('视频元数据加载超时')), 10000);
                    v.addEventListener('loadedmetadata', () => { clearTimeout(timer); resolve(); }, {once:true});
                });
                const target = Number.isFinite(v.duration) ? Math.min(requested, Math.max(0, v.duration - 0.1)) : requested;
                if (Math.abs(v.currentTime - target) > 0.1) await new Promise((resolve, reject) => {
                    const timer = setTimeout(() => reject(new Error('视频定位超时')), 10000);
                    v.addEventListener('seeked', () => { clearTimeout(timer); resolve(); }, {once:true});
                    v.currentTime = target;
                });
                v.pause();
                return v.currentTime;
            }""", float(timestamp))
            image = await video.screenshot(type="jpeg", quality=70, timeout=15000)
            frames.append({"time": actual, "mime_type": "image/jpeg", "data": base64.b64encode(image).decode("ascii")})
        return json.dumps({"ok": True, "content_id": content_id, "frames": frames}, ensure_ascii=False)


@upstream.mcp.tool()
async def companion_account_info() -> str:
    """读取当前网页账号的真实用户标识，不把登录 Cookie 当作用户 ID。"""
    async with upstream._browser.lock:
        page = await upstream._browser.page()
        if not page.url.startswith("https://www.douyin.com/"):
            await page.goto("https://www.douyin.com/", wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(1500)
        raw = await page.evaluate("""async () => {
            const response = await fetch('/aweme/v1/web/user/profile/self/?device_platform=webapp&aid=6383&channel=channel_pc_web',
                                         {credentials:'include'});
            if (!response.ok) throw new Error('账号信息接口 HTTP ' + response.status);
            return await response.text();
        }""")
        result = json.loads(raw)
        if result.get("status_code", 0) != 0:
            raise RuntimeError(f"抖音账号信息接口返回错误：{result}")
        user = result["user"]
        return json.dumps({"ok": True, "uid": str(user["uid"]), "nickname": user["nickname"]}, ensure_ascii=False)


@upstream.mcp.tool()
async def companion_notice_page(group: str, min_time: str = "0", max_time: str = "0") -> str:
    """读取一页评论或 @ 通知；不主动修改平台已读状态。"""
    async with upstream._browser.lock:
        page = await upstream._browser.page()
        if not page.url.startswith("https://www.douyin.com/"):
            await page.goto("https://www.douyin.com/", wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(1500)
        raw = await page.evaluate("""async ({group, minTime, maxTime}) => {
            const query = new URLSearchParams({device_platform:'webapp', aid:'6383',
                channel:'channel_pc_web', is_new_notice:'1', is_mark_read:'0',
                notice_group:group, count:'20', min_time:minTime, max_time:maxTime});
            const response = await fetch('/aweme/v1/web/notice/?' + query, {credentials:'include'});
            if (!response.ok) throw new Error('通知接口 HTTP ' + response.status);
            return await response.text();
        }""", {"group": group, "minTime": min_time, "maxTime": max_time})
        # 在 Python 侧解析，保留平台返回的 64 位 ID 精度。
        result = json.loads(raw)
        if result.get("status_code", 0) != 0:
            raise RuntimeError(f"抖音通知接口返回错误：{result}")
        return json.dumps({"ok": True, "notices": result["notice_list_v2"],
                           "has_more": result["has_more"], "min_time": result.get("min_time", 0),
                           "max_time": result.get("max_time", 0)}, ensure_ascii=False)


async def _fill_caption(page, editor, body: str, tags: list[str]) -> None:
    await editor.click()
    if body:
        await page.keyboard.insert_text(body)
    for raw_tag in tags:
        tag = raw_tag.strip().lstrip("#")
        await page.keyboard.type(" #" + tag, delay=20)
        await page.wait_for_timeout(500)
        await page.keyboard.press("Enter")


async def _publication_music(page, music: str) -> None:
    music = str(music or "").strip()
    if not music:
        return
    await page.get_by_text("添加音乐", exact=False).first.click(timeout=15000)
    search = page.locator('input[placeholder*="搜索音乐"], input[placeholder*="搜索歌曲"]').first
    await search.wait_for(state="visible", timeout=15000)
    await search.fill(music)
    await page.wait_for_timeout(1000)
    result = page.get_by_text(music, exact=True).last
    if not await result.count():
        result = page.locator('[role="option"], [class*="music"], [class*="song"]').filter(has_text=music).last
    await result.click(timeout=15000)


async def _publication_options(page, cover_path: str, ai_generated: bool, music: str = "") -> None:
    if music:
        await _publication_music(page, music)
    if cover_path:
        await page.get_by_text("设置封面", exact=False).first.click()
        image_input = page.locator('input[type="file"][accept*="image"]').last
        await image_input.set_input_files(cover_path)
        await page.get_by_role("button", name="保存", exact=True).last.click(timeout=30000)
    if ai_generated:
        await page.get_by_text("自主声明", exact=False).first.click()
        await page.get_by_text("内容由AI生成", exact=False).last.click()
        await page.get_by_role("button", name="确定", exact=True).last.click()


async def _submit_publication(page) -> None:
    await page.get_by_role("button", name="发布", exact=True).click(timeout=600000)
    confirm = page.get_by_role("button", name="确认发布")
    try:
        await confirm.wait_for(state="visible", timeout=8000)
    except PlaywrightTimeoutError:
        pass
    else:
        await confirm.click()
    success = page.get_by_text("发布成功", exact=False).first
    navigation = asyncio.create_task(
        page.wait_for_url("**/manage**", wait_until="commit", timeout=120000)
    )
    success_text = asyncio.create_task(success.wait_for(state="visible", timeout=120000))
    done, pending = await asyncio.wait({navigation, success_text}, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    for task in done:
        task.result()


@upstream.mcp.tool()
async def companion_publish_images(files: list[str], title: str = "", content: str = "",
                                   tags: list[str] | None = None, cover_path: str = "",
                                   ai_generated: bool = False, music: str = "") -> str:
    """在独立登录的创作者中心上传并发布图文。"""
    if not files:
        raise ValueError("抖音图文发布需要至少一张图片")
    async with upstream._browser.lock:
        page = await upstream._browser.page()
        await page.goto("https://creator.douyin.com/creator-micro/content/upload?default-tab=3",
                        wait_until="domcontentloaded", timeout=45000)
        async with page.expect_file_chooser(timeout=30000) as chooser:
            await page.get_by_role("button", name="上传图文").click()
        await (await chooser.value).set_files(files)
        await page.wait_for_url("**/post/image**", timeout=600000)
        editor = page.locator(".zone-container").first
        await editor.wait_for(state="visible", timeout=60000)
        title_field = page.locator('input[placeholder*="标题"]').first
        has_title_field = await title_field.is_visible()
        if has_title_field:
            await title_field.fill(title)
        body = "\n\n".join(part for part in ("" if has_title_field else title, content) if part)
        await _fill_caption(page, editor, body, tags or [])
        await _publication_options(page, cover_path, ai_generated, music)
        await _submit_publication(page)
        return json.dumps({"ok": True, "published": True, "management_url": page.url, "content_type": "image"},
                          ensure_ascii=False)


@upstream.mcp.tool()
async def companion_publish_video(file: str, title: str, content: str = "", tags: list[str] | None = None,
                                  cover_path: str = "", ai_generated: bool = False, music: str = "") -> str:
    """上传视频并填写独立的标题、正文、话题与可选发布信息。"""
    async with upstream._browser.lock:
        page = await upstream._browser.page()
        await page.goto("https://creator.douyin.com/creator-micro/content/upload",
                        wait_until="domcontentloaded", timeout=45000)
        upload = page.locator('input[type="file"]').first
        await upload.wait_for(state="attached", timeout=60000)
        await upload.set_input_files(file)
        title_field = page.locator('input[placeholder*="标题"], textarea[placeholder*="标题"], '
                                   '[data-e2e="title-input"] input').first
        await title_field.wait_for(state="visible", timeout=600000)
        await title_field.fill(title)
        if content or tags:
            editor = page.locator('[data-e2e="desc-input"], .notranslate[contenteditable="true"], '
                                  '.zone-container[contenteditable="true"], [contenteditable="true"], '
                                  'textarea[placeholder*="简介"]').first
            await editor.wait_for(state="visible", timeout=60000)
            await _fill_caption(page, editor, content, tags or [])
        await _publication_options(page, cover_path, ai_generated, music)
        await _submit_publication(page)
        return json.dumps({"ok": True, "published": True, "management_url": page.url, "content_type": "video"},
                          ensure_ascii=False)


if __name__ == "__main__":
    upstream.mcp.run(transport="stdio")
