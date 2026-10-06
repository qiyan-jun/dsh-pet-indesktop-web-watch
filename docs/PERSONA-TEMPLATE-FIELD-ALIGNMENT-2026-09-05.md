# 台词模板变量名契约：代码 kwargs ↔ 模板占位符（2026-09-09 覆盖重写）

> 本文档由 `pet/persona_template.py` 的常量**自动生成**（VARIABLES / PARAMETERS /
> CONDITIONAL_PARAMETERS / EVENT_DESCRIPTIONS 为唯一真相源）。改任何变量/参数必须先改
> 代码常量再重新生成本文；运行时由
> `tests/test_persona_template.py::test_all_advertised_fields_reach_presentation_layer`
> （AST 双向校验）兜底：模板宣称的 `{变量}` 名 == 调用点 `_dialogue/_persona_text`
> 显式注入的 kwargs 名，多宣称或少宣称都会红。

## 一、总规则（先读）

1. 模板里能写的 `{变量名}` = 代码传入的 kwargs 名，一一对应、没有别名/翻译层：
   代码 `name=` 就写 `{name}`，`toolName=` 就写 `{toolName}`，不存在 `{tool_name}`。
2. 每个事件可用变量分两级：
   - 保证可用：调用点无条件注入；
   - 条件可用（CONDITIONAL_PARAMETERS）：上游提供才注入；缺失/为空/为 null 时渲染端
     自动隐藏该占位符（不会原样露出 `{xxx}`），文案里可以直接写，无需自己兜底。
3. 每个事件的可用变量全集见第三节逐 key 表（= PARAMETERS[key]），含义见第二节变量字典。
   不在该 key 表里的字段（即使上游记录里有）不能在该事件文案中使用，否则原样露出。
4. 上下文记录字段（approval/question/model_access/execution/failed 等同轮触发的额外字段）
   只写进导出模板顶层 upstream 做说明，不进 per-key parameters（v2 规则），
   避免 AI 写出运行时替换不了的占位符。
5. 易混淆命名（EVENT_DESCRIPTIONS 已逐 key 消歧）：
   - activity.* = Agent 正在调用工具（进行中）；failure.tool = 工具调用出错；
     approval.tool = 待审批的工具调用。三者都含 tool 但语义不同。
   - tool = activity.* 原始工具名；toolName = approval.* 审批工具名；
     label 为同名双义（工具标签 vs 会话标签，见变量字典）。

## 二、变量语义字典（代码 kwargs 名 ↔ 模板 `{变量}`）

| 变量名（代码/模板一致） | 语义 |
|---|---|
| `name` | Agent 展示名称（所有事件都会注入） |
| `command` | 命令文本（approval.command=待审批命令；activity.*=工具命令，上游记录提供时可用；已折叠单行、超长截断） |
| `label` | 标签（approval.tool/activity.*=工具中文标签；approval.command/generic、question.*、model_access.*、failure.*=会话标签，上游提供时可用） |
| `body` | 问题内容（question.one；含 header 前缀） |
| `count` | 数量（question.many=问题数；model_access.many=连续模型访问失败次数） |
| `reasons` | 循环/行为检测的判断原因（watchdog.*、pattern.*；已格式化为文本） |
| `detail` | 桥接安装失败详情（bridge.install.failed） |
| `text` | 余额查询结果文本（balance.result） |
| `tool` | 原始工具名（activity.*） |
| `toolName` | 审批原始工具名（approval.*；上游记录提供时可用） |
| `argsKey` | 工具参数摘要键（activity.*；上游记录提供时可用） |
| `callId` | 工具调用 ID（activity.*；上游记录提供时可用） |
| `step` | turn 内步骤序号（activity.*；上游记录提供时可用） |
| `sessionName` | 会话名（当前会话自身的标题/名字，来自会话元数据 sessionName；仅解析出真实名称时才注入，独立于 projectName——绝不拼组合串，无元数据时占位符自动隐藏，不会回退成 sessionId） |
| `projectName` | 会话所属项目名（含 sessionId 的弹窗均可用；上游记录提供时可用） |
| `errorCode` | 错误码（model_access.*、failure.*；上游记录提供时可用） |
| `errorMessage` | 错误信息原文（model_access.*、failure.*；上游记录提供时可用） |
| `consecutiveRetryCount` | 已连续模型访问失败次数（model_access.*；上游记录提供时可用） |
| `retry` | 本轮重试序号（model_access.*；上游记录提供时可用） |
| `retries` | 本轮已重试次数（failure.*；上游记录提供时可用） |
| `retryExhausted` | 是否重试耗尽（failure.*；上游记录提供时可用） |
## 三、逐事件变量表（生成自 PARAMETERS / CONDITIONAL_PARAMETERS）

