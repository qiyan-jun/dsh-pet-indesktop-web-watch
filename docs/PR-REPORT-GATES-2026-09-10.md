# PR：事件汇报统一为概率门（report_gates）

**一句话**：把「事件汇报控制」从**5 个布尔开关 + 1 个整型百分比**换成**按事件聚合类别的 8 个概率门**
（0.00–1.00，无开关），设置页用一个**默认展开**的可折叠框按类分组收纳（每类「概率滑块 + 该类气泡文案」同组），
右键菜单只给 0/1 两端快捷入口；旧键一次性迁移并删除，不留兼容别名。

- 分支：`fix-post-merge-bug-clean`
- 日期：2026-09-10
- 关联文档：`docs/SETTINGS-REPORT-PROBABILITY-2026-09-10.md`（设置门禁准入契约与准出证据）、`docs/SETTINGS-CHANGE-GATES.md`

---

## 1. 背景与用户口径（2026-09-10 定稿）

改造前的问题：

1. **粒度错**：只有「过程汇报」一类有频率控制（`report_probability` 0-100），其余类别只能全开或全关。
2. **模型混**：布尔开关（`notify_state` / `notify_activity` / `notify_approval` / `notify_done` / `notify_exec_failed`）
   和百分比是两套语义，`notify_activity=false` 与 `report_probability=0` 表达同一件事，取交集判定。
3. **位置散**：概率项与它真正控制的文案行不在同一处，用户找不到「这条气泡我该去哪儿调频」。

用户定稿口径：

- 全部事件汇报控制统一为**概率门**：值是**通过概率** `0.00–1.00`，`0` = 该类完全不汇报，`1` = 全部汇报；
  **不再有布尔开关**。
- 门按**事件聚合类别**划分，设置页把它们收进「自动化与联动 → 事件气泡触发概率」下的一个可折叠框，
  与**该类**的文案行同组（位置绑定）；该折叠框排在「文案风格与模板」分区**末尾**。
- 概率只作用在**出气泡的汇报路径**；检测器本身（卡住检测 / 行为识别 / 探索看门狗）与 `raw_record`
  数据链路**不经过**概率门。
- 三条交互口径：**默认展开**、**滑块是唯一细粒度控制项**、**菜单只给 0/1**。

---

## 1.5 同批纳入的事件改名（`rate_limit` → `model_access`）

本次提交同时完成 **`rate_limit` → `model_access`** 的事件语义改名。**它与概率门是同一批文件里的
双向依赖，无法拆成两个可运行的提交**，因此合并为一次提交：

| 依赖方向 | 说明 |
|---|---|
| 概率门 → 改名 | `pet/agent_link.py` 的模型访问汇报路径按**新**预设键 `model_access.one/many` 取文案；预设文件不改名则文案回落成兜底句 |
| 概率门 → 改名 | 同上文件 `from .model_access_tracker import ModelAccessTracker`；模块/类不改名则**提交点直接 ImportError** |
| 改名 → 概率门 | 改名后的 `model_access` 事件类正是第 6 个门（`model_access`）控制的类别 |

改名范围（用户可见文案保持「模型访问失败」语义；429 等服务端限流码只作为**数据字面量**参与匹配，
不体现在命名上）：

- `pet/rate_limit_tracker.py` → `pet/model_access_tracker.py`；
  `RateLimitTracker` → `ModelAccessTracker`；`_RATE_CODES` → `_MODEL_ACCESS_CODES`；
  `is_rate_limit()` → `is_model_access()`。
- 预设键 `rate_limit.one/many` → `model_access.one/many`（`persona_presets/legacy.json`、`whale_maid.json`）。
- `pet/persona_template.py` 字段名、`pet/agent_event_normalizer.py` 规范名、
  `integrations/dsh-pet-bridge/index.js` 桥接侧事件名，以及三份相关文档。
- 对应测试：`test_persona_settings.py`、`test_persona_template.py`、`test_proactive.py`、
  `test_semantic_event_field_contract.py`、`test_settings_and_resources.py`。

> 这条依赖是**提交点自检**发现的：把改名文件排除在提交外时，干净检出 HEAD 会
> 连 `import pet.agent_link` 都失败（11 个契约用例红）——见 §7「提交点自检」。

