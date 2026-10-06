# -*- coding: utf-8 -*-
"""网页评论的提示词构造 —— 零 Qt 纯逻辑层。

把 `digest.build_digest()` 的摘要变成两条消息（system 人设 + user 页面信息），
并约定"模型可以拒绝说话"的逃生口（`<SKIP>`）：宁可不冒泡，也不硬凑一句废话。
"""
from __future__ import annotations

import re
from typing import Any

from .policy import ACTION_SUGGEST

#: 模型主动弃权标记（模型认为这页没什么可说时回复它，桌宠就不冒泡）
SKIP_TOKEN = "<SKIP>"

_COMMENT_RULES = (
    "你正在看主人浏览器里打开的网页。用一句话说点跟当前页面内容真正相关的评论"
    "（{max_chars} 字以内，中文，口语化，像随口感叹或吐槽）。"
    "要求：① 抓住内容里的具体点（某个观点、某个数字、某个写法），不要复述标题；"
    "② 不要用「我注意到」「看起来您」「建议您」这类助手腔；"
    "③ 不要输出 emoji、引号、markdown、换行；"
    "④ 如果你确实看不出值得一说的东西，只回复 {skip}。"
)

_SUGGEST_RULES = (
    "主人已经在这个网页上停留一段时间了。给一条**具体可执行**的小建议"
    "（{max_chars} 字以内，中文，一句话）。"
    "要求：① 必须针对当前内容本身（代码页可指向可验证/可复用之处，"
    "文档页可指向可沉淀成笔记的要点，论文页可指向方法与结论，"
    "新闻页可指出值得对照的其它信息）；② 不要泛泛而谈「多喝水」「注意休息」；"
    "③ 不要输出 emoji、引号、markdown、换行；"
    "④ 如果没什么值得建议的，只回复 {skip}。"
)


def _format_seconds(value: float) -> str:
    total = int(max(0.0, value))
    minutes, seconds = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def format_page(digest: dict[str, Any]) -> str:
    """把摘要压成给模型看的紧凑段落（不含查询串、不含表单内容）。"""
    lines: list[str] = []
    title = str(digest.get("title") or "").strip()
    if title:
        lines.append(f"页面标题：{title}")
    domain = str(digest.get("domain") or "").strip()
    kind_label = str(digest.get("kind_label") or "").strip()
    if domain:
        lines.append(f"站点：{domain}（{kind_label}）" if kind_label else f"站点：{domain}")
    url = str(digest.get("url") or "").strip()
    if url:
        lines.append(f"地址：{url}")
    headings = digest.get("headings") or []
    if headings:
        lines.append("小标题：" + " / ".join(str(h) for h in headings))
    video_title = str(digest.get("video_title") or "").strip()
    if video_title:
        position = _format_seconds(float(digest.get("video_position") or 0.0))
        duration = _format_seconds(float(digest.get("video_duration") or 0.0))
        lines.append(f"正在播放：{video_title}（{position}/{duration}）")
    selection = str(digest.get("selection") or "").strip()
    if selection:
        lines.append(f"主人刚选中了这段文字：「{selection}」")
        lines.append("请优先针对这段被选中的文字来评论。")
    excerpt_text = str(digest.get("excerpt") or "").strip()
    if excerpt_text:
        lines.append(f"页面正文节选：{excerpt_text}")
    if not lines:
        lines.append("（页面信息不足）")
    return "\n".join(lines)


def build_system_prompt(persona: str, *, action: str, max_chars: int, pet_name: str = "") -> str:
    """人设 + 身份 + 本次任务的输出约束。"""
    rules = (_SUGGEST_RULES if action == ACTION_SUGGEST else _COMMENT_RULES).format(
        max_chars=max_chars, skip=SKIP_TOKEN
    )
    parts = [str(persona or "").strip()]
    if pet_name:
        parts.append(f"你的名字是「{pet_name}」。")
    parts.append(rules)
    return "\n".join(part for part in parts if part)


def build_messages(
    digest: dict[str, Any],
    *,
    action: str,
    persona: str,
    max_chars: int,
    pet_name: str = "",
) -> list[dict[str, Any]]:
    """构造两条消息（system + user），与 vision/chat 同口径的 OpenAI 兼容格式。"""
    return [
        {"role": "system", "content": build_system_prompt(persona, action=action, max_chars=max_chars, pet_name=pet_name)},
        {"role": "user", "content": format_page(digest)},
    ]


def clean_reply(text: str, max_chars: int) -> str:
    """清洗模型输出：去引号/换行/markdown 痕迹并截断；返回空串表示"不说"。"""
    if not isinstance(text, str):
        return ""
    stripped = text.strip()
    if not stripped:
        return ""
    if SKIP_TOKEN.lower() in stripped.lower():
        return ""
    # 只取第一行（模型偶尔会带上解释），去掉常见包装
    first = stripped.splitlines()[0].strip()
    first = re.sub(r"^[「『\"'“”‘’\s]+", "", first)
    first = re.sub(r"[」』\"'“”‘’\s]+$", "", first)
    first = first.replace("**", "").replace("`", "")
    first = " ".join(first.split())
    if len(first) > max_chars:
        first = first[:max_chars].rstrip()
    return first
