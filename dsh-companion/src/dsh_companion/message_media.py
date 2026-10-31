"""入站图片与表情：下载、解密、转换成 DSH 图片输入并保存可复用路径。"""
from __future__ import annotations

import base64
import hashlib
import json
import io
from pathlib import Path
from typing import Any

import httpx
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from PIL import Image


def _mime(data: bytes) -> str | None:
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png'
    if data.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg'
    if data.startswith((b'GIF87a', b'GIF89a')):
        return 'image/gif'
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return 'image/webp'
    return None


def mime_for_bytes(data: bytes) -> str | None:
    """返回图片真实 MIME；给本地媒体和平台下载结果共用。"""
    return _mime(data)


def extension_for_mime(media_type: str) -> str:
    return {'image/png': 'png', 'image/jpeg': 'jpg', 'image/webp': 'webp', 'image/gif': 'gif'}[media_type]


def image_block_from_path(path: str | Path) -> dict[str, str]:
    """读取已经保存的图片，生成 DSH 图片块。"""
    file_path = Path(path)
    data = file_path.read_bytes()
    media_type = _mime(data)
    if media_type is None:
        raise ValueError('本地文件不是支持的图片格式')
    return {'type': 'image', 'mimeType': media_type,
            'data': base64.b64encode(data).decode('ascii'), 'path': str(file_path.resolve())}


def visual_blocks_from_path(path: str | Path, *, max_frames: int = 4) -> list[dict[str, str]]:
    """An animated sticker is sampled across its duration, not frozen at frame 0."""
    with Image.open(path) as image:
        if getattr(image, 'n_frames', 1) == 1:
            block = image_block_from_path(path)
            return [{key: block[key] for key in ('type', 'mimeType', 'data')}]
        durations = []
        for index in range(image.n_frames):
            image.seek(index)
            durations.append(image.info.get('duration', 100) or 100)
        total = sum(durations)
        count = min(max_frames, image.n_frames)
        targets = [total * i / (count-1) for i in range(count)]
        indices=[];elapsed=0;target=0
        for index, duration in enumerate(durations):
            while target < count and (targets[target] < elapsed+duration or index == len(durations)-1):
                if index not in indices:indices.append(index)
                target += 1
            elapsed += duration
        blocks=[]
        for index in indices:
            image.seek(index)
            frame=image.convert('RGBA')
            frame.thumbnail((768,768), Image.Resampling.LANCZOS)
            output=io.BytesIO();frame.save(output,format='PNG')
            blocks.extend([{'type':'text','text':f'动图第 {index+1}/{image.n_frames} 帧'},
                {'type':'image','mimeType':'image/png','data':base64.b64encode(output.getvalue()).decode('ascii')}])
        return blocks


def persist_image_block(block: dict[str, Any], folder: Path, *, stem: str = 'message') -> dict[str, Any] | None:
    """把临时图片块复制到媒体库，并返回文件路径、哈希和 MIME。"""
    source = block.get('path')
    if not source:
        return None
    source_path = Path(source)
    data = source_path.read_bytes()
    media_type = block.get('mimeType') or _mime(data)
    if media_type is None:
        return None
    digest = hashlib.sha256(data).hexdigest()
    folder.mkdir(parents=True, exist_ok=True)
    destination = folder / f'{stem}-{digest[:16]}.{extension_for_mime(media_type)}'
    if not destination.exists():
        destination.write_bytes(data)
    return {'path': str(destination.resolve()), 'mime_type': media_type,
            'sha256': digest, 'size': len(data)}


def normalize_message_content(payload: dict[str, Any]) -> dict[str, Any]:
    """Resolve Douyin sticker URL lists, including messages saved before this fix."""
    content = dict(payload.get('content') or {})
    if content.get('kind') != 'emoji':
        return content
    raw = payload.get('raw_content') or {}
    if isinstance(raw, str):
        raw = json.loads(raw)
    resource = raw.get('url') or {}
    candidates = list(content.get('urls') or [])
    candidates.append(content.get('url') or '')
    if isinstance(resource, dict):
        candidates.extend(resource.get('url_list') or [])
        candidates.append(resource.get('uri') or '')
    elif isinstance(resource, str):
        candidates.append(resource)
    content['urls'] = list(dict.fromkeys(url for url in candidates if url.startswith(('https://', 'http://'))))
    content['url'] = next(iter(content['urls']), '')
    return content


def _sources(content: dict[str, Any]) -> tuple[list[str], str]:
    if content.get('kind') == 'emoji':
        return list(content.get('urls') or [content.get('url', '')]), ''
    if content.get('kind') == 'image':
        image = content.get('image') or {}
        urls = [url for key in ('originUrls', 'largeUrls', 'mediumUrls', 'thumbUrls')
                for url in (image.get(key) or [])]
        return urls, image.get('skey', '')
    return [], ''


def source_urls_for_content(content: dict[str, Any]) -> list[str]:
    return list(dict.fromkeys(url for url in _sources(content)[0] if url))


def cache_image(content: dict[str, Any], folder: Path, *, index: int,
                client: httpx.Client | None = None) -> dict[str, str] | None:
    """从平台下载原图或表情，写入本轮临时目录并返回 DSH 支持的内联图片块。"""
    urls, skey = _sources(content)
    if not urls:
        return None
    errors = []
    owned = client is None
    client = client or httpx.Client(follow_redirects=True, timeout=20)
    try:
        for url in dict.fromkeys(urls):
            if not url:
                continue
            try:
                response = client.get(url)
                response.raise_for_status()
                data = response.content
                media_type = _mime(data)
                if media_type is None and skey:
                    data = AESGCM(bytes.fromhex(skey)).decrypt(data[:12], data[12:], None)
                    media_type = _mime(data)
                if media_type is None:
                    raise ValueError('平台返回的内容不是支持的图片格式')
                extension = {'image/png': 'png', 'image/jpeg': 'jpg', 'image/webp': 'webp', 'image/gif': 'gif'}[media_type]
                path = folder / f'{index}.{extension}'
                path.write_bytes(data)
                return {'type': 'image', 'mimeType': media_type,
                        'data': base64.b64encode(data).decode('ascii'), 'path': str(path.resolve())}
            except Exception as exc:
                errors.append(f'{type(exc).__name__}: {exc}')
    finally:
        if owned:
            client.close()
    raise RuntimeError('平台图片读取失败：' + '; '.join(errors))
