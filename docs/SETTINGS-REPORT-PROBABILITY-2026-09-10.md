# 设置变更记录：事件汇报概率门 report_gates（2026-09-10）

按 `docs/SETTINGS-CHANGE-GATES.md` 记录准入契约与准出证据。本次变更类型：**新增设置组 +
替换既有开关模型 + 调整既有默认值**（不引入平台分支）。

本文件的前身版本把事件汇报记为一个「过程汇报概率」数值项（`report_probability`）。
用户 2026-09-10 定稿后**该模型被整体替换**为「按事件聚合类别的概率门」，旧键已一次性
迁移并删除（不留兼容别名）。下文即当前口径。

## 1. 准入契约

| 字段 | 内容 |
|---|---|
| setting_id | `agent_link.report_gates.<gate>`（8 个门，见 §2 门表） |
| domain_id / group_id | `agent_link` / 设置页「自动化与联动 → 事件气泡触发概率」 |
| title | 汇报概率（每类事件的通过概率） |
| description | 这一类气泡的通过概率。0.00 = 该类完全不汇报（静音），1.00 = 每次都汇报，中间值按概率抽稀。概率只作用于「出气泡」这一步，卡住 / 行为重复 / 循环等检测本身不受影响；右键菜单只提供 0/1 两端快捷入口。 |
| search_aliases | 汇报概率、事件汇报、概率门、抽稀、静音、report_gates、提醒频率 |
| default | 见 §2 门表（7 类 1.00，过程汇报 0.60） |
| capability_requirement / platform_availability | 无（纯配置项，不依赖音频/托盘/IPC，全平台一致） |
| disclosure_level | 一级：折叠框「事件气泡触发概率」**默认展开**，8 个分类子分组与滑块直接可见；该折叠框排在「文案风格与模板」分区**末尾** |
| dependency | 逻辑依赖 `agent_link` 的对应 Agent 联动开关；概率门额外作用于出气泡那一步，与三重节流（同 agent 10s / 全局 8s / 同工具 60s）叠加 |
| preview_target | 无独立预览（概率不产生可视化产物）；改动即时生效，无需重启 |
| commit_policy | 保存即落盘（与其他 agent_link 数值项一致） |
| migration | 旧键一次性迁移：5 个 `notify_*` 布尔开关 → 1.0/0.0，旧百分比 `report_probability`(0-100) → 过程汇报概率。迁移后**不再写出旧键** |
| recovery | 设置页滑块可随时改回；`0.00` 与 `1.00` 均为可逆端点值；旧键迁移是单向的（旧键不再回写） |

## 2. 门表（8 类事件聚合）

门名即配置键名，顺序即设置页展示顺序（`pet/report_gates.py::REPORT_GATE_KEYS`）。

| # | 门名 | 中文标签 | 默认 | 覆盖的事件键 |
|---|---|---|---|---|
| 1 | `state` | 状态提醒（开始干活 / 思考） | 1.00 | `start`、`thinking` |
| 2 | `activity` | 过程汇报（读文件 / 跑命令 / 改代码…） | **0.60** | `activity.*` |
| 3 | `approval` | 审批与提问（需要你处理） | 1.00 | `approval.*`、`question.*`、`agent.attention` |
| 4 | `done` | 任务完成 | 1.00 | `done.*` |
| 5 | `exec_failed` | 失败与错误 | 1.00 | `failure.*`、`agent.error` |
| 6 | `model_access` | 模型访问失败 | 1.00 | `model_access.*`、`llm_error.*` |
| 7 | `stuck` | 检测类提醒（卡住 / 行为重复 / 循环） | 1.00 | `stuck.*`、`pattern.*`、`watchdog.*` |
| 8 | `bridge` | 桥接、写回与查询 | 1.00 | `bridge.*`、`balance.*`、`dsh.writeback.*`、`agent.missing` |

**默认展开 / 滑块唯一控制项 / 菜单只给 0/1** —— 本次定稿的三条交互口径：

1. **默认展开**：折叠框 `CollapsibleGroup` 用 `set_expanded(True)` 构建。这些文案行改造前
   就在该页可见，折叠框只提供「可以收起来」，不把原有入口藏起来；搜索命中折叠框内的行时
   也会自动展开（`_search_settings` 沿祖先链找 `CollapsibleGroup` 并 `set_expanded(True)`），
   否则「搜到了却看不见」。
