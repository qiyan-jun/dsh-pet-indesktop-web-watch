# v4.2.0

> 从 **v4.1.0 到 v4.2.0** 的功能与修复完整汇总。包含自 v4.1.0 发布之后合并到主干的全部开发内容（PR #57/#64/#65/#68/#70/#71/#72/#73/#76/#79/#80/#82/#85/#86/#87/#90/#91/#93/#94/#96 及大量直推修复）。

---

## 📦 下载

发布构建由 tag 自动触发，产物生成后本 Release 会包含以下文件：

| 平台 | 变体 | 安装版 / 绿色版 |
|---|---|---|
| Windows x64 | WebM + Chat（AI 对话） | `dsh-pet-standalone-webm-chat-setup.exe` / `dsh-pet-standalone-webm-chat-portable.zip` |
| Windows x64 | WebM 无 Chat（纯桌宠） | `dsh-pet-standalone-webm-setup.exe` / `dsh-pet-standalone-webm-portable.zip` |
| macOS arm64 | WebM + Chat | `dsh-pet-standalone-webm-chat-macos-arm64.zip` |
| macOS arm64 | WebM 无 Chat | `dsh-pet-standalone-webm-macos-arm64.zip` |
| Linux x86_64 | WebM + Chat | `dsh-pet-standalone-webm-chat-linux-x86_64.zip` |
| Linux x86_64 | WebM 无 Chat | `dsh-pet-standalone-webm-linux-x86_64.zip` |

均为 **onedir 形态**：安装版与绿色版运行期都不解压、不产生临时缓存，启动快、卸载干净。GIF 变体自 v4.0.0 起停供。

## ⚠️ 升级说明

- 直接覆盖安装或使用新版便携版即可；配置目录按变体独立，不影响旧版数据。
- v4.0.0 以来的配置/会话数据会继续沿用，**无需迁移**；旧实验键（`decode_broker_enabled`、`click_sound_path`、`throw_max_speed` 等）自动清理/迁移，残留值无害。`animation_prewarm_enabled` 这个独立键已在后续版本删除，配置里残留的值会被直接忽略（预热行为现由省电模式统一控制）。
- **新开关大多默认关（灰度）**：单进程多开、边缘探头、省电模式、点击直连黄金回旋等需要先在设置中开启；**单进程多开改动后需重启生效**。
- **「清除子肥鱼」改为「退出子肥鱼」**：只退出、**不删除**该子肥鱼的设置/会话/待办数据，下次生成原样恢复；操作不再弹确认框，结果写日志。
- **多窗提醒行为变更**：多开时「过程汇报 / 提醒 / 联动气泡」只在**首个可见窗**展示（此前每只桌宠各弹一遍）；动画仍全窗扇出（多只一起表演保持不变）。单窗体验不受影响。

## 🚨 重要声明 / 已知问题

- **系统通知功能仍未接入系统原生弹窗**
  - AI 对话的“对话完成 / 生成失败 / 需要授权”目前只在聊天界面不在前台时用**应用自绘的右下角提示气泡**提醒（点击可跳回会话或打开 AI 设置），**尚不能**弹出 Windows 操作中心 toast / macOS 通知中心 / Linux 桌面通知。
  - 原因：Windows 下 `QSystemTrayIcon.showMessage` 的原生气泡在托盘图标被系统收进“隐藏的图标”区域时可能不弹出（v4.1.0 评估后弃用该通道）；要真正进入 Windows 通知中心还需设置 AppUserModelID 并调用原生通知 API（WinRT），在 PyInstaller 冻结、多实例、绿色版与安装版并存下，打包身份与升级路径的验证成本高。macOS 系统通知需要用户授权且对签名打包的 `.app` 强依赖；Linux 桌面通知依赖各发行版 DBus/notify 服务，行为不一。
  - 三平台一致且可靠的原生通知列入后续待办；在此之前不把“系统通知”作为正式可用能力宣告。
- **其它已知限制**（详见 README）：安装包/安装器未签名（SmartScreen / Gatekeeper 需手动放行）；macOS 仅发布 Apple Silicon（arm64）；Linux 仅 x86_64 + 需少量系统库、建议 X11；GIF 变体不再发布。

---

## ✨ v4.1.0 之后的新内容（v4.2.0 增量）

### 🎏 桌宠本体玩法
- **黄金回旋**：右键菜单新增「黄金回旋」入口（旧版 legacy 菜单与默认 modern 菜单都有）；连点逐圈累计加速（700ms → ×0.82 → 200ms 下限）；没有点击素材时也不再“只抬手不启动”。
- **拖文件模拟吃掉**：把文件拖到桌宠身上会播放吃动画并记录统计（**不真实删除文件**）。
- **新动画素材**：同步上游 9 个动画（工作状态×6 + 碎碎念×3），本地动画总数 **97 → 106**。
- **托盘与多窗子菜单「回到右下角」**：会先取消探头姿态再移动，不再歪着被拖回去。

