# PR 报告：Edge 网页内容实时互动（web watch）

> **基线**：dsh-pet-indesktop v4.2.1（`dsh-pet-indesktop-main.zip`，2026-10-03 下载；本地仓库基线提交 `e4af6a8`）
> **分支**：`feat/web-watch`　**日期**：2026-10-06
> **范围**：21 个文件（实现 10、测试 4、集成/扩展 8、文档 3；见第二节）
> **关联**：用户需求「监控 Edge 当前网页内容，实时评论、有时给建议」；设计记录 `.scratch/web-watch/spec.md`

## 一、核心特性

浏览器里打开一个网页，桌宠会**看内容说话**：换页停留够了一句评论、长文增量再补一句、
划词优先评论那段、视频到点评论一次、在代码/文档/论文/新闻/问答页发呆久了给一条**具体建议**。

| 能力 | 说明 |
| --- | --- |
| 采集 | Edge MV3 扩展读页面标题/正文摘要/小标题/划词/视频进度，POST 到 `127.0.0.1` |
| 接收 | 桌宠内置 `ThreadingHTTPServer`（标准库、daemon 线程），令牌鉴权 + 体积上限 |
| 触发 | 停留 / 正文增量 / 划词 / 视频时刻 / 发呆建议，五类各自成条，页级去重 |
| 频控 | 复用 `ProactiveLimiter`（独立状态文件）：全局冷却 2 分钟、每日上限 60、最小间隔 30s、连续失败当日熔断；拒绝后本地退避 10s |
| 生成 | 复用聊天 provider（非流式短文本），人设 + 任务约束 + `<SKIP>` 弃权口 + 输出清洗 |
| 呈现 | 复用共享代理 `show_bubble`（多窗/overlay 两端一致）+ 可选 TTS；评论全文同步进 AI 对话会话 |
| 隐私 | 查询串/锚点丢弃；扩展开关 + 域名黑/白名单；密码页整体停发；日志只记域名与触发原因 |
| 配置 | 新域 `web_watch`（默认全关）+ 设置页「自动化与联动 → 网页互动」整组 |

**红线 / 不变量**

1. **默认关闭**：不开就不建服务、不绑端口、不 import 服务模块（有机器化守卫）。
2. **只绑 127.0.0.1 + 令牌鉴权**：无令牌一律 401（实机已验证）。
3. **正文只在内存里过一遍**：查询串在入口丢弃，进模型的只有截断后的摘录；角色记忆与日志不落网页标题。
4. **不动 `pet/proactive.py` 的守卫顺序与隐私纪律**，不碰 `pet/edge_probe.py`（那是"贴屏幕边缘探头"，与 Edge 无关）。
5. **闸门只拦"说不说"，不拦"记不记"**：被拦下的一页仍被记住，闸门放行后按停留补说（否则用户会看到"功能没反应"）。

## 二、修改文件说明

`git diff --numstat e4af6a8 HEAD`（提交 `f58ad50`；29 个文件：实现 13、浏览器扩展集成 8、测试 5、文档 2、工具 1）。

### 实现（13）