| 事件 key | 可用变量 | 其中条件可用（缺失自动隐藏） | 一句话语义（description） |
|---|---|---|---|
| `activity.default` | `name`, `tool`, `label`, `command`, `argsKey`, `callId`, `step`, `sessionName`, `projectName` | `command`, `argsKey`, `callId`, `step`, `sessionName`, `projectName` | Agent 在做其它工具操作——过程汇报，进行中 |
| `activity.edit` | `name`, `tool`, `label`, `command`, `argsKey`, `callId`, `step`, `sessionName`, `projectName` | `command`, `argsKey`, `callId`, `step`, `sessionName`, `projectName` | Agent 正在编辑代码——工具调用过程汇报，进行中 |
| `activity.read` | `name`, `tool`, `label`, `command`, `argsKey`, `callId`, `step`, `sessionName`, `projectName` | `command`, `argsKey`, `callId`, `step`, `sessionName`, `projectName` | Agent 正在读取文件——工具调用的过程汇报，进行中，不是错误 |
| `activity.run` | `name`, `tool`, `label`, `command`, `argsKey`, `callId`, `step`, `sessionName`, `projectName` | `command`, `argsKey`, `callId`, `step`, `sessionName`, `projectName` | Agent 正在运行/测试——工具调用过程汇报，进行中 |
| `activity.search` | `name`, `tool`, `label`, `command`, `argsKey`, `callId`, `step`, `sessionName`, `projectName` | `command`, `argsKey`, `callId`, `step`, `sessionName`, `projectName` | Agent 正在搜索/查找——工具调用过程汇报，进行中 |
| `agent.attention` | `name` | - | Agent 需要用户处理/注意（状态提示） |
| `agent.error` | `name` | - | Agent 出错或异常（错误场景） |
| `agent.missing` | `name` | - | 本机未检测到该 Agent 安装 |
| `approval.command` | `name`, `command`, `toolName`, `sessionName`, `projectName`, `label` | `toolName`, `sessionName`, `projectName`, `label` | Agent 请求审批一条命令（等待用户决策） |
| `approval.generic` | `name`, `toolName`, `sessionName`, `projectName`, `label` | `toolName`, `sessionName`, `projectName`, `label` | 通用审批等待用户决定 |
| `approval.tool` | `name`, `label`, `toolName`, `sessionName`, `projectName` | `toolName`, `sessionName`, `projectName` | Agent 请求审批一次工具调用（等待用户决策；不是工具已执行） |
| `balance.loading` |  | - | 余额查询中提示（Pet 公共事件，应写 global） |
| `balance.result` | `text` | - | 余额查询结果（Pet 公共事件，应写 global；占位符 {text}） |
| `bridge.install.failed` | `name`, `detail` | - | 联动通信桥安装失败（Pet 公共事件，应写 global） |
| `bridge.install.pending` | `name` | - | 正在安装联动通信桥（Pet 公共事件，应写 global） |
| `bridge.install.success` | `name` | - | 联动通信桥安装完成（Pet 公共事件，应写 global） |
| `bridge.uninstall.failed` | `name` | - | 联动通信桥卸载失败（Pet 公共事件，应写 global） |
| `done.attention` | `name` | - | 任务停下等待用户确认（收尾） |
| `done.success` | `name` | - | 本轮任务完成（收尾） |
| `dsh.writeback.failed` |  | - | Agent 写回 DSH 失败（错误场景，按 Agent 路由可配专属层） |
| `failure.generic` | `name`, `source`, `errorCode`, `errorMessage`, `retries`, `retryExhausted`, `sessionName`, `projectName` | `source`, `errorCode`, `errorMessage`, `retries`, `retryExhausted`, `sessionName`, `projectName` | 本轮运行通用失败（错误场景） |
| `failure.retry` | `name`, `source`, `errorCode`, `errorMessage`, `retries`, `retryExhausted`, `sessionName`, `projectName` | `source`, `errorCode`, `errorMessage`, `retries`, `retryExhausted`, `sessionName`, `projectName` | 本轮多次重试后仍失败（错误场景） |
| `failure.tool` | `name`, `source`, `errorCode`, `errorMessage`, `retries`, `retryExhausted`, `sessionName`, `projectName` | `source`, `errorCode`, `errorMessage`, `retries`, `retryExhausted`, `sessionName`, `projectName` | 工具执行失败——Agent 调用工具时出错（错误场景；不是「正在执行工具」的过程提示） |
| `llm_error.api` |  | - | AI 服务出错（错误场景） |
| `pattern.control` | `name`, `reasons` | - | 行为重复检测达到干预级别（建议介入） |
| `pattern.warning` | `name`, `reasons` | - | 行为重复检测警告（模式提醒，非阻断） |
| `question.empty` | `name`, `sessionName`, `projectName`, `label` | `sessionName`, `projectName`, `label` | Agent 提问：等待用户从选项选择 |
| `question.many` | `name`, `count`, `sessionName`, `projectName`, `label` | `sessionName`, `projectName`, `label` | Agent 提问：多个问题等待回答 |
| `question.one` | `name`, `body`, `sessionName`, `projectName`, `label` | `sessionName`, `projectName`, `label` | Agent 提问：单个问题等待回答 |
| `model_access.many` | `count`, `errorCode`, `errorMessage`, `consecutiveRetryCount`, `retry`, `sessionName`, `projectName` | `errorCode`, `errorMessage`, `consecutiveRetryCount`, `retry`, `sessionName`, `projectName` | 模型访问失败：连续多次（服务侧 429，错误场景） |
| `model_access.one` | `count`, `errorCode`, `errorMessage`, `consecutiveRetryCount`, `retry`, `sessionName`, `projectName` | `errorCode`, `errorMessage`, `consecutiveRetryCount`, `retry`, `sessionName`, `projectName` | 模型访问失败：单次（服务侧 429，错误场景） |
| `start` | `name` | - | Agent 开始工作（进行中状态提示，非出错） |
| `stuck.reminder` | `name` | - | 卡住检测提醒：Agent 疑似钻牛角尖，建议人工介入 |
| `thinking` | `name` | - | Agent 正在思考（进行中状态提示，非出错） |
| `watchdog.warning` | `name`, `reasons` | - | 循环检测（重复探索行为）风险预警，非阻断 |

