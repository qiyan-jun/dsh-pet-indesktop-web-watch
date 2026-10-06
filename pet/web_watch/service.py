# -*- coding: utf-8 -*-
"""网页互动的 Qt 侧装配：接收端 → 守卫/策略 → 频控 → LLM → 气泡。

线程模型（与 `pet/proactive.py` 同构）：
- HTTP 接收端 = 标准库 daemon 线程，只做校验与**入队**（`deque.append` 线程安全），
  绝不在该线程碰 Qt 或窗口；
- 决策 = GUI 线程上的 `QTimer`（有页面盯着时 1s，空闲时 5s 退避），
  主线程只做微秒级判定；
- 生成 = `threading.Thread(daemon=True)` 单飞（`_request_in_flight`）+ 代次令牌
  （`_generation`）作废迟到结果；结果经 `QObject` 信号回到 GUI 线程冒泡。

频控复用 `pet/proactive_limiter.py::ProactiveLimiter`，但**用独立状态文件**
（`web_watch_state.json`）：与主动识屏各自记账、互不抢额度；`dry_run` 由本服务
自己解释（不传给它），避免复用识屏的 dry-run 状态文件名。

计费口径：默认关闭；开启后每次真正调用模型前经 `limiter.consume_budget()` 扣当日
额度，连续失败 3 次当日熔断——与主动识屏完全一致的"免费档不撞限流"保护。
"""
from __future__ import annotations

import collections
import copy
import json
import logging
import os
import secrets
import time
from typing import Any, Callable

from ..proactive_limiter import ProactiveLimiter
from .digest import domain_of
from .llm import WebWatchLlmError, post_text_request
from .policy import ACTION_SUGGEST, Decision, Gates, WebWatchPolicy, effective_web_watch_config
from .prompts import build_messages, clean_reply
from .server import DEFAULT_HOST, WebWatchServer

log = logging.getLogger("dsh-pet-standalone")

#: 共享令牌文件名（放在用户配置目录：与 config.json 同级，卸载即清）
TOKEN_FILE_NAME = "web_watch_token.txt"
#: 频控状态文件名（与识屏分开记账）
STATE_FILE_NAME = "web_watch_state.json"
#: 流水线状态快照文件名：设置页（独立进程）与用户排查都读它，不需要 IPC
STATUS_FILE_NAME = "web_watch_status.json"
#: 每 tick 最多消费的事件数：扩展异常刷屏时不许把 GUI 线程拖住
MAX_EVENTS_PER_TICK = 8
#: 心跳：有页面在盯时 1s，完全空闲时退避到 5s（稳态开销≈0）
TICK_ACTIVE_MS = 1000
TICK_IDLE_MS = 5000
#: 频控拒绝后的本地退避秒数（见 _dispatch：避免 1Hz 反复锁频控状态文件）
REFUSAL_BACKOFF_SECONDS = 10.0
#: 状态快照写盘节流（秒）：事件可能密集，但没必要每次都落盘
STATUS_WRITE_MIN_INTERVAL_S = 1.0

#: 策略层跳过原因 → 给用户看的解释（排查时"为什么没反应"全在这张表里）
SKIP_LABELS: dict[str, str] = {
    "disabled": "功能未启用",
    "invisible": "桌宠当前不可见",
    "agent_busy": "Agent 工作中（开了「Agent 工作时闭嘴」）",
    "fullscreen": "全屏中",
    "user_active": "你正在用键鼠（开了「仅当我闲置时评论」）",
    "domain_blocked": "该域名被黑名单拦下或不在白名单里",
    "heartbeat": "心跳事件（只刷新状态）",
    "page_leave": "页面已离开",
    "waiting_dwell": "停留时间还不够（换页停留门限）",
    "text_too_short": "页面正文太短（不足 min_text_chars，短页面不评论）",
    "no_page": "还没收到任何页面信息",
    "selection_disabled": "划词评论已关闭",
    "selection_too_short": "划词太短",
    "selection_duplicate": "这段划词刚说过",
    "video_disabled": "视频点评已关闭",
    "video_done": "这页已经评论过",
    "video_paused": "视频暂停中",
    "video_early": "还没到视频首句时刻",
    "comment_disabled": "页面评论已关闭",
    "none": "条件未满足（停留/闲置/页面类型）",
}