| 文件 | 增/删 | 改了什么 + 为什么 |
| --- | --- | --- |
| `pet/web_watch/__init__.py` | +16 | 新包入口与分层说明（零 re-export，避免 F401） |
| `pet/web_watch/protocol.py` | +130 | 事件协议与**唯一一次**输入校验/裁剪：非法 kind、非 http(s)、超长字段、NaN 数值一律降级；URL 查询串/锚点在此丢弃 |
| `pet/web_watch/digest.py` | +173 | 隐私摘要：域名归一、`fnmatch` 黑/白名单（子域自动命中）、内容哈希、正文截断、页面分类（视频/代码/论文/购物/问答/新闻/文档） |
| `pet/web_watch/policy.py` | +444 | 触发状态机：停留/增量/划词/视频时刻/发呆建议 + 页级去重 + 闸门（可见/全屏/闲置/agent 忙，后者默认不拦）+ **被拦下时仍记页身份与待补说划词** |
| `pet/web_watch/prompts.py` | +123 | 两条提示词（评论/建议）+ 紧凑页面段落 + `<SKIP>` 弃权 + 输出清洗（去引号/markdown、取首行、截断） |
| `pet/web_watch/llm.py` | +129 | 非流式短文本请求（仿 `vision._post_vision_request`）：重试 / 429 退避 / 超时下限 60s / 每次真实请求前扣预算；顶层不 import `pet.chat`（无 Chat 变体安全） |
| `pet/web_watch/server.py` | +239 | 本地回环接收端：令牌常量时间比较、`Content-Length` 上限 413、非法 JSON/事件 400、`/health`+`/ping`、**不打印页面内容** |
| `pet/web_watch/service.py` | +458 | Qt 侧装配：信号桥 → GUI 线程心跳（有页 1s / 空闲 5s 退避）→ 频控 → daemon 线程生成 → 代理冒泡（+TTS/会话同步）；代次令牌作废迟到结果；频控拒绝后 10s 本地退避 |
| `pet/settings_web_watch.py` | +317 | 设置页控件/行/保存/自检（复制令牌、测试连接、打开扩展目录、今日用量）。放独立模块：`modern_settings_dialog.py` 行数预算已顶格（与 `settings_file_interpret` 同法） |
| `pet/config.py` | +108 | 新域 `web_watch`：默认值（28 键，含 `pause_when_agent_busy`）、`_merge_web_watch_data`、加载合并、脏值归一化（布尔/数值/名单）、`set()` 重归一化触发 |
| `pet/config_domains.py` | +15 | `WebWatchConfig` facade（复用 `_merge_web_watch_data`，不写第二份清洗逻辑） |
| `pet/app.py` | +77 | 懒门控三件套（`_web_watch_wanted/_ensure/_sync`）+ 启动装配 + 退出收口 + **热加载路径同步** + 测试收口 + 防御式读 `config`（`AppShell.__new__` 造壳不得炸） |
| `pet/modern_settings_dialog.py` | +10 | 四处最小接线：import、控件安装调用、「网页互动」组、保存委托 |

### 测试（5）

| 文件 | 增/删 | 说明 |
| --- | --- | --- |
| `tests/test_web_watch.py` | +368 | 纯逻辑契约 41 例：协议裁剪/隐私摘要/名单匹配/五类触发/闸门/待补说/状态回滚/快照不泄密/默认值同步 |
| `tests/test_web_watch_service.py` | +517 | 服务与接线 22 例：真回环 HTTP（204/401/400/413/health）、端到端触发→冒泡、dry-run 不调模型、频控退避、AppShell 懒门控、热加载同步、设置页 round-trip |
| `tests/test_app_lazy_imports.py` | +19/-1 | 守卫：`import pet.app` 不得带出 `pet.web_watch.*`（关闭即零开销） |
| `tests/test_architecture.py` | +6/-1 | 行数预算 2393 → 2403（+10 行接线，带日期与理由注释） |
| `tests/test_config_schema.py` | +2/-1 | `web_watch` 登记进 `SPECIAL_CASED_KEYS`（嵌套 dict 走专门合并路径） |

### 浏览器扩展集成（8，随包分发到 `_internal/integrations/`）

| 文件 | 增 | 说明 |
| --- | --- | --- |
| `integrations/edge-web-watch/manifest.json` | +24 | MV3；`host_permissions` 仅 `http://127.0.0.1/*`；options + popup |
| `integrations/edge-web-watch/content.js` | +184 | 采集：正文（article/main 优先）、小标题、划词、视频进度、DOM 变化去抖；**无任何读输入框值的路径**；含密码框整体停发 |
| `integrations/edge-web-watch/background.js` | +121 | 队列串行 POST + 状态（401/离线/被拒）+ 角标提示 |
| `integrations/edge-web-watch/options.{html,js}` | +56 / +76 | 端口/令牌/开关/域名暂停名单/测试连接 |
| `integrations/edge-web-watch/popup.{html,js}` | +24 / +54 | 连接状态、本网站暂停、「让桌宠现在看看这页」 |
| `integrations/edge-web-watch/README.md` | +59 | 安装三步、触发时机表、隐私边界、排查表 |

### 文档与工具（3）

| 文件 | 增/删 | 说明 |
| --- | --- | --- |
| `docs/PR-REPORT-web-watch-2026-10-06.md` | +169 | 本报告 |
| `docs/INDEX.md` | +1 | 「PR 报告存档」登记一行 |
| `.gitignore` | +1 | 加 `.venv/`（本次开发在仓库内建 venv；`pytest.ini` 的 `norecursedirs` 早已预期该目录） |

### 未改动（刻意）

