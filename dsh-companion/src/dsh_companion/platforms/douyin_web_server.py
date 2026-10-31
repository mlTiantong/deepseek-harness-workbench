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


async def _download_note_image(page, url: str) -> bytes:
    import httpx
    from urllib.parse import urljoin
    cookies = httpx.Cookies()
    for cookie in await page.context.cookies([url]):
        cookies.set(cookie["name"], cookie["value"], domain=cookie.get("domain", ""), path=cookie.get("path", "/"))
    async with httpx.AsyncClient(cookies=cookies, timeout=90, follow_redirects=False) as client:
        for _ in range(4):
            parsed = urlparse(url)
            host = (parsed.hostname or "").lower()
            allowed = upstream._media_url_allowed(url) or (
                parsed.scheme == "https" and not parsed.username and not parsed.password
                and (host == "douyinpic.com" or host.endswith(".douyinpic.com")))
            if not allowed:
                raise ValueError("图文图片地址不在允许的媒体域名内")
            async with client.stream("GET", url, headers={"Referer": upstream.HOME_URL, "User-Agent": upstream.UA}) as response:
                if response.is_redirect:
                    url = urljoin(url, response.headers.get("location", ""))
                    continue
                response.raise_for_status()
                data = bytearray()
                async for chunk in response.aiter_bytes(65536):
                    data.extend(chunk)
                    if len(data) > 20 * 1024 * 1024:
                        raise ValueError("图文图片超过下载大小上限")
                return bytes(data)
    raise ValueError("图文图片重定向次数过多")


@upstream.mcp.tool()
async def companion_video_frames(content_id: str, timestamps: list[float] | None = None,
                                 frame_count: int = 8) -> str:
    """Capture time-labelled visual frames. Audio is not transcribed."""
    import re
    match = re.search(r"(?:video|note)/(\d+)", content_id)
    content_id = match.group(1) if match else content_id
    if not content_id.isdigit() or not 1 <= frame_count <= 16:
        raise ValueError("需要数字作品 ID 和 1 至 16 帧")
    async with upstream._browser.lock:
        page = await upstream._browser.page()
        # Capture the detail response: image posts have no video player, even at /video/ URLs.
        net = upstream.Net(page, upstream._is_detail_url)
        try:
            await page.goto(f"https://www.douyin.com/video/{content_id}", wait_until="domcontentloaded", timeout=45000)
            hit = await net.wait_for(lambda _u, data: bool((data.get("aweme_detail") or {}).get("aweme_id")), timeout=8)
            detail = ((hit or {}).get("data") or {}).get("aweme_detail") or {}
        finally:
            net.close()
        images = detail.get("images") or []
        if images:
            from io import BytesIO
            from PIL import Image
            frames = []
            for item in images[:16]:
                addresses = item.get("url_list") or item.get("download_url_list") or []
                if not addresses:
                    raise ValueError("图文图片没有可读取的媒体地址")
                image = await _download_note_image(page, addresses[0])
                picture = Image.open(BytesIO(image)).convert("RGB")
                picture.thumbnail((960, 960))
                out = BytesIO()
                picture.save(out, format="JPEG", quality=80)
                frames.append({"mime_type": "image/jpeg", "data": base64.b64encode(out.getvalue()).decode("ascii")})
            return json.dumps({"ok": True, "platform": "douyin", "content_id": content_id,
                               "content_type": "image", "title": detail.get("desc") or "",
                               "duration": 0, "frames": frames, "image_count": len(images),
                               "audio_understood": False}, ensure_ascii=False)
        player = page.locator("video:visible").first
        await player.wait_for(state="visible", timeout=30000)
        await player.scroll_into_view_if_needed()
        duration = await player.evaluate("""async v => {
            v.muted = true;
            if (v.readyState < 1) await new Promise((resolve, reject) => {
                const timer = setTimeout(() => reject(new Error('视频元数据加载超时')), 15000);
                v.addEventListener('loadedmetadata', () => { clearTimeout(timer); resolve(); }, {once:true});
            });
            return v.duration;
        }""")
        if not duration or duration <= 0:
            raise ValueError("无法获取抖音视频时长")
        times = timestamps if timestamps is not None else [round(duration * (i + 0.5) / frame_count, 2)
                                                           for i in range(frame_count)]
        if not times or len(times) > 16 or any(t < 0 or t >= duration for t in times):
            raise ValueError("请选择视频时长内的 1 至 16 个时间点")
        frames = []
        for timestamp in times:
            actual = await player.evaluate("""async (v, requested) => {
                if (Math.abs(v.currentTime - requested) > 0.05) await new Promise((resolve, reject) => {
                    const timer = setTimeout(() => reject(new Error('视频定位超时')), 15000);
                    v.addEventListener('seeked', () => { clearTimeout(timer); resolve(); }, {once:true});
                    v.currentTime = requested;
                });
                v.pause();
                return v.currentTime;
            }""", float(timestamp))
            image = await player.screenshot(type="jpeg", quality=75, timeout=15000)
            from io import BytesIO
            from PIL import Image
            picture = Image.open(BytesIO(image)).convert("RGB")
            picture.thumbnail((960, 960))
            compressed = BytesIO()
            picture.save(compressed, format="JPEG", quality=75)
            image = compressed.getvalue()
            frames.append({"time": actual, "mime_type": "image/jpeg", "data": base64.b64encode(image).decode("ascii")})
        return json.dumps({"ok": True, "platform": "douyin", "content_id": content_id,
                           "title": await page.title(), "duration": duration, "frames": frames,
                           "subtitles": "", "audio_understood": False}, ensure_ascii=False)


@upstream.mcp.tool()
async def companion_download_video(content_id: str) -> str:
    """下载播放器视频到临时文件，供宿主在本机提取音频。"""
    import re
    import tempfile
    match = re.search(r"(?:video|note)/(\d+)", content_id)
    content_id = match.group(1) if match else content_id
    if not content_id.isdigit():
        raise ValueError("需要抖音数字作品 ID")
    async with upstream._browser.lock:
        page = await upstream._browser.page()
        net = upstream.Net(page, upstream._is_detail_url)
        try:
            await upstream._goto(page, upstream.VIDEO_URL.format(aweme_id=content_id), settle=2)
            hit = await net.wait_for(lambda _u, data: bool((data.get("aweme_detail") or {}).get("aweme_id")), timeout=15)
            detail = ((hit or {}).get("data") or {}).get("aweme_detail") or {}
            video = detail.get("video") or {}
            addresses = [video.get("play_addr")] + [x.get("play_addr") for x in video.get("bit_rate") or []]
            urls = [u for address in addresses for u in (address or {}).get("url_list", [])
                    if upstream._media_url_allowed(u)]
            if not urls:
                raise ValueError("抖音未提供可读取的音轨视频地址")
            folder = upstream.HOME / "media" / "audio-temp"
            folder.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(prefix="video-", suffix=".mp4", dir=folder, delete=False) as f:
                path = Path(f.name)
            try:
                await upstream._download_media(page, urls[0], path, max_bytes=256 * 1024 * 1024)
            except BaseException:
                path.unlink(missing_ok=True)
                raise
            return json.dumps({"ok": True, "path": str(path)}, ensure_ascii=False)
        finally:
            net.close()

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
