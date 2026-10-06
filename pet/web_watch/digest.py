# -*- coding: utf-8 -*-
"""网页内容摘要 —— 零 Qt 纯逻辑层。

把一页网页压成"够模型说一句相关的话、又不泄露隐私"的最小摘要：
只留域名/路径（丢查询串）、标题、前几个小标题、一段正文摘录、划词。

本模块不写文件、不发网络、不依赖 Qt，可直接单测。
"""
from __future__ import annotations

import hashlib
import re
from typing import Any
from urllib.parse import urlsplit

from .protocol import WebEvent, strip_url

#: 交给模型的正文摘录上限（字符）。太长既费 token 也容易把无关内容塞进提示词。
DEFAULT_EXCERPT_CHARS = 1200
#: 摘要里保留的小标题条数
MAX_HEADINGS_FOR_PROMPT = 3
#: 路径超过该长度时截断（有些站点把参数塞在路径里）
MAX_PATH_CHARS = 120

_PAGE_KIND_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("video", ("bilibili.com", "youtube.com", "youtu.be", "iqiyi.com", "v.qq.com", "youku.com", "douyin.com", "netflix.com")),
    ("code", ("github.com", "gitlab.com", "stackoverflow.com", "developer.mozilla.org", "docs.python.org", "readthedocs.io", "pypi.org", "nuget.org", "npmjs.com")),
    ("paper", ("arxiv.org", "scholar.google", "researchgate.net", "ieeexplore", "sciencedirect", "springer", "cnki.net", "doi.org")),
    ("shopping", ("taobao.com", "tmall.com", "jd.com", "pinduoduo.com", "amazon.", "ebay.", "suning.com")),
    ("qa", ("zhihu.com", "reddit.com", "quora.com", "v2ex.com", "stackexchange.com", "tieba.baidu.com")),
    ("news", ("news.", "36kr.com", "sspai.com", "thepaper.cn", "xinhuanet.com", "bbc.com", "reuters.com")),
    ("doc", ("docs.", "notion.so", "yuque.com", "feishu.cn", "confluence", "wikipedia.org", "baike.baidu.com")),
)

#: 值得主动给建议的页面类型（"建议"只在有明确增益的场合出现，日常刷视频/购物不给）
SUGGESTIBLE_KINDS = ("code", "paper", "doc", "news", "qa")

_KIND_LABELS = {
    "video": "视频",
    "code": "代码/技术文档",
    "paper": "论文/学术",
    "shopping": "购物",
    "qa": "问答社区",
    "news": "新闻资讯",
    "doc": "文档/百科",
    "other": "普通网页",
}


def domain_of(url: str) -> str:
    """取小写域名并去掉 www. 前缀；非法 URL 返回空串。"""
    text = strip_url(url)
    if not text:
        return ""
    try:
        host = urlsplit(text).hostname or ""
    except ValueError:
        return ""
    host = host.lower().strip()
    if host.startswith("www."):
        host = host[4:]
    return host


def url_for_prompt(url: str) -> str:
    """给模型看的 URL：只留 scheme://host/path，路径超长截断。"""
    text = strip_url(url)
    if not text:
        return ""
    try:
        parts = urlsplit(text)
    except ValueError:
        return ""
    path = parts.path or "/"
    if len(path) > MAX_PATH_CHARS:
        path = path[:MAX_PATH_CHARS] + "…"
    scheme = parts.scheme or "https"
    return f"{scheme}://{parts.netloc}{path}"


def match_domain_rules(rules: list[str] | None, domain: str) -> bool:
    """域名规则匹配（与 proactive 白名单同口径：大小写不敏感 + fnmatch 通配）。

    规则为空 = 不匹配（调用方决定"空名单"的语义，本函数不做默认放行）。
    """
    import fnmatch

    if not rules or not domain:
        return False
    target = domain.strip().lower()
    for rule in rules:
        if not isinstance(rule, str):
            continue
        pattern = rule.strip().lower()
        if not pattern:
            continue
        if fnmatch.fnmatch(target, pattern):
            return True
        # "example.com" 也应命中 "news.example.com"（用户不必写 *.）
        if not pattern.startswith("*") and fnmatch.fnmatch(target, "*." + pattern):
            return True
    return False


def domain_allowed(domain: str, *, blacklist: list[str] | None, whitelist: list[str] | None) -> bool:
    """黑名单优先；白名单为空 = 允许其余全部域名。"""
    if not domain:
        return False
    if match_domain_rules(blacklist, domain):
        return False
    if whitelist:
        return match_domain_rules(whitelist, domain)
    return True


def content_hash(*parts: Any) -> str:
    """内容指纹（标题/URL/正文）：用于"同一页没变过就别重复说话"。"""
    digest = hashlib.sha1()
    for part in parts:
        if isinstance(part, (list, tuple)):
            text = "|".join(str(item) for item in part)
        else:
            text = str(part or "")
        digest.update(text.encode("utf-8", "replace"))
        digest.update(b"\x1f")
    return digest.hexdigest()[:16]


def excerpt(text: str, limit: int = DEFAULT_EXCERPT_CHARS) -> str:
    """正文摘录：折叠空白后截断（不追加省略号，避免模型把它当成正文的一部分）。"""
    cleaned = " ".join((text or "").split())
    if limit > 0 and len(cleaned) > limit:
        return cleaned[:limit]
    return cleaned


def page_kind(domain: str, url: str = "", title: str = "") -> str:
    """本地关键词页面分类（零网络、零模型）：决定要不要给建议、给什么口吻。"""
    haystack = f"{domain} {url} {title}".lower()
    if not haystack.strip():
        return "other"
    for kind, needles in _PAGE_KIND_RULES:
        if any(needle in haystack for needle in needles):
            return kind
    if re.search(r"\.(pdf)(\b|$)", haystack):
        return "paper"
    return "other"


def page_kind_label(kind: str) -> str:
    return _KIND_LABELS.get(kind, _KIND_LABELS["other"])


def build_digest(event: WebEvent, *, excerpt_chars: int = DEFAULT_EXCERPT_CHARS) -> dict:
    """把事件压成模型可读的最小摘要（同时作为缓存指纹的输入）。"""
    domain = domain_of(event.url)
    kind = page_kind(domain, event.url, event.title)
    body = excerpt(event.text, excerpt_chars)
    digest = {
        "domain": domain,
        "kind": kind,
        "kind_label": page_kind_label(kind),
        "url": url_for_prompt(event.url),
        "title": event.title,
        "headings": list(event.headings[:MAX_HEADINGS_FOR_PROMPT]),
        "excerpt": body,
        "selection": event.selection,
        "video_title": event.video_title,
        "video_position": event.video_position,
        "video_duration": event.video_duration,
    }
    digest["hash"] = content_hash(domain, digest["url"], event.title, body, event.selection)
    return digest