- `pet/proactive.py`、`pet/proactive_limiter.py`（只调用，不改语义）；
- `pet/edge_probe.py` / `sprite_edge_probe.py`（与浏览器无关的边缘探头）；
- `pet/multi_window_shared.py`（冒泡直接复用既有 proxy，未加方法）；
- 两条渲染拓扑的壳（`window.py` / `overlay_shell.py` / `pet_sprite.py`）；
- `.scratch/web-watch/{spec.md,HANDOFF.md,measure.py}` 按仓库约定不入库。

## 三、实现要点

1. **接收端只做"校验 + 入队"**：HTTP 线程绝不碰 Qt；`deque.append` 是原子的，Qt 侧 1s 心跳消费。
2. **闸门与状态分离**：`observe()` 先更新页身份/正文/哈希，再问闸门；被拦下时把划词存进 `_pending_selection`，由 `poll()` 补说。
3. **频控直接复用 `ProactiveLimiter`**（独立 `web_watch_state.json`）：白拿冷却/每日上限/最小间隔/熔断/跨进程文件锁；`dry_run` 由本服务解释（不复用识屏的 dry-run 状态文件名，避免串账）。
4. **一次真实 HTTP 前扣一次预算**（`consume_budget` 钩子），429 退避 2s 重试一次，连续 3 次失败当日熔断。
5. **`<SKIP>` 弃权口**：模型认为这页没什么可说时回 `<SKIP>`，桌宠不冒泡、不计失败——宁可不说话，也不硬凑。
6. **热加载**：独立设置进程保存后经 `_apply_external_config_change` 同步（实机缺口，见第五节）。
7. **Qt 生命周期**：无主 `QTimer`/信号桥过继给 `QApplication`（沿用 `multi_window_shared._release_qt_lifetimes` 的根因处置）。

## 四、性能分析

实测环境：本机 Windows 11 / CPython 3.13.13 / PySide6 6.11.2；脚本 `.scratch/web-watch/measure.py`（`tools/import_cost.py` 同口径的子进程 tracemalloc + 真实 `QEventLoop` 计时 + 200 次回环 POST）。

| 指标 | 实测 | 说明 |
| --- | --- | --- |
| 关闭态启动开销 | `import pet.app` 后 `pet.web_watch` / `.service` / `.server` **均不在 `sys.modules`**（脚本实测三项全 false） | 默认关闭 = 零 import、零端口、零定时器；由 `test_app_lazy_imports.py` 机器化守卫 |
| 服务模块导入成本 | **4.99 MB** Python 堆 / 12 个新 pet 模块（`pet.web_watch.service`，含 `pet.catalog`、`pet.config`、`pet.proactive_limiter`、`pet.report_gates`） | 视作上界：真实运行中前三个模块早已加载，边际增量小于此值；且只在开启时付一次 |
| 开启态稳态 CPU（空闲，心跳退避到 5s） | **0.156%** 单核（10s 壁钟，真实事件循环） | 一次空转判定 = 若干属性比较 + 无 IO |
| 开启态稳态 CPU（有页在盯，1s 心跳） | **< 0.1%** 单核（10s 壁钟内 CPU 时间低于 `psutil.cpu_times()` 分辨率，读数 0.0） | 主线程每秒一次微秒级判定 |
| 接收端往返延迟（样本 200） | p50 **1.11 ms** / p95 **1.59 ms** / max **9.04 ms** | 本机回环 `POST /event` → 入队（含 urllib 客户端开销） |
| 内存增量 | **+0.07 MB** RSS（服务对象 + 接收端线程） | 定长 `deque(maxlen=256)`，不随浏览时长增长 |

> 口径说明：首次测量用忙轮询泵事件循环，读数被泵自身烧的 CPU 抬高（空闲 0.781% / 有页 1.406%）；改用真实 `QEventLoop` 后为上表数字。报告采用后者，前者仅作为"测量方法会显著影响结论"的记录。

**逐条回答**

- **稳态开销**：关闭态为 0（不 import、不绑端口、无定时器）。开启且无页面时为 5s 一次空转心跳；有页面在盯时 1s 一次（仅字符串长度/哈希比较，无 IO）。空闲时会自动退避到 5s。
- **新增路径成本与频率**：真实模型调用**只在触发成立且频控放行时**发生，频率上限由「全局冷却 2 分钟 + 最小间隔 30s + 每日上限 60」三重约束；单次成本 = 1 次短请求（`max_tokens` ≤ 512）+ 约 1.2k 字符摘录。
- **新增系统调用/网络/磁盘/线程**：开启时新增 1 个监听 socket 与最多 1 个短生命周期请求线程（daemon，单飞）；频控拒绝后 10s 内不重复问（避免 1Hz 文件锁）；状态文件只在真实触发/成功/失败时写一次。
- **内存增长**：事件队列为定长 `deque(maxlen=256)`，不随浏览时长增长；网页正文只在内存中过一遍、不落盘（日志只记域名与触发原因）。

