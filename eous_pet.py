#!/usr/bin/env python3
"""Eous —— 一个漂浮在桌面上的像素宠物（Petdex / Codex 风格）。

素材：Petdex 的精灵图，8x9（1536x1872）或 8x11（1536x2288）网格，
单元格 192x208；前九行依次是 idle / running / running-left / running-right /
waving / jumping / failed / review / waiting。

装了好几只用 petdex 装的宠物时，右键菜单可以随时换一只。

运行时会在 127.0.0.1:7777 上开一个本地 HTTP 服务，协议与 Petdex 桌面端一致：
    POST /state   {"state": "running", "duration": 3000}
    POST /bubble  {"text": "正在读文件", "busy": true}
    GET  /health
所以任何 petdex 风格的 agent 钩子都能直接驱动这只宠物。
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import queue
import random
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import tkinter as tk
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ctypes import wintypes

from PIL import Image, ImageDraw, ImageFont, ImageTk

APP_NAME = "eous-pet"
HOME = Path.home()
CONFIG_DIR = HOME / ".eous-pet"
CONFIG_PATH = CONFIG_DIR / "config.json"

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
ROW_OF = {name: row for row, name in enumerate(STATES)}

# 每帧停留毫秒数：跑动快、发呆慢。
FRAME_MS = {
    "idle": 170,
    "running": 95,
    "running-left": 100,
    "running-right": 100,
    "waving": 130,
    "jumping": 110,
    "failed": 220,
    "review": 190,
    "waiting": 150,
}


CHATTER = [
    "在的！",
    "摸鱼中…",
    "需要帮忙吗？",
    "代码写完了吗？",
    "记得喝水～",
    "Eous 待命中",
    "今天也要加油！",
    "要不要休息一下？",
]
FAIL_LINES = ["出错了…", "这个我搞不定 >_<"]
DONE_LINES = ["搞定！", "完成啦！"]

AMBIENT_STATES = ["waving", "review", "jumping", "running"]

# 每只宠物有自己的性格：聊什么、爱做什么动作、被戳了怎么反应。
# 列表里同一个状态写多次就是提高权重。没登记的宠物用 DEFAULT_PROFILE。
DEFAULT_PROFILE: dict[str, list[str]] = {
    "chatter": CHATTER,
    "ambient": AMBIENT_STATES,
    "click": ["waving", "jumping", "review"],
}

PET_PROFILES: dict[str, dict[str, list[str]]] = {
    "eous": {  # 活泼的小助手
        "chatter": ["在的！", "代码写完了吗？", "记得喝水～", "Eous 待命中", "今天也要加油！"],
        "ambient": ["running", "running", "review", "waving", "jumping"],
        "click": ["jumping", "running", "waving"],
    },
    "fairy": {  # 冷静的计算型
        "chatter": ["我很聪明的！", "在算了在算了", "数据不会骗人", "别担心，交给我"],
        "ambient": ["review", "review", "waiting", "waving", "jumping"],
        "click": ["review", "waving", "jumping"],
    },
    "hoshimi-miyabi": {  # 寡言、专注
        "chatter": ["……", "有事请讲", "刀已备好", "专注一点", "别分心"],
        "ambient": ["review", "review", "review", "waiting", "waving"],
        "click": ["review", "waving", "jumping"],
    },
    "remielle-2": {  # 软绵绵的
        "chatter": ["困了…", "陪我一会儿", "发呆中…", "要不要休息？"],
        "ambient": ["waiting", "waiting", "review", "waving", "jumping"],
        "click": ["waving", "review", "jumping"],
    },
    "ye-shunguang-jk": {  # 轻快
        "chatter": ["嘿～", "今天天气不错", "走神了一下", "喝杯茶吗？"],
        "ambient": ["waving", "waving", "review", "running", "jumping"],
        "click": ["waving", "jumping", "review"],
    },
    "yixuan-qpet": {  # 元气
        "chatter": ["冲鸭！", "今天也要开心", "加油加油！", "我在这儿呢"],
        "ambient": ["jumping", "running", "running", "waving", "review"],
        "click": ["jumping", "waving", "running"],
    },
}


def profile_for(name: str) -> dict[str, list[str]]:
    return PET_PROFILES.get(name, DEFAULT_PROFILE)

COLS = 8
GRID_ROWS = (9, 11)  # v1 是 8x9，v2（ChatGPT 导出）是 8x11，前 9 行状态相同
KEY = "#ff00fe"  # 抠图色，必须和素材里任何颜色都不重合
KEY_RGB = (255, 0, 254)

DEFAULT_PORT = 7777
PORT_TRIES = 12
DEFAULT_SCALE = 0.5  # 默认「小」

# 模型调用速度的数据源：ZCode 的会话数据库。
SPEED_DB = HOME / ".zcode" / "cli" / "db" / "db.sqlite"

# 右键菜单的观感（Tk 用系统原生菜单，能调的只有字体和配色）
MENU_BG = "#ffffff"
MENU_FG = "#24262b"
MENU_DISABLED = "#b4b6bc"
MENU_FONT = ("Microsoft YaHei UI", 10)

SPEED_POLL_S = 1.0
SPEED_HISTORY = 8  # 保留最近几次调用，用来算平均速度
ACCENT = (255, 138, 32)



def fmt_duration(ms: float) -> str:
    """把毫秒说成人话：23 秒 / 3 分 12 秒 / 1 小时 5 分。"""
    total = max(0, round(ms / 1000))
    if total < 60:
        return f"{total} 秒"
    minutes, seconds = divmod(total, 60)
    if minutes < 60:
        return f"{minutes} 分 {seconds} 秒" if seconds else f"{minutes} 分"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} 小时 {minutes} 分" if minutes else f"{hours} 小时"


def enable_dpi_awareness() -> float:
    """让 Tk 直接用物理像素，返回 DPI 缩放系数（96dpi 记为 1.0）。

    不声明 DPI 感知的话，Windows 会把窗口位图拉伸，像素画会发糊，
    而且逻辑坐标和实际坐标对不上。
    """
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # PROCESS_SYSTEM_DPI_AWARE
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            return 1.0
    try:
        return max(1.0, ctypes.windll.user32.GetDpiForSystem() / 96.0)
    except (AttributeError, OSError):
        return 1.0


DPI_SCALE = 1.0


def _declare_win32() -> None:
    """给用到的 Win32 函数声明参数类型。

    64 位下这步不能省：HANDLE 是 64 位指针，不声明的话 ctypes 按 32 位 int 传，
    `HWND_TOPMOST` 这个 -1 会变成 0x00000000FFFFFFFF —— 不是合法窗口句柄，
    于是 SetWindowPos 整个调用失败：返回 0、窗口既不动也不置顶，还不抛异常。
    （这个坑真踩过：浮窗怎么都摆不正，查了半天才发现是参数类型没声明。）
    """
    try:
        user32 = ctypes.windll.user32
        user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                        ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                        ctypes.c_uint]
        user32.SetWindowPos.restype = wintypes.BOOL
        user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.c_void_p]
        user32.GetWindowRect.restype = wintypes.BOOL
        user32.IsWindow.argtypes = [wintypes.HWND]
        user32.IsWindow.restype = wintypes.BOOL
        user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.GetWindowLongW.restype = ctypes.c_long
        user32.PostMessageW.argtypes = [wintypes.HWND, ctypes.c_uint,
                                        ctypes.c_void_p, ctypes.c_void_p]
        user32.PostMessageW.restype = wintypes.BOOL
    except (AttributeError, OSError):
        pass  # 非 Windows 就算了，用到这些函数的地方本来也只有 Windows 才走


_declare_win32()


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


# --------------------------------------------------------------------------- #
# 抖音浮窗（双击宠物打开）
# --------------------------------------------------------------------------- #
DOUYIN_URL = "https://www.douyin.com/"
DOUYIN_SIZE = (460, 820)  # 竖屏比例，接近手机
DOUYIN_TITLE_HINTS = ("douyin.com", "抖音")


def find_browser() -> str | None:
    """找一个 Chromium 内核浏览器——要用它的 app 模式开无边框窗口。"""
    for name in ("msedge", "chrome", "brave"):
        found = shutil.which(name)
        if found:
            return found
    for path in (
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ):
        if Path(path).is_file():
            return path
    return None


def chromium_windows() -> dict[int, tuple[int, int, int, int, str]]:
    """当前所有 Chromium 顶层窗口：hwnd -> (左, 上, 宽, 高, 标题)。"""
    out: dict[int, tuple[int, int, int, int, str]] = {}
    user32 = ctypes.windll.user32
    callback = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def visit(hwnd, _param):
        name = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, name, 64)
        if name.value == "Chrome_WidgetWin_1" and user32.IsWindowVisible(hwnd):
            rect = _RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            title = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, title, 256)
            out[hwnd] = (rect.left, rect.top, rect.right - rect.left,
                         rect.bottom - rect.top, title.value)
        return True

    user32.EnumWindows(callback(visit), 0)
    return out


def rects_overlap(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah


class DouyinWindow:
    """双击宠物打开的抖音浮窗。

    不想内嵌浏览器（tkinter 没有浏览器内核，装 WebView2 之类又多一个依赖），
    改用 Edge/Chrome 的 app 模式开一个无边框窗口，再用 Win32 自己设尺寸、
    位置和最前。**不能指望启动参数**——实测浏览器已经在跑的时候
    `--window-size` / `--window-position` 会被忽略（新窗口交给已有进程创建），
    所以必须自己 SetWindowPos。
    """

    SETTLE_S = 20.0  # 等窗口出现的最长时间
    WM_CLOSE = 0x0010

    def __init__(self):
        self.hwnd: int | None = None

    @property
    def open(self) -> bool:
        return bool(self.hwnd) and bool(ctypes.windll.user32.IsWindow(self.hwnd))

    def toggle(self, rect: tuple[int, int, int, int]) -> str:
        if self.open:
            self.close()
            return "抖音浮窗收起来了"
        return self._open(rect)

    def close(self) -> None:
        if self.open:
            ctypes.windll.user32.PostMessageW(self.hwnd, self.WM_CLOSE, 0, 0)
        self.hwnd = None

    def _open(self, rect: tuple[int, int, int, int]) -> str:
        exe = find_browser()
        if not exe:
            return "没找到 Edge 或 Chrome，开不了浮窗"
        before = chromium_windows()
        try:
            subprocess.Popen(
                [exe, f"--app={DOUYIN_URL}", "--no-first-run", "--no-default-browser-check"],
                close_fds=True,
            )
        except OSError as exc:
            return f"启动浏览器失败：{exc}"

        hwnd = self._wait_for_new(before)
        if not hwnd:
            return "浮窗没等到，浏览器起来了吗？"
        self.hwnd = hwnd
        return self._place(hwnd, rect)

    def _place(self, hwnd: int, rect: tuple[int, int, int, int]) -> str:
        """把窗口摆到指定位置并钉在最前。

        刚探测到窗口时它还在初始化——Edge 随后会拿自己记忆的尺寸再覆盖一次，
        所以必须"设一次、验一次、不对再设"，只调一次会被吃掉。
        """
        user32 = ctypes.windll.user32
        x, y, w, h = rect
        HWND_TOPMOST, SWP_NOACTIVATE, WS_EX_TOPMOST = -1, 0x0010, 0x0008
        deadline = time.monotonic() + 4.0
        while time.monotonic() < deadline:
            user32.SetWindowPos(hwnd, HWND_TOPMOST, x, y, w, h, SWP_NOACTIVATE)
            time.sleep(0.25)
            got = _RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(got))
            style = user32.GetWindowLongW(hwnd, -20)  # GWL_EXSTYLE
            if (got.left, got.top, got.right - got.left, got.bottom - got.top) == (x, y, w, h) \
                    and style & WS_EX_TOPMOST:
                return "抖音浮窗来了（再双击宠物收起来）"
        return "浮窗开了，但没摆正位置"

    def _wait_for_new(self, before: dict) -> int | None:
        """等那个新窗口出现。

        只认"启动前不存在"的窗口，这样绝不会误伤用户自己开着的浏览器。
        标题像抖音的直接认；标题认不出来时，如果这段时间只多出一个窗口，
        那也只能是它。
        """
        deadline = time.monotonic() + self.SETTLE_S
        seen: set[int] = set()
        while time.monotonic() < deadline:
            time.sleep(0.3)
            new = {h: info for h, info in chromium_windows().items() if h not in before}
            seen |= set(new)
            for hwnd, info in new.items():
                title = info[4].lower()
                if any(hint in title for hint in DOUYIN_TITLE_HINTS):
                    return hwnd
        return seen.pop() if len(seen) == 1 else None


def list_monitors() -> list[tuple[int, int, int, int]]:
    """所有显示器的 (左, 上, 右, 下)；拿不到就返回空表。

    Tk 的 winfo_screenwidth/height 只给**主屏**尺寸，用它做边界判断会把宠物
    锁在主屏里：多显示器时拖到副屏会被弹回来。所以这里直接问 Win32 要每块屏
    的范围，夹边界时逐块判断——注意不能退而求其次用"虚拟桌面包围盒"，
    因为显示器之间可能有空隙（本机主屏 0~2160、副屏 3240~6120，中间 1080px
    谁都不属于），夹进包围盒的宠物会停在空隙里，topmost 却没人显示它。
    """
    found: list[tuple[int, int, int, int]] = []
    try:
        user32 = ctypes.windll.user32
        callback = ctypes.WINFUNCTYPE(
            ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p,
            ctypes.POINTER(_RECT), ctypes.c_void_p,
        )

        def visit(_hmon, _hdc, rect_ptr, _data):
            r = rect_ptr.contents
            found.append((r.left, r.top, r.right, r.bottom))
            return 1

        user32.EnumDisplayMonitors(None, None, callback(visit), 0)
    except (AttributeError, OSError, ValueError):
        return []
    return [r for r in found if r[2] > r[0] and r[3] > r[1]]


# --------------------------------------------------------------------------- #
# 素材
# --------------------------------------------------------------------------- #
def find_sheet(pet: str, sheet: str | None, pet_dir: str | None) -> Path:
    """按优先级定位精灵图：显式路径 > 显式目录 > petdex 安装目录 > 本地 assets。"""
def discover_pets() -> dict[str, Path]:
    """扫描 petdex 和 Codex 的宠物目录，返回 {名字: 精灵图路径}。

    两个目录都有同一只时以 petdex 为准；没有精灵图的目录跳过。
    """
    found: dict[str, Path] = {}
    for base in (HOME / ".petdex" / "pets", HOME / ".codex" / "pets"):
        if not base.is_dir():
            continue
        for pet_dir in sorted(base.iterdir()):
            if not pet_dir.is_dir() or pet_dir.name in found:
                continue
            for fname in ("spritesheet.webp", "spritesheet.png"):
                if (pet_dir / fname).is_file():
                    found[pet_dir.name] = pet_dir / fname
                    break
    return found


def resolve_sheet(pet: str | None, sheet: str | None, pet_dir: str | None,
                  pets: dict[str, Path]) -> tuple[str, Path]:
    """定下用哪只宠物，返回 (名字, 精灵图路径)。"""
    if sheet:
        path = Path(sheet)
        if not path.is_file():
            raise SystemExit(f"找不到精灵图：{path}")
        return path.parent.name or "custom", path
    if pet_dir:
        for fname in ("spritesheet.webp", "spritesheet.png"):
            if (Path(pet_dir) / fname).is_file():
                return Path(pet_dir).name, Path(pet_dir) / fname
        raise SystemExit(f"{pet_dir} 里没有精灵图")
    if pet and pet in pets:
        return pet, pets[pet]
    if pets:
        first = next(iter(pets))
        if pet:
            print(f"[{APP_NAME}] 没有装 {pet!r}，改用 {first!r}", file=sys.stderr)
        return first, pets[first]
    raise SystemExit("一只宠物都没有。先装一只：npx -y petdex install eous")


def load_frames(sheet_path: Path) -> tuple[dict[str, list[Image.Image]], int, int]:
    """把精灵图切成帧，自动探测每行真实帧数（尾部空格子不算）。

    两种网格都认：8x9（v1）和 8x11（v2，ChatGPT 导出就是这种）。
    v2 的前 9 行和 v1 是同一套状态、同样的顺序；多出来的两行是额外动作，
    这里登记成 extra-1 / extra-2——它们进不了状态菜单（没有名字），
    但会加进这只宠物的随机动作池，于是不同宠物能做的动作就不一样了。
    """
    sheet = Image.open(sheet_path).convert("RGBA")
    width, height = sheet.size
    if width % COLS:
        raise SystemExit(f"精灵图宽度 {width} 不是 {COLS} 列的整数倍")
    rows = next((r for r in GRID_ROWS if height % r == 0), None)
    if rows is None:
        raise SystemExit(f"精灵图高度 {height} 对不上 {' 或 '.join(map(str, GRID_ROWS))} 行")
    cw, ch = width // COLS, height // rows

    frames: dict[str, list[Image.Image]] = {}
    for row in range(rows):
        name = STATES[row] if row < len(STATES) else f"extra-{row - len(STATES) + 1}"
        row_frames = []
        for col in range(COLS):
            cell = sheet.crop((col * cw, row * ch, (col + 1) * cw, (row + 1) * ch))
            alpha = cell.getchannel("A")
            opaque = sum(alpha.histogram()[8:])
            if opaque < 50:  # 空帧，后面不会有内容了
                break
            row_frames.append(cell)
        if row_frames:
            frames[name] = row_frames
        elif name in ROW_OF:
            raise SystemExit(f"精灵图第 {row} 行（{name}）是空的")
    return frames, cw, ch


def _hard_alpha(img: Image.Image) -> Image.Image:
    """把 alpha 二值化，避免抠图色在边缘混出粉色描边。"""
    alpha = img.getchannel("A").point(lambda v: 255 if v >= 128 else 0)
    return alpha


def composite_on_key(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    """RGBA -> RGB：保留原色（不透明处），其余填抠图色。"""
    out = Image.new("RGB", size, KEY_RGB)
    out.paste(img.convert("RGB"), (0, 0), _hard_alpha(img))
    return out


def scale_frames(frames: dict[str, list[Image.Image]], scale: float) -> dict[str, list[Image.Image]]:
    if scale == 1.0:
        return frames
    out = {}
    for name, items in frames.items():
        resized = []
        for img in items:
            w, h = img.size
            size = (max(1, round(w * scale)), max(1, round(h * scale)))
            # 缩小用 BOX（干净的整数平均），放大用 LANCZOS
            resample = Image.BOX if scale < 1 else Image.LANCZOS
            resized.append(img.resize(size, resample))
        out[name] = resized
    return out


def load_font(px: int) -> ImageFont.FreeTypeFont:
    for path in (
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\msyhl.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
        r"C:\Windows\Fonts\simsun.ttc",
    ):
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, px)
            except OSError:
                continue
    return ImageFont.load_default()


def wrap_text(text: str, font: ImageFont.FreeTypeFont, max_w: int, draw: ImageDraw.ImageDraw) -> list[str]:
    """按字符宽度折行，兼容中文（没有空格可断）。"""
    lines, cur = [], ""
    for ch in text:
        if ch == "\n":
            lines.append(cur)
            cur = ""
            continue
        probe = cur + ch
        if draw.textlength(probe, font=font) > max_w and cur:
            lines.append(cur)
            cur = ch
        else:
            cur = probe
    if cur:
        lines.append(cur)
    return lines or [""]


def bubble_metrics(scale: float) -> tuple[int, int, int, int]:
    """气泡的（字号、行间距、内边距、尾巴高度），单位是物理像素。

    字号有下限：不给下限的话「小」档只有 11px，完成提醒那种两三行的气泡
    根本看不清。渲染和占位共用这一份，免得两边算法不一致把文字裁掉。
    """
    return (
        max(int(round(13 * scale)), int(round(10.5 * DPI_SCALE))),
        max(2, int(round(4 * scale))),
        max(4, int(round(8 * scale))),
        max(3, int(round(6 * scale))),
    )


def bubble_height(scale: float, lines: int = 3) -> int:
    """气泡区要留多高——按最常见的三行算，再矮就放不下提醒文案了。"""
    font_px, line_gap, pad, tail = bubble_metrics(scale)
    return max(int(round(74 * scale)), lines * (font_px + line_gap) + pad * 2 + tail)


def render_bubble(text: str, box_w: int, box_h: int, scale: float) -> Image.Image:
    """在 box_w x box_h 的画布底部画一个圆角对话气泡（尾巴朝下）。"""
    ss = 3  # 超采样，让圆角更顺
    font_px, line_gap, pad, tail_h = bubble_metrics(scale)
    font = load_font(font_px * ss)
    pad *= ss
    line_gap *= ss
    tail_h *= ss
    radius = max(2, int(round(9 * scale))) * ss
    border = max(1, int(round(1.2 * scale * ss)))

    canvas = Image.new("RGBA", (box_w * ss, box_h * ss), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    max_text_w = box_w * ss - pad * 2 - 4
    lines = wrap_text(text, font, max_text_w, draw)
    line_h = font.size + line_gap
    text_w = max(draw.textlength(ln, font=font) for ln in lines)
    bubble_w = int(min(box_w * ss - 2, text_w + pad * 2))
    bubble_h = line_h * len(lines) + pad * 2

    x0 = (box_w * ss - bubble_w) // 2
    y1 = box_h * ss - tail_h
    y0 = y1 - bubble_h

    draw.rounded_rectangle(
        (x0, y0, x0 + bubble_w, y1),
        radius=radius,
        fill=(255, 255, 255, 246),
        outline=(70, 62, 58, 255),
        width=border,
    )
    # 小尾巴
    cx = box_w * ss // 2
    tw = int(5 * scale * ss)
    draw.polygon(
        [(cx - tw, y1 - 1), (cx + tw, y1 - 1), (cx, y1 + tail_h)],
        fill=(255, 255, 255, 246),
    )
    draw.line([(cx - tw, y1), (cx + tw, y1)], fill=(255, 255, 255, 246), width=border)

    for i, line in enumerate(lines):
        draw.text(
            (x0 + pad, y0 + pad + i * line_h),
            line,
            font=font,
            fill=(38, 34, 32, 255),
        )

    return canvas.resize((box_w, box_h), Image.LANCZOS)


# --------------------------------------------------------------------------- #
# 本地 HTTP 桥：兼容 petdex 的 /state 与 /bubble
# --------------------------------------------------------------------------- #
class Bridge(BaseHTTPRequestHandler):
    pet: "Pet | None" = None
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # 静音
        pass

    def _reply(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8", "replace") or "{}")
        except json.JSONDecodeError:
            return {}

    def _speed_stat(self) -> dict | None:
        watcher = self.pet.watcher if self.pet else None
        stat = watcher.latest if watcher else None
        if not stat:
            return None
        fields = ("model", "variant", "rate", "output_tokens", "reasoning_tokens",
                  "input_tokens", "gen_ms", "ttft_ms", "basis", "at")
        out = {k: stat.get(k) for k in fields}
        avg = watcher.average
        if avg:
            out["average_rate"] = round(avg, 2)
        out["rate"] = round(stat["rate"], 2)
        return out

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            payload = {"ok": True, "pet": APP_NAME, "state": self.pet.state if self.pet else None}
            if self.pet:
                payload["strip_visible"] = self.pet._strip_visible()
                payload["task_active"] = bool(
                    self.pet.turn_watcher and self.pet.turn_watcher.is_active()
                )
            stat = self._speed_stat()
            if stat:
                payload["speed"] = stat
            self._reply(200, payload)
        elif parsed.path == "/speed":
            stat = self._speed_stat()
            if stat is None:
                self._reply(503, {"error": "no_speed_data"})
            else:
                self._reply(200, stat)
        elif parsed.path == "/set":
            # 免引号的简化接口：GET /set?state=running&duration=1500
            # 给 .bat 钩子用，省掉在 cmd 里转义 JSON 的麻烦。
            query = parse_qs(parsed.query)
            state = (query.get("state") or [""])[0]
            if self.pet is None:
                self._reply(503, {"error": "no_pet"})
            elif state not in ROW_OF:
                self._reply(400, {"error": "bad_state", "allowed": STATES})
            else:
                try:
                    duration = float((query.get("duration") or ["0"])[0] or 0)
                except ValueError:
                    duration = 0.0
                self.pet.events.put(("state", state, duration))
                self._reply(200, {"ok": True, "state": state})
        else:
            self._reply(404, {"error": "not_found"})

    def do_POST(self):
        data = self._body()
        if self.pet is None:
            self._reply(503, {"error": "no_pet"})
            return
        route = self.path.split("?")[0].rstrip("/")
        if route == "/state":
            state = data.get("state")
            if state not in ROW_OF:
                self._reply(400, {"error": "bad_state", "allowed": STATES})
                return
            self.pet.events.put(("state", state, float(data.get("duration") or 0)))
            self._reply(200, {"ok": True, "state": state})
        elif route == "/bubble":
            text = str(data.get("text") or "")
            self.pet.events.put(("bubble", text, float(data.get("ttl") or 0)))
            self._reply(200, {"ok": True})
        elif route == "/quit":
            self.pet.events.put(("quit", None, 0))
            self._reply(200, {"ok": True})
        else:
            self._reply(404, {"error": "not_found"})


def start_bridge(pet: "Pet", port: int) -> int | None:
    Bridge.pet = pet
    for candidate in range(port, port + PORT_TRIES):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", candidate), Bridge)
        except OSError:
            continue
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return candidate
    return None


def find_running_instance(port: int) -> int | None:
    """已经在跑的话返回它的端口，避免重复启动出现两只宠物。

    只探测配置里记下的端口和传入的端口：这台机器上连本机的关闭端口不是
    立刻被拒绝，而是被静默丢弃直到超时，所以逐个扫 12 个端口要花掉近 5 秒。
    """
    candidates: list[int] = []
    try:
        saved = json.loads(CONFIG_PATH.read_text(encoding="utf-8")).get("port")
        if saved:
            candidates.append(int(saved))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        pass
    if port not in candidates:
        candidates.append(port)

    for candidate in candidates:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{candidate}/health", timeout=0.35) as resp:
                if json.loads(resp.read().decode("utf-8") or "{}").get("pet") == APP_NAME:
                    return candidate
        except (urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError):
            continue
    return None


# --------------------------------------------------------------------------- #
# 模型调用速度
# --------------------------------------------------------------------------- #
class SpeedWatcher(threading.Thread):
    """后台读 ZCode 的 model_usage 表，算出最近一次模型调用的生成速度。

    实测结论（这台机器上 1149 个采样点、三次独立实验）：这张表只在调用
    **结束**时落一条 completed 记录，流式过程中既没有 running 行，
    part 表的内容也是一次性写入的。所以这里给的是"每次调用结束后"的准确
    速度，而不是流式过程中的瞬时值。
    """

    def __init__(self, db_path: Path, poll_s: float = SPEED_POLL_S):
        super().__init__(daemon=True, name="speed-watcher")
        self.db_path = db_path
        self.poll_s = poll_s
        self._lock = threading.Lock()
        self._latest: dict | None = None
        self._history: list[dict] = []
        self.error: str | None = None
        self._stop = threading.Event()

    @property
    def latest(self) -> dict | None:
        with self._lock:
            return dict(self._latest) if self._latest else None

    @property
    def average(self) -> float | None:
        with self._lock:
            rates = [h["rate"] for h in self._history]
        return sum(rates) / len(rates) if rates else None

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                stat = query_latest_speed(self.db_path)
                if stat is not None:
                    with self._lock:
                        if not (self._latest and self._latest["id"] == stat["id"]):
                            self._latest = stat
                            self._history.append(stat)
                            del self._history[:-SPEED_HISTORY]
                self.error = None
            except (sqlite3.Error, OSError, ValueError) as exc:
                self.error = str(exc)
            self._stop.wait(self.poll_s)


def query_latest_speed(db_path: Path) -> dict | None:
    """读一次 model_usage，返回最近一次模型调用的速度统计；没有数据返回 None。"""
    if not db_path.is_file():
        return None
    # 只读打开，别干扰正在写入的 ZCode；按 rowid 倒序取一行，不扫全表。
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)
    try:
        row = con.execute(
            "select id, model_id, variant, status, output_tokens, reasoning_tokens,"
            " input_tokens, first_token_at, completed_at, duration_ms, started_at"
            " from model_usage order by rowid desc limit 1"
        ).fetchone()
    finally:
        con.close()
    if not row:
        return None

    (rid, model, variant, status, out_tok, reason_tok, in_tok,
     first_tok, completed, dur_ms, started) = row
    if status != "completed" or not out_tok:
        return None

    # 优先用「首 token 之后」的纯生成时间，这样不含排队和首包延迟；
    # 缺首 token 时间时退而用总耗时（会偏低，因为分母混入了 TTFT）。
    if first_tok and completed and completed > first_tok:
        gen_ms, ttft_ms, basis = completed - first_tok, first_tok - started, "first_token"
    elif dur_ms:
        gen_ms, ttft_ms, basis = dur_ms, None, "duration"
    else:
        return None
    if gen_ms <= 0:
        return None

    return {
        "id": rid,
        "model": model,
        "variant": variant,
        "rate": out_tok / (gen_ms / 1000.0),
        "output_tokens": out_tok,
        "reasoning_tokens": reason_tok or 0,
        "input_tokens": in_tok or 0,
        "gen_ms": gen_ms,
        "ttft_ms": ttft_ms,
        "basis": basis,
        "at": completed or started,
    }


class TurnWatcher(threading.Thread):
    """盯着任务在不在跑——速度条据此决定显不显示。

    没有"正在运行"的记录可读：`turn_usage` 和 `model_usage` 都只在**一轮结束时**
    才落行。所以"在跑"只能用写入活动来近似——任务进行时 ZCode 会不断写 part 表
    （工具调用、步骤边界），任务停下就断了。实测写入间隔是锯齿状的：多数几秒到
    十几秒，但纯文本生成期间出现过 **43 秒静默**，所以窗口放宽到 90 秒。
    光放宽不够，那样任务结束后速度条会赖着不走，所以再用 `turn_usage` 的收尾
    落行来兜底：一轮结束就立刻收起，不等窗口过期。
    """

    ACTIVE_GAP_MS = 90_000  # 多久没写入就算不在跑了（要盖住最长的生成静默）
    GRACE_MS = 1_000  # 忽略收尾那一瞬间自身的写入，免得刚收起又被点亮

    def __init__(self, db_path: Path, poll_s: float = SPEED_POLL_S):
        super().__init__(daemon=True, name="turn-watcher")
        self.db_path = db_path
        self.poll_s = poll_s
        self._lock = threading.Lock()
        self._last_write_ms = 0
        self._last_completed_ms = 0
        self.error: str | None = None
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def is_active(self) -> bool:
        """此刻是否有一个任务在跑。"""
        with self._lock:
            last_write, idle_since = self._last_write_ms, self._last_completed_ms
        if not last_write:
            return False
        if last_write <= idle_since + self.GRACE_MS:
            return False  # 那些写入就是上一轮收尾时写的
        return time.time() * 1000 - last_write < self.ACTIVE_GAP_MS

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                self._poll()
                self.error = None
            except Exception as exc:  # noqa: BLE001 — 后台线程不能因为一次意外异常就死掉
                self.error = f"{type(exc).__name__}: {exc}"
            self._stop.wait(self.poll_s)

    def _poll(self) -> None:
        if not self.db_path.is_file():
            return
        con = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True, timeout=2.0)
        con.row_factory = sqlite3.Row
        try:
            row = con.execute(
                "select status, completed_at from turn_usage order by rowid desc limit 1"
            ).fetchone()
            try:
                write_row = con.execute(
                    "select max(time_updated) m from"
                    " (select time_updated from part order by rowid desc limit 20)"
                ).fetchone()
            except sqlite3.Error:
                write_row = None  # 表结构对不上就只靠另一半判断，不整条腿瘸掉
        finally:
            con.close()

        write_ms = (write_row["m"] or 0) if write_row else 0
        completed = (row["completed_at"] or 0) if row else 0
        ended = bool(row) and row["status"] in ("completed", "error", "cancelled")

        with self._lock:
            if write_ms:
                self._last_write_ms = max(self._last_write_ms, write_ms)
            if ended and completed:
                # 一轮结束了（取消也算）——速度条据此立刻收起
                self._last_completed_ms = max(self._last_completed_ms, completed)


def _vivid(rgb: tuple[float, float, float]) -> tuple[int, int, int]:
    """把主色提到适合画在深色药丸上的鲜艳程度。"""
    r, g, b = (max(0, min(255, int(round(c)))) for c in rgb)
    h, s, v = Image.new("RGB", (1, 1), (r, g, b)).convert("HSV").getpixel((0, 0))
    s = max(s, 70)  # 0-255 空间：太灰的提上来
    v = max(v, 225)  # 太暗的提亮，免得在深色底上看不见
    return Image.new("HSV", (1, 1), (h, s, v)).convert("RGB").getpixel((0, 0))


def dominant_color(frames: list[Image.Image],
                   fallback: tuple[int, int, int] = ACCENT) -> tuple[int, int, int]:
    """从宠物帧里挑出主色，用来给速度条配色。

    不直接数"出现最多的颜色"——大面积的白毛/黑描边会把它带偏。做法是：
    丢掉太暗（描边、阴影）和太灰（高光、白毛）的像素，在剩下的鲜艳像素里
    按色相分桶找峰值，再把峰值和左右相邻的桶按「饱和度 × 亮度」加权平均
    （相邻桶也算进来是为了避免主色刚好卡在分桶边界上被切成两半），
    最后把颜色提到适合画在深色底上的鲜艳度。
    """
    buckets: dict[int, list[float]] = {}
    for img in frames[:4]:  # 取前几帧就够，省时间
        small = img.convert("RGBA").resize((48, 52), Image.BOX)
        rgb_data = small.convert("RGB").getdata()
        hsv_data = small.convert("HSV").getdata()
        for (r, g, b), (h, s, v), (_, _, _, a) in zip(rgb_data, hsv_data, small.getdata()):
            if a < 200 or v < 70 or s < 60:
                continue  # 半透明 / 太暗 / 太灰
            weight = float(s) * float(v)
            bucket = buckets.setdefault(h // 8, [0.0, 0.0, 0.0, 0.0])  # 32 个色相桶
            bucket[0] += weight
            bucket[1] += r * weight
            bucket[2] += g * weight
            bucket[3] += b * weight

    if not buckets:
        return fallback
    best = max(buckets, key=lambda k: buckets[k][0])
    total = [0.0, 0.0, 0.0, 0.0]
    for k in (best - 1, best, best + 1):
        chunk = buckets.get(k % 32)
        if chunk:
            for i in range(4):
                total[i] += chunk[i]
    if total[0] <= 0:
        return fallback
    return _vivid((total[1] / total[0], total[2] / total[0], total[3] / total[0]))


def _mix(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return tuple(int(round(x + (y - x) * t)) for x, y in zip(a, b))  # type: ignore[return-value]


def render_stats(stat: dict | None, width: int, height: int, dpi: float,
                 accent: tuple[int, int, int] = ACCENT) -> Image.Image | None:
    """把模型调用速度画成一条圆角小药丸；没有数据就返回 None（这一行留空）。"""
    if not stat or width <= 0 or height <= 0:
        return None
    rate = stat["rate"]
    text = f"{rate:.0f} tok/s" if rate >= 10 else f"{rate:.1f} tok/s"

    ss = 3  # 超采样，让圆角和文字都顺一点
    w, h = width * ss, height * ss
    margin = max(1, int(1.5 * ss))
    pill_h = h - margin * 2
    font_px = max(int(pill_h * 0.52), 1)
    font = load_font(font_px)
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    text_w = probe.textlength(text, font=font)

    dot_r = max(1, int(pill_h * 0.13))
    pad = max(2, int(pill_h * 0.42))
    gap = max(1, int(dot_r * 1.6))
    need = pad * 2 + dot_r * 2 + gap + text_w
    if need > w - margin * 2:  # 放不下就缩字号，最多试三次
        for factor in (0.85, 0.7, 0.6):
            font = load_font(max(int(font_px * factor), 1))
            text_w = probe.textlength(text, font=font)
            need = pad * 2 + dot_r * 2 + gap + text_w
            if need <= w - margin * 2:
                break

    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    pill_w = int(min(w - margin * 2, need))
    x0 = (w - pill_w) // 2
    y0 = (h - pill_h) // 2
    draw.rounded_rectangle(
        (x0, y0, x0 + pill_w, y0 + pill_h),
        radius=pill_h // 2,
        fill=(*_mix((20, 22, 28), accent, 0.16), 236),  # 底色也沾一点主色
        outline=(*accent, 255),
        width=max(1, int(1.1 * ss)),
    )
    cy = y0 + pill_h // 2
    draw.ellipse((x0 + pad, cy - dot_r, x0 + pad + dot_r * 2, cy + dot_r), fill=(*accent, 255))

    bbox = draw.textbbox((0, 0), text, font=font)
    tx = x0 + pad + dot_r * 2 + gap
    ty = cy - (bbox[3] - bbox[1]) / 2 - bbox[1]
    draw.text((tx, ty), text, font=font, fill=(255, 255, 255, 255))

    return img.resize((width, height), Image.LANCZOS)


# --------------------------------------------------------------------------- #
# 宠物本体
# --------------------------------------------------------------------------- #
class Pet:
    def __init__(self, root: tk.Tk, sheet_path: Path, cfg: dict,
                 watcher: SpeedWatcher | None = None,
                 turn_watcher: TurnWatcher | None = None,
                 pets: dict[str, Path] | None = None,
                 pet_name: str = "eous"):
        self.root = root
        self.events: queue.Queue = queue.Queue()
        self.watcher = watcher
        self.turn_watcher = turn_watcher
        self.pets = pets or {}
        self.pet_name = pet_name
        self.profile = profile_for(pet_name)
        self.user_scale = float(cfg.get("scale", DEFAULT_SCALE))
        self.scale = self.user_scale * DPI_SCALE
        self.topmost = bool(cfg.get("topmost", True))
        self.show_bubbles = bool(cfg.get("bubbles", True))
        self.show_speed = bool(cfg.get("speed", True))
        self.douyin = DouyinWindow()
        self.port = cfg.get("port")

        self._raw, self.cell_w, self.cell_h = load_frames(sheet_path)
        self._scaled = scale_frames(self._raw, self.scale)
        self._photos: dict[str, list[ImageTk.PhotoImage]] = {}
        self.accent = dominant_color(self._raw["idle"])

        self.state = cfg.get("state", "idle")
        if self.state not in ROW_OF:
            self.state = "idle"
        self._frame_i = 0
        self._frame_t0 = time.monotonic()
        self._state_until: float | None = None
        self._bubble_until: float | None = None
        self._next_ambient = time.monotonic() + random.uniform(20, 55)
        self._drag: dict | None = None
        self._bubble_photo: ImageTk.PhotoImage | None = None
        self._stats_photo: ImageTk.PhotoImage | None = None
        self._stats_item = None
        self._stats_key = None
        self._save_job = None

        self.screens = list_monitors() or [
            (0, 0, root.winfo_screenwidth(), root.winfo_screenheight())
        ]

        self._build_window()
        self._build_canvas()
        self._build_menu()
        self._apply_state_visual()

    # -- 窗口 -------------------------------------------------------------- #
    def _sprite_size(self) -> tuple[int, int]:
        w, h = self._scaled["idle"][0].size
        return w, h

    def _clamp(self, x: int, y: int) -> tuple[int, int]:
        """把窗口塞进离目标最近的显示器里，保证它一定落在某块屏上。

        逐块显示器各夹一次，取离目标最近的结果——这样往副屏方向拖会被吸进副屏，
        而不是停在两块屏之间的空隙里（那里谁也显示不了它）。
        """
        best: tuple[float, int, int] | None = None
        for left, top, right, bottom in self.screens:
            cx = max(left, min(x, right - self.win_w))
            cy = max(top, min(y, bottom - self.win_h))
            dist = float((cx - x) ** 2 + (cy - y) ** 2)
            if best is None or dist < best[0]:
                best = (dist, cx, cy)
        return (x, y) if best is None else (best[1], best[2])

    def _build_window(self) -> None:
        sw, sh = self._sprite_size()
        self.bubble_h = bubble_height(self.scale) if self.show_bubbles else 0
        # 状态条给小尺寸留一个可读的字号下限，不然「小」档会糊成一团；
        row_h = max(int(round(28 * self.scale)), int(round(17 * DPI_SCALE)))
        self.stats_h = row_h if self.show_speed else 0
        self.win_w, self.win_h = sw, self.bubble_h + sh + self.stats_h

        self.root.overrideredirect(True)
        self.root.attributes("-topmost", self.topmost)
        try:
            self.root.attributes("-transparentcolor", KEY)
        except tk.TclError:
            pass  # 非 Windows：背景会是不透明的品红
        self.root.configure(bg=KEY)

        # 默认落在主屏右下角（Tk 的 screen 尺寸就是主屏），
        # 但边界判断走虚拟桌面，所以宠物可以待在副屏上
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        x = int(cfg_get("x", screen_w - self.win_w - 60))
        y = int(cfg_get("y", screen_h - self.win_h - 90))
        x, y = self._clamp(x, y)
        self.root.geometry(f"{self.win_w}x{self.win_h}+{x}+{y}")

    def _build_canvas(self) -> None:
        self.canvas = tk.Canvas(
            self.root,
            width=self.win_w,
            height=self.win_h,
            bg=KEY,
            highlightthickness=0,
            bd=0,
        )
        self.canvas.pack(fill="both", expand=True)

        self._build_photos()
        self._bubble_item = None
        self._sprite_item = self.canvas.create_image(
            0, self.bubble_h, anchor="nw", image=self._photos[self.state][0]
        )
        self._rebuild_stats()

        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_motion)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Double-Button-1>", self._on_double_click)
        self.canvas.bind("<Button-3>", self._on_menu)

    def _build_photos(self) -> None:
        for name, items in self._scaled.items():
            self._photos[name] = [
                ImageTk.PhotoImage(composite_on_key(img, img.size)) for img in items
            ]

    # -- 右键菜单 ---------------------------------------------------------- #
    def _build_menu(self) -> None:
        self.menu = tk.Menu(self.root, tearoff=0)
        self.var_pet = tk.StringVar(value=self.pet_name)
        if len(self.pets) > 1:
            pet_menu = tk.Menu(self.menu, tearoff=0)
            for name in self.pets:
                pet_menu.add_radiobutton(
                    label=name, value=name, variable=self.var_pet,
                    command=lambda n=name: self.set_pet(n),
                )
            self.menu.add_cascade(label="宠物", menu=pet_menu)
            self.menu.add_separator()
        state_menu = tk.Menu(self.menu, tearoff=0)
        for name in STATES:
            state_menu.add_command(label=name, command=lambda n=name: self.set_state(n, 0))
        self.menu.add_cascade(label="状态", menu=state_menu)
        self.menu.add_command(label="随机动作", command=self.random_action)
        self.menu.add_separator()

        self.var_top = tk.BooleanVar(value=self.topmost)
        self.var_bub = tk.BooleanVar(value=self.show_bubbles)
        self.var_speed = tk.BooleanVar(value=self.show_speed)
        self.menu.add_checkbutton(label="总在最前", variable=self.var_top, command=self._toggle_top)
        self.menu.add_checkbutton(label="显示气泡", variable=self.var_bub, command=self._toggle_bubbles)
        self.menu.add_checkbutton(label="显示速度", variable=self.var_speed, command=self._toggle_speed)
        self.menu.add_command(label="抖音浮窗", command=self.toggle_douyin)
        self.menu.add_separator()
        self.menu.add_command(label="退出", command=self.quit)
        self._style_menu(self.menu)

    def _all_menus(self, menu: tk.Menu) -> list[tk.Menu]:
        """把菜单和它的级联子菜单一起收集出来。"""
        out = [menu]
        end = menu.index("end")
        if end is not None:
            for i in range(end + 1):
                try:
                    if menu.type(i) == "cascade":
                        out += self._all_menus(menu.nametowidget(menu.entrycget(i, "menu")))
                except tk.TclError:
                    continue  # 这一项不是菜单（分隔线之类）
        return out

    def _style_menu(self, menu: tk.Menu) -> None:
        """给菜单上色：浅色底、宠物主色做选中高亮。

        Tk 用的是系统原生菜单，圆角、图标这类做不了；字体、配色、间距倒是都能改。
        主色取自当前宠物，所以换宠物时菜单会跟着换色（set_pet 里会再调一次）。
        """
        accent = self.accent
        hexed = "#%02x%02x%02x" % accent
        # 按亮度决定高亮上的字色：深色主色（fairy 的蓝）配白字，
        # 浅色主色（remielle-2 的粉）配深字
        bright = 0.299 * accent[0] + 0.587 * accent[1] + 0.114 * accent[2]
        active_fg = "#1b1b1b" if bright > 150 else "#ffffff"
        for m in self._all_menus(menu):
            try:
                m.configure(
                    font=MENU_FONT,
                    bg=MENU_BG, fg=MENU_FG,
                    activebackground=hexed, activeforeground=active_fg,
                    selectcolor=hexed,  # 勾选/单选标记的颜色
                    disabledforeground=MENU_DISABLED,
                    activeborderwidth=0, bd=1, relief="solid",
                )
            except tk.TclError:
                pass

    def _on_menu(self, event) -> None:
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()

    def _toggle_top(self) -> None:
        self.topmost = bool(self.var_top.get())
        self.root.attributes("-topmost", self.topmost)
        self._save()

    def _toggle_bubbles(self) -> None:
        self.show_bubbles = bool(self.var_bub.get())
        if not self.show_bubbles:
            self._hide_bubble()
        self._rebuild_geometry()
        self._save()

    def _toggle_speed(self) -> None:
        self.show_speed = bool(self.var_speed.get())
        self._rebuild_geometry()
        self._save()

    # -- 布局 -------------------------------------------------------------- #
    def _rebuild_geometry(self) -> None:
        x, y = self.root.winfo_x(), self.root.winfo_y()
        self._build_window()
        # 变高之后可能顶出屏幕（宠物贴着底边时），拉回显示器内
        x, y = self._clamp(x, y)
        self.canvas.configure(width=self.win_w, height=self.win_h)
        self.root.geometry(f"{self.win_w}x{self.win_h}+{x}+{y}")
        self.canvas.coords(self._sprite_item, 0, self.bubble_h)
        self._stats_key = None  # 尺寸变了，状态条必须重画
        self._rebuild_stats()

    def set_pet(self, name: str) -> None:
        """换一只宠物：重读精灵图、重建全部帧。"""
        if name == self.pet_name or name not in self.pets:
            self.var_pet.set(self.pet_name)
            return
        try:
            raw, cw, ch = load_frames(self.pets[name])
        except (SystemExit, OSError) as exc:
            self.say(f"这只换不了：{exc}", 6)
            self.var_pet.set(self.pet_name)
            return

        self.pet_name = name
        self.profile = profile_for(name)
        self._raw, self.cell_w, self.cell_h = raw, cw, ch
        self._scaled = scale_frames(self._raw, self.scale)
        self._build_photos()
        self.accent = dominant_color(self._raw["idle"])  # 速度条和菜单都跟着新宠物换色
        self._style_menu(self.menu)
        # 换了宠物帧数和尺寸都可能变，状态归零免得索引越界
        self.state = "idle"
        self._state_until = None
        self._frame_i = 0
        self._frame_t0 = time.monotonic()
        self._hide_bubble()
        self._rebuild_geometry()
        self._apply_state_visual()
        self._save()

    # -- 交互 -------------------------------------------------------------- #
    def _on_press(self, event) -> None:
        self._drag = {
            "x": event.x_root,
            "y": event.y_root,
            "wx": self.root.winfo_x(),
            "wy": self.root.winfo_y(),
            "moved": False,
            "prev": self.state,
        }

    def _on_motion(self, event) -> None:
        d = self._drag
        if not d:
            return
        dx, dy = event.x_root - d["x"], event.y_root - d["y"]
        if not d["moved"] and abs(dx) + abs(dy) < 5:
            return
        if not d["moved"]:
            d["moved"] = True
            self.set_state("running", 0)
        nx, ny = self._clamp(d["wx"] + dx, d["wy"] + dy)
        self.root.geometry(f"+{nx}+{ny}")

    def _on_release(self, event) -> None:
        d = self._drag
        self._drag = None
        if not d:
            return
        if d["moved"]:
            self.set_state(d["prev"] if d["prev"] != "running" else "idle", 0)
            self._save()
        else:
            # 单击：蹭一下（反应也按宠物性格来）
            pool = [s for s in self.profile["click"] if s in self._photos] or ["waving"]
            self.set_state(random.choice(pool), 1600)
            if random.random() < 0.6:
                self.say(random.choice(self.profile["chatter"]), 3)

    # -- 状态 -------------------------------------------------------------- #
    def set_state(self, name: str, duration_ms: float = 0) -> None:
        # 用 _photos 判断而不是 ROW_OF：8x11 的宠物还有 extra-N 这种额外动作
        if name not in self._photos:
            return
        changed = name != self.state
        self.state = name
        self._state_until = time.monotonic() + duration_ms / 1000.0 if duration_ms > 0 else None
        if changed:
            self._frame_i = 0
            self._frame_t0 = time.monotonic()
            self._apply_state_visual()

    def _apply_state_visual(self) -> None:
        frames = self._photos.get(self.state) or self._photos["idle"]
        self._frame_i %= len(frames)
        self.canvas.itemconfig(self._sprite_item, image=frames[self._frame_i])

    def say(self, text: str, ttl_s: float = 4.0) -> None:
        if not self.show_bubbles or not text:
            return
        img = render_bubble(text, self.win_w, self.bubble_h, self.scale)
        self._bubble_photo = ImageTk.PhotoImage(composite_on_key(img, img.size))
        if self._bubble_item is None:
            self._bubble_item = self.canvas.create_image(0, 0, anchor="nw", image=self._bubble_photo)
        else:
            self.canvas.itemconfig(self._bubble_item, image=self._bubble_photo)
        self._bubble_until = time.monotonic() + ttl_s if ttl_s > 0 else None

    def _hide_bubble(self) -> None:
        if self._bubble_item is not None:
            self.canvas.delete(self._bubble_item)
            self._bubble_item = None
        self._bubble_photo = None
        self._bubble_until = None

    # -- 速度状态条 -------------------------------------------------------- #
    def _paint(self, item, img: Image.Image | None, y: int):
        """把一条药丸画到画布上；img 为 None 就把这一行清掉。

        返回 (item, photo)。photo 必须留着引用，否则 Tk 会把它回收掉变成空白。
        """
        if img is None:
            if item is not None:
                self.canvas.delete(item)
            return None, None
        photo = ImageTk.PhotoImage(composite_on_key(img, img.size))
        if item is None:
            item = self.canvas.create_image(0, y, anchor="nw", image=photo)
        else:
            self.canvas.coords(item, 0, y)
            self.canvas.itemconfig(item, image=photo)
        return item, photo

    def _strip_visible(self) -> bool:
        """速度条该不该出现：只在任务进行时显示，免得挂着一个几分钟前的旧数字晃眼。

        没有活动检测可用时退回"一直显示"。
        """
        if not (self.show_speed and self.watcher):
            return False
        if self.turn_watcher is None:
            return True
        return self.turn_watcher.is_active()

    def _rebuild_stats(self) -> None:
        """重画速度条；数值没变就直接返回，避免无谓地新建 PhotoImage。"""
        y = self.bubble_h + self._sprite_size()[1]

        if not self._strip_visible() or self.stats_h <= 0:
            if self._stats_item is not None:
                self._stats_item, self._stats_photo = self._paint(self._stats_item, None, y)
            self._stats_key = None
            return

        stat = self.watcher.latest if self.watcher else None
        key = None if stat is None else (stat["id"], round(stat["rate"], 1), self.accent)
        if key == self._stats_key:
            return
        self._stats_key = key
        img = render_stats(stat, self.win_w, self.stats_h, DPI_SCALE, self.accent)
        self._stats_item, self._stats_photo = self._paint(self._stats_item, img, y)

    def _ambient_pool(self) -> list[str]:
        """这只宠物能做的随机动作。

        按它自己的性格偏好（列表里重复写就是加权），再加上它独有的额外动作——
        extra-N 只有 8x11 网格的宠物才有（比如 remielle-2），所以不同宠物
        能做的动作天然不一样。
        """
        pool = [s for s in self.profile["ambient"] if s in self._photos]
        pool += sorted(s for s in self._photos if s.startswith("extra-"))
        return pool or ["waving"]

    def random_action(self) -> None:
        """右键「随机动作」：按这只宠物自己的偏好挑一个。"""
        self.set_state(random.choice(self._ambient_pool()), 3000)

    def _ambient(self, now: float) -> None:
        self._next_ambient = now + random.uniform(22, 60)
        if self.state != "idle" or self._drag:
            return
        self.set_state(random.choice(self._ambient_pool()), random.uniform(2.5, 4.5) * 1000)
        if random.random() < 0.45:
            self.say(random.choice(self.profile["chatter"]), 3.5)

    # -- 主循环 ------------------------------------------------------------ #
    def _drain(self, now: float) -> None:
        while True:
            try:
                kind, value, extra = self.events.get_nowait()
            except queue.Empty:
                return
            if kind == "state":
                self.set_state(value, extra)
                if value == "failed":
                    self.say(random.choice(FAIL_LINES), 3)
                elif value in ("waving", "jumping"):
                    pass
            elif kind == "bubble":
                self.say(str(value), extra or 4.0)
            elif kind == "quit":
                self.quit()

    def _tick(self) -> None:
        now = time.monotonic()
        self._drain(now)

        if self._state_until and now >= self._state_until:
            self._state_until = None
            self.set_state("idle", 0)
        if now >= self._next_ambient:
            self._ambient(now)

        frames = self._photos[self.state]
        if now - self._frame_t0 >= FRAME_MS.get(self.state, 150) / 1000.0:
            self._frame_t0 = now
            self._frame_i = (self._frame_i + 1) % len(frames)
            self.canvas.itemconfig(self._sprite_item, image=frames[self._frame_i])

        self._rebuild_stats()
        self.root.after(30, self._tick)

    # -- 配置 -------------------------------------------------------------- #
    def _save(self) -> None:
        if self._save_job:
            self.root.after_cancel(self._save_job)
        self._save_job = self.root.after(600, self._save_now)

    def _save_now(self) -> None:
        self._save_job = None
        try:
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            cfg = {}
        # 窗口还没 map 时 winfo_x/y 是 0，别把好位置覆盖掉
        if self.root.winfo_ismapped():
            cfg["x"], cfg["y"] = self.root.winfo_x(), self.root.winfo_y()
        cfg.update(
            topmost=self.topmost,
            bubbles=self.show_bubbles,
            speed=self.show_speed,
            state=self.state,
            pet=self.pet_name,
        )
        if self.port:
            cfg["port"] = self.port
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass

    def quit(self) -> None:
        self.douyin.close()  # 浮窗是我们开出来的，走的时候一起收掉
        self._save_now()
        self.root.destroy()

    # -- 抖音浮窗 ---------------------------------------------------------- #
    def _douyin_rect(self) -> tuple[int, int, int, int]:
        """浮窗放哪儿：主屏右侧贴底；要是会压住宠物就往左让开。"""
        w, h = DOUYIN_SIZE
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        x = max(0, screen_w - w - 24)
        y = max(0, screen_h - h - 96)
        pet = (self.root.winfo_x(), self.root.winfo_y(), self.win_w, self.win_h)
        if rects_overlap((x, y, w, h), pet):
            x = max(0, pet[0] - w - 16)
        return x, y, w, h

    def toggle_douyin(self) -> None:
        self.say(self.douyin.toggle(self._douyin_rect()), 4)

    def _on_double_click(self, _event) -> None:
        """双击：跳一下 + 开/收抖音浮窗。"""
        self.set_state("jumping", 1500)
        self.toggle_douyin()


def cfg_get(key, default):
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8")).get(key, default)
    except (OSError, json.JSONDecodeError):
        return default


# --------------------------------------------------------------------------- #
# 预览（不需要窗口，用于自检）
# --------------------------------------------------------------------------- #
def preview(sheet_path: Path, out: Path, state: str, text: str, scale: float,
            stat: dict | None = None) -> None:
    frames, _, _ = load_frames(sheet_path)
    accent = dominant_color(frames["idle"])
    frames = scale_frames(frames, scale)
    sprite = frames[state][0]
    w, h = sprite.size
    bubble_h = bubble_height(scale)
    stats_h = max(int(round(28 * scale)), int(round(17 * DPI_SCALE))) if stat else 0
    canvas = Image.new("RGB", (w, bubble_h + h + stats_h), (24, 26, 33))

    if text:
        bubble = render_bubble(text, w, bubble_h, scale)
        canvas.paste(bubble, (0, 0), bubble)
    canvas.paste(sprite, (0, bubble_h), sprite)
    if stats_h:
        strip = render_stats(stat, w, stats_h, DPI_SCALE, accent)
        if strip is not None:
            canvas.paste(strip, (0, bubble_h + h), strip)
    canvas.save(out)
    print(f"wrote {out}  ({state}, {w}x{bubble_h + h + stats_h}, 主色 #{accent[0]:02x}{accent[1]:02x}{accent[2]:02x})")


# --------------------------------------------------------------------------- #
def main() -> None:
    global DPI_SCALE
    ap = argparse.ArgumentParser(description="桌面宠物（Petdex 风格）")
    ap.add_argument("--pet", help="用哪只宠物（petdex 的 slug），默认沿用上次选的那只")
    ap.add_argument("--list-pets", action="store_true", help="列出已安装的宠物后退出")
    ap.add_argument("--sheet", help="直接指定精灵图路径")
    ap.add_argument("--pet-dir", help="指定宠物目录")
    ap.add_argument("--scale", type=float, help="缩放，默认读配置或 1.0")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT, help="本地 HTTP 桥端口")
    ap.add_argument("--no-bridge", action="store_true", help="不开本地 HTTP 桥")
    ap.add_argument("--db", help="ZCode 会话数据库路径（读模型调用速度用）")
    ap.add_argument("--no-speed", action="store_true", help="不读数据库、不显示速度")
    ap.add_argument("--preview", help="只渲染一张预览图到指定路径后退出")
    ap.add_argument("--preview-state", default="idle")
    ap.add_argument("--preview-text", default="你好呀！")
    args = ap.parse_args()

    pets = discover_pets()
    if args.list_pets:
        if not pets:
            print("一只都没装。试试：npx -y petdex install eous")
        for name in pets:
            print(f"  {name:24s} {pets[name]}")
        return

    cfg = {}
    try:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass

    pet_name, sheet_path = resolve_sheet(
        args.pet or cfg.get("pet"), args.sheet, args.pet_dir, pets
    )

    if args.preview:
        DPI_SCALE = enable_dpi_awareness()
        try:
            stat = query_latest_speed(Path(args.db) if args.db else SPEED_DB)
        except (sqlite3.Error, OSError, ValueError):
            stat = None
        preview(sheet_path, Path(args.preview), args.preview_state, args.preview_text,
                (args.scale or DEFAULT_SCALE) * DPI_SCALE, stat)
        return

    # 尺寸固定「小」——菜单里的三档去掉了，--scale 只留给预览和调试用
    cfg["scale"] = args.scale or DEFAULT_SCALE
    if args.no_speed:
        cfg["speed"] = False

    already = find_running_instance(args.port)
    if already:
        print(f"[{APP_NAME}] 已经在运行了（端口 {already}），不再启动第二只。", file=sys.stderr)
        return

    DPI_SCALE = enable_dpi_awareness()

    db_path = Path(args.db) if args.db else SPEED_DB

    watcher = None
    if cfg.get("speed", True):
        watcher = SpeedWatcher(db_path)
        watcher.start()

    turn_watcher = None
    if cfg.get("speed", True):
        turn_watcher = TurnWatcher(db_path)
        turn_watcher.start()

    root = tk.Tk()
    root.title(APP_NAME)
    pet = Pet(root, sheet_path, cfg, watcher=watcher, turn_watcher=turn_watcher,
              pets=pets, pet_name=pet_name)

    print(f"[{APP_NAME}] 当前宠物: {pet_name}  (共 {len(pets)} 只可选)")
    if watcher is not None:
        print(f"[{APP_NAME}] 速度状态条已开启")
    if turn_watcher is not None:
        print(f"[{APP_NAME}] 任务活动检测已开启（速度条据此显示/隐藏）")

    if not args.no_bridge:
        port = start_bridge(pet, args.port)
        if port:
            pet.port = port
            # 等窗口真正 map 之后再落盘，否则 winfo_x/y 还是 0
            root.after(400, pet._save_now)
            print(f"[{APP_NAME}] bridge on http://127.0.0.1:{port}  (sheet: {sheet_path})")
        else:
            print(f"[{APP_NAME}] 端口 {args.port}+ 都被占用，未启动 HTTP 桥", file=sys.stderr)

    root.after(30, pet._tick)
    try:
        root.mainloop()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