---

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

除过程汇报默认 0.60（提醒量最大，默认抽稀）外，其余 7 类默认 1.00，**默认行为与改造前一致**。

---

## 3. 关键设计决策

### 3.1 判决语义：`roll < probability`，边界取「小于」

```python
# pet/report_gates.py
def should_report(probability: float, roll: float) -> bool:
    return float(roll) < float(probability)
```

- 量纲统一 0.0–1.0（旧版是 0-100 百分比）；`0.6` 时 `roll = 0.6` **不**汇报。
- `roll` 来自 `AgentLinkManager._rng()`，**可注入**（`AgentLinkManager(..., rng=...)`）：
  否则默认 60% 抽稀会让默认路径的用例时而弹、时而静默（不确定来自随机，不是被测行为）。

### 3.2 未知事件不抽稀（新事件不被静默丢弃）

`should_report_event` 对**没有归属门**的事件键直接放行。新增事件键上线时不会因为「没人给它配门」
而被静默吃掉；已归属的事件键仍受门控制。

### 3.3 门只管「出气泡」，不管检测

概率判决放在**出气泡**那一步。卡住检测 / 行为识别 / 探索看门狗照常运行，`raw_record` 链路完全不经概率门。
因此 `stuck` 门为 0 时：卡住**动画照播**，只是不弹提醒气泡。驱动对此有专项断言。

### 3.4 `model_access` 门的判决放在**记账之前**

```python
# pet/agent_link.py::_on_model_access
# 汇报概率门放在**记账之前**：被抽稀掉的一次不应写入 _model_access_cache，
# 否则「关掉模型访问失败提醒」会连带压掉随后的通用失败横幅，用户看到的
# 是一类静音把另一类也吞了。
if not self._report_allowed(self.cfg.get("agent_link", {}), "model_access.one"):
    return
```

缓存里存在活跃提醒会触发「抑制通用失败横幅」分支，所以被抽稀的一次必须不记账。

### 3.5 过程汇报：抽稀丢弃**不记账**

三重节流（同 agent 10s / 全局 8s / 同工具 60s）在**前**，概率门在**后**；门判决失败时不更新
`_last_activity` / `_activity_global_last`。否则概率会与节流**叠加衰减**，把过程汇报压得比设定值还低。
驱动用「时钟不前进也能汇报」验证这一点。

### 3.6 清洗：布尔不是特例，无法解析回落**该门默认**

```python
# pet/report_gates.py::clean_report_gates
try:
    number = float(value)
except (TypeError, ValueError):
    result[key] = REPORT_GATE_DEFAULTS[key]   # 回落该门默认
    continue
result[key] = min(1.0, max(0.0, number))
```

- **刻意不做 `isinstance(value, bool)` 特判**：`True` → `1.0`、`False` → `0.0`，
  这正是旧开关迁移到概率端点的机制，特判反而会把它按默认值丢掉。
- `"abc"` / `None` → **该门默认值**。注意 `activity` 的默认是 `0.60`，**不是 1.0**——
  这一点由契约测试的参数表锁死（`("abc", 0.6)`、`(None, 0.6)`）。
- 未知门名丢弃（防止拼写错误静默生效）；越界收敛到 `[0, 1]`；数值字符串可解析。

### 3.7 设置页：默认展开 + 滑块唯一控制项 + 菜单只给 0/1

- **默认展开**：`gates_box.set_expanded(True)`。这些文案行改造前就在该页可见，折叠框只提供
  「可以收起来」，不把原有入口藏起来。搜索命中折叠框内的行时 `_search_settings` 沿祖先链找到
  `CollapsibleGroup` 并自动展开（否则「搜到了却看不见」）。
- **滑块唯一控制项**：`ProbabilitySlider`（0.00–1.00，20 档 × 0.05，无障碍名带类别）是该类概率的
  唯一细粒度控制项，没有开关。
- **菜单只给 0/1**：右键菜单「Agent 联动」只给 3 个高频类（开始干活 / 任务完成 / 过程汇报）的
  两端快捷入口——勾选 = `1.0` 全报、取消 = `0.0` 静音；勾选态按当前概率是否 `> 0` 呈现，
  tooltip 提示当前值并指向设置页。写入的是 `report_gates`，**不再产生旧的 `notify_*` 平铺键**。