### 🕹️ 边缘交互、点击玩法与省电
- **边缘探头** `edge_probe_enabled`（设置 → 桌宠行为 → 边缘探头）：拖到屏幕左右边缘自动进入 ±45° 探头姿态（按旋转投影 bbox 计算露出量，常驻露出 0.55、点击拉直 0.82、数秒自动退回）；探头激活时点击不播点击音效。
- **撞飞彩蛋（被撞飞翻鱼头）**：探头激活状态下被撞，头部跟随速度方向整帧旋转（`90+atan2(vy,vx)`）；低速触碰边界/其它桌宠自动回正；阈值按实机反馈调到 780px/s；撞飞落地停稳 5s 后若静止在边缘会重新吸附（倒计时内拖拽作废）。
- **点击触发黄金回旋** `golden_spin_on_click` + 子开关 **点击回旋跳过动画** `golden_spin_direct`（设置 → 桌宠行为 → 点击反馈）：点击动画播完自动接回旋；开启直连后点击直接回旋、跳过 Q 弹/点击素材，再点 130ms 内收尾当前圈并立即开下一圈。
- **省电模式**（原「闲置降帧」升级，设置 → 桌宠行为 → 动画与移动）：闲置 30s+ 动画按半帧率呈现（24fps 素材 → 12fps 观感、时长不变）并停止后台动画预热，任何交互/Agent 忙碌立即回满。
- **音乐自动唱歌** `music_sing_enabled`：检测到后台播放音乐时自动播唱歌动画，检测 timer 按需启停。
- **拖拽合帧**：`mouseMoveEvent` 只记录最新目标、8ms 定时器消费（约 120Hz），碰撞提交维持 20Hz；拖拽/点击/菜单期间低优先级预热让路挂起。

### 💬 台词、气泡与提醒
- **气泡配图大小可调** `self_talk_image_scale`（50–300%，默认 100，设置 → 桌宠行为 → 自言自语）：标准气泡 220×140 目标框随缩放、呼吸气泡按 base_size；自言自语关闭时该行随从属行隐藏。
- **图片目录预览抽屉**：自言自语图片目录（桌宠行为）与彩蛋弹窗图片目录（外观 → 彩蛋入口）新增「预览」→ 右侧 3 列瀑布流缩略图（保留宽高比、512px 解码上限、文件名省略 + 全名 tooltip、空态文案、延迟解码）。
- **台词模板（JSON）导入 / 导出 / 复制**：一键导入弹窗文案模板；导出文档含 `sources`/`displayHint`/`upstream` 字段与脱敏隐私说明。
- **台词模板 v2 渲染能力**：预设台词支持 `{payload.嵌套字段}`、`{data.字段}`、`{questions[0][label]}`、`{count:02d}` 等占位符（未知/缺失字段原样保留，只遍历 mapping/list、不暴露任意对象属性）；预设台词不再展示原始 `{callId}`（用户看到“调用号是 call01oE3dtln…”没有信息量），占位符契约保留、自定义模板仍可用。
- **表达风格** `dialogue_mode`（设置 → 桌宠行为 → 表达风格）：内置「默认模式」「鲸鱼娘女仆模式」不可编辑，选「自定义台词」后可粘贴 JSON 导入。统一 Agent 联动相关提示气泡（状态/过程汇报/完成/审批/提问/错误/限流/卡住/循环检测）与余额气泡的说话方式；随机自言自语与点击台词在「桌宠行为 → 自言自语」中单独配置。
- **待办提醒**（PR #72，设置 → 桌宠行为 → 待办提醒）：右键菜单「待办」面板增删改（带矢量图标）；到期/提前提醒（`todo_reminder_lead_minutes` 可设，默认提前 5 分钟）、10 分钟宽限、一次性待办自动归档、错过宽限的提醒静默盖戳（醒来不轰炸）；桌宠可见时气泡、否则走桌面通知（由 `system_notifications_enabled` 总门控，默认开）；数据按实例落盘（`todo_items[-instance].json`，原子写）。
- **会话保存原子化/异步化**：`SessionStore.append_message(s)` 在全局 io 锁内“读→追加→提交”原子完成，modern/legacy/quick chat 三前端与「看看屏幕」问答同步全部走原子路径——生成回复期间其它前端写入不再被整会话保存覆盖；不开聊天窗的识屏问答也会原子落盘；写盘入串行后台 worker（读穿 pending、诚实 flush/close、关闭屏障防双 writer、`os.replace` 遇瞬时 WinError 5 有界退避重试）；Legacy 聊天时间戳改本地时区显示。

