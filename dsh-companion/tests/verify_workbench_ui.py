"""用真实浏览器验证评论设置、自主方向和空人设的读写。"""
from pathlib import Path
import socket
import threading
import time
from tempfile import TemporaryDirectory
from unittest.mock import patch
import uvicorn
from dsh_companion.app import Companion, create_app
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "var/feature-qa"
OUTPUT.mkdir(parents=True, exist_ok=True)

async def idle(self):
    pass

with TemporaryDirectory(prefix="companion-ui-") as folder, patch.object(Companion, "start", idle), patch.object(Companion, "stop", idle):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    app = create_app(ROOT, data_dir=folder)
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.05)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="chrome", headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(f"http://127.0.0.1:{port}/")
            page.wait_for_load_state("networkidle")
            page.locator('.nav[data-view="platforms"]').click()
            page.wait_for_load_state('networkidle')
            expect(page.locator('#video-reading-form [name="comment_limit"]')).to_have_value("30")
            expect(page.locator('#video-reading-form [name="comments_enabled"]')).to_be_checked()
            page.locator('#video-reading-form [name="comment_limit"]').fill("50")
            page.locator('#video-reading-form [name="comments_enabled"]').uncheck()
            with page.expect_response(lambda r: r.url.endswith("/api/config/media") and r.request.method == "PUT") as response:
                page.locator('#video-reading-form button[type="submit"]').click()
            assert response.value.json()["comment_limit"] == 50
            assert response.value.json()["comments_enabled"] is False
            page.reload(); page.wait_for_load_state("networkidle")
            page.locator('.nav[data-view="platforms"]').click()
            page.wait_for_load_state('networkidle')
            expect(page.locator('#video-reading-form [name="comment_limit"]')).to_have_value("50")
            expect(page.locator('#video-reading-form [name="comments_enabled"]')).not_to_be_checked()
            page.locator('#video-reading-form [name="comments_enabled"]').check()
            with page.expect_response(lambda r: r.url.endswith("/api/config/media") and r.request.method == "PUT"):
                page.locator('#video-reading-form button[type="submit"]').click()
            page.screenshot(path=str(OUTPUT / "comment-settings.png"), full_page=True)

            page.locator('.nav[data-view="settings"]').click()
            page.wait_for_load_state('networkidle')
            page.locator('#autonomy-form [name="direction"]').fill("研究城市夜景，做一份拍摄指南")
            with page.expect_response(lambda r: r.url.endswith("/api/config/autonomy") and r.request.method == "PUT") as response:
                page.locator('#autonomy-form button[type="submit"]').click()
            assert response.value.json()["direction"] == "研究城市夜景，做一份拍摄指南", response.value.json()
            page.reload(); page.wait_for_load_state("networkidle")
            page.locator('.nav[data-view="settings"]').click()
            page.wait_for_load_state('networkidle')
            expect(page.locator('#autonomy-form [name="direction"]')).to_have_value("研究城市夜景，做一份拍摄指南")
            page.screenshot(path=str(OUTPUT / "autonomy-settings.png"), full_page=True)
            with page.expect_response(lambda r: r.url.endswith("/api/pet/start") and r.request.method == "POST") as response:
                page.locator('#start-desktop-pet').click()
            assert response.value.json()["running"] is True
            time.sleep(2)
            assert app.state.companion.pet.status()["running"]
            assert app.state.companion.store.get_config("desktop_pet")["enabled"]
            with page.expect_response(lambda r: r.url.endswith("/api/pet/stop") and r.request.method == "POST") as response:
                page.locator('#stop-desktop-pet').click()
            assert response.value.json()["running"] is False
            assert not app.state.companion.store.get_config("desktop_pet")["enabled"]


            page.locator('.nav[data-view="memory"]').click()
            page.wait_for_load_state('networkidle')
            expect(page.locator('#persona-editor')).to_have_value("")
            page.locator('#persona-editor').fill("自定义人格")
            with page.expect_response(lambda r: r.url.endswith("/api/memories") and r.request.method == "POST"):
                page.locator('#save-persona').click()
            with page.expect_response(lambda r: r.url.endswith("/api/memories") and r.request.method == "POST"):
                page.locator('#clear-persona').click()
            expect(page.locator('#persona-editor')).to_have_value("")
            page.reload(); page.wait_for_load_state("networkidle")
            page.locator('.nav[data-view="memory"]').click()
            page.wait_for_load_state('networkidle')
            expect(page.locator('#persona-editor')).to_have_value("")
            assert page.locator('#restore-whale-persona').count() == 0
            page.screenshot(path=str(OUTPUT / "empty-persona.png"), full_page=True)

            page.set_viewport_size({"width": 390, "height": 844})
            page.locator('.nav[data-view="platforms"]').click()
            page.wait_for_load_state('networkidle')
            expect(page.locator('#video-reading-form [name="comment_limit"]')).to_be_visible()
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            page.screenshot(path=str(OUTPUT / "comment-settings-mobile.png"), full_page=True)
            assert not errors, errors
            browser.close()
    finally:
        app.state.companion.pet.stop()
        server.should_exit = True
        thread.join()
print("Workbench UI passed: persisted comment settings, autonomy direction, empty persona, real native pet start/stop; desktop + mobile.")