2. **滑块唯一控制项**：`ProbabilitySlider`（0.00–1.00，20 档 × 0.05）是该类概率的**唯一**
   细粒度控制项，没有布尔开关。滑块与该类的气泡文案行同组（每个门一个分类子分组），
   让「设置位置」与「真正控制的位置」绑定。
3. **菜单只给 0/1**：右键菜单「Agent 联动」子菜单只提供 3 个高频类的两端快捷入口
   （开始干活 / 任务完成 / 过程汇报）——勾选 = 1.0 全报、取消 = 0.0 静音；勾选态按当前
   概率是否 `> 0` 呈现，tooltip 提示当前值并指向设置页。细粒度概率一律回设置页调。

## 3. 清洗与边界

`pet/config.py::_clean_agent_link_data` → `pet/report_gates.py::clean_report_gates`：

- 未给的门 → 该门默认值（`REPORT_GATE_DEFAULTS`）；
- 未知门名 → 丢弃（防止拼写错误静默生效）；
- **无法解析的值（`None` / `"abc"`）→ 回落该门默认值**——注意 activity 的默认是 `0.60`，
  不是 1.0；
- 数值字符串 `"0.75"` → 0.75；
- 越界 → 收敛到 `[0, 1]`（`-0.5`→0.0、`1.5`→1.0）；
- 布尔**不是特例**：`True`→`1.0`、`False`→`0.0`（这正是旧开关迁移到概率端点的机制）。

旧键迁移（仅当新形状 `report_gates` 未给出时生效，新旧并存以新形状为准）：

```python
result["report_gates"] = clean_report_gates(raw.get("report_gates"))
if not isinstance(raw.get("report_gates"), dict):
    for legacy_key, gate in LEGACY_SWITCH_GATES.items():   # notify_state/activity/approval/done/exec_failed
        if legacy_key in raw:
            gates[gate] = 1.0 if bool(raw[legacy_key]) else 0.0
    for legacy_key, gate in LEGACY_PERCENT_GATES.items():  # report_probability(0-100) → activity
        if legacy_key in raw:
            percent = _float_or_default(raw.get(legacy_key), REPORT_GATE_DEFAULTS[gate] * 100.0, 0.0, 100.0)
            gates[gate] = min(1.0, max(0.0, percent / 100.0))
# 迁移后旧键一律从结果里 pop 掉，配置里不留兼容别名
```

## 4. 判决语义（为什么放在这里）

`pet/report_gates.py::should_report(probability, roll) → roll < probability`（边界取「小于」）

- 量纲已随概率门统一为 **0.0–1.0**（旧版是 0-100 百分比）：`0.0` 永不汇报、`1.0` 全报；
  `0.6` 时 `roll = 0.6` 不汇报。
- 统一入口是 `AgentLinkManager._report_allowed(agent_cfg, event_key)`，经
  `should_report_event(gates, event_key, roll)` 按事件键找到所属门再判决。
- **未知事件（没有归属门）不抽稀，直接放行**——新事件上线时不会被静默丢弃。
- 只作用于**出气泡**这一步：检测器本身（卡住检测 / 行为识别 / 探索看门狗）与
  `raw_record` 数据链路不经过概率门，检测能力不受影响（`stuck` 门关闭时卡住动画照播，
  只是不弹气泡）。
- `model_access` 门的判决放在**记账之前**：被抽稀掉的一次不写 `_model_access_cache`，
  否则「关掉模型访问失败提醒」会连带压掉随后的通用失败横幅（缓存里的活跃提醒会触发
  抑制分支，用户看到的是一类静音把另一类也吞了）。
- `rng` 可注入（`AgentLinkManager(..., rng=...)`），供测试与驱动使用确定序列；
  否则默认 60% 过程汇报抽样会使默认路径的用例时而弹时而静默。

## 5. 准出证据

