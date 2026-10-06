# -*- coding: utf-8 -*-
"""网页评论的非流式短文本请求 —— 仿 `pet/vision.py::_post_vision_request` 的骨架。

与视觉请求的差别只有三处：没有图片 part、`max_tokens` 收到几百、
输出交给 `prompts.clean_reply()` 清洗（含 `<SKIP>` 弃权）。

导入纪律（与 vision.py 一致）：顶层不 import `pet.chat`，无 Chat 打包变体
（`packaging/pet_entry_no_chat.py`）下本模块不会被调用。
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from typing import Any, Callable

log = logging.getLogger("dsh-pet-standalone")

#: 短评不需要长输出；太大反而拖慢生成（实测模型往返中位 4s、最差 19s）。
#: 40 字评论 + 少量余量即可，取 192 留出推理模型偶尔的思考 token。
DEFAULT_MAX_TOKENS = 192
#: 单次请求超时下限。实测免费档偶发卡住（>36s 无响应）：原来 60s × 3 次重试
#: = 最长静默 3 分钟，用户体感就是"它死了"。收到 30s 并只重试 1 次（共 2 次尝试），
#: 卡住的请求快速失败，不阻塞后续页面的互动。
MIN_TIMEOUT_SECONDS = 30.0
#: 最多尝试次数（含首次）
MAX_ATTEMPTS = 2


class WebWatchLlmError(RuntimeError):
    """网页评论请求失败（网络/额度/空回复）。"""


def _detail(raw: bytes) -> str:
    try:
        data = json.loads(raw.decode("utf-8", "replace"))
    except Exception:
        return " ".join(raw.decode("utf-8", "replace").split())[:300] or "Provider 请求失败"
    if isinstance(data, dict) and isinstance(data.get("error"), dict):
        return str(data["error"].get("message", "Provider 请求失败"))
    return "Provider 请求失败"


def _extract_text(data: Any) -> str:
    choices = data.get("choices") if isinstance(data, dict) else None
    if not choices:
        raise WebWatchLlmError("模型没说话（无 choices）")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, list):  # 部分实现把 content 拆成 parts
        content = "".join(
            str(part.get("text", "")) for part in content if isinstance(part, dict) and part.get("type") == "text"
        )
    text = content.strip() if isinstance(content, str) else ""
    if not text:
        raise WebWatchLlmError("模型没说话（空回复）")
    return text


def post_text_request(
    messages: list[dict[str, Any]],
    provider: Any,
    *,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    consume_budget: Callable[[], bool] | None = None,
    timeout: float | None = None,
) -> str:
    """向聊天 provider 发一次非流式请求，返回整段文本。

    consume_budget：每次真实 HTTP 请求前调用（返回 False = 当日额度已尽，
    立即停止，不再重试）——由 ProactiveLimiter 提供，与主动识屏同一套语义。
    """
    # 延迟导入：顶层 import pet.chat 会让无 Chat 变体启动即崩（见模块 docstring）
    from ..chat.providers import _make_ssl_context, build_browser_headers, normalize_chat_endpoint

    endpoint = normalize_chat_endpoint(provider.base_url, provider.chat_path)
    payload = {
        "model": provider.model,
        "messages": messages,
        "stream": False,
        "temperature": provider.temperature,
        "max_tokens": max(64, min(int(max_tokens), 1024)),
    }
    model_name = str(payload["model"] or "")
    base_url = str(provider.base_url or "")
    if model_name.lower().startswith("deepseek") and "deepseek" in base_url.lower():
        # 推理模型默认开思考（十几秒才说话）：短评关掉推理直答
        payload["thinking"] = {"type": "disabled"}
    headers = build_browser_headers({"Content-Type": "application/json"})
    if getattr(provider, "api_key", ""):
        headers["Authorization"] = f"Bearer {provider.api_key}"

    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    effective_timeout = max(float(timeout or getattr(provider, "timeout", 0) or 0), MIN_TIMEOUT_SECONDS)
    data: Any = None
    last_error: Exception | None = None
    for attempt in range(MAX_ATTEMPTS):
        if consume_budget is not None and not consume_budget():
            raise WebWatchLlmError("今天的网页互动额度用完了")
        try:
            with urllib.request.urlopen(
                request,
                timeout=effective_timeout,
                context=_make_ssl_context(getattr(provider, "verify_ssl", True)),
            ) as resp:
                data = json.loads(resp.read().decode("utf-8", "replace"))
            break
        except urllib.error.HTTPError as exc:
            try:
                raw = exc.read(2048)
            except Exception:
                raw = b""
            if exc.code == 429 and attempt < 1:
                last_error = exc
                time.sleep(2.0)
                continue
            raise WebWatchLlmError(_detail(raw)) from exc
        except (urllib.error.URLError, OSError) as exc:
            last_error = exc
        if attempt < MAX_ATTEMPTS - 1:
            time.sleep(1.0)
    if data is None:
        if isinstance(last_error, urllib.error.HTTPError):
            raise WebWatchLlmError("模型当前访问量大（免费档高峰限流），等会儿再说") from last_error
        reason = getattr(last_error, "reason", last_error)
        raise WebWatchLlmError(f"网络连接失败：{reason}")
    return _extract_text(data)