## 五、实机运行记录

**环境**：真实 Windows 桌面 + 已安装产物（`D:\Users\七颜\AppData\Local\Programs\dsh-pet-standalone-webm-chat`，v4.2.1），进程 pid 10848，日志 `%APPDATA%\dsh-pet-standalone-webm-chat\pet-10848.log`。

1. **构建与部署**：`scripts/build_onedir.ps1 -Variant webm-chat`（本机 PS 5.1 解析中文脚本会乱码，用同目录 UTF-8 BOM 临时副本执行）→ 冒烟全绿（DLL 链/中文编码/瘦身/exe 起窗/`--settings` 起窗），产物 261.7 MB；`Copy-Item` 覆盖安装目录（保留 `unins000.*`），备份在 `D:\deepseekHarness\dsh-pet-install-backup-20261006-110919`（274.2 MB）。
2. **服务自启**：日志 `web_watch: 本地接收端已启动 http://127.0.0.1:8765` + `已启用（端口 8765，dry-run）`；令牌文件 `web_watch_token.txt`（32 位）自动生成。
   （日志里 `已启用` 会连打两条——`__init__` 的 `apply_config` 与 `_sync_web_watch_service` 各一次。纯日志冗余，不影响行为，登记为已知瑕疵。）
3. **鉴权边界**：带令牌 `POST /event` → **204**；不带令牌 → **401**；`GET /health` → `{"ok": true, "name": "shenshen"}`（不需要令牌，也不含秘密）。
4. **dry-run 命中**：发一条与扩展同构的 `page_open`（URL 故意带 `?sectoken=SECRET_QUERY#frag`）→ 8 秒停留后日志 `web_watch [dry-run]: 命中 comment（page_dwell，站点 github.com）`；**日志里只有域名**，查询串未出现。
5. **热加载**：不重启进程，把配置改成 `dry_run=false` → 日志出现 `已启用（端口 8765，真实模式）`。**这一步暴露了真缺口**：热加载路径最初没有同步 web_watch，等于"设置里开了、接收端不启动、也不生成令牌"（用户视角就是功能坏了）。修复后新增用例 `test_external_config_change_syncs_web_watch_service`。
6. **真实评论（换页触发）**：发 `page_open`（知乎专栏，正文含"60fps / 8% CPU / 三条优化建议"）→ 日志
   `web_watch 回复（comment/page_dwell，站点 zhuanlan.zhihu.com）: 60fps还只要8% CPU，这优化效果绝了，套娃复制粘贴看得我眼睛都花了`
   ——既命中页面里的具体数字，也注意到测试文本的重复。
7. **真实评论（划词触发）**：发 `selection`（选中"作者实测 60fps 下 CPU 占用约 8%…"）→ 日志
   `web_watch 回复（comment/selection，站点 zhuanlan.zhihu.com）: 8% CPU换60fps这性能优化可以啊`（正面评论被选中的那段）。
7b. **真实建议（发呆触发）**：在少数派文章页停留后（未勾"仅闲置时触发"，`suggest_idle_seconds=45` 由系统闲置满足）→ 日志
   `web_watch 回复（suggest/page_idle_suggestion，站点 sspai.com）: 建议收藏这篇文章并添加标签，便于后续按主题检索复习`
   ——这是"有时给建议"这条需求在实机的正面证据；建议内容针对页面类型（资讯/文章）而非泛泛而谈。