### 🎛️ 设置与菜单重构（PR #64）
- **七能力域侧栏**：常规（含 macOS Dock 设置）、桌宠、互动、菜单、桌面组件、AI 与对话、自动化与联动；菜单域内含三个同级任务用页内 Tab（菜单编排 / 快捷启动 / 外观）。
- **菜单编排（可配置右键菜单）**：左侧编辑、右侧实时预览。支持显隐勾选、移动到（子菜单/根）、新建子菜单、插入/删除分割线、更换别名（编辑器显示“别名（原名）”、运行时菜单只显别名）、图标覆盖（内置矢量 / none / 本地图片 PNG·JPG·WebP·BMP·GIF·TIFF ≤5MB，contain/cover 显示）、恢复默认名称/图标/布局、删除子菜单（二次确认，子项提升回根）。
  - 默认模板 `modern-default-v1.json` 内置显式分割线；配置缺失回默认、损坏/不支持的 schema 只保留「桌宠设置/退出」安全菜单；旧自定义树自动补入新默认 action（按最近模板兄弟锚点插入，不动现有顺序/显隐）。
  - 运行时能力判定：动作缺条件置灰 + tooltip；彩蛋关闭后节点保留原位显示「已停用」；无快捷应用时 quick_launch 置灰。
- **快捷启动**：设置内双行应用列表编辑（添加默认浏览器/选应用文件/移除）；右键菜单「快捷启动」子菜单无项时恒出现并显示禁用占位「尚未配置快捷项」。
- **外观/依赖显隐统一**：菜单颜色主题成为设置窗口显式主题来源（开关/下拉/chevron/弹层明暗沿祖先链读取 `settingsDark`，选主题即时整页生效）；ToggleSwitch 从属项统一「开显示/关隐藏」（灵动岛、彩蛋、碰撞、主动识屏、自言自语、半透明、Agent 音效等经审计）；彩蛋入口标题/提示文字继承菜单前景色、悬停取主题 `light_hover`/`dark_hover`（深色主题下不再不可读）。
- **三态响应式**：1600 / 900 / 720px 断点（wide / medium / compact），窄宽隐藏「位置」列；125% 字体 + 极端中英文案矩阵逐页验收。
- **macOS 原生 Dock 右键菜单**：显示桌宠 / 桌宠设置 / AI 对话 / 退出（`setAsDockMenu`），鼠标穿透后仍有稳定恢复入口。

### 👀 识屏与灵动岛
- **识屏自我识别提示**：看看屏幕与主动识屏的视觉请求会注入角色显示名（`pet_name`）——模型知道画面角落的桌宠就是自己的化身，以第一人称看待，不再把角色说成“陌生程序”；只加 user 文本，不污染自定义 system prompt。
- **可选服务开关式加载门控**：碰撞 IPC / 待办 / Agent 联动 / 主动识屏按配置懒创建，关闭即不装配、不启动、不拉线程与定时器；PIL 延后到截图时才导入。
- **灵动岛余额峰谷按系统时间刷新**：按北京时间档位直接显示「当前档位 → 下一档切换时间」，单发 QTimer 排到下一 9:00/12:00/14:00/18:00（或下周一 9:00）边界自动重排；峰谷颜色随档位生效。
- **灵动岛单击聚合全部窗**（多开时）。

