# 人工说明
- **1**为桌宠的音乐自动播放动作增加了识别歌词并显示功能.其中验证了网易云和QQ音乐确实可用,其中网易云不提供播放进度接口,选择了根据歌词文件进行估算,会概率有偏差,QQ音乐提供了接口,显示效果比较好,设置中可调节歌词显示延迟.
- **2**修改了obs模式下气泡朝向错误,只修复了前4个气泡,水泡气泡很不好适配并且我认为效果一般我不打算做了.
- **3**增加了agent使用时计费功能,原理是开始和结束工作时两次查询到的余额差值,多agent或者期间充值会显示不正确.
- **4**为1和3的修改部分同时做了菜单联动.
- **5**内容由cloudecode使用deepseek-flash模型制作,注意审视实际内容,本次制作消耗大约20元人民币,希望作者采用;以下内容为ai生成项目介绍:

# 改动说明

> 基线：`20118c8`（Merge PR #114 from klxxya/fix/collision-perf）
> 范围：26 个文件，新增 8 个文件（2649 行）
> 状态：全部改动**未提交**，仍是工作区状态

---

# 一、新功能

## 1. 歌词显示

在桌宠头顶气泡里显示当前播放歌曲的歌词，随播放进度滚动。

### `pet/now_playing.py`（新增，284 行）

用 Windows SMTC（`winrt-Windows.Media.Control`）读曲目与进度，并提供播放控制。

- winrt 的 async API 用 `asyncio.run()` 同步化；异常时返回 `None` 不抛。
- 多会话时按 `playback_status` 优先取正在播放的那个。
- `Playback.position` 允许为 `None`：QQ音乐上报进度，网易云恒为 0，需回退到本地计时。
- 外推修正：SMTC 的 `position` 是采样值而非连续快照，按 `位置 ≈ position + (now - last_updated_time)` 消除轮询粒度造成的滞后。
- 提供 `toggle_play_pause()` / `play_session_for(exe_name)` / `skip_track(direction)`。

### `pet/music_lyric.py`（新增，488 行）

取词 + LRC 解析 + 磁盘缓存。

- **三源并发**：QQ音乐 → lrclib → 网易云，按匹配质量排序，但同时发起请求，靠前的源在 1.2 秒优势窗口内返回即采用。
- **纯音乐识别**：平台对纯音乐给的是占位文案（`此歌曲为没有填词的纯音乐，请您欣赏`），只在整首（≤3 行）全部匹配占位语时判定。
- **LRC 解析**：跳过无时间戳的标签行、一行多时间戳展开、应用 `[offset:]` 偏移、小数位按长度换算。
- **缓存**：LRU 上限 2000 首，只存歌词文本。

### `pet/music_lyric_controller.py`（新增，688 行）

控制器：轮询（1s）→ 切歌检测 → 后台取词 → 按进度更新气泡。

- **进度跟踪 `LyricTracker`**（纯逻辑，无 Qt 依赖）：有真实进度的播放器直接用；没有的以"检测到切歌的时刻"为基准本地累加。
- **启动时歌已在播**：不猜起点，等下一首。
- **歌词提前量**：默认 +1 秒，范围 −2.0~+3.0，可在设置里调。
- **让路机制**：检测到气泡被别的提示占用就让路 5 秒。
- **位置稳定**：同一首歌内锁定气泡宽度，只在切歌时重新量宽。

### 相关修改

| 文件 | 改动 |
|---|---|
| `pet/speech_bubble.py` | `show_text` 新增 `title_first` / `width_locked` 参数：标题放正文上方、11px、短标题不折行 |
| `pet/speech_bubble_text.py` | 新增 `keep_breaks` 参数，透传到 `normalize_bubble_text` / `_wrap_bubble_lines` / `elide_bubble_text` / `paginate_bubble_text`（默认行为不变） |
| `pet/window_optional_services.py` | 歌词控制器装配 + `sync_music_lyric` + `set_instrumental_playing` |
| `pet/window.py` | `show_bubble` 透传 `title_first` / `width_locked`（净增 0 行） |
| `requirements.txt` | winrt 列为可选依赖 |

---

## 2. Agent 消费统计

Agent 每轮结束时显示本轮消费金额。

### `pet/agent_cost.py`（新增，130 行）

用余额差值估算：开始干活时记余额快照，本轮结束时再查一次，两者相减。

- 余额只有 2 位小数，不足 ¥0.005 的变化被抹平为 0。
- 并发时标注「（含其他会话）」。
- 纯逻辑，网络查询由调用方注入，可离线单测。

### `pet/agent_link.py`（+114）

两个挂点，均复用现有机制：

| 位置 | 改动 |
|---|---|
| busy 起始边沿 | `_cost_note_start(agent_key)` 记余额快照 |
| `_fire_done()` | `_cost_finish(agent_key)` 异步结算并补气泡 |

- 余额查询走后台线程 + Qt 信号回主线程，避免阻塞主线程。
- 绕过余额缓存直连底层 `fetch_balance()`：现成入口有 30 秒缓存，会让差值恒为 0。

---

## 3. 音乐右键菜单

新增「**音乐**」一级分类，含 5 项：

```
音乐 ▶
  让人家歇一会儿嘛（暂停 / 播放）
  给主人换一首（切歌）
  人家今天不唱了（退出音乐模式）
  ──────────────────────────
  打开网易云音乐给主人放歌
  打开QQ音乐给主人放歌
```