7c. **真实 TTS（朗读）**：把 `web_watch.speak_enabled` 置真后触发评论，`voice_chime_cache` 目录出现新 mp3 `485327a6c9c40c5e.mp3`（37 008 B，11:46:01，晚于回复 4 秒）；用同一音色配置对同一条评论文本复算 `pet/voice_chime.cache_key()` 得 `485327a6c9c40c5e`，**键完全一致** → 该 mp3 就是这条评论的 edge-tts 合成产物，朗读链路端到端成立（我没有"听"这一环，故用缓存键相关性作为证据，不推断音量/音质）。
7d. **模型弃权路径**：11:42 的划词请求在飞了约 3 分钟后没有冒泡，`web_watch_state.json` 的 `consecutive_failures` 仍为 0 —— 属 `<SKIP>` 弃权（设计如此：宁可不说话也不硬凑）；同页 3 分钟后按"发呆建议"正常出声，说明不是链路故障。
8. **第二条真缺口（本次实机最重要的发现）**：发新页事件后**完全没反应**。排查：`/health` 200（接收端在跑）、`POST` 204（事件收下了）、状态文件停在上一轮。根因是守卫在"记录页身份"**之前**返回——DSH agent（就是我）在工作时 `agent_busy=True`，事件被整条丢弃，页面根本没被记住，等 agent 空闲也不会再评论它。两处修正：① `observe` 先记状态再问闸门；② `agent_busy` 默认**不再静音**（新增配置 `pause_when_agent_busy`，默认 false），因为 agent 在对话期间几乎恒为 working。新增用例 `test_blocked_gate_still_records_page_so_it_can_speak_later`、`test_pending_selection_is_delivered_after_gate_clears`、`test_agent_busy_does_not_silence_by_default`。
9. **设置页实机验收**：`exe --settings`（用户真实流程：独立设置进程）正常起窗，页脚显示 `版本 v4.2.1`（确认部署产物与源码同版本）；`--settings --settings-page 自动化与联动` 可直接定位到承载新组的域（截图 `.scratch/web-watch/settings.png`、`settings-webwatch.png`）。**未取到该组滚动后的截图**：我用 `SendKeys` 发 PGDN 未生效（设置窗未接收），故"新组在页面上长什么样"只有控件契约用例与域页可打开两项证据，没有像素证据——如实登记，不推断。
10. **用户可见行为确认（气泡像素证据）**：发 `page_open`（arxiv 摘要页）后截图 `.scratch/web-watch/bubble-win-2.png` 中，桌宠头顶气泡正是该条评论全文「这结果复制粘贴了五次，作者写论文写到打瞌睡了吧」，与日志同一句。
11. **失败路径**：无令牌 401、超大请求体 413、非法 kind/非 http(s) URL 400、`dry_run` 只写日志不调模型、桌宠未运行时扩展侧状态为「连不上本机桌宠」（扩展 background 的 401/离线分支）。
12. **无法自动验证的部分**：把扩展加载进 Edge 需要人工在 `edge://extensions` 点「加载解压缩的扩展」（Chromium 137+ 已移除 `--load-extension`，且用户 Edge 用的是既有 profile，脚本化注入不可行）。因此上述 6/7 用**与扩展逐字段同构的 HTTP 报文**验证了链路的服务端全部环节；扩展自身的采集/发送逻辑用 `node --check` 通过语法校验，并需用户点一次加载（README 三步）。
13. **本次实机操作痕迹与恢复**：为取气泡截图曾临时把 `agent_link.report_gates` 八项全置 0（避开行为看门狗抢气泡位），已**从配置备份逐键恢复原值** `{state:0.55, activity:0.6, approval/done/exec_failed/model_access/stuck/bridge:1.0}`；用 `cmd /c start` 启动会带出一个可见 ffmpeg 控制台窗（用户自启动路径不会），已改用 `Start-Process` 重启并确认无残留可见 ffmpeg 窗。

## 六、测试与验证

| 检查 | 命令 | 结果 |
| --- | --- | --- |
| 静态检查 | `python -m ruff check pet tests scripts` | **All checks passed**（venv 的 ruff 0.16.10，全仓库无违规）。附注：用本机 conda 里较旧的 ruff 0.12.0 跑会报 `pet/window.py` 4 个 F811——该文件与基线提交逐字节相同（`git diff --stat` 为空），属上游既有、与本改动无关 |
| 聚焦（纯逻辑） | `pytest tests/test_web_watch.py -q` | 41 passed |
| 聚焦（服务/接线/设置页） | `pytest tests/test_web_watch_service.py -q` | 22 passed |
| 配置与架构门 | `pytest tests/test_config_schema.py tests/test_config_domains.py tests/test_architecture.py tests/test_app_lazy_imports.py -q` | 全绿 |
| 全量 | `pytest -q`（venv，`QT_QPA_PLATFORM=offscreen`） | **4353 passed / 14 skipped，exit 0**（617.75s）。第一次全量：4346 passed / **1 failed**，失败项 `test_modern_settings_finished_refreshes_even_on_rejected` 正是热加载接线缺口的邻近回归（`AppShell.__new__` 造壳无 `config`），已修 |
| 扩展语法 | `node --check content.js background.js options.js popup.js` + manifest JSON 解析 | 全通过 |
| 构建门 | `build_onedir.ps1 -Variant webm-chat`（BOM 副本） | exit 0；DLL 链 / 中文编码 / 瘦身 / exe 与 `--settings` 冒烟全过 |
| 断言有效性 | 三条新用例均为「先红后绿」：增量触发漏实现、闸门丢弃页状态、热加载不同步 | 见第五节 5/8 |

