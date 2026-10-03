"""合成事件自检：验证拖拽/单击/缩放/气泡/速度条/换宠物逻辑，不触碰真实鼠标。"""

import shutil
import sqlite3
import sys
import tempfile
import time
import tkinter as tk
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import eous_pet as E  # noqa: E402

# 自检会把设置写到 CONFIG_PATH，绝不能让它碰真实的 ~/.eous-pet/config.json
# ——否则跑一次自检就把用户的「小」改成「大」了（踩过这个坑）。
SANDBOX = Path(tempfile.gettempdir()) / "eous_selfcheck_cfg"
shutil.rmtree(SANDBOX, ignore_errors=True)
SANDBOX.mkdir(parents=True, exist_ok=True)
E.CONFIG_DIR = SANDBOX
E.CONFIG_PATH = SANDBOX / "config.json"


class Ev:
    def __init__(self, x=0, y=0):
        self.x_root, self.y_root = x, y


pets = E.discover_pets()
print("发现的宠物:", {n: p.parent.name for n, p in pets.items()})
pet_name, sheet = E.resolve_sheet("eous", None, None, pets)
print("选中的宠物:", pet_name, "->", sheet.name)
root = tk.Tk()
root.withdraw()
pet = E.Pet(root, sheet, {}, pets=pets, pet_name=pet_name)
root.update()
print("frames/state:", {k: len(v) for k, v in pet._photos.items()})
print("window:", pet.win_w, "x", pet.win_h, "bubble_h:", pet.bubble_h, "stats_h:", pet.stats_h)

# 拖拽
pet._on_press(Ev(500, 500))
pet._on_motion(Ev(560, 530))
moved = root.geometry()
dragging_state = pet.state
pet._on_release(Ev(560, 530))
print(f"drag: geometry={moved} dragging_state={dragging_state} after_release={pet.state}")

# 多显示器边界：宠物得能待在副屏上，还不能被丢进显示器之间的空隙
mons = E.list_monitors()
print(f"检测到 {len(mons)} 块显示器: {mons}")
assert mons, "至少该识别出主屏"
primary_w, primary_h = root.winfo_screenwidth(), root.winfo_screenheight()
print(f"Tk 看到的主屏 {primary_w}x{primary_h}")

saved_screens = pet.screens
# 构造一块带空隙的布局：主屏 0~1440，副屏 2400~3840，中间 960px 是空隙
gap = 960
pet.screens = [
    (0, 0, primary_w, primary_h),
    (primary_w + gap, 0, primary_w + 2400, primary_h),
]

x, _ = pet._clamp(primary_w + gap + 100, 100)  # 落在副屏里的位置要原样保留
assert x == primary_w + gap + 100, f"副屏内的位置被改动了: {x}"
inside_primary = primary_w - pet.win_w - 100  # 留出窗口宽度，别贴到屏幕外的边上
x, _ = pet._clamp(inside_primary, 100)  # 主屏内的位置也要原样保留
assert x == inside_primary, f"主屏内的位置被改动了: {x}"
x, _ = pet._clamp(primary_w + 900, 100)  # 空隙中间：离副屏更近，应被吸过去
assert x == primary_w + gap, f"空隙里的位置没被修正: {x}"
print("带空隙的双屏布局：主屏/副屏/空隙三种情况都正确")

# 不管从哪儿拖，结果都必须完整落在某一块显示器上
for probe in [(0, 0), (10**6, 10**6), (-10**6, -10**6), (primary_w + 500, 500),
              (primary_w + gap + 50, primary_h + 50)]:
    x, y = pet._clamp(*probe)
    assert any(l <= x <= r - pet.win_w and t <= y <= b - pet.win_h
               for l, t, r, b in pet.screens), f"clamp{probe} 的结果不在任何显示器上: {(x, y)}"
print("任意位置拖拽后都完整落在某块显示器上")

# 副屏挂在主屏左边（负坐标）也要对
pet.screens = [(-1920, 0, 0, primary_h)]
assert pet._clamp(-1500, 50) == (-1500, 50), "负坐标的显示器没处理正确"
assert pet._clamp(-3000, 50)[0] == -1920, "负方向越界没被拉回"
print("副屏在主屏左边（负坐标）：正确")
pet.screens = saved_screens
print(f"本机实际显示器: {pet.screens}")