| 项 | 证据 |
|---|---|
| 默认值 | `tests/test_report_gates.py::test_default_report_gates_are_probabilities`（8 门齐备、区间合法、activity=0.6、其余 1.0）；`tests/test_agent_link.py::TestAgentLinkManager::test_default_all_disabled` 断言完整默认字典 |
| 门表与事件映射 | `test_gate_for_event_maps_every_dialogue_key`（每个气泡文案键都有归属门，不留无门事件）、`test_gate_for_event_examples`（17 例映射） |
| 清洗与边界 | `test_clean_report_gates_clamps_and_falls_back` 10 例（含 `"abc"`/`None` → 0.6、`True`→1.0、`False`→0.0）、`test_clean_report_gates_ignores_unknown_gate_names`、`test_clean_report_gates_keeps_other_gates_when_one_is_partial` |
| 判决 | `test_should_report_uses_unit_probability` 10 例（含 `0.6/0.6` 边界取小于） |
| 迁移 | `test_legacy_switches_migrate_to_probability`（5 开关 × 2 值）、`test_legacy_report_probability_percent_migrates_to_activity`、`test_new_shape_wins_over_legacy_on_conflict`、`test_default_ships_no_legacy_switch_keys` |
| 各类门实际生效 | `ptmp-gate-drive.py` 40/0 —— 8 个门逐个「关=静音 / 开=必报」，含 activity 0.60 的 roll 0.59/0.60 边界、未知事件放行、model_access 门关时不记账 |
| 设置页入口 | `ptmp-ui-entry-drive.py` 60/0 —— 8 滑块存在且默认正确、折叠框默认展开 + 8 个分类子分组、搜索命中自动展开、保存→落盘→重载、菜单 0/1 写入 |
| 持久化往返 | `ptmp-ui-entry-drive.py`：滑块改值 → `_save()` → 重新加载 Config 一致；重开设置页读回一致 |
| 越界收敛 | `ptmp-ui-entry-drive.py`：手写 `99.0`→1.0、`-5.0`→0.0 |
| 菜单只写两端值 | `ptmp-ui-entry-drive.py`：勾选写入 1.0、取消写入 0.0，落盘不含旧 `notify_*` 键 |
| 保存策略 | 保存即落盘，与同组数值项一致 |
| 恢复 | 滑块可随时改回；无破坏性写入 |
| 跨平台 | 无平台分支；仅在 Windows 做真实 GUI 构建（两个驱动均 offscreen） |
| 视觉与文档 | 本文件；`CONTEXT.md` 已同步 |

复跑命令（本地已执行）：

```
python -m ruff check pet tests
python -m pytest tests/test_report_gates.py tests/test_config_domains.py -q
python -m pytest tests/test_agent_link.py -q
python ptmp-gate-drive.py        # 40 通过 / 0 失败
python ptmp-ui-entry-drive.py    # 60 通过 / 0 失败
```

## 6. 未完成项

1. 设置页门禁的布局/可访问性三档（紧凑/常规/宽屏无裁切、字体放大、Tab 顺序、明暗主题）
   与代表截图未做；跨平台仅 Windows + offscreen。
2. 全量 pytest 由用户执行（沙箱无法跑 `tmp_path` 用例，仅 `test_config_domains.py` 的部分
   用例因此报环境 ERROR）。

## 7. 标识符决策（原遗留问题 4）

**结论：不改名。** 用户可见语义已统一为「模型访问失败」，但以下标识符保持原样：

| 标识符 | 位置 | 保持不变的理由 |
|---|---|---|
| `model_access.one` / `model_access.many` | 人格文案词表键（`pet/persona_template.py`、`pet/persona_presets/*.json`） | **已改名**（原 `rate_limit.*`）。名字按语义走，不留状态码痕迹；用户已保存的自定义文案由加载期迁移兜底（`Config._migrate_dialogue_phrase_fields`）。 |
| 线协议事件名 `model_access` | 桥接写出 → pet 消费 | **已改名**（原 `rate_limit`）。桥接与 pet 同仓同版本发布（`integrations/dsh-pet-bridge/index.js` 写出、`pet/agent_event_normalizer.py` 归一），两端一起改；上游真实状态码（`429`/`RATE_LIMIT` 等）**作为数据字面量保留**，否则识别不出真实错误。 |
| `alert_id` 前缀 `429-rate-limit:` | 运行期内部标识 | 不落盘、不对用户展示，改名无收益 |
| 旧键 `notify_*` / `report_probability` | 历史配置字段 | **已删除**：一次性迁移到 `report_gates` 后不再写出，也不做双写（见 `docs/SETTINGS-CHANGE-GATES.md` 的迁移纪律）。 |

若后续确实要改键名，按第二刀既有纪律做「换新名 + config 一次性迁移（不留兼容别名）」，
并在 `docs/PERSONA-PHRASES-PRESET-STORAGE-2026-09-08.md` 登记。
