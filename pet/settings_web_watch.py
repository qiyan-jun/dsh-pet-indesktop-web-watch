# -*- coding: utf-8 -*-
"""设置页「网页互动」域：控件创建 / 行装配 / 保存 / 自检。

**为什么单独一个模块**：``modern_settings_dialog.py`` 行数预算已顶格（2393），
与 ``settings_file_interpret.py`` 同一处置：控件与行全部在本模块构建，对话框只做
三处接线（控件安装 ``_build_web_watch_controls``、域导航挂组、保存委托
``_write_config``）。

**接线契约**（与 settings_file_interpret / settings_interaction 同口径）：

- 行由 ``build_web_watch_rows`` 构建、挂进「自动化与联动」域的「网页互动」组；
- 行**不在** ``_rebuild_domain_navigation`` 开头的 ``all_rows`` 快照里，因此不需要
  ``claim``，也不会被判成「待分类（开发期）」；
- ``objectName`` 仍是 ``settingRow_<键>``，设置搜索与显隐联动照旧生效；
- 数值区间与 ``pet/config.py`` 的归一化区间、``pet/web_watch/policy.py`` 的有效区间
  三处同源（改一处必须同步，tests/test_web_watch.py 的常量同步用例会把漂移打红）。
"""
from __future__ import annotations

import datetime
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QWidget

from .config import _default_web_watch_data
from .settings_widgets import BrowserDoubleSpinBox, BrowserSpinBox, SettingRow, ToggleSwitch

# 与 config.py 的 web_watch 归一化区间同源
_RANGE_DWELL = (0, 600)
_RANGE_DELTA = (80, 20000)
_RANGE_IDLE = (0, 3600)
_RANGE_COOLDOWN = (0.1, 120.0)
_RANGE_PORT = (1, 65535)
_RANGE_CAP = (1, 9999)
_RANGE_MIN_INTERVAL = (10, 3600)

TOKEN_FILE_NAME = "web_watch_token.txt"
STATE_FILE_NAME = "web_watch_state.json"
STATUS_FILE_NAME = "web_watch_status.json"


# ---------------------------------------------------------------- 路径与状态查询