### 🤖 Agent 联动与 DSH 生态
- **v4.2.0 之后的修复批**（2026-09，PR57 审计跟进）：桥接插件归零外部依赖——根治「打包副本缺依赖导致用户整个 dsh 插件树加载失败、web/headless/desktop 全 profile 无法启动」的事故（PR 门禁新增双层零依赖校验）；cordis 运行审批气泡按真实写盘形状读取（此前从未弹出过）；问题气泡在 mux 断线后可被兜底关闭（升级不再丢失 callId）；点「终止/自动优化」不再伴随「请更新/重装 bridge」假提醒；无 pnpm 时关闭联动改为备份并手改 manifest（不再假成功留悬空链接）；LLM API 错误（`llm_error`）真正进入 error 态；探索看门狗计时改任务级（启动宽限整个任务只送一次、连续运行降阈值真正生效），桌宠隐藏时看门狗暂停（隐藏期提醒恢复后补发而非永久丢失）；控制成功后给看门狗记宽限（不再刚点完又弹）；设置页新增「行为重复检测」组（开关 + 9 阈值）；移除一批引入后从未接线的死机制（事件分发层/Judge/宽限接口等）。
- **DSH 富事件状态**：thinking（思考）/ working（干活，带工具名）/ attention（需确认）/ error / idle 多态呈现；多会话聚合优先级 attention > error > working > thinking > idle，子代理不抢状态；`agent/status` 始终作为聚合基线被采纳（旧宿主与富事件并存时以最后到达的事件推进状态，并不存在「见过富事件后自动停用」的开关）。
- **统一事件层 / 事件队列**（PR #57）：bridge → monitor → AgentLinkManager → PetWindow 三层事件契约（`agent-event/v1`），定义审批/问题关联键、交互队列与气泡生命周期（见 `docs/DSH-BRIDGE-PET-EVENT-CONTRACT-2026-09-02.md`）。语义分类的 dataclass 保留，事件分发层（`AgentEventRuntime`）在实现后经评估移除——它自引入起零消费方，实际消费方只有 `AgentLinkManager._on_normalized_event` 直连 `normalized_event`。
- **审批与提问交互**：审批气泡带「同意 / 拒绝」按钮；提问气泡支持多问题项（section header + 选项多选 + 气泡内提交，全部带选项时在气泡内完成，含自由文本时提示回 DSH 界面）；阻塞交互按 `interaction_id` 独立存储，同一 agent 并发审批/提问互不覆盖；新增 cordis `request-run` 交互。交互/告警气泡（sticky、interactive、alert）主体点击为 no-op，只有普通无按钮气泡点主体才打开快速对话。
- **探索循环 Watchdog 与控制气泡**：风险分达到控制阈值时发常驻气泡（带「自动优化（replan） / 终止（interrupt） / 忽略」）；控制请求最长阻塞 30s，按钮回调只做“收气泡 + 起后台线程”，结果经 Qt 信号回主线程（GUI 线程不阻塞）；回执按相位/失败原因区分文案（成功 / 超时 / 会话不存在 / 被拒绝）；会话结束联动收起控制气泡；Watchdog 总开关关闭时不建线程、不订阅事件（零常驻开销）。
- **行为检测器与限流跟踪**：W6/W10 窗口告警可升级 control（升级路径跳过 step/time 门控并统计全量窗口）；429 限流按 session 独立计数并展示连续次数；stuck / pattern / exploration 三个检测器共享 30s 跨模块弹窗节流（同档抑制、越级放行）。
- **Harness 复用本机已有实例**：探测顺序改为配置端口优先、其次官方默认 3080——用户已自己跑着 dsh web 时直接复用打开，不再重复拉起第二个实例（关联 issue #10 双开浏览器）；菜单点击启动新实例时冒泡提示「正在后台启动」（首次 npx 拉包可能几分钟）。
- **「随桌宠启动 dsh 服务」** `harness_autostart`（默认关，设置 → 常规 → 应用启动，仅主桌宠可设置）：开机自启场景下主窗就绪即后台拉起 dsh web，只起服务、不开浏览器、不弹窗口。
- **dsh 启动链路静默化**：所有探测子进程隐藏窗口（`CREATE_NO_WINDOW`，去掉会弹常驻终端的组合）；`--no-open` 能力探测三级缓存（进程内 dict → 落盘 `harness_probe_cache.json` 按 `dsh --version` 匹配 → 慢探测），超时放宽到 30s。
- **macOS Node 解析**：新增桌面安全 Node 解析器（`pet/node_runtime.py`），Harness 与 Agent bridge 共用并增强 PATH 传播给 npm/pnpm。

### 🖥️ 多开、共享解码与子肥鱼
- **单进程多开（省内存）** `experimental_single_process_spawn`（设置 → 常规 → 多开，默认关，**重启生效**）：开启后「生小肥鱼」在本进程内创建第二个桌宠实例（独立 slot / 配置 / 会话 / 素材库），不再拉起新进程。
- **同角色共享解码链**：进程内 `DecodeFanoutHub`（帧扇出）替代早前的 shm broker——同角色多窗只保留 **1 条 ffmpeg 解码链**，其余窗口“订阅”进食（首发窗发布、订阅窗进食、handover 扶正、消费侧看门狗）。
- **进程级共享子系统 + 托盘聚合**（flag 开时）：Agent 联动/主动识屏/全屏探测上移到进程级共享，事件扇出到各可见窗；托盘聚合为「单托盘 + 每窗子菜单（显示/隐藏、切换角色、退出这只）」；DSH 插件授权弹窗只弹一次；主窗退出自动提升下一窗为主。
- **slot 配置作用域**：每窗独立项（形象/位置/聊天）存 `config-slot-N.json`；进程级共享项（托盘/共享解码/Agent 联动/待办）以主桌宠 `config.json` 为准。
- **新实例初始配置跟随主设置（slot 落种）**：新开小肥鱼首占某 slot 时用主配置落种（剔除位置/朝向等每窗状态键），已有存档一律不覆盖；可继承主大小或独立尺寸（`spawn_inherit_size`/`spawn_scale`）、继承灵动岛开关（`spawn_inherit_dynamic_island`）；设置里可「一键清除子肥鱼」。
- **子肥鱼生命周期**：退出子肥鱼**只退出不删数据**；运行时标记 v2（未拖动过的新生鱼也能被枚举）、两段式杀法（无控制台弹窗）、杀前用 exe 路径核验 pid 身份（防 pid 复用误杀）、`GetExitCodeProcess == STILL_ACTIVE` 判真死活、slot-0（主鱼）永不杀、单进程模式先按进程内登记表链式关窗再文件级清理（防 UI 冻结）。