# 单击（不动）
pet._on_press(Ev(500, 500))
pet._on_release(Ev(500, 500))
print("click: state=", pet.state)

# 状态与气泡
for s in E.STATES:
    pet.set_state(s, 0)
    assert pet.state == s and pet._frame_i == 0
print("all 9 states ok")
pet.say("自检气泡")
root.update()
print("bubble created:", pet._bubble_item is not None, "photo:", pet._bubble_photo is not None)
pet._hide_bubble()
print("bubble hidden:", pet._bubble_item is None)

# 尺寸固定「小」：菜单里的三档已经去掉了，set_scale 也不该再存在
assert not hasattr(pet, "set_scale"), "尺寸应该固定，set_scale 该删掉了"
assert pet.user_scale == E.DEFAULT_SCALE, f"尺寸不是固定的「小」: {pet.user_scale}"
print(f"尺寸固定「小」 -> 渲染缩放 {pet.scale} 窗口 {pet.win_w}x{pet.win_h}")

# 速度条：真实读一次数据库
watcher = E.SpeedWatcher(E.SPEED_DB)
watcher.start()
t0 = time.time()
while watcher.latest is None and time.time() - t0 < 6:
    time.sleep(0.2)
pet.watcher = watcher
pet._stats_key = None
pet._rebuild_stats()
print("speed db exists:", E.SPEED_DB.is_file(), "error:", watcher.error)
print("latest speed:", None if not watcher.latest else {
    "model": watcher.latest["model"],
    "rate": round(watcher.latest["rate"], 2),
    "basis": watcher.latest["basis"],
})
print("stats item drawn:", pet._stats_item is not None)

# 开关速度条
pet.var_speed.set(False)
pet._toggle_speed()
root.update()
print("after hide -> stats_h:", pet.stats_h, "item:", pet._stats_item)
pet.var_speed.set(True)
pet._toggle_speed()
root.update()
print("after show -> stats_h:", pet.stats_h, "item drawn:", pet._stats_item is not None)

watcher.stop()

for ms, want in ((23_000, "23 秒"), (95_000, "1 分 35 秒"), (120_000, "2 分"),
                 (3_900_000, "1 小时 5 分"), (7_200_000, "2 小时")):
    got = E.fmt_duration(ms)
    assert got == want, f"fmt_duration({ms}) = {got!r}，期望 {want!r}"
print("fmt_duration ok")

# 气泡字号下限：不加下限时「小」档只有 11px，提醒根本看不清
saved_dpi = E.DPI_SCALE
E.DPI_SCALE = 1.5  # 本机是 150%
small = 0.5 * E.DPI_SCALE
font_px, _, _, _ = E.bubble_metrics(small)
print(f"小档（渲染缩放 {small}）气泡字号 {font_px}px，气泡区高 {E.bubble_height(small)}px")
assert font_px >= 15, f"小档字号太小了：{font_px}px"
assert E.bubble_height(small) >= 3 * (font_px + 2), "气泡区放不下三行"
for scale in (0.75, 1.5, 2.25):
    px = E.bubble_metrics(scale)[0]
    assert 15 <= px <= 40, f"缩放 {scale} 的字号不合理：{px}"
print("字号在不同缩放下都合理:", [E.bubble_metrics(s)[0] for s in (0.75, 1.5, 2.25)])
E.DPI_SCALE = saved_dpi

# 活动检测：速度条只在任务进行时显示
tmp2 = Path(tempfile.gettempdir()) / "eous_activity_test.sqlite"
tmp2.unlink(missing_ok=True)
con = sqlite3.connect(tmp2)
con.execute("create table part (id text, time_updated integer)")
con.execute(
    "create table turn_usage (session_id text, turn_id text, status text, started_at integer,"
    " completed_at integer, duration_ms integer, model_request_count integer,"
    " tool_call_count integer, tool_error_count integer, output_tokens integer)"
)
con.execute("insert into part values ('p1', ?)", (int(time.time() * 1000) - 2000,))
con.commit()
con.close()