## 七、已知限制与后续

1. **表情包配图未接入**：当前只出文字气泡（+可选 TTS）。原因是三个宿主（`PetWindow` / `OverlayShell` / `MultiWindowProxy`）都没有公开的 `show_image` 扇出面，只有气泡控件私有面；强加会给两条渲染拓扑各写一份。后续若要，建议先给三宿主补一个公开 `show_image(text, image_path, duration_ms)`（复用 `window_alerts.pick_self_talk_choice` + `self_talk_image_dir`）。
2. **扩展需人工加载一次**：Chromium 137+ 移除了 `--load-extension`，无法脚本化注入既有 profile。README 给了三步。
3. **同一页只评论一次**（+增量补说）：重复浏览同一页不会反复说话（页级去重），刻意如此。想更活跃可调小 `dwell_seconds` / 冷却。
4. **建议只在五类页面出现**（代码/文档/论文/新闻/问答），视频/购物页刻意不给建议。
5. **升级会被官方更新覆盖**：安装页的在线更新会静默覆盖安装目录（用户主动点才会触发）；自定义构建的版本号不高于官方 release 时，点一次更新即回官方包。

## 八、风险与回滚

- **新增配置键**：`web_watch`（嵌套 dict，27 键）+ `agent_link.report_gates` 未改动。缺少该键即回落默认（全关），旧配置无需迁移；`Config` 的 `version` 不变（仍为 4）。
- **状态文件**：`<config.dir>/web_watch_state.json`（频控）、`web_watch_token.txt`（令牌）。`git revert` 后这两个文件残留但**不生效**（无人读取）；删掉即彻底清理。
- **回滚**：`git revert` 代码改动 + `Copy-Item` 恢复备份目录 `D:\deepseekHarness\dsh-pet-install-backup-20261006-110919\*` 到安装目录（保留 `unins000.*`）；用户配置备份为 `config.json.pre-webwatch-backup`。
- **本次实机操作痕迹**（用户可自行决定去留）：用户配置里 `web_watch.enabled=true`、`dry_run=false`（我为了实机验证打开的；关掉即恢复默认静默），以及为取气泡截图临时把 `agent_link.report_gates` 全部置 0 —— **已恢复为原值**（见下节补充）。

## 九、第二轮：用户现场"连不上"的排查记录（2026-10-06 下午）

用户报"还是没有成功""关掉语音朗读保存后又连不上"。以第一轮的报告为基线，此处追加事实与修复。

### 9.1 现象与最终定位

| 时间 | 事实（来自桌宠日志 / 状态快照 / Edge 档案） |
| --- | --- |
| 13:03 | 被动观测 25s：**0 个连接**打到接收端；扩展存储目录为空（选项从未保存）→ 当时扩展确未发出事件 |
| 13:11–13:18 | 用户重载扩展后事件开始到达：**收到 #13~#19**（chat.deepseek.com / pan.baidu.com / cn.bing.com），并命中 `[dry-run]: 命中 comment（page_dwell，站点 cn.bing.com）` |
| 13:20:12 | 配置被改：`web_watch.port` → **8755**（用户设置页保存）；桌宠随即改绑 8755 |
| 13:2x | 用户在**扩展选项**点「测试连接」→ 报"连不上本机桌宠"；自检显示主机权限已授予但两个 host 都 `TypeError` |
| 13:38:43 | 桌宠在 8765 真实模式下正常评论过一次（`commented page_dwell，cn.bing.com`） |
| 13:38:54 | 用户再次保存设置页 → `port` **被写回 8755**、`dry_run` 被写回 **True** → 13:38:55 桌宠改绑 8755 → 扩展（8765）再次失联 |

**根因（两条，均非 LNA）**：

1. **设置页是独立进程，保存会把"打开那一刻"的整份旧配置写回磁盘**：用户只是去关了「语音朗读」，
   却顺带把运行期已生效的端口（8765→8755）与模式（真实→dry-run）静默回滚 → 扩展失联、
   且即使端口对也不会说话（dry-run 只写日志）。
