"""Follow creator round logs, switching automatically when a new round starts."""
from __future__ import annotations

import argparse
import codecs
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def latest_log() -> Path | None:
    return max((ROOT / "logs").glob("????-??-??T*.log"), default=None, key=lambda p: p.name)


def main() -> None:
    parser = argparse.ArgumentParser(description="Follow the creator's emitted analysis, progress and results.")
    parser.add_argument("--once", action="store_true", help="Print the current tail, then exit.")
    parser.add_argument("--lines", type=int, default=100, help="Initial tail line count.")
    args = parser.parse_args()
    if args.lines < 1:
        parser.error("--lines must be positive")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    print("创作者实时动态：模型输出的分析、执行进展和最终结果。")
    print("自动跟随新轮次；关闭此窗口或按 Ctrl+C 只关闭查看，不停止创作者。\n")
    current = None
    offset = 0
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    waiting = False
    try:
        while True:
            latest = latest_log()
            if latest is None:
                if not waiting:
                    print("尚无运行日志，等待第一轮启动……")
                    waiting = True
            else:
                if latest != current:
                    current = latest
                    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
                    print(f"\n—— 当前轮次：{current.stem} ——\n")
                    with current.open("rb") as log:
                        # Keep initial rendering bounded without retaining a whole long run.
                        size = log.seek(0, 2)
                        start = max(0, size - 1024 * 1024)
                        log.seek(start)
                        data = log.read()
                        offset = log.tell()
                    if start:
                        data = data.partition(b"\n")[2]
                    initial = decoder.decode(data)
                    print("".join(initial.splitlines(keepends=True)[-args.lines:]), end="", flush=True)
                else:
                    with current.open("rb") as log:
                        if log.seek(0, 2) < offset:
                            offset = 0
                            decoder.reset()
                        log.seek(offset)
                        data = log.read(65536)
                        offset = log.tell()
                    if data:
                        print(decoder.decode(data), end="", flush=True)
                if (ROOT / "PAUSED").exists():
                    if not waiting:
                        print("\n[状态] 创作者当前已暂停。查看窗口会继续等待新日志。")
                        waiting = True
                else:
                    waiting = False
            if args.once:
                break
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n已关闭查看；创作者运行状态不变。")


if __name__ == "__main__":
    main()