---

## 4. 迁移策略（一次性，不留兼容别名）

`pet/config.py::_clean_agent_link_data`：

| 旧键 | 迁移到 | 规则 |
|---|---|---|
| `notify_state` | `report_gates.state` | `True` → 1.0，`False` → 0.0 |
| `notify_activity` | `report_gates.activity` | 同上 |
| `notify_approval` | `report_gates.approval` | 同上 |
| `notify_done` | `report_gates.done` | 同上 |
| `notify_exec_failed` | `report_gates.exec_failed` | 同上 |
| `report_probability`（0-100） | `report_gates.activity` | `percent / 100.0`，越界收敛 |

- 仅在**新形状 `report_gates` 未给出**时迁移；新旧并存**以新形状为准**（`test_new_shape_wins_over_legacy_on_conflict`）。
- 迁移后旧键一律 `pop` 掉，配置里**不留兼容别名、不做双写**。
- `stuck_detect` / `pattern_detect` **不是门**：它们是独立布尔设置，保持原样。

---

## 5. 顺带修复的缺陷

**`dialogue_*` 中不归属任何事件门的行会从设置页消失。**

设置页重建时 `claim_prefix("dialogue_")` 把全部 `dialogue_*` 行认领进概率门分组，但分组是按
`gate_for_event(event_key)` 分桶的：**表达风格**（`dialogue_mode`）、**专属文案对象**（`dialogue_scope`）、
**弹窗文案模板 JSON**（`dialogue_template_actions`）这三行不是「某个事件的气泡文案」，
`gate_for_event` 返回 `None`，于是落进空串桶——**既被认领（不再进 leftovers）又没人放回页面**，
结果从设置页彻底消失。

修复：把空串桶的行显式放回本域，作为「文案风格与模板」分区成组展示；「事件气泡触发概率」折叠框
排在该分区**末尾**（`pet/modern_settings_dialog.py`）。

该缺陷由全量套件捕获（`test_express_style_rows_move_to_agent_domain`、
`test_modern_settings_panel_uses_sidebar_and_includes_ai_settings`），两例在修复前**红**、修复后**绿**。

---

## 6. 变更文件

### 生产代码

| 文件 | 变更 |
|---|---|
| `pet/report_gates.py` | **新增**：门定义（键/标签/默认值）、事件键→门映射、判决纯函数 `should_report` / `should_report_event` / `clean_report_gates`。只放纯数据与纯函数，不导入 `pet.config`，避免循环导入 |
| `pet/config.py` | 默认配置改为 `report_gates`；旧键一次性迁移并 pop；`report_gates` 走 `clean_report_gates` |
| `pet/agent_link.py` | 统一入口 `_report_allowed(agent_cfg, event_key)`；15 处出气泡路径接门；`model_access` 门前置到记账之前；`rng` 可注入 |
| `pet/settings_pet_controls.py` | 8 个 `ProbabilitySlider`（`reportGateSlider_<gate>`），无开关 |
| `pet/modern_settings_dialog.py` | 8 个门行 + `CollapsibleGroup` 折叠框（默认展开、按门分组收纳该类文案行）+ 保存写回 + 搜索自动展开 + **孤儿行放回本域** |
| `pet/window.py` | `_set_agent_link_option`：门名 → `report_gates` 两端值（1.0 / 0.0），不再写 `notify_*` |
| `pet/context_menus/shared.py` | 菜单 0/1 快捷入口（3 个高频类），勾选态按概率 `> 0`，tooltip 提示当前值 |
| `pet/settings_widgets.py` | 新增 `ProbabilitySlider`（20 档 × 0.05）与 `CollapsibleGroup`（折叠框，默认展开）两个组件 |

### 同批改名（`rate_limit` → `model_access`，本提交的硬依赖）