## 四、维护不变量

1. `set(PARAMETERS) == set(phrase_keys())`；`CONDITIONAL_PARAMETERS[key]` 是 `PARAMETERS[key]` 的子集。
2. 改 `_dialogue()/_persona_text()` 调用点的显式 kwargs 时，必须同步更新
   PARAMETERS（保证+条件全集）、CONDITIONAL_PARAMETERS（缺失隐藏）、VARIABLES（语义字典）、
   EVENT_DESCRIPTIONS（一句话语义），并重新生成本文——AST 测试只守集合一致，
   “新增参数未登记”靠 code review 把关。
3. 设置页侧 `DIALOGUE_KEY_PARAMS == dict(PARAMETERS)`（单一真相源，有测试）。
4. 留空数组 = 沿用默认模式台词；导入模板忽略未知键；`_说明` 段导入时忽略、可保留。

---
> 历史：前身《台词模板字段对齐审计（2026-09-05）》历 v1（组级上下文混入 parameters）到
> v2（严格逐 key）再到 v3（以桥接源码为真相，撤 activity target/ok 等虚假宣称）再到
> v4（sessionName/projectName/label/toolName/command/argsKey/errorCode/errorMessage/…
> 升级为条件注入自动隐藏）。2026-09-09 起改为由代码常量自动生成的覆盖重写，并引入
> EVENT_DESCRIPTIONS 逐事件语义消歧，防止 AI 把 failure.tool（工具出错）误读成
> activity.*（工具执行中）。
