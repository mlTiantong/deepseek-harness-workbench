"""OpenAI 兼容图像生成接口。"""
from __future__ import annotations

import base64
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from .message_media import _mime
from .store import Store


class ImageGeneration:
    def __init__(self, store: Store, data_dir: Path):
        self.store = store
        self.data_dir = data_dir
        self.key_file = data_dir / "image-api-key.txt"
        self.output_dir = data_dir / "generated-images"

    def config(self) -> dict[str, Any]:
        return {**self.store.get_config("image_generation"), "key_saved": self.key_file.exists()}

    def configure(self, *, url: str, api_key: str = "", model: str = "") -> dict[str, Any]:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if api_key:
            self.key_file.write_text(api_key, encoding="utf-8")
        self.store.set_config("image_generation", {"url": url.strip(), "model": model.strip()})
        return self.config()

    async def generate(self, prompt: str, *, model: str = "", size: str = "", n: int = 1) -> dict[str, Any]:
        config = self.store.get_config("image_generation")
        url = config["url"].rstrip("/")
        if not url or not self.key_file.exists():
            raise ValueError("请先在运行设置填写图像生成 URL 和 API Key")
        if urlsplit(url).path.rstrip("/") in ("", "/v1"):
            url += "/images/generations"
        payload: dict[str, Any] = {"prompt": prompt, "n": n}
        model = model or config["model"]
        if model:
            payload["model"] = model
        if size:
            payload["size"] = size
        key = self.key_file.read_text(encoding="utf-8").strip()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        async with httpx.AsyncClient(timeout=300, follow_redirects=True) as client:
            response = await client.post(url, headers={"Authorization": "Bearer " + key}, json=payload)
            response.raise_for_status()
            data = response.json()
            images = []
            for item in data["data"]:
                if item.get("b64_json"):
                    image = base64.b64decode(item["b64_json"])
                else:
                    downloaded = await client.get(item["url"])
                    downloaded.raise_for_status()
                    image = downloaded.content
                mime = _mime(image)
                extension = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp",
                             "image/gif": "gif"}[mime]
                path = self.output_dir / f"{uuid.uuid4().hex}.{extension}"
                path.write_bytes(image)
                images.append({"path": str(path.resolve()), "mime_type": mime,
                               "revised_prompt": item.get("revised_prompt")})
        return {"images": images, "model": model, "usage": data.get("usage")}