### `pet/music_players.py`（新增，118 行）

定位播放器可执行文件：先自动搜常见路径，再允许手动覆盖（配置键 `music_player_paths`）。

搜索覆盖两个盘符 + AppData，目录名 + 浅层扫描（限 3 层 / 200 目录），结果进程内缓存（含"找不到"的负结果）。

### 菜单接线

| 文件 | 改动 |
|---|---|
| `pet/context_menus/shared.py` | 6 个独立 builder（每项可单独配置）+「打开并播放」的启动逻辑 |
| `pet/context_menus/registry.py` | 菜单动作注册 |
| `pet/context_menus/legacy.py` | 旧版菜单同步 |
| `pet/menu_templates/modern-default-v1.json` | 插入 `music` 子菜单节点（`type: submenu`，含 5 个子项，设置里可展开编辑） |

---

## 4. 新增设置项

| 配置键 | 默认 | 位置 |
|---|---|---|
| `music_lyric_enabled` | `False` | 桌宠 → 音乐关联 |
| `music_lyric_lead_seconds` | `1.0` | 同上（范围 −2.0~3.0） |
| `music_lyric_cache_limit` | `2000` | 内部 |
| `music_sing_grace_seconds` | `6.0` | 内部 |
| `agent_cost_enabled` | `False` | 自动化与联动 → 消费统计（该页首个分组） |

均按项目约定三处同步登记：默认值 dict + reload 白名单 + `test_config_schema.py` 快照。

---

# 二、修复原有问题

## 1. 气泡三角朝向错误（直播捕获模式下）

**问题**：开启 `stream_capture_mode` 后，气泡三角指向桌宠的**反方向**。

**原因**：`speech_bubble.py` 的 `_update_surface_geometry` 有四个方向分支，条件全部要求"气泡与桌宠完全不重叠"。而直播模式下气泡是主窗子控件、可用区被限制在主窗矩形内，空间不足时会压在桌宠身上 → 四个分支全部失效 → 落到 `else`（原来是无条件朝上），与桌宠实际方位无关。

**修复**：`else` 兜底改为按主导方向判定：

```
dx = anchor_center.x() - local.center().x()
dy = anchor_center.y() - local.center().y()
if abs(dx) > abs(dy):   横向主导 → 按 dx 正负朝右/朝左
elif dy >= 0:           朝下
else:                   朝上
```

只在重叠时生效，不影响原有四个分支。

## 2. 音乐自动唱歌被间奏打断

**问题**：唱歌动画播放中会突然退出，停止唱歌。

**原因**：`is_music_playing()` 只看音频峰值，歌曲的前奏/间奏/轻声段会让峰值瞬时跌破阈值 → 判定"音乐停了" → 立刻退出。

**修复**（`pet/window_alerts.py`）：加宽限期，只有持续静音超过 `music_sing_grace_seconds`（默认 6 秒）才真的退出；中途一有声音就清零重算。

## 3. 纯音乐被当作有词曲目

**问题**：纯音乐（配乐/OST/演奏曲）会显示一句占位文案，并放起唱歌动画。

**原因**：平台对纯音乐返回的不是空歌词，而是占位文案（实测 QQ 音乐：`此歌曲为没有填词的纯音乐，请您欣赏` / `纯音乐，请欣赏`）。

**修复**：`music_lyric.py` 增加识别——只在整首（≤3 行）全部匹配占位语时判定为纯音乐，避免误判真歌词里出现的"纯音乐"字样。识别后不显示歌词、不唱歌，改为显示「正在听《歌名》」+ 随机提示。

---

# 三、测试

新增 3 个测试文件，共 77 个用例（全部离线）：

| 文件 | 用例数 | 覆盖 |
|---|---|---|
| `tests/test_music_lyric.py` | 53 | LRC 解析、曲目匹配、三源优先级、缓存往返与淘汰、进度跟踪两模式、纯音乐识别、常驻标题、让路 |
| `tests/test_agent_cost.py` | 18 | 差值计算、缺失基线静默、并发标注、精度边界、状态管理 |
| `tests/test_music_sing_grace.py` | 6 | 宽限期行为 |

**全量测试**：`2082 passed, 2 failed`

两个失败（`test_agent_link_dep_specs::test_safe_probes_swallow_permission_errors`、`test_drag_move_coalescing::test_drag_coalesce_timer_is_about_120hz`）已用原始代码验证为既有问题（Windows 权限模拟 / 计时器精度），与本次改动无关。

`ruff check pet/ tests/` 干净。

---

# 四、已知遗留

**吐气水泡（breath_bubble）拖尾方向** —— 拖尾坐标写死在窗口右下角（`local.width() - 71.0*sx`），不跟随桌宠方位。正常模式与直播模式表现一致。

---

# 五、依赖变更

`requirements.txt` 新增（可选依赖，独立注释块）：

```
winrt-Windows.Media.Control>=3.0; platform_system == "Windows"
winrt-Windows.Foundation>=3.0; platform_system == "Windows"
winrt-Windows.Foundation.Collections>=3.0; platform_system == "Windows"
winrt-Windows.Storage.Streams>=3.0; platform_system == "Windows"
```

不装时 `now_playing.available()` 返回 `False`，设置页开关置灰并说明原因，其余功能不受影响。

**打包注意**：winrt 是 namespace package，PyInstaller 需要显式登记 `hiddenimports`（`winrt.windows.media.control` / `winrt.windows.foundation.collections`），否则会漏收。
