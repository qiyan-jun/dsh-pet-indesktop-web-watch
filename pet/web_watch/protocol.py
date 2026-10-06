# -*- coding: utf-8 -*-
"""Edge 网页互动（web watch）事件协议 —— 零 Qt 纯逻辑层。

浏览器扩展把页面信息 POST 到桌宠的本地回环端口，这里是**唯一一次**校验与裁剪：
不可信字段（网页标题/正文/划词都来自外部页面）一律降级为安全默认或截断，
绝不把原始输入直接带进触发策略与提示词。

隐私边界（与 pet/proactive.py 的"标题不落盘"同一口径）：
- URL 只保留 scheme/host/path，**查询串与锚点一律丢弃**（搜索词、token、追踪参数）；
- 正文只保留调用方明确提交的正文摘要字段，不做二次抓取；
- 本模块不写文件、不发网络、不依赖 Qt。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: 事件类型：页面打开 / 页面内容变化 / 划词 / 视频进度 / 离开 / 心跳
EVENT_KINDS = ("page_open", "page_update", "selection", "video", "page_leave", "heartbeat")

MAX_URL_CHARS = 2048
MAX_TITLE_CHARS = 300
MAX_TEXT_CHARS = 6000
MAX_SELECTION_CHARS = 800
MAX_HEADINGS = 8
MAX_HEADING_CHARS = 120

#: 请求体字节上限：超出即拒绝（HTTP 413），不解析、不记录内容
MAX_BODY_BYTES = 64 * 1024


def _clean_str(value: Any, limit: int) -> str:
    """把任意输入压成有上限的单行字符串（控制字符与多余空白一并清除）。"""
    if not isinstance(value, str):
        return ""
    # 去掉控制字符（含 \r \t），再把连续空白折叠成单个空格
    cleaned = "".join(" " if ch in "\r\n\t" else ch for ch in value if ch == " " or ch >= " ")
    cleaned = " ".join(cleaned.split())
    if len(cleaned) > limit:
        cleaned = cleaned[:limit]
    return cleaned


def _clean_float(value: Any, default: float = 0.0) -> float:
    """数值字段：bool 视为非法（True 静默变 1.0 属错误类型穿透）。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    try:
        num = float(value)
    except (TypeError, ValueError):
        return default
    if num != num or num in (float("inf"), float("-inf")):  # NaN / inf
        return default
    return num


def _clean_headings(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    out: list[str] = []
    for item in value:
        text = _clean_str(item, MAX_HEADING_CHARS)
        if text:
            out.append(text)
        if len(out) >= MAX_HEADINGS:
            break
    return tuple(out)


def strip_url(url: str) -> str:
    """只保留 scheme://host/path：查询串与锚点是隐私高发区，直接丢弃。"""
    text = _clean_str(url, MAX_URL_CHARS)
    if not text:
        return ""
    for sep in ("?", "#"):
        idx = text.find(sep)
        if idx >= 0:
            text = text[:idx]
    return text


@dataclass(frozen=True)
class WebEvent:
    """一条已校验的网页事件（不可变，跨线程传递安全）。"""

    kind: str
    url: str = ""
    title: str = ""
    text: str = ""
    headings: tuple[str, ...] = ()
    selection: str = ""
    video_title: str = ""
    video_position: float = 0.0
    video_duration: float = 0.0
    video_paused: bool = False
    ts: float = 0.0

    @property
    def is_page_scoped(self) -> bool:
        """是否属于"当前页面"身份（换页判定只看这些事件）。"""
        return self.kind in ("page_open", "page_update", "selection", "video")


def parse_event(payload: Any) -> WebEvent | None:
    """校验并裁剪一条事件；非法输入返回 None（调用方按"丢弃"处理，不抛异常）。"""
    if not isinstance(payload, dict):
        return None

    kind = _clean_str(payload.get("kind"), 32).lower()
    if kind not in EVENT_KINDS:
        return None

    url = strip_url(payload.get("url", ""))
    if kind != "heartbeat" and not url.lower().startswith(("http://", "https://")):
        # 非 http(s)（浏览器内部页、本地文件、扩展页）一律不参与互动
        return None

    return WebEvent(
        kind=kind,
        url=url,
        title=_clean_str(payload.get("title"), MAX_TITLE_CHARS),
        text=_clean_str(payload.get("text"), MAX_TEXT_CHARS),
        headings=_clean_headings(payload.get("headings")),
        selection=_clean_str(payload.get("selection"), MAX_SELECTION_CHARS),
        video_title=_clean_str(payload.get("video_title"), MAX_TITLE_CHARS),
        video_position=max(0.0, _clean_float(payload.get("video_position"))),
        video_duration=max(0.0, _clean_float(payload.get("video_duration"))),
        video_paused=bool(payload.get("video_paused", False)),
        ts=max(0.0, _clean_float(payload.get("ts"))),
    )
