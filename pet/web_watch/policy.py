# -*- coding: utf-8 -*-
"""网页互动触发策略 —— 零 Qt 纯逻辑层。

把"浏览器送来的一堆事件"变成"现在该不该说一句"的决策。设计口径沿用
`docs/PROACTIVE_SCREEN_PLAN.md` 的漏斗（守卫 → 白名单 → 停留 → 频控），
但判定对象从"像素变化"换成"页面内容与用户动作"：

    页面打开 ──停留 dwell_seconds──┐
    正文增量 ≥ content_delta_chars ─┤
    划词选中 ≥ min_selection_chars ─┼─→ 评论（comment）
    视频播放到 N 秒 ──────────────┘
    同一页停留久 + 用户闲置 ──────────→ 建议（suggest，仅限期页面类型）

本模块只做**页内语义与页级去重**（每页只评论一次/只建议一次、选段去重）；
"两次说话至少隔多久 / 今日上限 / 连续失败熔断"这类跨页全局频控由
`pet/proactive_limiter.py` 的 `ProactiveLimiter` 负责（独立状态文件），
两者职责不重叠。

不写文件、不发网络、不依赖 Qt；时钟由调用方注入，便于单测。
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from .digest import SUGGESTIBLE_KINDS, build_digest, content_hash, domain_allowed, domain_of
from .protocol import WebEvent

#: 评论 / 建议两种动作
ACTION_COMMENT = "comment"
ACTION_SUGGEST = "suggest"

#: 配置默认值。数值区间在 effective_web_watch_config 里 clamp。
DEFAULT_WEB_WATCH_CONFIG: dict[str, Any] = {
    "enabled": False,
    "dry_run": False,
    "port": 8765,
    "token": "",
    "whitelist": [],
    "blacklist": [],
    "dwell_seconds": 8,
    "content_delta_chars": 400,
    "min_text_chars": 120,
    "comment_enabled": True,
    "require_idle": False,
    "min_idle_seconds": 20,
    # 实机修正：DSH agent 在工作时几乎恒为 working，若默认静音，用户整段对话期间
    # 网页互动都会"看起来坏了"。默认不静音（频控/停留已足够防打扰），
    # 想要"agent 干活时别插嘴"的用户可显式打开。
    "pause_when_agent_busy": False,
    "selection_enabled": True,
    "min_selection_chars": 4,
    "video_enabled": True,
    "video_first_moment_seconds": 20.0,
    "suggest_enabled": True,
    "suggest_min_dwell_seconds": 25.0,
    "suggest_idle_seconds": 45.0,
    "suggest_min_text_chars": 200,
    "speak_enabled": False,
    # 流水线可见性：把"收到事件/跳过原因/派发结论"写进日志与状态快照。
    # 默认开——这是用户排查"为什么没反应"的唯一入口，噪音很低（跳过原因只在变化时打）。
    "verbose_log": True,
    "cooldown_minutes": 2.0,
    "daily_cap": 60,
    "min_request_interval_seconds": 30,
    "max_comment_chars": 40,
    "excerpt_chars": 1200,
}

#: 数值键 → (默认值, 最小, 最大)；与 ProactiveLimiter 的有效区间对齐，
#: 避免"策略层允许、频控层又 clamp 成另一个值"的双份语义。
_INT_RANGES: dict[str, tuple[int, int, int]] = {
    "port": (8765, 0, 65535),
    "dwell_seconds": (8, 0, 600),
    "content_delta_chars": (400, 80, 20000),
    "min_text_chars": (120, 0, 5000),
    "min_idle_seconds": (20, 0, 3600),
    "min_selection_chars": (4, 1, 200),
    "daily_cap": (60, 1, 9999),
    "min_request_interval_seconds": (30, 30, 3600),
    "max_comment_chars": (40, 8, 200),
    "excerpt_chars": (1200, 200, 6000),
}
_FLOAT_RANGES: dict[str, tuple[float, float, float]] = {
    "cooldown_minutes": (2.0, 0.5, 120.0),
    "video_first_moment_seconds": (20.0, 0.0, 3600.0),
    "suggest_min_dwell_seconds": (25.0, 0.0, 3600.0),
    "suggest_idle_seconds": (45.0, 0.0, 3600.0),
}
_BOOL_KEYS = (
    "enabled",
    "dry_run",
    "comment_enabled",
    "require_idle",
    "pause_when_agent_busy",
    "selection_enabled",
    "video_enabled",
    "suggest_enabled",
    "speak_enabled",
    "verbose_log",
)
_LIST_KEYS = ("whitelist", "blacklist")


def _clamp_num(value: Any, default: float, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return float(default)
    try:
        num = float(value)
    except (TypeError, ValueError):
        return float(default)
    if num != num:  # NaN
        return float(default)
    return max(minimum, min(maximum, num))


def effective_web_watch_config(raw: dict | None) -> dict[str, Any]:
    """算出有效运行时配置：补默认值、clamp 数值、规范化布尔与名单。"""
    result = dict(DEFAULT_WEB_WATCH_CONFIG)
    raw = raw if isinstance(raw, dict) else {}
    for key, value in raw.items():
        if key in result and value is not None:
            result[key] = value

    for key in _BOOL_KEYS:
        result[key] = bool(result.get(key, DEFAULT_WEB_WATCH_CONFIG[key]))
    for key in _LIST_KEYS:
        value = result.get(key)
        if isinstance(value, (list, tuple)):
            result[key] = [str(item).strip() for item in value if str(item).strip()]
        else:
            result[key] = []
    result["token"] = str(result.get("token") or "").strip()
    for key, (default, low, high) in _INT_RANGES.items():
        result[key] = int(round(_clamp_num(result.get(key), default, low, high)))
    for key, (default, low, high) in _FLOAT_RANGES.items():
        result[key] = _clamp_num(result.get(key), default, low, high)

    # require_idle=False 时闲置门形同不存在（与 proactive 同口径，便于调用方直接比大小）
    if not result["require_idle"]:
        result["min_idle_seconds"] = 0
    return result


@dataclass(frozen=True)
class Gates:
    """调用方（Qt 服务）提供的现场门：桌宠是否可见、用户是否在忙。"""

    visible: bool = True
    user_idle_seconds: float = 0.0
    agent_busy: bool = False
    fullscreen: bool = False


@dataclass(frozen=True)
class Decision:
    """一次"该说话了"的决策（策略层产出，交给服务层做频控与实际生成）。"""

    action: str
    reason: str
    digest: dict


class WebWatchPolicy:
    """页级触发状态机（跨页全局频控不在此处）。

    - `observe(event, gates, now)`：处理一条浏览器事件（打开/变化/划词/视频/离开）；
    - `poll(gates, now)`：定时轮询，负责"停留够了"和"发呆久了给建议"这两类
      时间驱动触发（事件本身不会周期性到达）；
    - `release(decision)`：频控层拒绝（冷却中/超额）时撤销决策，避免白白烧掉
      "这一页已经说过话"的额度。
    """

    def __init__(self, cfg: dict | None = None, *, clock=time.time) -> None:
        self._clock = clock
        self.cfg: dict[str, Any] = effective_web_watch_config(cfg)
        self.last_skip_reason = ""
        self.reset()

    # ---- 配置与状态 ----------------------------------------------------

    def update_config(self, cfg: dict | None) -> None:
        self.cfg = effective_web_watch_config(cfg)

    def reset(self) -> None:
        """清空当前页状态（换页/停用/换配置时调用）。"""
        self._key = ""
        self._first_seen = 0.0
        self._text_len = 0
        self._hash = ""
        self._commented = False
        self._commented_len = 0
        self._suggested = False
        self._last_selection_hash = ""
        self._video_marked = False
        # 页内缓存：poll 在没有新事件时也要能复现摘要
        self._title = ""
        self._headings: tuple[str, ...] = ()
        self._text = ""
        # 被闸门拦下、待补说的划词（poll 消费）
        self._pending_selection = ""

    def snapshot(self) -> dict[str, Any]:
        """给设置页/日志看的只读状态（不含任何网页正文，避免隐私外泄）。"""
        return {
            "domain": domain_of(self._key),
            "dwell_seconds": round(max(0.0, self._clock() - self._first_seen), 1) if self._key else 0.0,
            "commented": self._commented,
            "suggested": self._suggested,
            "last_skip_reason": self.last_skip_reason,
        }

    # ---- 守卫 ----------------------------------------------------------

    def _gate_skip(self, gates: Gates) -> str:
        """返回非空字符串 = 被守卫拦下（同时记录原因供诊断）。

        守卫只决定"此刻能不能开口"，**不决定"要不要记住这一页"**：
        `observe` 先更新页内状态再问这里，否则 agent 忙时打开的一页会被整条丢弃，
        等 agent 空闲下来也永远不会评论它（实机踩过）。
        """
        if not gates.visible:
            return "invisible"
        if self.cfg["pause_when_agent_busy"] and gates.agent_busy:
            return "agent_busy"
        if gates.fullscreen:
            return "fullscreen"
        if self.cfg["require_idle"] and gates.user_idle_seconds < self.cfg["min_idle_seconds"]:
            return "user_active"
        return ""

    def _skip(self, reason: str) -> None:
        self.last_skip_reason = reason
        return None

    # ---- 事件入口 ------------------------------------------------------

    def observe(self, event: WebEvent, gates: Gates, now: float | None = None) -> Decision | None:
        """处理一条事件；返回决策或 None（被守卫/阈值/去重拦下）。

        顺序很关键：**先记录页内状态，再问守卫**。守卫只决定"此刻能不能开口"，
        若在记录之前就因守卫返回，agent 忙 / 桌宠隐藏期间打开的那一页会被整条
        丢弃——用户回到桌面前后都不会再评论它（实机踩过：/health 200、事件 204、
        日志却毫无动静）。
        """
        now = self._clock() if now is None else now
        cfg = self.cfg
        if not cfg["enabled"]:
            return self._skip("disabled")

        if event.kind == "page_leave":
            self.reset()
            return self._skip("page_leave")

        domain = domain_of(event.url)
        if not domain_allowed(domain, blacklist=cfg["blacklist"], whitelist=cfg["whitelist"]):
            return self._skip("domain_blocked")

        if event.kind == "heartbeat":
            return self._skip("heartbeat")

        # 换页 = 页面身份变化：重置页级状态（停留计时从这一刻起算）
        if event.url != self._key:
            self.reset()
            self._key = event.url
            self._first_seen = now
        self.remember_page(event)

        text = event.text or ""
        if len(text) > self._text_len:
            self._text_len = len(text)
        new_hash = content_hash(event.url, event.title, text)
        if new_hash != self._hash:
            self._hash = new_hash

        blocked = self._gate_skip(gates)
        if blocked:
            # 被闸门拦下也要留住"用户刚划了这段"：闸门放行后由 poll 补说，
            # 否则划词这种最强信号会在 agent 忙/桌宠隐藏时白白丢掉。
            if event.kind == "selection" and cfg["selection_enabled"]:
                selection = (event.selection or "").strip()
                if len(selection) >= cfg["min_selection_chars"]:
                    self._pending_selection = selection
            return self._skip(blocked)

        if event.kind == "selection":
            return self._on_selection(event, now)
        if event.kind == "video":
            return self._on_video(event, now)
        # 正文增量：长文/文档页一边滚一边加载，读到新内容时值得再补一句
        delta = self._text_len - self._commented_len
        if (
            event.kind in ("page_open", "page_update")
            and cfg["comment_enabled"]
            and self._commented
            and delta >= cfg["content_delta_chars"]
        ):
            return self._comment("content_delta", event, now)
        # page_open / page_update：不立刻说话，交给 poll 判停留（事件只刷新状态）
        if cfg["dwell_seconds"] <= 0:
            return self._comment("page_dwell", event, now)
        return self._skip("waiting_dwell")

    # ---- 定时入口 ------------------------------------------------------

    def poll(self, gates: Gates, now: float | None = None) -> Decision | None:
        """时间驱动触发：停留够了 → 评论；发呆久了且页面值得 → 建议。"""
        now = self._clock() if now is None else now
        cfg = self.cfg
        if not cfg["enabled"] or not self._key:
            return self._skip("no_page")
        blocked = self._gate_skip(gates)
        if blocked:
            return self._skip(blocked)

        domain = domain_of(self._key)
        if not domain_allowed(domain, blacklist=cfg["blacklist"], whitelist=cfg["whitelist"]):
            return self._skip("domain_blocked")

        # 闸门拦下时留下的划词：闸门一放行就优先补说（划词是最强信号）
        if cfg["selection_enabled"] and self._pending_selection:
            selection = self._pending_selection
            self._pending_selection = ""
            self._commented = True
            self._commented_len = self._text_len
            self._last_selection_hash = content_hash(selection)
            return Decision(ACTION_COMMENT, "selection", self._make_digest(self._selection_event(selection)))

        dwell = now - self._first_seen
        # 先把"为什么还没说话"说清楚：笼统的 none 对用户排查毫无帮助
        if not self._commented and dwell < cfg["dwell_seconds"]:
            return self._skip("waiting_dwell")
        if not self._commented and self._text_len < cfg["min_text_chars"]:
            return self._skip("text_too_short")
        if (
            cfg["comment_enabled"]
            and not self._commented
            and self._text_len >= cfg["min_text_chars"]
            and dwell >= cfg["dwell_seconds"]
        ):
            return self._comment("page_dwell", None, now)

        if (
            cfg["suggest_enabled"]
            and not self._suggested
            and self._commented
            and dwell >= cfg["suggest_min_dwell_seconds"]
            and gates.user_idle_seconds >= cfg["suggest_idle_seconds"]
            and self._text_len >= cfg["suggest_min_text_chars"]
        ):
            kind = self._page_kind()
            if kind in SUGGESTIBLE_KINDS:
                self._suggested = True
                return Decision(ACTION_SUGGEST, "page_idle_suggestion", self._make_digest(None))
        return self._skip("none")

    # ---- 判定细则 ------------------------------------------------------

    def _on_selection(self, event: WebEvent, now: float) -> Decision | None:
        cfg = self.cfg
        if not cfg["selection_enabled"]:
            return self._skip("selection_disabled")
        selection = (event.selection or "").strip()
        if len(selection) < cfg["min_selection_chars"]:
            return self._skip("selection_too_short")
        sel_hash = content_hash(selection)
        if sel_hash == self._last_selection_hash:
            return self._skip("selection_duplicate")
        self._last_selection_hash = sel_hash
        # 划词是"用户此刻正在看这段"的最强信号：跳过停留，直接说
        self._commented = True
        self._commented_len = self._text_len
        return Decision(ACTION_COMMENT, "selection", self._make_digest(event))

    def _on_video(self, event: WebEvent, now: float) -> Decision | None:
        cfg = self.cfg
        if not cfg["video_enabled"]:
            return self._skip("video_disabled")
        if self._commented or self._video_marked:
            return self._skip("video_done")
        if event.video_paused:
            return self._skip("video_paused")
        if event.video_position < cfg["video_first_moment_seconds"]:
            return self._skip("video_early")
        self._video_marked = True
        self._commented = True
        self._commented_len = self._text_len
        return Decision(ACTION_COMMENT, "video_moment", self._make_digest(event))

    def _comment(self, reason: str, event: WebEvent | None, now: float) -> Decision | None:
        if not self.cfg["comment_enabled"]:
            return self._skip("comment_disabled")
        self._commented = True
        self._commented_len = self._text_len
        return Decision(ACTION_COMMENT, reason, self._make_digest(event))

    def _selection_event(self, selection: str) -> WebEvent:
        """用页内缓存构造一条"只剩划词"的事件（给摘要用）。"""
        return WebEvent(
            kind="selection",
            url=self._key,
            title=self._title,
            text=self._text,
            headings=self._headings,
            selection=selection,
        )

    def _make_digest(self, event: WebEvent | None) -> dict:
        if event is not None:
            return build_digest(event, excerpt_chars=self.cfg["excerpt_chars"])
        return build_digest(
            WebEvent(
                kind="page_update",
                url=self._key,
                title=self._title,
                text=self._text,
                headings=self._headings,
            ),
            excerpt_chars=self.cfg["excerpt_chars"],
        )

    # 页内缓存（poll 需要在没有新事件时也能复现摘要）

    def remember_page(self, event: WebEvent) -> None:
        """记录当前页最近一次正文/标题（poll 用它构造摘要）。"""
        if event.title:
            self._title = event.title
        if event.headings:
            self._headings = event.headings
        if event.text:
            self._text = event.text

    def _page_kind(self) -> str:
        from .digest import page_kind

        return page_kind(domain_of(self._key), self._key, self._title)

    def page_kind(self) -> str:
        """当前页类型（设置页展示用）。"""
        return self._page_kind()

    # ---- 频控拒绝回滚 --------------------------------------------------

    def release(self, decision: Decision | None) -> None:
        """频控层拒绝该决策时调用：撤销"已说过话"的标记，下次还有机会。"""
        if decision is None:
            return
        if decision.action == ACTION_COMMENT:
            self._commented = False
            self._video_marked = False
        elif decision.action == ACTION_SUGGEST:
            self._suggested = False