### ⚡ 流畅度与性能（实测口径）
- **高刷屏流畅度（PR #70）**：物理/弹跳/拖拽节拍跟随主屏刷新率（165Hz→6ms、120Hz→8ms，≤90Hz 保持 16ms）；QTimer 改 PreciseTimer（Windows 粗定时器 16ms 在 15.6/31.2ms 抖动）；`moveEvent` DPR 兜底轮询限频 10Hz；新增 `PET_PERF_STATS` 观测模式（默认零开销）。
- **内存**：三开热机 361–402MB/只 → 多进程约 270MB/3 只；**单进程多窗 3 窗 181–197MB 且 3.5h 无单调上涨**（双窗 340MB vs 双进程 460MB）。
- **主进程慢涨根因修复**：每个播过的 clip 会永久持帧 ≈1.76MB/段（97 段 ≈170MB），改为切走/弃播/硬停即清空显示槽——15min 显示槽恒定 3.5MB（修前同口径 26MB 线性涨）。
- **解码**：ffmpeg 常驻循环解码（`-stream_loop -1 -readrate`）消灭“每 10s 杀进程重启”的 churn；`-threads 1`（实测每进程 −13MB）；圈边界定期回收 `ffmpeg_recycle_minutes`（默认 10min，0=关）。
- **帧转换链**：移除帧缓存改直接重建（无缓存重建实测每帧 1.13ms（1 倍缩放）、2.37ms（2 倍缩放），24fps 下代价可忽略），改为首帧 8MB 字节预算 + pinned 交互核（约 5MB）；**预测式首帧预热** `predict_prewarm_lead_ms`（默认 350ms，高级键）：当前动画剩余 ≤350ms 提前后台解码下一段首帧进 LRU，首帧 LRU 不逐出高频交互链（点击定格次数 8 → 1–2 次/局）。
- **省电模式**：解码 CPU −54.6%（台架 5.2% vs 11.5%）。
- **碰撞预测限频**：反弹预测每 tick 圆链扫描限 33Hz、状态上报先限流再建状态——多实例碰撞活跃期 CPU **61%→43%**。
- **其它**：窗口隐藏即停（暂停解码/定时器）；关闭全部音效时不再加载 QtMultimedia（约 38MB）；PIL 延后导入；Windows mask bounds 走 Qt C++ 路径（0.32ms/帧，与绘制逐像素一致）。
- **高级内存调节键**（README「内存调节（高级）」）：`first_frame_cache_max_mb`（默认 8）、`predict_prewarm_lead_ms`（默认 350）、`ffmpeg_recycle_minutes`（默认 10）。

### 🔐 安全与跨平台
- **API Key 明文自动迁移 keyring（PR #68）**：升级加载时把磁盘明文 API Key（含视觉 Key）迁入系统 keyring；keyring 不可用时回退内存明文，不覆盖已有 keyring 值。
- **Linux Fcitx 中文输入（PR #71）**：构建时按 PySide6 Qt 精确版本编译 Fcitx5 Qt6 输入法插件随包分发（内置 Qt 与系统插件 ABI 不兼容导致中文输入法失效），真实 Fcitx/Rime 探针验证；Linux 设置页不再创建 Windows 专属「光标隐藏穿透」开关。
- **macOS**：Dock 隐藏彻底生效并加恢复提示（issue #74，设置关闭「显示 Dock 图标」后真正隐藏 + 恢复路径提示气泡）；原生 Dock 快捷菜单；Finder 启动的 Node/Homebrew 解析（issue #67）。
- **直播捕获（OBS / 直播姬）兼容（#79）**：快速对话气泡并入主窗子内容、可点击、保持向上生成（自动申请透明头顶空间）；不再冒出空白小气泡。
- **Windows**：穿透切换改原生 `WS_EX_TRANSPARENT`，根治打字时桌宠频闪（见下方修复清单）。
- **DLC / 换角色场景加固（PR #94）**：为后续同路径替换素材换角色做准备——`PetWindow._switch` 增加中央存在性守卫（目标动画不在当前素材库时判失败返回，不再让 `lib.movie(name)` 的 KeyError 崩进 GUI 线程），一次性守住余额档位动画与音乐唱歌动画两条绕过资源池的直传路径；配置记住的角色素材目录缺失（如 DLC 卸载）时**回退默认角色**重试一次，不再直接弹错退出；点击台词绑定对话框改走 `catalog.resolve_character_video_dir`（外部 DLC 目录优先）并识别 webm/gif，不再只读内置目录、不再回退到与磁盘真实文件名已漂移的旧常量。

