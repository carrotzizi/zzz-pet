# 桌面宠物（Petdex / Codex 风格）

一个漂浮在 Windows 桌面上的像素宠物：九种动画状态、对话气泡、多显示器拖拽，
会跟着你宠物的主色给状态条配色，并读 ZCode 的会话数据库显示**模型调用速度**、
不依赖 agent 钩子。

素材用的是 [Petdex](https://petdex.dev) 的公开宠物目录，精灵图格式是
ChatGPT / Codex 桌宠通用的 8×9 或 8×11 网格，所以整个目录里装的宠物都能直接用：
装了 6 只就在右键菜单里换，各自有独立的性格、台词和专属动作。

## 运行

需要 **Windows** + **Python 3.10 以上**，依赖只有一个 Pillow：

```bash
pip install pillow
```

再装一只宠物（素材不随仓库分发，原因见文末「关于素材」）：

```bash
npx -y petdex install eous
```

然后双击 **`启动Eous.bat`**，或者：

```bash
python eous_pet.py
```

- 尺寸固定「小」，出现在主屏右下角；位置和各个开关都会记住。
- 重复启动只会有一只：第二次启动会检测到已在运行并直接退出。
- 不带 `--pet` 启动就用上次选的那只；`python eous_pet.py --list-pets` 看装了哪些。

## 操作

| 操作 | 效果 |
| --- | --- |
| 左键拖动 | 搬动宠物（拖动时切到 running，松手回到原状态）；**可以拖到副屏上** |
| 左键单击 | 蹭一下，随机做个动作 + 说句话 |
| 双击 | 跳一下 |
| 右键 | 菜单：**宠物**（装了多只时才有）/ 状态 / 随机动作 / 总在最前 / 显示气泡 / **显示速度** / 关于 / 退出 |

## 显示模型调用的速度

宠物脚下有一条小药丸，显示最近一次模型调用的生成速度（tok/s）：

```
        ╭──────────╮
        │ ● 10 tok/s│
        ╰──────────╯
```

数据来自 ZCode 的会话数据库 `~/.zcode/cli/db/db.sqlite` 里的 `model_usage` 表，
每秒轮询一次，只读不写、按 rowid 倒序取一行，不扫全表（库有 400 多 MB）。

速度的算法：优先用「输出 token 数 ÷（完成时间 − 首 token 时间）」，这样分母是纯生成
时间，不含排队和首包延迟；如果这次调用没有记录首 token 时间，就退而用总耗时当分母，
这时数字会偏低。想细看数据用 `GET /speed`，那里面会带 `basis` 字段说明这次用的是哪种口径。

### 一个必须说清楚的限制：它不是流式实时的

我原本想做成"数字在生成过程中不断跳动"，实测做不到。这台机器上我用三次独立实验
（共 1149 个采样点）确认了：

- `model_usage` 只在调用**结束**时写入一条 `completed` 记录。流式过程中
  `status='running'` 的行数始终为 0，150 秒里 566 个采样点一次都没见到。
- `part` 表（消息片段）的内容也是一次性写入的：跟踪 642 个采样点，同一个片段 id 的
  长度从未发生变化，文本和 reasoning 两种类型都是如此。

所以宠物给的是**每次调用结束后刷新的准确速度**，粒度是"一次模型调用"，
而不是流式过程中的瞬时值。宿主日志里也只有 RPC 记录和渲染层订阅事件，没有逐块
的 token 产出信息，拿不到更细的粒度。

### 什么时候显示：只在任务进行时

速度条不是常驻的——没有任务在跑的时候会收起来，免得挂着一个几分钟前的旧数字晃眼。

判断"在不在跑"只能靠**数据库写入活动**：任务进行时 ZCode 会不停写 `part` 表
（工具调用、步骤边界），任务停下就断了。我实测过写入间隔，是锯齿形的：

- 多数时候几秒到十几秒一次；
- 但纯文本生成期间**出现过 43 秒完全静默**（我连续输出长文的时候）。

所以窗口不能太短，定成了 90 秒，否则长文生成到一半速度条会闪掉。光放宽还不够——
那样任务结束后它会赖着不走两分钟，所以"跑完"用 `turn_usage` 落行这个明确事件来收尾，
**一结束立刻隐藏**，不等窗口过期。

还有个细节：收尾那一瞬间本身也会写库，如果不加保护，刚隐藏就会被重新点亮。
所以只有当写入时间**晚于**最近一轮的完成时间（留 1 秒余量）才算"在跑"。

`/health` 里可以直接看这两个状态：

```bash
curl -s http://127.0.0.1:7777/health
#   "task_active": true,     任务在跑吗
#   "speed_visible": true,   速度条现在显示吗
```

菜单里可以整个关掉速度条（「显示速度」），也可以用 `--no-speed` 或 `--db` 启动。

其它接口：

```bash
curl -s http://127.0.0.1:7777/speed     # 最近一次调用的完整数据
curl -s http://127.0.0.1:7777/health    # 顺带带一个 speed 字段
```

## 换宠物

用 petdex 装的宠物会被自动发现，右键菜单最上面多一个「宠物」子菜单，点一下就换。

这台机器上认到 6 只：`eous`、`fairy`、`hoshimi-miyabi`、
`remielle-2`、`ye-shunguang-jk`、`yixuan-qpet`。选中的那只记在配置里，下次启动还是它。

不想要的可以删掉素材目录（比如 `rm -rf ~/.petdex/pets/xxx`），菜单里就不会再出现；
想装回来用 `npx -y petdex install xxx`。

再装新的（装完重启一下宠物，菜单才会刷新）：

```bash
npx -y petdex@latest install hoshimi-miyabi
```

命令行也能用：

```bash
python eous_pet.py --list-pets     # 看装了哪些
python eous_pet.py --pet fairy     # 直接用某只启动
```

扫描 `~/.petdex/pets/` 和 `~/.codex/pets/`，两边都有同一只时以 petdex 为准；
没有精灵图的目录直接跳过（比如只装了半截的 `yixuan`）。

**两种精灵图网格都认**：8x9（1536x1872）和 8x11（1536x2288，ChatGPT 导出就是这种）。
v2 的前 9 行和 v1 是同一套状态、同样的顺序——remielle-2 就是 8x11，实测能正常加载。

### 每只宠物有自己的性格

换了宠物不只是换个皮，它们说话和爱做的动作都不一样：

| 宠物 | 性格 | 常做的动作 |
| --- | --- | --- |
| eous | 活泼小助手（"代码写完了吗？"） | running、review |
| fairy | 冷静计算型（"数据不会骗人"） | review、waiting |
| hoshimi-miyabi | 寡言专注（"……"、"刀已备好"） | review、waiting |
| remielle-2 | 软绵绵（"困了…"） | waiting、review |
| ye-shunguang-jk | 轻快（"嘿～"） | waving、review |
| yixuan-qpet | 元气（"冲鸭！"） | jumping、running |

聊天的台词、发呆时爱做的动作、被点一下的反应，三样都按宠物分别配；池子里同一个动作
写两次就是提高权重（miyabi 的 `review` 写了三次，所以它多半在沉思）。没登记的宠物用
`DEFAULT_PROFILE`，想加新的在 `PET_PROFILES` 里补一段就行。

### 专属动作：8x11 多出来的那两行

8x11（v2）精灵图比 8x9 多两行，之前是直接忽略的。现在它们被登记成 `extra-1` /
`extra-2` 并加进**这只宠物**的随机动作池——所以不同宠物能做的动作是字面意义上不同的。
实测 `remielle-2` 有这两个专属动作，其他几只没有（它们本来就是 8x9）。

它们不进「状态」菜单（没有官方名字），只能在随机动作里出现。

### 速度条的颜色跟着宠物走

速度条的描边、圆点和底色都从当前宠物的精灵图里提取主色，换宠物就换色：

| 宠物 | 主色 | 宠物 | 主色 |
| --- | --- | --- | --- |
| eous | `#e18019` 橙 | remielle-2 | `#f4b1be` 粉 |
| fairy | `#3d60e1` 蓝 | ye-shunguang-jk | `#e18662` 暖橘 |
| hoshimi-miyabi | `#65b4e1` 浅蓝 | | |

提取方法不是"数出现最多的颜色"——大面积的白毛和黑描边会把它带偏。实际做法是：
把不透明像素转成 HSV，丢掉太暗的（描边、阴影）和太灰的（高光、白毛），
在剩下的鲜艳像素里按色相分 32 个桶找峰值，再把峰值和左右相邻的桶按
「饱和度 × 亮度」加权平均（相邻桶也算进来，免得主色卡在分桶边界上被切成两半），
最后把颜色提到适合画在深色底上的鲜艳度。七只宠物提取出来的主色互不相同，
耗时每只 60~95ms，只在启动和换宠物时算一次。

## 动画状态

九行精灵图对应九个状态，右键菜单或 `petctl` 都能切换：

`idle` `running` `running-left` `running-right` `waving` `jumping` `failed` `review` `waiting`

自己发呆的时候，宠物每隔 20~60 秒会随机做个小动作，偶尔冒一句话。

## 让 agent 驱动它

程序启动时会在 `127.0.0.1:7777` 开一个本地 HTTP 服务，
协议和 Petdex 桌面端完全一致，所以任何 petdex 风格的钩子都能直接用：

```bash
# 命令行
python petctl.py state running
python petctl.py state failed --duration 4000
python petctl.py say "正在读 eous_pet.py"
python petctl.py states
python petctl.py health
python petctl.py quit

# 或者直接打 HTTP
curl -X POST http://127.0.0.1:7777/state  -H "Content-Type: application/json" -d "{\"state\":\"running\",\"duration\":3000}"
curl -X POST http://127.0.0.1:7777/bubble -H "Content-Type: application/json" -d "{\"text\":\"正在读文件\"}"
```

Codex / Petdex 那套事件到状态的映射是现成的，照着抄即可：

| agent 事件 | 状态 |
| --- | --- |
| 工具开始执行（read / grep / glob） | `review` |
| 工具开始执行（其它） | `running` |
| 工具执行结束 | `idle` |
| 工具失败 | `failed` |
| 会话结束 / 停止 | `waving` |
| 用户发来新指令 / 会话开始 | `jumping` |
| 等待用户输入 | `waiting` |

### ZCode 钩子配置

ZCode 的钩子写在 `~/.zcode/cli/config.json`，本机已按官方 schema 配好（用户级，所有工作区通用）。
**注意：如上节实测，当前版本里它们尚未真正执行**，配置本身没问题。

| ZCode 事件 | matcher | 效果 |
| --- | --- | --- |
| `SessionStart` | — | `jumping 2000` |
| `UserPromptSubmit` | — | `jumping 2000` |
| `PreToolUse` | `*` | `running` |
| `PreToolUse` | `Read\|Grep\|Glob` | `review`（后写的覆盖前面的，所以读文件是 review） |
| `PostToolUse` | — | `idle` |
| `PostToolUseFailure` | — | `failed 4000` |
| `PermissionRequest` | — | `waiting` |
| `Stop` | — | `waving 2500` |

每条都是 `tools/hook.bat <状态> [毫秒]`，带毫秒数的状态播完会自动回到 idle。

三个设计上的选择：

- **用 curl 而不是 Python**。ZCode 的钩子是同步执行的（`async` 字段目前没有实际效果），
  每次工具调用都会阻塞等待。Python 启动约 150ms，curl 约 20ms，差一个数量级。
- **`hook.bat` 永远 `exit /b 0`**。宠物没开着的时候 curl 会失败，
  不强制返回 0 的话 ZCode 里每次工具调用都会报一条钩子错误。
- **走 `GET /set?state=…` 而不是 POST JSON**。cmd 里 `\"` 转义的 JSON 传给 curl 是坏的
  （实测状态不会变），免引号的 GET 接口没有这个坑。

> 钩子在 ZCode **启动会话时**读取，所以改完配置要新开一个会话（或重启 ZCode）才生效。

`petctl.py` 走的是 POST JSON，仍然可用，适合手动触发或从别的 agent 调用。
想让别的 agent 也驱动它，把 `tools/hook.bat` 当成通用入口即可。

## 文件

| 文件 | 说明 |
| --- | --- |
| `eous_pet.py` | 宠物本体（Tk + Pillow，无第三方依赖；含读速度的 `SpeedWatcher` 和盯完成事件的 `TurnWatcher`） |
| `petctl.py` | 给运行中的宠物发指令的 CLI |
| `启动Eous.bat` | 免黑框启动 |
| `tools/hook.bat` | agent 钩子入口：`hook.bat <状态> [毫秒]`，永远返回 0 |
| `tools/ensure_running.bat` | 拉起宠物（没跑才拉）+ 设状态，给 SessionStart 钩子用 |
| `assets/spritesheet.webp` | Eous 精灵图（1536×1872，8 列 × 9 行，单元格 192×208） |
| `tools/selfcheck.py` | 合成事件自检，验证拖拽/状态/气泡/速度条/字形/多屏边界/换宠物 |
| `tools/shot.py` | 截屏取证用的小工具（PrintWindow，不受多屏和 DPI 影响） |
| `~/.eous-pet/config.json` | 位置、置顶、气泡、速度条、当前宠物、端口 |

## 开机自启

已经配好了：启动文件夹里有一个快捷方式

```
%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Eous Pet.lnk
```

指向 `pythonw.exe eous_pet.py`，登录即启动、不弹黑框。想取消就删掉这个快捷方式。

> 注意：宠物**不要**从 agent 的工具调用里启动。那样它是工具进程的子进程，
> ZCode 一重启就会被整棵进程树一起清掉（这正是之前宠物消失的原因）。
> 用快捷方式或 `启动Eous.bat` 启动，进程挂在资源管理器下，才不会跟着没。

## 钩子在当前版本里的实测情况

`~/.zcode/cli/config.json` 里的钩子配置是**按官方 schema 写的、结构正确**，但我在本机实测
**它们没有真正执行**，所以先不要指望"agent 一动宠物就跟着动"：

- 在工具调用进行中查询宠物状态，全程是 `idle`，说明 `PreToolUse` 没有被触发。
- 今天（配置写完之后）ZCode 启动过，但日志里除了启动时注册 `hooks` 通道，
  没有任何一条钩子执行记录。

从程序包里看，这个版本的钩子有一条**运行时的总开关**，不止配置文件里的 `hooks.enabled`：

```js
function sHr(e){ return { configuredEnabled: e.sourceEnabled && e.declarationEnabled && e.runtimeHooksEnabled, ... } }
```

而且工作区钩子还有一套 trust/admission 审核流程（`trustState` / `pending_trust` /
`effectiveRunnable`）。官方文档说"配置文件里 `hooks.enabled: true` 就会无条件执行"，
但和这个版本的实际行为对不上。

**所以现在的可靠部分是**：开机/登录自启 + `petctl.py` 手动驱动。
想要它跟着 agent 动，两条路：

1. 在 **Settings → Plugin Management** 里看钩子列表，确认这几条是否显示为可运行、
   是否需要信任授权。
2. 把钩子打包成一个本地插件——文档明确说**只要有插件贡献钩子，钩子运行器就会被自动启用**，
   这正好绕开上面那个总开关。需要的话我可以做。

钩子本身是好的：`tools/hook.bat` 手动执行有效、返回码 0；状态映射也用 Python 复刻
ZCode 的 matcher 逻辑验证过（见下）。所以一旦开关打通，配置不用改。

## 实现上的几个坑

- **DPI**：程序会先声明 DPI 感知，再按 `dpi/96` 放大精灵图。不这么做的话，
  在 150% 缩放的屏幕上 Windows 会把窗口位图拉糊，而且逻辑坐标和实际坐标对不上。
- **透明**：Tk 在 Windows 上用 `-transparentcolor` 抠图（品红 `#ff00fe`）。
  直接 alpha 混合会在边缘混出粉色描边，所以合成时把 alpha 二值化——
  像素画本身边缘就是硬边，这样反而更干净。
- **气泡字号必须有下限**：字号原本是纯按宠物缩放算的，「小」档只有 11px，
  三行的那种气泡文案根本看不清。现在下限是 10.5 个逻辑像素
  （本机 150% 缩放即 16px），并且气泡区的预留高度由同一套公式算出来，
  免得文字变大了却没地方放。顺带把提醒文案拆成短行——气泡宽度被宠物宽度卡着
  （「小」档才 144px），用 `·` 连成长串会被折得断断续续。
- **多显示器别用虚拟桌面包围盒**：Tk 的 `winfo_screenwidth/height` 只给**主屏**尺寸，
  拿它做边界判断会把宠物锁在主屏里，拖到副屏会被弹回来。但改成"虚拟桌面"的
  包围盒也是错的——显示器之间可能有空隙，本机主屏 `0~2160`、副屏 `3240~6120`，
  中间 1080px 谁都不属于，夹进包围盒的宠物会停在空隙里：窗口是 topmost 的，
  却没有任何显示器显示它，用户就找不回来了。正确做法是用
  `EnumDisplayMonitors` 取每块屏的范围，逐块各夹一次，取离目标最近的结果，
  这样既能待在副屏上，又保证一定落在某块屏里。

## 许可

代码是 MIT，见 [LICENSE](LICENSE)。宠物素材不在此列——它们是各家作者的
同人作品，版权归原游戏方所有，这个仓库不再分发。

## 关于素材

**这个仓库不含任何宠物素材**，只有代码。原因很简单：精灵图是 Petdex 上网友提交的
**同人像素画**（角色版权属于各家游戏公司），不适合再分发一份；而且素材本来就能用一条
命令拿到。`assets/` 已经写进 `.gitignore`，本地留一份只是为了 `--sheet` 调试方便。

程序读取顺序是 `~/.petdex/pets/<名字>/` 和 `~/.codex/pets/<名字>/`，
所以只要用 petdex 装过就能被认出来：

```bash
npx -y petdex install eous          # 装一只
python eous_pet.py --list-pets      # 看装了哪些
```

感谢 [Petdex](https://petdex.dev) 提供宠物目录，以及各位像素画作者——
比如 Eous 那只是 Petdex 上的 `CIME` 提交的。

## 另一个选择

不想用这个自建版本的话，官方路线是装
[Petdex Desktop](https://petdex.dev/download)，用 petdex 装好素材之后，
在那个应用的 Settings 里选成当前宠物即可。