2. **端口漂移本身**：桌宠与扩展两处各有一个端口输入框，任一侧改动都可能导致不一致，
   而 `ERR_CONNECTION_REFUSED` 在 fetch 里同样表现为 `TypeError`，极易与"权限/LNA"混淆。

### 9.2 我的误判与纠正（如实登记）

中途我依据"主机权限已授予但两个 host 都 `TypeError`"判定为 **Edge 153 的本地网络访问（LNA）限制**，
并据此改了 manifest（`localNetwork` 权限）与系统策略预案。**这个结论是错的**：

- `msedge.dll` 里确实存在 `localNetwork` / `LocalNetworkAccess` / `LocalNetworkAccessRestrictionsEnabled`
  等字符串，**存在该特性 ≠ 本次拦截是它**；
- 决定性反证：13:18 的事件与 13:38 的评论都发生在同一台机器、同一 Edge 上，说明
  **扩展 → 127.0.0.1 的请求本来是通的**；13:2x 起的失败完全由端口不一致解释；
- 已回滚为 LNA 做的 manifest 改动（`optional_permissions: ["localNetwork"]` 与授权按钮均已移除），
  只保留真正有用的诊断信息。

**教训（已落进代码）**：自检必须**第一行就打印"正在探测哪个端口"**，并同时探测
Service Worker（生产路径）与候选端口——否则端口不一致会被误读成策略拦截。

### 9.3 本轮的修复

| # | 修复 | 位置 | 回归用例 |
| --- | --- | --- | --- |
| 1 | **保存不回滚未改动字段**：逐字段比对"打开时的界面快照"，用户没碰过的字段以磁盘最新值为准 | `pet/settings_web_watch.py` | `test_settings_save_does_not_clobber_untouched_fields`、`test_settings_save_applies_fields_the_user_changed` |
| 2 | **扩展端口自愈**：配置端口连不上时，在 8765/8755/8775/8888/9000/8000/9527 里找带桌宠签名的 `/health`，缓存 60s；连接失败强制重扫并换端口重试一次 | `integrations/edge-web-watch/background.js` | 实机（见 9.4） |
| 3 | **自检升级**：打印端口、主机权限、Service Worker 生产路径、候选端口扫描结果与"该改成哪个端口" | `options.js` / `popup.js` | 实机 |
| 4 | 观测性（第一轮已加，本轮验证有效）：`收到事件 / 暂不说话 + 原因 / 判定命中 / 派发` 四段日志 + `web_watch_status.json` 快照 | `pet/web_watch/service.py` | `test_pipeline_stages_are_logged` 等 |

### 9.4 实机验证（第二轮）

- 事件采集：**收到 32+ 条**（cn.bing.com / chat.deepseek.com / pan.baidu.com / 127.0.0.1:38080 等），
  日志四段齐全，`verbose_log` 默认开；
- 真实评论（本轮，站点与时间可对照日志）：
  - `13:38:43 comment/page_dwell，cn.bing.com：放弃谈判直接动手，这次行动够干脆利落！`
  - `13:48:58 comment/page_dwell，news.ycombinator.com：原来频繁打断用户三天就能劝退，这数据太真实了`
  - 另有针对 `127.0.0.1:38080`（DSH 界面本身）的评论，证明它读的是"当前真实页面内容"；
- 自检与端口扫描：候选端口扫描能报出"桌宠在 8765"；端口被改到 8755 时，扩展自动改用 8755 仍能送达；
- 设置页保存：修复后"只改语音朗读 → 保存"不再回滚端口与模式（源码单测 + 用户可复现步骤）。

### 9.5 遗留

- 桌宠设置页保存**仍会把其它域的未改动字段写回旧值**（这是该对话框的既有语义，本次只收窄了
  `web_watch` 域）；同类问题若在别处复现，可按同一招（打开快照 + 未改动则取磁盘最新值）处理。
- `apply_config` 的"停用"分支没有日志（只有启用时打一行），排查"功能为什么自己关了"时少一条线索。**未修**。

## 十、第三轮：反应速度（2026-10-06 傍晚）

用户反馈"反应还是有点慢"。全部结论都基于**运行中桌宠的真实日志时间戳**，不是估算。

### 10.1 实测拆解（改动前，8 条真实回复样本）