### ✨ 其它（含小项）
- 彩蛋入口深色主题下文字可读（标题/提示继承菜单前景色、悬停取主题色）。
- 点击音效预热时机收敛（仅在开关/音效包变化时预热），播放前显式 stop（不再无声）。
- icon.ico 随素材确定性重建（构建链）。
- 桌宠显隐 `[VIS]` 观测日志（频闪排查用）。
- 快速对话（QuickChat）：收到顶层 `WindowDeactivate` 自动关闭并复用原关闭路径停止在飞请求。

---

## 🐛 Bug 修复

### 桌面与交互
- **气泡行尾字被裁切（PR #96）**：联动长台词偶发“一行里有一个字被气泡遮住”——文本折行与 label 宽度此前用两套互不相干的度量（折行按整型 `horizontalAdvance` 累加、label 宽度按 `QFontMetrics.boundingRect(TextWordWrap)` 二次排版，两者可差一个字），label 比真实绘制的行窄时行尾那个字被切在边界上（女仆模式 111 条台词里 30 条命中）。现在 label 尺寸由“真正绘制的行”计算（`bubble_label_size` / `bubble_wrap_width`，不再二次排版），并在量文本前 `ensurePolished()` 保证度量与绘制同字体；气泡最大宽度不变，贴近边界时提早一个字换行。
- **打字时桌宠频闪（Windows，根治）**：全屏判定排除工具窗口与截图覆盖层（PixPin/Snipaste 等），穿透切换改原生 `WS_EX_TRANSPARENT`，不再触发 Qt flags 变更导致原生窗口销毁重建（每次重建=消失一瞬）。
- **快速连点 / 切动画卡顿**：GUI 线程绝不再 join 退役 reader（曾每帧等 0.5s）。
- **动画切换静默停滞**：启动失败显式降级 + 有限重试。
- **动画间隔语义**：恢复消融结论——gap 超时不再打断正在播的待机/转向，gap 期间异常 act 结束立即续链，不再出现“最长 `animation_gap_seconds` 停帧”。
- **边缘探头被撞后会话悬空**：撞击前显式 cancel 会话；拖到右下角不再带着探头姿态。
- **打字机/点击音效无声**：播放前显式 stop，预热时机收敛。
- **全屏隐藏误判**：工具窗口/输入法候选框不再被当作全屏；截图覆盖层进程级排除。
- **显示与缩放**：跨 DPI 屏/系统缩放变化后画面即时重建（Qt 信号驱动）；squash 期间命中 mask 重建限频（收势帧强制同步）；素材原地替换后不再残留旧帧。
- **WebM 播放**：僵尸 reader / ffmpeg 进程泄漏根因修复（`destroyed` 槽不触发自身 bound-method：无 receiver callable + cleanup 显式断开）；stop 主动 terminate + kill 兜底；Popen 生命周期由 reader 线程独占；播放队列满时等待主线程消费保时间轴；cleanup 等待 reader 线程退出；fan-out 圈末自然解散 + abort reason 透传。

