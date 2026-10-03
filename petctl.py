#!/usr/bin/env python3
"""给正在运行的 Eous 桌面宠物发指令（走本地 HTTP 桥，协议和 Petdex 一致）。

    python petctl.py state running
    python petctl.py state failed --duration 4000
    python petctl.py say "正在读文件"
    python petctl.py states
    python petctl.py health
    python petctl.py quit
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

CONFIG_PATH = Path.home() / ".eous-pet" / "config.json"
STATES = [
    "idle",
    "running",
    "running-left",
    "running-right",
    "waving",
    "jumping",
    "failed",
    "review",
    "waiting",
]


def find_port(explicit: int | None) -> int:
    if explicit:
        return explicit
    try:
        saved = json.loads(CONFIG_PATH.read_text(encoding="utf-8")).get("port")
        if saved:
            return int(saved)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        pass
    return 7777


def post(path: str, payload: dict, port: int) -> dict:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=3) as resp:
        return json.loads(resp.read().decode("utf-8") or "{}")


def main() -> int:
    ap = argparse.ArgumentParser(description="控制 Eous 桌面宠物")
    ap.add_argument("--port", type=int, help="覆盖自动探测到的端口")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_state = sub.add_parser("state", help="切换动画状态")
    p_state.add_argument("state", choices=STATES)
    p_state.add_argument("--duration", type=float, default=0, help="保持毫秒数，0 表示一直保持")

    p_say = sub.add_parser("say", help="显示对话气泡")
    p_say.add_argument("text")
    p_say.add_argument("--ttl", type=float, default=4.0, help="气泡停留秒数")

    sub.add_parser("states", help="列出可用状态")
    sub.add_parser("health", help="检查宠物是否在运行")
    sub.add_parser("quit", help="让宠物退出")

    args = ap.parse_args()

    if args.cmd == "states":
        print("\n".join(STATES))
        return 0

    port = find_port(args.port)
    try:
        if args.cmd == "state":
            print(post("/state", {"state": args.state, "duration": args.duration}, port))
        elif args.cmd == "say":
            print(post("/bubble", {"text": args.text, "ttl": args.ttl}, port))
        elif args.cmd == "health":
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=3) as resp:
                print(resp.read().decode("utf-8"))
        elif args.cmd == "quit":
            print(post("/quit", {}, port))
    except urllib.error.URLError as exc:
        print(f"连不上 127.0.0.1:{port}（{exc.reason}）。宠物在运行吗？", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