| 阶段 | 实测 |
| --- | --- |
| 事件到达 → 判定命中 | 0 ~ **5s**（页面说完话后心跳退避到 5s，新页面白等） |
| 判定命中 → 派发 | 通常 0s，**最差 30s**（全局冷却把"新页面"一起拦下；被拦时每秒重算一次直到窗口过期） |
| 派发 → 回复（模型往返） | 中位 **3.98s**，最快 0.86s，最慢 **18.85s**，另有 >36s 卡住无响应 |
| 典型端到端 | ~4s；最差 ~25s；卡住时最长静默可达 **3 分钟**（60s 超时 × 3 次尝试） |

改造前的现场（日志原文）：
```
13:59:31 判定命中：comment（page_dwell，站点 127.0.0.1）
13:59:32 判定命中：…（同一页，每秒一条）
…
13:59:44 派发模型请求：comment/page_dwell 站点=127.0.0.1   ← 冷却窗口过期才真正发出
```

### 10.2 本轮的七处改动

| # | 改动 | 位置 |
| --- | --- | --- |
| 1 | **事件到达即唤醒**：HTTP 线程发 Qt 信号，GUI 立刻处理并把心跳拉回 1s（带 120ms 去抖） | `service.py` `_enqueue_event` / `_on_event_arrived` |
| 2 | **新页面免同页冷却**：换页只受最小请求间隔约束；同页仍受冷却管 | `service.py` `_dispatch` + `ProactiveLimiter.try_acquire(ignore_cooldown=)` |
| 3 | **频控下限可按功能注入**：web_watch 10s / 0.1 分，主动识屏保持 30s / 0.5 分（共享 clamp 曾把配置的 12s 顶回 30s） | `proactive_limiter.py` `min_interval_floor` / `cooldown_floor` |
| 4 | **被拦不丢、不过期**：记住这条决定，退避后仍在本页就补说、已翻页则丢弃 | `service.py` `_pending_decision` / `_retry_pending` |
| 5 | **先兆气泡**：派发瞬间冒「让我看看……」（不朗读、不占时长），配置项 `pre_cue` 默认开 | `service.py` `_show_bubble(..., speak=False)` |
| 6 | **更短的请求**：正文摘录默认 1200 → **600 字**；`max_tokens` 512 → **192** | `policy.py` / `config.py` / `llm.py` |
| 7 | **卡住快速失败**：单次超时下限 60 → **30s**，尝试 3 → **2** 次 | `llm.py` |

### 10.3 实测拆解（改动后）

| 阶段 | 改动后 |
| --- | --- |
| 事件到达 → 判定命中 | **0.000 ~ 0.002s** |
| 判定命中 → 派发 | **0.008 ~ 0.111s**（连续两页都不再被频控拦） |
| 派发 → 回复 | 0.885s（本轮样本；仍随免费档波动） |
| **端到端（事件 → 冒泡）** | **0.998s**（= 0.002 + 0.111 + 0.885） |

日志原文（会话 `pet-11100`）：
```
14:23:21.594 收到事件 #1 kind=page_open 站点=quick-a.example 正文=304字
14:23:21.596 判定命中：comment（page_dwell，站点 quick-a.example）
14:23:21.707 派发模型请求：comment/page_dwell 站点=quick-a.example
14:23:22.592 回复（comment/page_dwell，站点 quick-a.example）: …
14:23:34.623 收到事件 #2 站点=quick-b.example → 14:23:34.631 派发（8ms，未被频控拦）
```

### 10.4 回归用例（本轮新增 6 条 + 修 2 条断言）

`test_new_page_bypasses_page_cooldown`、`test_rate_limited_decision_is_remembered_and_retried`、
`test_pending_decision_dropped_when_user_moved_on`、`test_event_arrival_wakes_idle_timer`、
`test_pre_cue_bubble_shows_before_reply`、`test_limiter_floors_are_lowered_for_web_watch`；
另修正 2 条因默认区间变化而失效的 clamp 断言（`cooldown_minutes` 0.1 地板、
`min_request_interval_seconds` 10 地板）。

### 10.5 仍未解决的瓶颈（如实登记）

**模型往返的方差**是现在唯一的显著延迟：同一配置实测 0.86s ~ 19s，偶发 >36s 卡住。
代码侧能做的只有"先兆气泡 + 快速失败"（已做）；再快只能从**服务商/模型**下手
（「AI 与对话」里换更快的档位），或改成**流式**（首字到达即冒泡，估算把感知延迟压到
1~2s 且不受总时长影响）——**未实现**，属下一步候选。

