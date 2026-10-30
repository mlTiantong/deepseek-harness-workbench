"""创作目录：列举、读取、扫描变化并唤醒创作工作者。"""
from __future__ import annotations

import base64
import mimetypes
import os
from pathlib import Path
from typing import Any

from .scheduler import Scheduler
from .store import Store


class CreationFolder:
    def __init__(self, store: Store, scheduler: Scheduler, root: str | Path):
        self.store = store
        self.scheduler = scheduler
        self.root = Path(root).resolve()

    def path(self) -> Path:
        folder = Path(self.store.get_config("creation_folder")).expanduser()
        if not folder.is_absolute():
            folder = self.root / folder
        folder.mkdir(parents=True, exist_ok=True)
        return folder.resolve()

    def open_folder(self) -> str:
        folder = self.path()
        os.startfile(folder)
        return str(folder)

    def list_files(self) -> list[dict[str, Any]]:
        folder = self.path()
        files = []
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                stat = path.stat()
                files.append({"path": str(path), "relative_path": str(path.relative_to(folder)),
                              "size": stat.st_size, "modified_at": stat.st_mtime,
                              "modified_ns": stat.st_mtime_ns,
                              "mime_type": mimetypes.guess_type(path.name)[0] or "application/octet-stream"})
        return files

    def read_file(self, path: str, *, binary: bool = False) -> dict[str, Any]:
        source = Path(path)
        if not source.is_absolute():
            source = self.path() / source
        data = source.read_bytes()
        return {"path": str(source.resolve()), "size": len(data),
                "encoding": "base64" if binary else "utf-8",
                "content": base64.b64encode(data).decode("ascii") if binary else data.decode("utf-8")}

    def scan(self) -> list[dict[str, Any]]:
        """新增或更新的创作素材进入 creator 队列，由角色决定后续工作。"""
        previous = self.store.get_config("creation_seen") or {}
        created = []
        for item in self.list_files():
            relative = item["path"]
            if previous.get(relative) == item["modified_ns"]:
                continue
            job = self.scheduler.enqueue("creator", "creation_file", {"file": item},
                                         priority=70, autonomous=True)
            created.append(job)
            previous[relative] = item["modified_ns"]
            self.store.set_config("creation_seen", previous)
        return created
