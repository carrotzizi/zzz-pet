"""截取宠物窗口的内容，用于取证。

不用屏幕截图：多显示器和 150% DPI 下，PIL 的 ImageGrab 覆盖范围和窗口坐标
对不上（实测 all_screens=True 只给 4080x1440，而副屏上的窗口在 x=5916），
裁切会直接报 "Coordinate 'right' is less than 'left'"。
PrintWindow 直接向窗口要内容，跟屏幕布局无关，透明处会是黑底——
看宠物、气泡、状态条画得对不对足够了。

用法：python tools/shot.py [窗口标题] [输出路径]
"""

import ctypes
import ctypes.wintypes as w
import pathlib
import sys

from PIL import Image

u = ctypes.windll.user32
g = ctypes.windll.gdi32
u.SetProcessDPIAware()

title = sys.argv[1] if len(sys.argv) > 1 else "eous-pet"
out = pathlib.Path(sys.argv[2] if len(sys.argv) > 2 else "tools/_window.png")

hwnd = u.FindWindowW(None, title)
if not hwnd:
    raise SystemExit(f"没找到窗口 {title!r}（宠物在运行吗？）")

rect = w.RECT()
u.GetWindowRect(hwnd, ctypes.byref(rect))
cw, ch = rect.right - rect.left, rect.bottom - rect.top

hdc = u.GetWindowDC(hwnd)
mdc = g.CreateCompatibleDC(hdc)
bmp = g.CreateCompatibleBitmap(hdc, cw, ch)
g.SelectObject(mdc, bmp)
if not u.PrintWindow(hwnd, mdc, 2):
    raise SystemExit("PrintWindow 失败")


class BITMAPINFO(ctypes.Structure):
    _fields_ = [
        ("biSize", w.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
        ("biPlanes", w.WORD), ("biBitCount", w.WORD), ("biCompression", w.DWORD),
        ("biSizeImage", w.DWORD), ("biXPelsPerMeter", ctypes.c_long),
        ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", w.DWORD),
        ("biClrImportant", w.DWORD),
    ]


info = BITMAPINFO()
info.biSize = ctypes.sizeof(BITMAPINFO)
info.biWidth = cw
info.biHeight = -ch  # 负数 = 自上而下
info.biPlanes = 1
info.biBitCount = 32
info.biCompression = 0
buf = ctypes.create_string_buffer(cw * ch * 4)
g.GetDIBits(mdc, bmp, 0, ch, buf, ctypes.byref(info), 0)

out.parent.mkdir(parents=True, exist_ok=True)
Image.frombuffer("RGBA", (cw, ch), buf, "raw", "BGRA", 0, 1).convert("RGB").save(out)
print(f"{out}  {cw}x{ch}  窗口位置 ({rect.left},{rect.top})")