| 文件 | 变更 |
|---|---|
| `pet/rate_limit_tracker.py` → `pet/model_access_tracker.py` | 模块与类改名（`RateLimitTracker` → `ModelAccessTracker` 等） |
| `pet/persona_presets/legacy.json`、`whale_maid.json` | 预设键 `rate_limit.one/many` → `model_access.one/many` |
| `pet/persona_template.py` | 字段名改名 |
| `pet/agent_event_normalizer.py` | 规范名改名 |
| `integrations/dsh-pet-bridge/index.js` | 桥接侧事件名改名 |
| `docs/DSH-BRIDGE-PET-EVENT-CONTRACT-2026-09-02.md`、`docs/OPEN-SOURCE-HARNESS-RISK-RESEARCH.md`、`docs/PERSONA-TEMPLATE-FIELD-ALIGNMENT-2026-09-05.md` | 文档同步改名 |

### 测试

| 文件 | 变更 |
|---|---|
| `tests/test_report_gates.py` | **新增**：纯逻辑契约（默认值 / 清洗边界 / 判决边界 / 事件键→门映射 / 迁移） |
| `tests/test_agent_link.py` | 基线从「旧开关复位」改为「**门基线 + `_agent_gates()` 助手**」；全部 `notify_*` / `report_probability` 站点迁移为门；产品默认值断言更新为完整默认字典 |
| `tests/test_config_domains.py` | 脏输入改为门形状（越界 + 无法解析）；新增旧键迁移并删除的域级用例 |
| `tests/test_report_probability.py` | **删除**（旧模型已被整体替换；其纯逻辑覆盖由 `test_report_gates.py` 承接，端到端覆盖由门行为驱动承接） |

### 文档

| 文件 | 变更 |
|---|---|
| `docs/SETTINGS-REPORT-PROBABILITY-2026-09-10.md` | 重写为概率门口径：门表、三条交互口径、清洗边界、判决语义、准出证据 |
| `CONTEXT.md` | 新增术语 **Report Gate** 与 **Gate Group**（含 `_Avoid_` 反例） |

### 工作区驱动（不随 PR 提交的本地自证脚本）

| 文件 | 作用 |
|---|---|
| `ptmp-gate-drive.py` | **新增**：门行为驱动，注入 `rng` 自证 8 个门「关=静音 / 开=必报」 |
| `ptmp-gate-live-drive.py` | **新增**：概率真的生效驱动，**不注入 rng**（真随机），自证实测出泡率 ≈ 设定概率、端点硬保证、装配链路闭环 |
| `ptmp-ui-entry-drive.py` | **重写**：设置页入口驱动（原为旧 `report_probability` 数值项验证），含标题与顶层位置自证 |

---

## 7. 准出证据

| 门 | 命令 | 结果 |
|---|---|---|
| 静态检查 | `python -m ruff check pet tests` | **All checks passed** |
| 契约（纯逻辑） | `python -m pytest tests/test_report_gates.py -q` | **54 passed** |
| 联动主套件 | `python -m pytest tests/test_agent_link.py -q` | **151 passed / 0 failed** |
| 门实际生效 | `python ptmp-gate-drive.py` | **44 通过 / 0 失败** |
| 概率真的生效（真随机） | `python ptmp-gate-live-drive.py` | **17 通过 / 0 失败** |
| 设置页入口 | `python ptmp-ui-entry-drive.py` | **67 通过 / 0 失败** |
| 全量 | `python -m pytest -q` | **全绿**（用户本地执行确认） |

### 概率真的生效（`ptmp-gate-live-drive.py`）

`ptmp-gate-drive.py` 注入固定 roll，只能证明**判决逻辑**对；要证明概率**在真实运行路径上确实
按比例抽稀**，必须用真随机源、真配置往返、真气泡出口。本驱动**不注入 rng**（默认 `random.random`），
N=3000 × 3 轮：

| 试验 | 设定概率 | 实测出泡 | 偏离 |
|---|---|---|---|
| 默认配置（出厂值） | 0.60 | 1806/3000 = 0.6020 | 0.22σ |
| | 0.60 | 1829/3000 = 0.6097 | 1.08σ |
| | 0.60 | 1818/3000 = 0.6060 | 0.67σ |
| 设置页滑块 → 落盘 → 重载 | 0.25 | 756/3000 = 0.2520 | 0.25σ |
| | 0.25 | 721/3000 = 0.2403 | 1.22σ |
| | 0.25 | 771/3000 = 0.2570 | 0.89σ |
| 同一 Config 实例当场改值 | 0.42 | 1228/3000 = 0.4093 | — |

