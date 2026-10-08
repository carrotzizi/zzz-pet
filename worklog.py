#!/usr/bin/env python3
"""从 ZCode 的会话数据库生成工作日志。

    python worklog.py                    # 今天
    python worklog.py 2026-10-07         # 指定日期
    python worklog.py --days 3           # 最近三天（含今天）
    python worklog.py --out 日志.md       # 指定输出文件

数据全部来自 `~/.zcode/cli/db/db.sqlite`：`turn_usage` 记每一轮的起止和耗时，
`tool_usage` 记每次工具调用（有 started_at 索引，查某一天很快），
`message` + `part` 里是你说过的话和工具的具体参数（改了哪个文件、跑了什么命令）。

只读，不动数据库。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

DB_PATH = Path.home() / ".zcode" / "cli" / "db" / "db.sqlite"
DEFAULT_DIR = Path.home() / "Desktop"

# 工具名 -> 它参数里哪个字段是"动过的文件"
FILE_ARG_OF = {
    "Read": "file_path",
    "Write": "file_path",
    "Edit": "file_path",
    "NotebookEdit": "notebook_path",
}
# 这些工具不算"干活"，不上榜
NOISE_TOOLS = {"TodoWrite", "TodoRead", "Skill", "TaskStop", "AskUserQuestion"}
# 一次日志里每类最多列几条，免得刷屏
MAX_REQUESTS = 12
MAX_FILES = 12
MAX_COMMANDS = 8


def day_bounds(day: dt.date) -> tuple[int, int]:
    """这一天的 [起, 止) 毫秒时间戳（本地时区）。"""
    start = dt.datetime.combine(day, dt.time.min)
    return int(start.timestamp() * 1000), int((start + dt.timedelta(days=1)).timestamp() * 1000)


def fmt_duration(ms: float) -> str:
    total = max(0, round(ms / 1000))
    if total < 60:
        return f"{total} 秒"
    minutes, seconds = divmod(total, 60)
    if minutes < 60:
        return f"{minutes} 分 {seconds} 秒" if seconds else f"{minutes} 分"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} 小时 {minutes} 分" if minutes else f"{hours} 小时"


def short(text: str, limit: int = 60) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def clean_request(text: str) -> str:
    """用户消息里常常缀着一大段页面上下文（`# Web page elements: ...`）。

    那是工具注入的，不是你写的，日志里只留你真正说的那句。切完如果空了，
    说明整条都是注入内容，返回空串让调用方跳过。
    """
    for marker in ("# Web page elements:", "# Web page info:", "URL: file://", "URL: http"):
        i = text.find(marker)
        if i == 0:
            return ""
        if i > 0:
            text = text[:i]
    return text.strip()


def collect(day: dt.date) -> dict:
    """把这一天发生的事从数据库里捞出来。"""
    start, end = day_bounds(day)
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        sessions = {}
        for row in con.execute(
            "select s.id, s.title, s.directory from session s "
            "where s.id in (select distinct session_id from turn_usage"
            "               where started_at >= ? and started_at < ?)",
            (start, end),
        ):
            sessions[row["id"]] = {
                "title": row["title"] or "",
                "dir": row["directory"] or "",
                "turns": [],
                "tools": Counter(),
                "errors": 0,
                "files": Counter(),
                "commands": Counter(),
                "requests": [],
            }

        for row in con.execute(
            "select session_id, started_at, completed_at, duration_ms, status,"
            " output_tokens, tool_call_count from turn_usage"
            " where started_at >= ? and started_at < ? order by started_at",
            (start, end),
        ):
            info = sessions.get(row["session_id"])
            if info is None:
                continue
            info["turns"].append(dict(row))

        for row in con.execute(
            "select session_id, tool_name, status from tool_usage"
            " where started_at >= ? and started_at < ?",
            (start, end),
        ):
            info = sessions.get(row["session_id"])
            if info is None:
                continue
            info["tools"][row["tool_name"]] += 1
            if row["status"] == "error":
                info["errors"] += 1

        # 你说过的话 + 工具的具体参数：都要从 message/part 里取
        msg_rows = list(
            con.execute(
                "select id, session_id, time_created, data from message"
                " where time_created >= ? and time_created < ? order by time_created",
                (start, end),
            )
        )
        by_session_msgs = defaultdict(list)
        for row in msg_rows:
            by_session_msgs[row["session_id"]].append(row)

        for session_id, rows in by_session_msgs.items():
            info = sessions.get(session_id)
            if info is None:
                continue
            ids = [r["id"] for r in rows]
            parts: dict[str, list[dict]] = defaultdict(list)
            for i in range(0, len(ids), 400):  # 分批，别让 IN 太长
                chunk = ids[i : i + 400]
                marks = ",".join("?" * len(chunk))
                for p in con.execute(
                    f"select message_id, data from part where message_id in ({marks})", chunk
                ):
                    try:
                        parts[p["message_id"]].append(json.loads(p["data"]))
                    except json.JSONDecodeError:
                        continue

            for row in rows:
                try:
                    meta = json.loads(row["data"])
                except json.JSONDecodeError:
                    continue
                if meta.get("role") == "user" and not meta.get("synthetic"):
                    text = "".join(
                        p.get("text") or ""
                        for p in parts.get(row["id"], [])
                        if p.get("type") == "text"
                    ).strip()
                    text = clean_request(text)
                    if text:
                        info["requests"].append(
                            (row["time_created"], short(text, 70))
                        )
                for part in parts.get(row["id"], []):
                    if part.get("type") != "tool":
                        continue
                    tool = part.get("tool") or ""
                    args = (part.get("state") or {}).get("input") or {}
                    key = FILE_ARG_OF.get(tool)
                    if key and args.get(key):
                        info["files"][str(args[key])] += 1
                    elif tool == "Bash" and args.get("command"):
                        info["commands"][short(str(args["command"]), 60)] += 1
    finally:
        con.close()
    return sessions


def usable_title(title: str, directory: str) -> str:
    """挑一个能当标题用的名字。

    会话标题有时是自动从你的第一条消息生成的，而那条消息可能是一串 sed 表达式
    （见过 `D\\. 事件中枢/,/^#` 这种），当标题没法看。这种就退回目录名。
    """
    title = title.strip()
    if title and not any(ch in title for ch in "^$\\|{}"):
        return title
    return directory or title or "（未命名会话）"


def group_sessions(sessions: dict) -> list[dict]:
    """把"同一件事"的会话合并成一条。

    子 agent 会各开一个会话、共用同一个标题，不合并的话日志里同一件事会重复七八遍。
    标题相同就归一组；没标题的按目录归。
    """
    groups: dict[tuple[str, str], dict] = {}
    for info in sessions.values():
        if not info["turns"]:
            continue
        title = usable_title(info["title"], info["dir"])
        key = (title, info["dir"])
        group = groups.get(key)
        if group is None:
            group = {
                "title": title,
                "dir": info["dir"],
                "turns": [],
                "tools": Counter(),
                "errors": 0,
                "files": Counter(),
                "commands": Counter(),
                "requests": [],
                "sessions": 0,
            }
            groups[key] = group
        group["turns"] += info["turns"]
        group["tools"] += info["tools"]
        group["errors"] += info["errors"]
        group["files"] += info["files"]
        group["commands"] += info["commands"]
        group["requests"] += info["requests"]
        group["sessions"] += 1
    for group in groups.values():
        group["turns"].sort(key=lambda t: t["started_at"])
        group["requests"].sort()
    return sorted(groups.values(), key=lambda g: g["turns"][0]["started_at"])


def render(day: dt.date, sessions: dict) -> str:
    groups = group_sessions(sessions)
    if not groups:
        return f"# 工作日志 · {day:%Y-%m-%d}\n\n这一天没有记录。\n"

    turns = [t for g in groups for t in g["turns"]]
    total_ms = sum(t["duration_ms"] or 0 for t in turns)
    total_out = sum(t["output_tokens"] or 0 for t in turns)
    all_tools, all_files = Counter(), Counter()
    for g in groups:
        all_tools.update(g["tools"])
        all_files.update(g["files"])

    out = [f"# 工作日志 · {day:%Y-%m-%d}", ""]
    out.append(
        f"共 **{sum(g['sessions'] for g in groups)} 个会话**（{len(groups)} 件事）、"
        f"**{len(turns)} 轮**，累计 **{fmt_duration(total_ms)}**"
        f"（模型输出 {total_out:,} token）"
    )
    busy = {k: v for k, v in all_tools.items() if k not in NOISE_TOOLS}
    top = "、".join(f"{k} {v}" for k, v in Counter(busy).most_common(6))
    out.append(f"工具调用 {sum(busy.values())} 次：{top}")
    out.append("")

    for info in groups:
        first = min(t["started_at"] for t in info["turns"])
        last = max(t["completed_at"] or t["started_at"] for t in info["turns"])
        span = dt.datetime.fromtimestamp(first / 1000).strftime("%H:%M")
        tail = dt.datetime.fromtimestamp(last / 1000).strftime("%H:%M")
        spent = fmt_duration(sum(t["duration_ms"] or 0 for t in info["turns"]))
        out.append(f"## {short(info['title'], 44)}")
        line = f"`{span}–{tail}` · {len(info['turns'])} 轮 · {spent}"
        if info["dir"] and info["dir"] != info["title"]:
            line += f" · `{info['dir']}`"
        out.append(line)
        out.append("")

        if info["requests"]:
            out.append("**你要求的**")
            for ts, text in info["requests"][:MAX_REQUESTS]:
                stamp = dt.datetime.fromtimestamp(ts / 1000).strftime("%H:%M")
                out.append(f"- `{stamp}` {text}")
            if len(info["requests"]) > MAX_REQUESTS:
                out.append(f"- …还有 {len(info['requests']) - MAX_REQUESTS} 条")
            out.append("")

        if info["files"]:
            out.append("**改过 / 读过的文件**")
            for path, count in info["files"].most_common(MAX_FILES):
                out.append(f"- `{path}`（{count} 次）")
            if len(info["files"]) > MAX_FILES:
                out.append(f"- …还有 {len(info['files']) - MAX_FILES} 个")
            out.append("")

        if info["commands"]:
            out.append("**跑过的命令**")
            for cmd, count in info["commands"].most_common(MAX_COMMANDS):
                times = f" ×{count}" if count > 1 else ""
                out.append(f"- `{cmd}`{times}")
            out.append("")

        tools = {k: v for k, v in info["tools"].items() if k not in NOISE_TOOLS}
        if tools:
            summary = "、".join(f"{k} {v}" for k, v in Counter(tools).most_common())
            note = f"（其中 {info['errors']} 次出错）" if info["errors"] else ""
            out.append(f"工具：{summary}{note}")
            out.append("")

    return "\n".join(out).rstrip() + "\n"


def build(day: dt.date) -> str:
    return render(day, collect(day))


def default_out(day: dt.date) -> Path:
    """默认写到桌面的 工作日志_日期.md。"""
    return DEFAULT_DIR / f"工作日志_{day:%Y-%m-%d}.md"


def main() -> int:
    ap = argparse.ArgumentParser(description="生成某一天的工作日志")
    ap.add_argument("date", nargs="?", help="YYYY-MM-DD，默认今天")
    ap.add_argument("--days", type=int, default=1, help="从该日期起连续几天（含当天）")
    ap.add_argument("--out", help="输出文件，默认写到桌面的 工作日志_日期.md")
    ap.add_argument("--no-write", action="store_true", help="只打印，不写文件")
    args = ap.parse_args()

    if not DB_PATH.is_file():
        raise SystemExit(f"找不到会话数据库：{DB_PATH}")

    if args.date:
        try:
            first = dt.date.fromisoformat(args.date)
        except ValueError:
            raise SystemExit(f"日期格式不对：{args.date}（要 YYYY-MM-DD）") from None
    else:
        first = dt.date.today()

    days = [first + dt.timedelta(days=i) for i in range(max(1, args.days))]
    if len(days) == 1:
        text = build(days[0])
        name = f"工作日志_{days[0]:%Y-%m-%d}.md"
    else:
        text = "\n\n---\n\n".join(build(d) for d in days)
        name = f"工作日志_{days[0]:%Y-%m-%d}_{days[-1]:%Y-%m-%d}.md"

    if args.no_write:
        print(text)
        return 0

    out = Path(args.out) if args.out else default_out(days[0])
    if len(days) > 1 and not args.out:
        out = DEFAULT_DIR / f"工作日志_{days[0]:%Y-%m-%d}_{days[-1]:%Y-%m-%d}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