tw2 = E.TurnWatcher(tmp2, poll_s=0.2)
tw2.start()
time.sleep(0.7)
print("刚有写入 -> 在跑:", tw2.is_active())
assert tw2.is_active() is True

# 一轮收尾：若最后写入不晚于收尾时刻，就不该再算"在跑"
now2 = int(time.time() * 1000)
con = sqlite3.connect(tmp2)
con.execute("update part set time_updated=?", (now2,))
con.execute("insert into turn_usage values ('s','t9','completed',?,?,1000,1,1,0,5)", (now2, now2))
con.commit()
con.close()
time.sleep(0.7)
print("刚收尾 -> 在跑:", tw2.is_active())
assert tw2.is_active() is False, "任务收尾后不该还挂着速度"

# 下一轮开始写东西 -> 重新算在跑
time.sleep(1.2)
con = sqlite3.connect(tmp2)
con.execute("insert into part values ('p2', ?)", (int(time.time() * 1000),))
con.commit()
con.close()
time.sleep(0.7)
print("新任务开始写入 -> 在跑:", tw2.is_active())
assert tw2.is_active() is True
tw2.stop()

# 静默超过窗口 -> 不在跑
con = sqlite3.connect(tmp2)
con.execute("delete from part")
con.execute("delete from turn_usage")
con.execute("insert into part values ('p3', ?)",
            (int(time.time() * 1000) - E.TurnWatcher.ACTIVE_GAP_MS - 5000,))
con.commit()
con.close()
tw2 = E.TurnWatcher(tmp2, poll_s=0.2)
tw2.start()
time.sleep(0.7)
print("静默超过 90 秒窗口 -> 在跑:", tw2.is_active())
assert tw2.is_active() is False
tw2.stop()
tmp2.unlink(missing_ok=True)


# 气泡里不能出现字体缺字，否则渲染成一个方框
def missing_glyphs(text: str) -> list[str]:
    from PIL import Image, ImageDraw

    font = E.load_font(28)
    ref = Image.new("L", (40, 40), 0)
    ImageDraw.Draw(ref).text((2, 2), "\uffff", font=font, fill=255)
    ref_bytes = ref.tobytes()
    bad = []
    for ch in text:
        im = Image.new("L", (40, 40), 0)
        ImageDraw.Draw(im).text((2, 2), ch, font=font, fill=255)
        if im.tobytes() == ref_bytes:  # 和"必定缺字"的参照一模一样
            bad.append(ch)
    return bad


bubble_texts = (
    E.CHATTER
    + E.FAIL_LINES
    + E.DONE_LINES
    + [p for prof in E.PET_PROFILES.values() for p in prof["chatter"]]
    + ["专注结束 √\n已完成 3 轮\n起来活动一下吧", "休息结束\n回来继续吧"]
)
for text in bubble_texts:
    bad = missing_glyphs(text)
    assert not bad, f"气泡文案里有字体缺字 {bad!r}: {text!r}"
print(f"气泡文案字形检查 ok（{len(bubble_texts)} 条）")


# 完整链路：TurnWatcher 报出一轮完成 -> 宠物冒泡 + 记日志
class FakeTurns:
    """顶替 TurnWatcher：速度条只问它"在不在跑"。"""

    def __init__(self, active=False):
        self.active = active

    def is_active(self):
        return self.active


# 速度条只在任务进行时显示
pet.turn_watcher = FakeTurns(active=False)
pet._stats_key = None
pet._rebuild_stats()
root.update()
print("空闲 -> 速度条已隐藏:", pet._stats_item is None)
assert pet._stats_item is None, "空闲时不该显示速度"

pet.turn_watcher = FakeTurns(active=True)
pet._stats_key = None
pet._rebuild_stats()
root.update()
print("任务在跑 -> 速度条已显示:", pet._stats_item is not None)
assert pet._stats_item is not None, "任务进行时应当显示速度"

pet.show_speed = False
pet._stats_key = None
pet._rebuild_stats()
root.update()
print("手动关掉显示速度 -> 无论是否在跑都隐藏:", pet._stats_item is None)
assert pet._stats_item is None
pet.show_speed = True