其它自证项：

- **端点硬保证**：`0.00` → 0/3000 一条不出；`1.00` → 3000/3000 一条不漏。
- **概率只作用于气泡**：过程汇报门关的 50 次投喂里 `win.mark_activity()` 照常被调用 50 次；
  卡住门关时档位 1 的**焦急动画照旧播放**、只静音档位 2 的气泡。
- **真实装配链路闭环**：`app.py` 用 `self.config` 打开设置页、`PetWindow(lib, self.config, …)`
  与桌宠共用**同一 Config 实例**、`window_optional_services.py` 用 `AgentLinkManager(self, self.cfg)`
  构造管理器且**不注入 rng**；运行时在同一实例上把滑块改成 0.42，管理器当场按 0.42 抽稀。

> 命中率是二项分布：N=3000、p=0.6 时 σ≈0.0089，容差 ±0.05 约 5.6σ，所以真实随机也不会偶发飘红。


### 提交点自检（干净检出 HEAD 能跑）

把提交导出到**独立的干净 worktree**（`git worktree add --detach <commit>`）后重跑，而不是只在
工作区跑——因为工作区里并行的未提交改动会**掩盖**提交缺少依赖的问题。第一次自检就是这样发现
`agent_link.py` 引用的新模块名/类名没进提交：

```text
FAIL pet.agent_link -> ImportError: cannot import name 'ModelAccessTracker'
                      from 'pet.model_access_tracker'
```

补齐依赖后，干净检出 HEAD 的结果：

| 检查 | 结果 |
|---|---|
| `python -c "import pet.agent_link, pet.modern_settings_dialog, ..."`（10 个模块） | **全部 OK** |
| `python -m ruff check pet tests` | **All checks passed** |
| `python -m pytest tests/test_report_gates.py tests/test_agent_link.py tests/test_config_domains.py <2 个设置页用例> -q` | **239 passed / 0 failed** |

### 门行为驱动覆盖（`ptmp-gate-drive.py`，注入固定 roll，不赌随机）

- 默认门形状与默认值（8 门、activity 0.60、其余 1.0）；
- 8 个门逐个「关=静音 / 开=必报」，并断言走对了预设文案（thinking / 正在跑命令 / failure.retry / 通信桥…）；
- `activity = 0.60` 的 roll 0.59 / 0.60 **边界**（等于概率不放行）；
- 抽稀丢弃**不记账**（时钟不前进也能汇报，概率与节流不叠加衰减）；
- `stuck` 门关时**动画照播**、只静音气泡（检测能力不受影响）；
- `model_access` 门关时**不记账**（避免连带压掉通用失败横幅）；
- 未知事件在「全部门为 0」时仍放行。

### 设置页驱动覆盖（`ptmp-ui-entry-drive.py`）

- 8 个滑块存在、默认值与 `REPORT_GATE_DEFAULTS` 一致、区间合法；
- 折叠框默认**展开**、含 8 个分类子分组、顺序与 `REPORT_GATE_KEYS` 一致、8 个门行都入组；
- 搜索命中折叠框内的行 → **自动展开**；
- 保存 → 落盘 `config.json` → 重新加载一致 → **重开设置页读回一致**；落盘不含旧键；
- 越界写盘收敛（99.0 → 1.0、-5.0 → 0.0）；
- 菜单 0/1：勾选写 1.0、取消写 0.0，不产生 `notify_*` 键，勾选态与 tooltip 随当前概率。

> **环境说明（评审须知）**：本次验证在受限沙箱内进行，`tmp_path` 相关用例无法直接运行
> （pytest 以 0o700 建的临时目录被沙箱 ACL 拒绝访问）。为取得真实信号，验证时注入了一个
> **只替换 `tmp_path` 夹具**的临时 pytest 插件（放在仓库外、**不随 PR 提交**），
> 不改变任何被测逻辑与断言。`tests/collision_ipc.py` 一族的少量失败为环境性
> （`QLocalServer.listen` 在该沙箱被拒：`QLocalServerPrivate::addListener: 拒绝访问`），
> 与本次变更无关。

---

## 8. 兼容性与风险