def extension_dir() -> Path:
    """扩展目录：安装版在 ``_internal/integrations/`` 下，源码版在仓库 ``integrations/``。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "_internal" / "integrations" / "edge-web-watch"
    return Path(__file__).resolve().parent.parent / "integrations" / "edge-web-watch"


def current_token(config) -> str:
    """当前令牌：配置里写了就用配置的，否则读自动生成的令牌文件。"""
    cfg = config.get("web_watch") or {}
    if isinstance(cfg, dict) and str(cfg.get("token") or "").strip():
        return str(cfg["token"]).strip()
    try:
        return (Path(config.dir) / TOKEN_FILE_NAME).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def today_count(config) -> int:
    """今日已说话次数（读频控状态文件；跨天或文件缺失按 0）。"""
    try:
        data = json.loads((Path(config.dir) / STATE_FILE_NAME).read_text(encoding="utf-8"))
    except Exception:
        return 0
    if not isinstance(data, dict) or str(data.get("date")) != datetime.date.today().isoformat():
        return 0
    try:
        return int(data.get("count", 0))
    except (TypeError, ValueError):
        return 0


def read_pipeline_status(config) -> dict:
    """读运行中的桌宠写下的流水线快照（跨进程排查的唯一入口）。"""
    try:
        data = json.loads((Path(config.dir) / STATUS_FILE_NAME).read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _age_text(seconds: float) -> str:
    if seconds <= 0:
        return "从未"
    if seconds < 90:
        return f"{seconds:.0f} 秒前"
    if seconds < 5400:
        return f"{seconds / 60:.1f} 分钟前"
    return f"{seconds / 3600:.1f} 小时前"


def pipeline_text(config, port: int, token: str) -> str:
    """把"读到了吗 / 何时读的 / 处理了吗"三段拼成可读状态（设置页显示）。"""
    ok, probe_text = probe(port, token)
    lines = [("✅ " if ok else "⚠️ ") + probe_text]
    data = read_pipeline_status(config)
    if not data:
        lines.append("桌宠还没写过状态快照：确认「启用网页互动」已保存、桌宠在运行（保存后约 5 秒内会出现）")
        return "\n".join(lines)

    received = int(data.get("received") or 0)
    age = time.time() - float(data.get("last_event_ts") or 0.0)
    if received:
        lines.append(
            f"① 收到扩展事件 {received} 条 · 最近 {_age_text(age)}"
            f"（{data.get('last_event_kind') or '?'} @ {data.get('last_event_domain') or '?'}，"
            f"正文 {int(data.get('last_event_text_len') or 0)} 字 / 划词 {int(data.get('last_event_selection_len') or 0)} 字）"
        )
    else:
        lines.append("① 收到扩展事件 0 条 —— 扩展没发出来：检查扩展选项的端口/令牌，并在 Edge 扩展页点「Service Worker」看控制台报错")

    auth = int(data.get("auth_rejected") or 0)
    if auth:
        lines.append(f"⚠️ 有 {auth} 次请求因令牌不匹配被拒（401）→ 回桌宠点「复制令牌」重新粘贴到扩展选项")
    bad = int(data.get("received_bad") or 0)
    if bad:
        lines.append(f"⚠️ 有 {bad} 次请求格式非法被拒（400）→ 扩展版本与桌宠不匹配，重装扩展目录")

    if data.get("last_decision"):
        lines.append(f"② 最近判定：{data['last_decision']}")
    if data.get("last_skip"):
        lines.append(f"② 最近没说话的原因：{data['last_skip']}")
    if data.get("last_dispatch"):
        lines.append(f"③ 最近派发：{data['last_dispatch']}")
    lines.append(
        f"今日已说 {today_count(config)} 次 · 当前页 {data.get('current_domain') or '—'}"
        f"（停留 {float(data.get('current_dwell') or 0):.0f}s"
        f"{'，已评论' if data.get('current_commented') else ''}"
        f"{'，已建议' if data.get('current_suggested') else ''}）"
    )
    return "\n".join(lines)


def _direct_opener():
    """回环请求走显式直连：系统代理会把 127.0.0.1 吃掉（见 docs/NETWORK-PROXY-AND-VPN）。"""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def probe(port: int, token: str) -> tuple[bool, str]:
    """自检：接收端在不在 + 令牌对不对。返回 (是否正常, 可读说明)。"""
    opener = _direct_opener()
    try:
        with opener.open(f"http://127.0.0.1:{int(port)}/health", timeout=1.5) as resp:
            if int(getattr(resp, "status", 0)) != 200:
                return False, f"接收端返回 HTTP {getattr(resp, 'status', '?')}"
    except Exception:
        return False, "接收端没在运行（保存「启用网页互动」后重试）"
    if not token:
        return True, "接收端在运行；令牌尚未生成（保存后自动生成）"
    request = urllib.request.Request(
        f"http://127.0.0.1:{int(port)}/ping",
        data=b"{}",
        headers={"Content-Type": "application/json", "X-Pet-Token": token},
        method="POST",
    )
    try:
        with opener.open(request, timeout=2.0) as resp:
            body = json.loads(resp.read().decode("utf-8", "replace") or "{}")
            return True, "连接正常 · " + str(body.get("name") or "桌宠")
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            return False, "令牌不匹配（点「复制令牌」重新粘贴到扩展）"
        return False, f"接收端返回 HTTP {exc.code}"
    except Exception as exc:
        return False, f"探测失败：{exc}"


# ---------------------------------------------------------------- 控件


def create_web_watch_controls(dialog) -> None:
    """在对话框上创建本域控件（幂等；无聊天能力时不创建）。"""
    if not getattr(dialog, "include_ai", True):
        return
    if getattr(dialog, "ww_enabled_check", None) is not None:
        return

    defaults = _default_web_watch_data()
    cfg = dialog.config.get("web_watch") or {}
    cfg = cfg if isinstance(cfg, dict) else {}
    value = lambda key: cfg.get(key, defaults[key])  # noqa: E731 - 局部取值糖，可读性优先

    dialog.ww_enabled_check = ToggleSwitch(dialog)
    dialog.ww_enabled_check.setChecked(bool(value("enabled")))
    dialog.ww_dryrun_check = ToggleSwitch(dialog)
    dialog.ww_dryrun_check.setChecked(bool(value("dry_run")))

    dialog.ww_port_spin = BrowserSpinBox(dialog)
    dialog.ww_port_spin.setRange(*_RANGE_PORT)
    dialog.ww_port_spin.setValue(int(value("port")))

    dialog.ww_token_edit = QLineEdit(dialog)
    dialog.ww_token_edit.setReadOnly(True)
    dialog.ww_token_edit.setMinimumWidth(240)
    dialog.ww_token_edit.setText(current_token(dialog.config))
    dialog.ww_copy_btn = QPushButton("复制令牌", dialog)
    dialog.ww_copy_btn.setProperty("variant", "ghost")
    dialog.ww_probe_btn = QPushButton("测试连接", dialog)
    dialog.ww_probe_btn.setProperty("variant", "ghost")

    token_row = QWidget(dialog)
    token_layout = QHBoxLayout(token_row)
    token_layout.setContentsMargins(0, 0, 0, 0)
    token_layout.setSpacing(8)
    token_layout.addWidget(dialog.ww_token_edit, 1)
    token_layout.addWidget(dialog.ww_copy_btn, 0)
    token_layout.addWidget(dialog.ww_probe_btn, 0)
    dialog.ww_token_row = token_row

    dialog.ww_status_label = QLabel("点「测试连接」查看状态", dialog)
    dialog.ww_status_label.setWordWrap(True)
    dialog.ww_status_label.setObjectName("settingHint")
    dialog.ww_refresh_btn = QPushButton("刷新状态", dialog)
    dialog.ww_refresh_btn.setProperty("variant", "ghost")
    status_row = QWidget(dialog)
    status_layout = QHBoxLayout(status_row)
    status_layout.setContentsMargins(0, 0, 0, 0)
    status_layout.setSpacing(8)
    status_layout.addWidget(dialog.ww_status_label, 1)
    status_layout.addWidget(dialog.ww_refresh_btn, 0)
    dialog.ww_status_row = status_row

    for attr, key in (
        ("ww_comment_check", "comment_enabled"),
        ("ww_selection_check", "selection_enabled"),
        ("ww_video_check", "video_enabled"),
        ("ww_suggest_check", "suggest_enabled"),
        ("ww_speak_check", "speak_enabled"),
        ("ww_idle_check", "require_idle"),
        ("ww_agent_busy_check", "pause_when_agent_busy"),
        ("ww_precue_check", "pre_cue"),
        ("ww_verbose_check", "verbose_log"),
    ):
        switch = ToggleSwitch(dialog)
        switch.setChecked(bool(value(key)))
        setattr(dialog, attr, switch)

    def _spin(attr: str, key: str, limits: tuple[int, int], suffix: str = "") -> None:
        box = BrowserSpinBox(dialog)
        box.setRange(*limits)
        if suffix:
            box.setSuffix(suffix)
        box.setValue(int(value(key)))
        setattr(dialog, attr, box)

    def _dspin(attr: str, key: str, limits: tuple[float, float], suffix: str = "") -> None:
        box = BrowserDoubleSpinBox(dialog)
        box.setRange(*limits)
        box.setDecimals(1)
        if suffix:
            box.setSuffix(suffix)
        box.setValue(float(value(key)))
        setattr(dialog, attr, box)

    _spin("ww_dwell_spin", "dwell_seconds", _RANGE_DWELL, " 秒")
    _spin("ww_delta_spin", "content_delta_chars", _RANGE_DELTA, " 字")
    _spin("ww_idle_spin", "min_idle_seconds", _RANGE_IDLE, " 秒")
    _spin("ww_video_moment_spin", "video_first_moment_seconds", _RANGE_IDLE, " 秒")
    _spin("ww_suggest_idle_spin", "suggest_idle_seconds", _RANGE_IDLE, " 秒")
    _spin("ww_min_interval_spin", "min_request_interval_seconds", _RANGE_MIN_INTERVAL, " 秒")
    _spin("ww_cap_spin", "daily_cap", _RANGE_CAP, " 次")
    _dspin("ww_cooldown_spin", "cooldown_minutes", _RANGE_COOLDOWN, " 分钟")

    dialog.ww_blacklist_edit = QPlainTextEdit(dialog)
    dialog.ww_blacklist_edit.setPlaceholderText("这些网站不互动，一行一个域名\n例如：mail.example.com")
    dialog.ww_blacklist_edit.setPlainText("\n".join(str(x) for x in value("blacklist")))
    dialog.ww_blacklist_edit.setMinimumHeight(64)

    dialog.ww_whitelist_edit = QPlainTextEdit(dialog)
    dialog.ww_whitelist_edit.setPlaceholderText("留空 = 所有网站；填了就只在这些网站互动")
    dialog.ww_whitelist_edit.setPlainText("\n".join(str(x) for x in value("whitelist")))
    dialog.ww_whitelist_edit.setMinimumHeight(64)

    dialog.ww_open_ext_btn = QPushButton("打开扩展目录", dialog)
    dialog.ww_open_ext_btn.setProperty("variant", "ghost")

    def _copy_token() -> None:
        token = current_token(dialog.config)
        if not token:
            dialog.ww_status_label.setText("还没有令牌：先保存并启用网页互动，桌宠会自动生成")
            return
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(token)
        dialog.ww_status_label.setText("令牌已复制，粘贴到扩展的「访问令牌」里")

    def _refresh() -> None:
        port = int(dialog.ww_port_spin.value())
        token = current_token(dialog.config)
        dialog.ww_token_edit.setText(token)
        dialog.ww_status_label.setText(pipeline_text(dialog.config, port, token))

    def _open_dir() -> None:
        path = extension_dir()
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    dialog.ww_copy_btn.clicked.connect(_copy_token)
    dialog.ww_probe_btn.clicked.connect(_refresh)
    dialog.ww_refresh_btn.clicked.connect(_refresh)
    dialog.ww_open_ext_btn.clicked.connect(lambda: _open_dir())
    # 建页即填一次（读状态文件很便宜），免得用户以为"没数据"
    dialog.ww_status_label.setText(pipeline_text(dialog.config, int(dialog.ww_port_spin.value()), current_token(dialog.config)))
    # 记下"打开那一刻"的界面值：保存时用它判断哪些字段用户压根没碰过
    dialog._web_watch_open_snapshot = _collect_widgets(dialog)


# ---------------------------------------------------------------- 行与保存


def build_web_watch_rows(dialog) -> list[SettingRow]:
    """本域设置行（无聊天能力时返回空列表，整组不出现）。"""
    if getattr(dialog, "ww_enabled_check", None) is None:
        return []
    rows = [
        SettingRow("web_watch_enabled", "启用网页互动", "打开后桌宠会监听本机端口，接收 Edge 扩展发来的当前网页内容并评论/建议；关闭即释放端口，零开销。", dialog.ww_enabled_check),
        SettingRow("web_watch_dry_run", "验证模式（dry-run）", "命中条件只写日志、不调用模型、不消耗额度；建议先用它确认链路通不通。", dialog.ww_dryrun_check),
        SettingRow("web_watch_port", "本地接收端口", "扩展要填同一个端口；改端口后需重新保存并同步修改扩展设置。", dialog.ww_port_spin),
        SettingRow("web_watch_token", "访问令牌", "阻止其它程序往桌宠里塞内容：点「复制令牌」后粘贴到扩展的「访问令牌」。", dialog.ww_token_row, stacked=True),
        SettingRow("web_watch_status", "连接状态与用量", "「今日已说」= 今日真正调用模型说话的次数（受下方每日上限约束）。", dialog.ww_status_row, stacked=True),
        SettingRow("web_watch_comment", "页面评论", "换页停留够了、或长文继续加载出新内容时，评论一句。", dialog.ww_comment_check),
        SettingRow("web_watch_selection", "划词评论", "你选中一段文字时优先评论这段（最强的“正在看”信号）。", dialog.ww_selection_check),
        SettingRow("web_watch_video", "视频点评", "视频播放到设定时刻时评论一次（标题 + 进度）。", dialog.ww_video_check),
        SettingRow("web_watch_suggest", "主动建议", "在代码/文档/论文/新闻/问答页停留且你离开键鼠一段时间后，给一条具体建议（每页最多一条）。", dialog.ww_suggest_check),
        SettingRow("web_watch_speak", "语音朗读", "把评论用语音报时的音色读出来（与报时共用通道）。", dialog.ww_speak_check),
        SettingRow("web_watch_precue", "先说一句「让我看看……」", "命中后立刻给个反馈气泡，再等模型答复（模型往返通常 1~19 秒，先有动静体感快很多）。", dialog.ww_precue_check),
        SettingRow("web_watch_dwell", "换页停留门限", "同一页停留多久才可能开口（0 = 立即）。", dialog.ww_dwell_spin),
        SettingRow("web_watch_delta", "正文增量阈值", "长文/文档页新增这么多字才再补一句，防止刷屏。", dialog.ww_delta_spin),
        SettingRow("web_watch_video_moment", "视频首句时刻", "视频播放到这个时间点才评论（避免一开场就说话）。", dialog.ww_video_moment_spin),
        SettingRow("web_watch_require_idle", "仅当我闲置时评论", "勾选后敲键盘/动鼠标时不打扰；建议功能本身另有闲置要求。", dialog.ww_idle_check),
        SettingRow("web_watch_agent_busy", "Agent 工作时闭嘴", "DSH 正在跑任务时不插话。默认关：Agent 常常一直处于工作态，开着会让网页互动整段时间没反应。", dialog.ww_agent_busy_check),
        SettingRow("web_watch_verbose", "详细日志", "把「收到事件 / 为什么没说话 / 是否发出请求」写进 pet 日志。排查问题时就靠它。", dialog.ww_verbose_check),
        SettingRow("web_watch_idle_seconds", "闲置判定秒数", "勾选上一项后，键鼠静止该秒数才开口。", dialog.ww_idle_spin),
        SettingRow("web_watch_suggest_idle", "建议的闲置秒数", "离开键鼠多久后给建议（默认 45 秒）。", dialog.ww_suggest_idle_spin),
        SettingRow("web_watch_cooldown", "同页冷却", "同一页两次说话之间的最短间隔；换到新页面不受它限制（新页由「最小请求间隔」兜底）。", dialog.ww_cooldown_spin),
        SettingRow("web_watch_min_interval", "最小请求间隔", "跨页面的硬保护：两次真正调用模型之间至少隔这么久（免费档防限流），不建议小于 10 秒。", dialog.ww_min_interval_spin),
        SettingRow("web_watch_cap", "每日上限", "每天最多真正调用模型多少次；到达后当日不再说话。", dialog.ww_cap_spin),
        SettingRow("web_watch_blacklist", "不互动的网站", "一行一个域名；支持 example.com 匹配其子域。", dialog.ww_blacklist_edit, stacked=True),
        SettingRow("web_watch_whitelist", "只在指定网站互动", "留空 = 全部网站；填了则只在这些域名生效。", dialog.ww_whitelist_edit, stacked=True),
        SettingRow("web_watch_open_extension", "安装浏览器扩展", "打开扩展所在目录（Edge → 扩展 → 开发人员模式 → 加载解压缩的扩展）。", dialog.ww_open_ext_btn),
    ]
    return rows


def _collect_widgets(dialog) -> dict:
    """把本域所有界面控件读成一个 {配置键: 值} 字典（保存与"初始快照"共用一份口径）。"""
    return {
        "enabled": bool(dialog.ww_enabled_check.isChecked()),
        "dry_run": bool(dialog.ww_dryrun_check.isChecked()),
        "port": int(dialog.ww_port_spin.value()),
        "comment_enabled": bool(dialog.ww_comment_check.isChecked()),
        "selection_enabled": bool(dialog.ww_selection_check.isChecked()),
        "video_enabled": bool(dialog.ww_video_check.isChecked()),
        "suggest_enabled": bool(dialog.ww_suggest_check.isChecked()),
        "speak_enabled": bool(dialog.ww_speak_check.isChecked()),
        "pre_cue": bool(dialog.ww_precue_check.isChecked()),
        "require_idle": bool(dialog.ww_idle_check.isChecked()),
        "pause_when_agent_busy": bool(dialog.ww_agent_busy_check.isChecked()),
        "verbose_log": bool(dialog.ww_verbose_check.isChecked()),
        "dwell_seconds": int(dialog.ww_dwell_spin.value()),
        "content_delta_chars": int(dialog.ww_delta_spin.value()),
        "min_idle_seconds": int(dialog.ww_idle_spin.value()),
        "video_first_moment_seconds": float(dialog.ww_video_moment_spin.value()),
        "suggest_idle_seconds": float(dialog.ww_suggest_idle_spin.value()),
        "cooldown_minutes": float(dialog.ww_cooldown_spin.value()),
        "min_request_interval_seconds": int(dialog.ww_min_interval_spin.value()),
        "daily_cap": int(dialog.ww_cap_spin.value()),
        "blacklist": [line.strip() for line in dialog.ww_blacklist_edit.toPlainText().splitlines() if line.strip()],
        "whitelist": [line.strip() for line in dialog.ww_whitelist_edit.toPlainText().splitlines() if line.strip()],
    }


def _read_disk_web_watch(config) -> dict:
    """直接读 **磁盘上** 的 web_watch 段（独立设置进程的 Config 是启动时的快照）。"""
    try:
        raw = json.loads(Path(config.path).read_text(encoding="utf-8"))
    except Exception:
        return {}
    section = raw.get("web_watch") if isinstance(raw, dict) else None
    return dict(section) if isinstance(section, dict) else {}


def _unchanged(opened, current) -> bool:
    """判断某控件自打开以来是否被用户改动过（bool 与 1/0 不能相等混判）。"""
    if isinstance(opened, bool) or isinstance(current, bool):
        return bool(opened) == bool(current)
    if isinstance(opened, list) or isinstance(current, list):
        return list(opened or []) == list(current or [])
    if isinstance(opened, (int, float)) and isinstance(current, (int, float)):
        return abs(float(opened) - float(current)) < 1e-9
    return str(opened) == str(current)


def save_web_watch_settings(dialog) -> None:
    """``_write_config`` 委托：把本域控件写回 config。

    **关键语义（实测缺陷，2026-10-06）**：设置页跑在独立进程（``standalone=True``），
    它的 Config 是**打开那一刻**的快照；用户点保存会把整份旧值写回磁盘，从而把
    运行期已经生效的改动（例如热加载改过端口/dry-run）静默回滚——实测表现为
    "在设置里关掉语音朗读并保存后，扩展突然又连不上桌宠了"（端口被写回旧值）。

    因此这里逐字段判断：**用户没碰过的字段以磁盘最新值为准**，碰过的才以界面为准。
    未在界面暴露的键照旧原样保留。
    """
    if getattr(dialog, "ww_enabled_check", None) is None:
        return
    current = _collect_widgets(dialog)
    opened = dict(getattr(dialog, "_web_watch_open_snapshot", {}) or {})
    fresh = _read_disk_web_watch(dialog.config)

    data = dialog.config.get("web_watch") or {}
    data = dict(data) if isinstance(data, dict) else {}
    for key, value in current.items():
        if key in fresh and key in opened and _unchanged(opened[key], value):
            data[key] = fresh[key]  # 界面没动过 → 服从磁盘上的最新值（不回滚运行期改动）
        else:
            data[key] = value
    dialog.config.set("web_watch", data)
    dialog.config.save()
    # 保存后再对齐一次快照：连续两次保存不会因为磁盘未变而重复回滚
    dialog._web_watch_open_snapshot = dict(current)