# 换宠物：8x9 和 8x11 两种网格都要能加载
print(f"可选宠物 {len(pets)} 只")
accents = {}
for name in pets:
    raw, cw, ch = E.load_frames(pets[name])
    frames = {k: len(v) for k, v in raw.items()}
    accent = E.dominant_color(raw["idle"])
    accents[name] = accent
    assert set(E.STATES) <= set(raw), f"{name} 缺少状态"
    extras = sorted(s for s in raw if s.startswith("extra-"))
    assert all(len(v) > 0 for v in raw.values()), f"{name} 有空状态"
    from PIL import Image as _Image
    row_count = _Image.open(pets[name]).size[1] // ch
    assert len(extras) == max(0, row_count - len(E.STATES)), \
        f"{name} 的额外动作数量和网格行数对不上（{len(extras)} vs {row_count} 行）"
    assert len(accent) == 3 and all(0 <= c <= 255 for c in accent), f"{name} 主色不合法"
    print(f"  {name:20s} 单元格 {cw}x{ch}  主色 #{accent[0]:02x}{accent[1]:02x}{accent[2]:02x}  "
          f"帧数 {frames}")
assert len(set(accents.values())) >= len(pets) - 1, f"不同宠物的主色应该有区分度: {accents}"
assert pet.accent == accents[pet.pet_name], "启动时的主色和提取结果不一致"
print(f"各宠物主色互不相同（{len(set(accents.values()))}/{len(pets)} 种）")

# 每只宠物要有自己的性格：台词、动作偏好、被戳反应
profiles = {n: E.profile_for(n) for n in pets}
bare = [n for n, p in profiles.items() if p is E.DEFAULT_PROFILE]
assert len(set(tuple(p["chatter"]) for p in profiles.values())) == len(profiles), \
    "每只宠物的台词应当各不相同"
assert len(set(tuple(p["ambient"]) for p in profiles.values())) > 1, "动作偏好应当有差异"
assert sum(1 for p in profiles.values() if p is E.DEFAULT_PROFILE) <= 1, \
    f"这些宠物还没登记性格: {bare}"
for n, p in profiles.items():
    assert p["ambient"] and p["click"], f"{n} 的动作池是空的"
    assert all(s in E.STATES for s in p["ambient"] + p["click"]), f"{n} 的动作池里有未知状态"
print(f"每只宠物独立性格 ok（{len(profiles)} 只，台词各不相同）")

# 专属动作：只有 8x11 网格的宠物才有 extra-N
pet.set_pet(next(iter(pets)))
pool = pet._ambient_pool()
print(f"  {pet.pet_name} 的动作池: {pool}")
twelve = [n for n in pets if len(E.load_frames(pets[n])[0]) > len(E.STATES)]
if twelve:
    pet.set_pet(twelve[0])
    pool_extra = pet._ambient_pool()
    assert any(s.startswith("extra-") for s in pool_extra), "有额外动作的宠物没把它们放进动作池"
    print(f"  {twelve[0]} 的动作池含专属动作: {pool_extra}")
    pet.set_pet(next(iter(pets)))

if len(pets) > 1:
    other = next(n for n in pets if n != pet.pet_name)
    before = pet.accent
    pet.set_pet(other)
    root.update()
    assert pet.pet_name == other, "切换后名字没变"
    assert len(pet._photos[pet.state]) == len(pet._raw[pet.state]), "切换后帧数对不上"
    assert pet.accent == accents[other], "切换宠物后主色没跟着换"
    assert pet.accent != before, "两只宠物的主色居然一样"
    print(f"切换到 {other} 成功，idle 帧数 {len(pet._photos['idle'])}，"
          f"主色 #{pet.accent[0]:02x}{pet.accent[1]:02x}{pet.accent[2]:02x}，"
          f"窗口 {pet.win_w}x{pet.win_h}")
    pet.set_pet(pet_name)  # 换回来
    root.update()
    assert pet.pet_name == pet_name and pet.accent == before
    print(f"切回 {pet_name} 成功，主色也换回来了")

    # 不存在的名字应当被拒绝且不改变现状
    pet.set_pet("no-such-pet")
    assert pet.pet_name == pet_name, "不存在的宠物名不该切换成功"
    print("不存在的宠物名被正确拒绝")

root.destroy()
shutil.rmtree(SANDBOX, ignore_errors=True)
print("SELFCHECK OK")