**破坏性变更（配置形状）**

- 旧键 `notify_state` / `notify_activity` / `notify_approval` / `notify_done` / `notify_exec_failed` /
  `report_probability` **不再存在**。老配置读入时一次性迁移，迁移后不再写出。
- 外部若有代码/脚本读写这些旧键，需要改读 `agent_link.report_gates.<gate>`。
  （已知消费方：设置页、右键菜单、`AgentLinkConfig` facade——均已同步。）

**行为变化**

- 过程汇报默认由「100% 全报」变为 **60%**（旧 `report_probability` 默认 60 的等价延续），
  这是本次唯一**用户可见的默认行为变化**，且是用户明确要求的抽稀。
- 其余 7 类默认 1.00，行为与改造前一致。
- 概率只影响气泡；**检测、动画、`raw_record`、对话记忆不受影响**。

**风险与缓解**

| 风险 | 缓解 |
|---|---|
| 门值是概率，用户可能误以为 0.6 是「强度」 | 设置页 hint 写明「0.00 = 该类完全不汇报（静音），1.00 = 每次都汇报，中间值按概率抽稀」 |
| 概率 + 三重节流叠加导致过度衰减 | 抽稀丢弃不记账；驱动专项断言 |
| 关掉一类静音连带吞掉另一类 | `model_access` 门前置到记账之前；驱动专项断言 |
| 新事件键无人配门被静默丢弃 | 未知事件直接放行；契约测试锁死 |
| 折叠框把原有入口藏起来 | 默认展开 + 搜索自动展开 + 驱动断言 |
| `dialogue_*` 孤儿行再次被吞 | 显式放回本域 + 两例全量用例锁死 |

---

## 9. 未完成 / 后续

1. **设置页布局与可访问性三档验收**：紧凑 / 常规 / 宽屏无裁切、字体放大、Tab 可达、
   明暗主题对比度，以及 `docs/assets/` 代表截图——**未做**（沿用既有的量测验收遗留项）。
2. 跨平台验证仅 Windows + offscreen。
3. 全量 pytest 由用户在真实环境执行（本次沙箱受限，见 §7 环境说明）。

---

## 10. 评审指南（reviewer 看哪里）

按「先契约、后接线、最后交互」的顺序：

1. **语义与默认值** — `pet/report_gates.py`；重点看 `clean_report_gates` **没有** bool 特判的理由（§3.6）
   与 `activity` 默认 0.6。
2. **判决与不抽稀保证** — `should_report` / `should_report_event` / `_report_allowed`；
   确认「未知事件放行」「门只管出气泡」。
3. **两个易错顺序** — `_on_model_access` 的门在**记账之前**；`_on_agent_activity` 的门在三重节流**之后**且不记账。
4. **迁移** — `_clean_agent_link_data` 的 `report_gates` 优先 + 旧键 pop。
5. **设置页** — 折叠框默认展开、按门分组、保存写回、搜索自动展开、**孤儿行放回**。
6. **菜单** — 只写两端值，勾选态按概率 `> 0`。
7. **测试基线** — `tests/test_agent_link.py` 的 `_AGENT_GATE_BASELINE` + `_agent_gates()`：
   门基线只保留 `approval = 1.0`（它是交互身份/关闭配对用例的前置条件），
   每个用例只开自己那一类门；`_agent_gates()` **刻意返回完整 8 门**（部分字典会整块替换
   `report_gates`，让未点名的门回落到产品默认而破坏基线隔离）。

---

## 11. 复跑命令

```bash
python -m ruff check pet tests

# 契约（纯逻辑，不依赖 Qt / tmp_path）
python -m pytest tests/test_report_gates.py -q

# 联动主套件
python -m pytest tests/test_agent_link.py -q

# 工作区驱动自证（注入 rng，确定性判决）
python ptmp-gate-drive.py        # 门实际生效：8 门 + 边界 + 不记账 + 未知事件放行
python ptmp-gate-live-drive.py   # 概率真的生效：真随机源下的实测出泡率 + 装配链路闭环
python ptmp-ui-entry-drive.py    # 设置页入口：8 滑块 + 折叠框 + 搜索展开 + 落盘 + 菜单 0/1

# 全量
python -m pytest -q
```