### Agent 联动与 DSH
- **点“终止”没用**：主 agent 派后台子代理并行干活，用户点终止后 bridge 只停了子代理、主 agent 随后重派新子代理继续。现在 interrupt/replan 归一到根 agent 的当前回合；父级不可解析时如实回执「已终止子代理（主代理仍在运行，可能重新派发）」，不谎称整个会话已停。
- **提问气泡永久占住提醒队列**：`question/resolved` 的 callId 取数只看 `message.callId`，而当前 DSH 版本该字段挂在 `message.source.callId` → resolved 帧永远写不出；已与 `toolResultInfo` 对齐取数路径。
- **审批被静默吞掉**：审批去重降级键 `ap:tc:<tool>|<command>` 原先无条件加入，同会话 8 秒内两个不同审批（同一命令）会被静默吞掉；改为仅在没有 approvalId/rpcId 时才生成降级键并拼入 sessionId。
- **提醒队列卡死**：设置窗打开会隐藏当前气泡，恢复时原逻辑只处理“存活的 sticky 项”和“队列空”两种情形，非 sticky 普通提醒被隐藏后既没恢复也没出队，队列就此卡死（后续提醒含审批永久不再展示）；现在恢复时正确结束它并推进下一条。
- **审批气泡恢复后没有同意/拒绝按钮**：全屏隐藏→恢复路径漏传 `_sticky_buttons`（字段一直在正常存取，唯独这条路径漏了）。
- **单进程多窗下审批按钮一直丢失**：`MultiWindowProxy`（共享管理器看到的“窗集合替身”）此前没有 `show_alert`/`resolve_alert`，导致 `agent_link` 里所有 `hasattr(win, "show_alert")` 判断都落进降级分支；补上后审批与控制气泡按钮全部恢复。
- **首次告警必抛 `AttributeError`**：`exploration_watchdog` 的 payload `mode` 字段全仓无赋值、无消费方，首次触发告警必抛异常导致告警永远发不出（该模块此前零测试，本次补齐）——直接删除该字段而非补默认值。
- **429 与 `execution/failed` 双提醒**：429 气泡展示 15s > 合并冷却 8s，`turn/end` 的 `execution/failed` 常在 8–15 秒窗口到达而绕过旧抑制；收紧为“存在未 dismiss 的活跃 429 即抑制”，dismiss 后新失败仍正常提醒。
- **`dsh_control` 每次请求必抛 TypeError**：`_log_event` 形参 `directory` 与调用方传入的同名字段撞名（零调用方所以从未暴露），不修则整条控制链点了不生效。
- **opencode 假完成**：`step-finish` reason=`tool-calls`（模型停笔等工具结果）不再误报完成——治好长跑 task 子代理/慢工具（长 bash、dev server）的假完成音/气泡与回注后的假开始音。
- **诊断经常失败**：`maxTokens` 700 → 2048（推理型模型先消耗 token 在 reasoning 上，700 预算常被吃光、正文一个字没出），空输出时打印 chunk 类型统计便于定位。
- **插件无法激活 / 气泡只有 session id**：apiProxy 移出 `inject` 强依赖（当前 dsh 发布版没有此服务，强依赖会导致插件根本无法激活），改为可选读取；缺失时读 `~/.dsh/storages/session_projcache` 本地会话缓存兜底，气泡显示真实会话名。
- **dsh 开机自启弹浏览器 / 常驻空终端窗口（issue #10 根因）**：`--no-open` 探测落盘缓存 + 探测窗口隐藏 + 超时放宽。
- **macOS Finder 启动绕过 Homebrew Node 发现（issue #67）**：新增桌面安全 Node 解析器。
- **联动气泡让位门禁时钟域错误**：`_show_link_bubble` 用 `time.time()`（epoch）比较 `hold_bubble` 写入的 `time.monotonic()`，真实桌宠上恒判“未被占用”——普通气泡会顶掉识屏占位、重要气泡不排队直接覆盖；已统一为 `time.monotonic()`。
- **检测器连环换弹**：stuck / pattern / exploration 三个检测器此前各自 cooldown 独立，同一 busy 周期可能先后弹窗；现在同 agent/session 30s 窗口内已有任一检测器弹窗即抑制（动画照常），升级档位放行。
- **`closeEvent` 后的延迟路径抛错**：置空 `_speech_bubble` 后，托盘菜单 `aboutToShow`、`refresh_pet_settings`、`pause/resume` 等延迟路径触碰会抛错，已加 None 守卫。
- **“表达风格控制所有气泡”属过度承诺**：实际只驱动联动气泡 + 余额气泡（随机自言自语/点击台词单独配置），设置页与模板导出文案已改为准确范围。

### 子肥鱼与多开
- **子肥鱼杀不掉**：`GetExitCodeProcess == STILL_ACTIVE` 判定真死活（修探活误判）。
- **pid 复用误杀**：杀进程前用 exe 路径核验 pid 身份。
- **控制台弹窗**：`CREATE_NO_WINDOW` 无弹窗。
- **旧 glob 清不到 / 未拖动过的新生鱼枚举不到**：运行时标记 v2（`pet-runtime-v2-*`，showEvent 即登记）。
- **覆盖用户存档**：移除会强制重播种顶掉用户设置的路径，已有存档一律不覆盖。
- **POSIX 多开选举死循环（issue #42）**：协调者被杀后残留 Unix socket 导致重选死循环——改为先探测活监听者、无人应答再清理残留并重试 `listen`；`_accept_connection` bytesAvailable 兜底。

### 会话与文件
- **生成期间整会话保存覆盖其它前端写入**：会话保存改为原子追加（三前端 + 识屏问答全覆盖）。
- **删除当前会话后幻影消息写入新会话**：删除前先停打字机（modern + legacy）。
- **瞬时文件锁（WinError 5）**：`os.replace` 有界退避重试（Defender/索引器占用）。
- **Legacy 聊天时间未本地化**。
- 快速对话唤出后 macOS 残留空白窗。

