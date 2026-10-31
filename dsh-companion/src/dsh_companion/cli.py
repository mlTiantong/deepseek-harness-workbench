"""命令行入口：启动本机工作台或迁移用户已有模型 API 路由。"""
from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn

from .app import create_app
from .dsh_runtime import DshRuntime
from .store import Store


def main() -> None:
    parser = argparse.ArgumentParser(prog="dsh-companion")
    parser.add_argument("command", nargs="?", default="run", choices=("run", "import-model", "pet"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--api-file", help="URL、密钥、模型三行一组的配置文件")
    parser.add_argument("--route-index", type=int, default=0)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    if args.command == "pet":
        import httpx
        response = httpx.post(f"http://127.0.0.1:{args.port}/api/pet/start", timeout=5)
        response.raise_for_status()
        print("大肥鱼桌宠已打开。")
        return
    if args.command == "import-model":
        if not args.api_file:
            parser.error("import-model 需要 --api-file")
        store = Store(root / "var/companion.sqlite3")
        config = DshRuntime(store, root).import_model(args.api_file, args.route_index)
        print(f"已写入本项目模型路由：{config['provider']} / {config['model']}；密钥未显示。")
        return
    app = create_app(root, api_url=f"http://127.0.0.1:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