class WebWatchService:
    """进程级网页互动服务（由 `AppShell` 懒创建并持有引用）。"""

    def __init__(
        self,
        config: Any,
        target: Any,
        *,
        on_speak: Callable[[str], Any] | None = None,
        on_agent_busy: Callable[[], bool] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        # 延迟导入 Qt：本模块顶层保持零 Qt（与 pet/voice_chime_service.py 同口径）
        from PySide6.QtCore import QObject, QTimer, Signal

        class _Bridge(QObject):
            # (文本, 展示时长 ms)：worker 线程 emit → GUI 线程槽
            bubble_requested = Signal(str, int)

            def __init__(self, service: "WebWatchService", parent: Any = None) -> None:
                super().__init__(parent)
                self._service = service

            def _forward_bubble(self, text: str, duration_ms: int) -> None:
                self._service._show_bubble(text, duration_ms)

        self.cfg = config
        self.target = target
        self.on_speak = on_speak
        self.on_agent_busy = on_agent_busy
        self._clock = clock

        self._eff: dict[str, Any] = effective_web_watch_config(None)
        self.pet_name = ""
        self.bind_error = ""
        self._policy = WebWatchPolicy(config.get("web_watch", {}), clock=clock)
        self.limiter = ProactiveLimiter(
            config.dir / STATE_FILE_NAME,
            config.get("web_watch", {}),
            dry_run=False,  # dry_run 由本服务解释，不复用识屏的 dry-run 状态文件
            clock=clock,
        )
        self._bridge = _Bridge(self)
        self._bridge.bubble_requested.connect(self._bridge._forward_bubble)
        self._timer = QTimer()
        self._timer.setInterval(TICK_ACTIVE_MS)
        self._timer.timeout.connect(self._on_tick)

        self._events: collections.deque = collections.deque(maxlen=256)
        self._server: WebWatchServer | None = None
        self._server_port = 0
        self._generation = 0
        self._request_in_flight = False
        # 频控拒绝后的本地退避截止时刻：拒绝时 try_acquire 不盖时间戳，
        # 若每个 tick 都重问一遍就是 1Hz 的文件锁+读盘（跨进程锁），纯浪费。
        self._retry_after = 0.0
        self._stats: dict[str, Any] = {
            "spoken": 0,
            "dry_run_hits": 0,
            "failed": 0,
            "last_reason": "",
            "last_spoken_ts": 0.0,
            # —— 流水线可见性（用户排查三问：读到了吗 / 何时读的 / 上传处理了吗）——
            "received": 0,  # 收到多少条扩展事件（401/400 计数在 server.stats 里）
            "last_event_ts": 0.0,
            "last_event_kind": "",
            "last_event_domain": "",
            "last_event_text_len": 0,
            "last_event_selection_len": 0,
            "last_decision": "",
            "last_skip": "",
            "last_dispatch": "",
            "last_dispatch_ts": 0.0,
        }
        self._logged_skip = ""
        self._status_written_at = 0.0
        self._adopt_qt_lifetimes()
        self.apply_config()

    # ---- Qt 生命周期 ---------------------------------------------------

    def _adopt_qt_lifetimes(self) -> None:
        """parent 为 None 的 QObject 过继给 QApplication。

        与 `pet/multi_window_shared.py::_release_qt_lifetimes` 同一根因：无人认领的
        QTimer/信号桥会被 C++ 侧连接强引用住，解释器退出时跨线程析构 = 原生崩溃。
        """
        try:
            from PySide6.QtCore import QCoreApplication

            app = QCoreApplication.instance()
            if app is None:
                return
            for obj in (self._bridge, self._timer):
                if obj.parent() is None and obj.thread() is app.thread():
                    obj.setParent(app)
        except Exception:
            log.debug("web_watch Qt 生命周期过继失败", exc_info=True)

    # ---- 配置与生命周期 ------------------------------------------------

    def apply_config(self) -> None:
        """按最新配置刷新策略/频控/接收端；已运行则只刷参数不重置状态。"""
        raw = self.cfg.get("web_watch", {})
        eff = effective_web_watch_config(raw)
        self._eff = eff
        self._retry_after = 0.0  # 配置变了就重新给机会（可能刚把冷却/上限调大）
        self._policy.update_config(eff)
        self.limiter.update_config(eff, dry_run=False)
        self.pet_name = self._resolve_pet_name()

        if eff["enabled"]:
            token = self._resolve_token(eff)
            port_changed = self._server is not None and self._server_port != int(eff["port"])
            if self._server is None or port_changed:
                self._stop_server()
                self._start_server(eff, token)
            else:
                self._server.set_token(token)
                self._server.set_pet_name(self.pet_name)
            if not self._timer.isActive():
                self._timer.start()
            log.info(
                "web_watch: 已启用（端口 %s%s，%s）",
                self.bind_error and "-" or self._server_port,
                "" if not self.bind_error else f"，绑定失败：{self.bind_error}",
                "dry-run" if eff["dry_run"] else "真实模式",
            )
        else:
            self._generation += 1
            self._timer.stop()
            self._stop_server()
            self._events.clear()
            self._policy.reset()

    def pause(self) -> None:
        """窗口隐藏/活动暂停：停心跳但保留接收端（恢复时不必重新绑定端口）。"""
        self._timer.stop()

    def resume(self) -> None:
        self.apply_config()

    def stop(self) -> None:
        """进程级收口（幂等）：停心跳、关接收端、作废在飞结果。"""
        self._generation += 1
        try:
            self._timer.stop()
        except Exception:
            pass
        self._stop_server()
        self._events.clear()

    def is_running(self) -> bool:
        """真在跑 = 心跳在跑 且 接收端已绑定（只看对象存在不算）。"""
        return bool(self._timer.isActive() and self._server is not None and self._server.is_running)

    def status(self) -> dict[str, Any]:
        """设置页展示用的只读状态（绝不含网页正文）。"""
        page = self._policy.snapshot()
        server_stats = self._server_stats()
        return {
            "enabled": bool(self._eff["enabled"]),
            "dry_run": bool(self._eff["dry_run"]),
            "running": self.is_running(),
            "host": DEFAULT_HOST,
            "port": self._server_port,
            "token": self._current_token(),
            "bind_error": self.bind_error,
            "received": int(self._stats["received"]),
            "rejected_auth": int(server_stats.get("rejected_auth", 0)),
            "rejected_bad": int(server_stats.get("rejected_bad", 0)),
            "spoken": int(self._stats["spoken"]),
            "dry_run_hits": int(self._stats["dry_run_hits"]),
            "failed": int(self._stats["failed"]),
            "last_reason": str(self._stats["last_reason"]),
            "last_spoken_ts": float(self._stats["last_spoken_ts"]),
            "last_event_ts": float(self._stats["last_event_ts"]),
            "last_event_kind": str(self._stats["last_event_kind"]),
            "last_event_domain": str(self._stats["last_event_domain"]),
            "last_skip": str(self._stats["last_skip"]),
            "last_dispatch": str(self._stats["last_dispatch"]),
            "page": page,
        }

    def _server_stats(self) -> dict:
        return dict(getattr(self._server, "stats", {}) or {})

    # ---- 接收端 --------------------------------------------------------

    def _start_server(self, eff: dict, token: str) -> None:
        server = WebWatchServer(
            self._enqueue_event,
            host=DEFAULT_HOST,
            port=int(eff["port"]),
            token=token,
            pet_name=self.pet_name,
        )
        if server.start():
            self._server = server
            self._server_port = server.port
            self.bind_error = ""
        else:
            self.bind_error = server.bind_error or "绑定失败"
            self._server = None
            self._server_port = 0

    def _stop_server(self) -> None:
        server, self._server = self._server, None
        self._server_port = 0
        if server is not None:
            server.stop()

    def _enqueue_event(self, event: Any) -> None:
        """HTTP 线程调用：只入队（deque.append 原子）+ 记一条"收到了什么"的痕迹。

        这一步的日志是排查第一问（"打开网页时到底有没有读到内容"）的唯一证据：
        扩展若因令牌/端口问题发不进来，这里永远不会出现，而 401 计数会涨。
        """
        self._events.append(event)
        domain = domain_of(getattr(event, "url", ""))
        self._stats["received"] = int(self._stats.get("received", 0)) + 1
        self._stats["last_event_ts"] = self._clock()
        self._stats["last_event_kind"] = str(getattr(event, "kind", ""))
        self._stats["last_event_domain"] = domain
        self._stats["last_event_text_len"] = len(getattr(event, "text", "") or "")
        self._stats["last_event_selection_len"] = len(getattr(event, "selection", "") or "")
        if self._eff.get("verbose_log", True):
            log.info(
                "web_watch 收到事件 #%s kind=%s 站点=%s 正文=%d字 划词=%d字 小标题=%d个",
                self._stats["received"],
                self._stats["last_event_kind"],
                domain or "?",
                self._stats["last_event_text_len"],
                self._stats["last_event_selection_len"],
                len(getattr(event, "headings", ()) or ()),
            )

    def _resolve_token(self, eff: dict) -> str:
        """令牌：配置里写了就用配置的；否则读/建用户目录下的令牌文件。"""
        configured = str(eff.get("token") or "").strip()
        if configured:
            self._token_value = configured
            return configured
        path = self.cfg.dir / TOKEN_FILE_NAME
        try:
            if path.is_file():
                existing = path.read_text(encoding="utf-8").strip()
                if existing:
                    self._token_value = existing
                    return existing
            token = secrets.token_hex(16)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(token, encoding="utf-8")
            self._token_value = token
            return token
        except OSError as exc:
            log.warning("web_watch: 令牌文件读写失败（%s），本次使用临时令牌", exc)
            token = getattr(self, "_token_value", "") or secrets.token_hex(16)
            self._token_value = token
            return token

    def _current_token(self) -> str:
        return str(getattr(self, "_token_value", "") or self._eff.get("token") or "")

    # ---- 心跳与决策 ----------------------------------------------------

    def _gates(self) -> Gates:
        visible = True
        is_visible = getattr(self.target, "isVisible", None)
        if callable(is_visible):
            try:
                visible = bool(is_visible())
            except Exception:
                visible = True
        idle = 0.0
        try:
            from ..vision import get_system_idle_seconds

            idle = float(get_system_idle_seconds())
        except Exception:
            idle = 0.0
        busy = False
        if callable(self.on_agent_busy):
            try:
                busy = bool(self.on_agent_busy())
            except Exception:
                busy = False
        return Gates(visible=visible, user_idle_seconds=idle, agent_busy=busy)

    def _on_tick(self) -> None:
        """GUI 线程心跳：消费事件 → 时间驱动判定 → 派发生成。"""
        if not self._eff["enabled"]:
            return
        now = self._clock()
        gates = self._gates()
        processed = 0
        while self._events and processed < MAX_EVENTS_PER_TICK:
            processed += 1
            event = self._events.popleft()
            decision = self._policy.observe(event, gates, now)
            if decision is not None:
                self._note_decision(decision, now)
                self._dispatch(decision, now)
            else:
                self._note_skip(self._policy.last_skip_reason)
        decision = self._policy.poll(gates, now)
        if decision is not None:
            self._note_decision(decision, now)
            self._dispatch(decision, now)
        else:
            self._note_skip(self._policy.last_skip_reason)
        self._retune_interval()
        self._write_status()

    def _note_decision(self, decision: Decision, now: float) -> None:
        """记下"策略层判定了要说话"（第二问：什么时候开始处理的）。"""
        self._stats["last_decision"] = f"{decision.action}/{decision.reason}"
        self._logged_skip = ""  # 有新判定 → 允许下一次跳过原因再打一条
        log.info(
            "web_watch 判定命中：%s（%s，站点 %s）",
            decision.action,
            decision.reason,
            domain_of(decision.digest.get("url", "")) or "?",
        )

    def _note_skip(self, reason: str) -> None:
        """跳过原因只在**变化时**打一条 INFO：既不刷屏，又能回答"为什么没反应"。"""
        reason = str(reason or "")
        if not reason or reason == self._logged_skip:
            return
        self._logged_skip = reason
        self._stats["last_skip"] = f"{reason}（{SKIP_LABELS.get(reason, reason)}）"
        if self._eff.get("verbose_log", True):
            log.info("web_watch 暂不说话：%s（%s）", SKIP_LABELS.get(reason, reason), reason)

    def _write_status(self) -> None:
        """把流水线快照落盘（节流）：设置页/用户排查都读它，不需要 IPC。

        原子替换（tmp + os.replace）：设置页可能正在读，不能读到半个 JSON。
        """
        now = self._clock()
        if now - self._status_written_at < STATUS_WRITE_MIN_INTERVAL_S:
            return
        self._status_written_at = now
        page = self._policy.snapshot()
        payload = {
            "updated_at": now,
            "enabled": bool(self._eff["enabled"]),
            "dry_run": bool(self._eff["dry_run"]),
            "running": self.is_running(),
            "port": self._server_port,
            "received": int(self._stats["received"]),
            "auth_rejected": int(self._server_stats().get("rejected_auth", 0)),
            "received_bad": int(self._server_stats().get("rejected_bad", 0)),
            "last_event_ts": float(self._stats["last_event_ts"]),
            "last_event_kind": str(self._stats["last_event_kind"]),
            "last_event_domain": str(self._stats["last_event_domain"]),
            "last_event_text_len": int(self._stats["last_event_text_len"]),
            "last_event_selection_len": int(self._stats["last_event_selection_len"]),
            "last_decision": str(self._stats["last_decision"]),
            "last_skip": str(self._stats["last_skip"]),
            "last_dispatch": str(self._stats["last_dispatch"]),
            "spoken": int(self._stats["spoken"]),
            "failed": int(self._stats["failed"]),
            "current_domain": str(page.get("domain", "")),
            "current_dwell": float(page.get("dwell_seconds", 0.0)),
            "current_commented": bool(page.get("commented", False)),
            "current_suggested": bool(page.get("suggested", False)),
        }
        path = self.cfg.dir / STATUS_FILE_NAME
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(f".{id(self) & 0xFFFF}.tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            log.debug("web_watch: 状态快照写入失败", exc_info=True)

    def _retune_interval(self) -> None:
        """有在盯的页面就 1s 精判；完全空闲退避到 5s（开启态稳态开销≈0）。"""
        page = self._policy.snapshot()
        busy = bool(self._events)
        settled = page["commented"] and page["suggested"]
        desired = TICK_IDLE_MS if (not page["domain"] and not busy) or settled else TICK_ACTIVE_MS
        if self._timer.interval() != desired:
            self._timer.setInterval(desired)

    # ---- 生成与呈现 ----------------------------------------------------

    def _dispatch(self, decision: Decision, now: float) -> None:
        """频控 + 单飞检查后派发后台生成；被拒时回滚策略标记。

        每一处 return 都要留痕（`_stats["last_dispatch"]` + 日志）：用户排查第三问
        （"读完了到底有没有上传处理"）时，这里要能明确回答"卡在哪一步"，
        而不是只看到"什么都没发生"。
        """
        if self._request_in_flight:
            self._policy.release(decision)
            self._note_dispatch("in_flight", f"上一条还在生成中，本条回滚（{decision.reason}）")
            return
        if self._eff["dry_run"]:
            self._stats["dry_run_hits"] += 1
            self._stats["last_reason"] = f"dry_run:{decision.reason}"
            self._note_dispatch("dry_run", f"验证模式命中 {decision.action}（{decision.reason}），未调模型")
            log.info(
                "web_watch [dry-run]: 命中 %s（%s，站点 %s）",
                decision.action,
                decision.reason,
                domain_of(decision.digest.get("url", "")),
            )
            return
        if now < self._retry_after:
            # 刚被频控拒过：先退避，别用 1Hz 的文件锁去问同一件已知答案的事
            self._policy.release(decision)
            self._note_dispatch("backoff", f"频控退避中（还剩 {self._retry_after - now:.0f}s）")
            return
        allowed, reason = self.limiter.try_acquire()
        if not allowed:
            self._policy.release(decision)
            self._retry_after = now + REFUSAL_BACKOFF_SECONDS
            self._stats["last_reason"] = f"rate_limited:{reason}"
            self._note_dispatch("rate_limited", f"频控拦下：{reason}")
            log.info("web_watch: 频控拦下（%s），%.0fs 后再试", reason, REFUSAL_BACKOFF_SECONDS)
            return
        self._retry_after = 0.0
        try:
            provider, persona = self._resolve_provider()
        except Exception as exc:
            self._policy.release(decision)
            self.limiter.record_failure()
            self._stats["failed"] += 1
            self._note_dispatch("provider_error", f"provider 解析失败：{exc}")
            log.warning("web_watch: provider 解析失败 %s", exc)
            return

        import threading

        self._request_in_flight = True
        self._note_dispatch(
            "sent",
            f"已发出模型请求（{decision.action}/{decision.reason}，站点 {domain_of(decision.digest.get('url', ''))}）",
        )
        log.info(
            "web_watch 派发模型请求：%s/%s 站点=%s 正文=%d字 划词=%d字",
            decision.action,
            decision.reason,
            domain_of(decision.digest.get("url", "")),
            len(decision.digest.get("excerpt", "") or ""),
            len(decision.digest.get("selection", "") or ""),
        )
        threading.Thread(
            target=self._worker_generate,
            args=(decision, provider, persona, self._generation),
            daemon=True,
            name="web-watch-requester",
        ).start()

    def _note_dispatch(self, stage: str, detail: str) -> None:
        """记下派发阶段的结论（给状态快照与日志用）。"""
        self._stats["last_dispatch"] = f"{stage}: {detail}"
        self._stats["last_dispatch_ts"] = self._clock()

    def _resolve_provider(self) -> tuple[Any, str]:
        """provider 做浅拷贝再注入 key，避免与聊天共享可变对象产生竞态。"""
        chat_settings = self.cfg.chat_settings()
        provider = copy.copy(chat_settings.active_config)
        provider.api_key = self.cfg.resolve_api_key(provider)
        return provider, chat_settings.default_system_prompt

    def _resolve_pet_name(self) -> str:
        try:
            from .. import catalog

            return self.cfg.character_display_name(str(self.cfg.get("character", catalog.DEFAULT_CHARACTER)))
        except Exception:
            return ""

    def _worker_generate(self, decision: Decision, provider: Any, persona: str, gen: int) -> None:
        """后台线程：调模型 → 清洗 → （代次校验后）经信号回 GUI 线程冒泡。"""
        try:
            messages = build_messages(
                decision.digest,
                action=decision.action,
                persona=persona,
                max_chars=int(self._eff["max_comment_chars"]),
                pet_name=self.pet_name,
            )
            raw = post_text_request(messages, provider, consume_budget=self.limiter.consume_budget)
            if gen != self._generation:
                return  # 期间用户关闭功能/改配置：迟到结果一律丢弃
            text = clean_reply(raw, int(self._eff["max_comment_chars"]))
            if not text:
                # 模型主动弃权（<SKIP>）：算成功，不冒泡、不计失败
                self.limiter.record_success()
                return
            duration = max(3500, min(15000, 2500 + len(text) * 160))
            self._bridge.bubble_requested.emit(text, duration)
            self.limiter.record_success()
            self._stats["spoken"] += 1
            self._stats["last_spoken_ts"] = self._clock()
            self._stats["last_reason"] = f"{decision.action}:{decision.reason}"
            log.info(
                "web_watch 回复（%s/%s，站点 %s）: %s",
                decision.action,
                decision.reason,
                decision.digest.get("domain", ""),
                text,
            )
            self._sync_to_chat(decision, text)
        except WebWatchLlmError as exc:
            log.warning("web_watch: 生成失败 %s", exc)
            self.limiter.record_failure()
            self._stats["failed"] += 1
        except Exception:
            log.warning("web_watch: 生成异常", exc_info=True)
            self.limiter.record_failure()
            self._stats["failed"] += 1
        finally:
            self._request_in_flight = False

    def _show_bubble(self, text: str, duration_ms: int) -> None:
        """GUI 线程：冒泡（+可选语音）。扇出面由注入的 target 决定（多窗代理）。"""
        hold = getattr(self.target, "hold_bubble", None)
        if callable(hold):
            try:
                hold(duration_ms / 1000.0 + 2.0)
            except Exception:
                log.debug("web_watch: hold_bubble 失败", exc_info=True)
        show = getattr(self.target, "show_bubble", None)
        if callable(show):
            try:
                show(text, duration_ms=duration_ms)
            except Exception:
                log.warning("web_watch: 冒泡失败", exc_info=True)
        if self._eff.get("speak_enabled") and callable(self.on_speak):
            try:
                self.on_speak(text)
            except Exception:
                log.debug("web_watch: 语音播报失败", exc_info=True)

    def _sync_to_chat(self, decision: Decision, text: str) -> None:
        """把评论同步进 AI 对话会话（与主动识屏同一条通道）；标记不含标题。"""
        callback = getattr(self.target, "on_look_synced", None)
        if not callable(callback):
            return
        label = "网页建议" if decision.action == ACTION_SUGGEST else "网页评论"
        marker = f"[{label}] 站点：{decision.digest.get('domain', '')}（{decision.reason}）"
        try:
            callback(marker, text)
        except Exception:
            log.debug("web_watch: 同步进会话失败", exc_info=True)