### Linux / macOS
- **Dock 隐藏不彻底（issue #74）** + 恢复提示。
- **Linux 文本框无法用 Fcitx/Rime 输入中文（PR #71）**。
- **macOS 菜单深色主题下文字不可读**（彩蛋入口、hover）。

### 升级与配置
- **老版本磁盘明文 Key 在升级后聊天/视觉静默 401（PR #68）**：改为自动迁移 keyring。
- **死键 `animation_prewarm_enabled`**：该键已在后续版本删除（全仓零消费）；本次把误加回默认值/reload 白名单/schema 快照的三处登记同步删除，配置里残留的值直接忽略。
- **`set_policy` 部分字典误判开关变更**。
- **余额线程创建失败遗留 busy 状态**；余额成功/失败结果在有高优先级提醒或气泡抑制期间改走提醒队列，不覆盖当前提醒；余额错误按 HTTP 码 / 超时 / 网络失败 / JSON 无效分别提示。

### 健壮性批次（17+ 项）
畸形碰撞消息不抛异常、更新检查线程收口 + 重入防护、`>7 天 pet-*.log` 启动清理、SSE 空心跳行跳过、设置 Esc 关闭也落盘、图标解码 30s 超时逃生、菜单树释放 3s 总上限、`shiboken6.isValid` 守卫消 RuntimeWarning、vision 截图 DWM 调用加平台守卫（避免非 Windows 崩溃）、`dsh_state` 目录变更才重扫（支持 `scan_interval=0` 测试注入）、设置窗抑制恢复推进提醒队列、`_respond_interaction` 交互身份门禁等。

---

## 🧪 测试与工程汇总

- **架构治理**：`window.py` 4307 → 约 3900 行（行数预算 + 拆分公约 `docs/WINDOW_PY_SPLIT_GUIDE.md`）；`PetApp` 拆为 `AppShell`（进程级）+ `PetInstance`（每窗容器），为单进程多窗铺路；拆出 collision_client / platform_win / platform_mac / multi_window_shared / decode_fanout / predictive_prewarm / settings_widgets 等模块；`modern_settings_dialog.py` 4811 → 1857 行后继续清理孤儿簇（净 −1300+ 行死代码）。
- **架构红线机器化**：`tests/test_architecture.py`（纯逻辑层不依赖 Qt、解码链单向依赖、`PetWindow` 私有面冻结、行数预算、孤儿簇守卫）。
- **配置键纪律**：普通顶层键三处登记（默认值 + reload 白名单 + schema 快照），特例键走迁移路径。
- **CI**：三平台 PR 门禁（pytest offscreen + ruff）；时序 flake 家族隔离（webm 生命周期族、低优预热让路族等）并按需一次重跑；CI 成本纪律写入 `AGENTS.md`；修复 main 上 Windows/macOS 的原生崩溃（`parent=None` 的 manager 被循环 GC 在 worker 线程回收 → 跨线程删除带 QTimer/信号连接的 QObject 腐化 Qt 事件队列；现在 shutdown 过继给 QApplication + 停自带单发定时器 + 控制 worker 可取消，并新增 `TestManagerDeterministicTeardown` 回归）。
- **构建**：`scripts/fix_bridge_bundle.py`（打包产物的桥接零依赖防线：剥 node_modules 残留 + dist 清单零依赖校验 + hermetic 冒烟；桥接插件已归零外部依赖，pnpm junction 展开与 lockfile 快照校验随依赖清零移除）、`scripts/verify_bundle_qt.py`（Qt DLL 链校验）、中文编码自检、Qt/ICU DLL 冲突自检；onedir 构建 + 便携 zip 一条命令。PR 门禁新增桥接零依赖双层校验（全部 bridge 契约测试 + hermetic 冒烟）。

## 🙏 致谢

感谢本版所有贡献者与上游：

- [klxxya](https://github.com/klxxya)（PR #65/#70/#76/#80/#82/#85/#91/#93/#94，性能/结构/内存与多开主线、事件层遗留修复、CI 原生崩溃修复、DLC 加固）
- [Daliuq](https://github.com/Daliuq)（#57 DSH 事件层、事件队列与探索循环 Watchdog）
- [ushio2026-alt](https://github.com/ushio2026-alt)（#64 设置与菜单重构）
- [shinelon](https://github.com/shinelon)（#72 待办提醒）
- [MYming-yue](https://github.com/MYming-yue)（#68 keyring 迁移）
- [0x18d](https://github.com/0x18d)（#71 Linux Fcitx）
- 以及上游 [PC2005-cloud/dsh-pet](https://github.com/PC2005-cloud/dsh-pet) 的素材与实现基础。
