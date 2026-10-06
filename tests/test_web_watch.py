# -*- coding: utf-8 -*-
"""web_watch 纯逻辑层契约测试（协议解析 / 隐私摘要 / 触发策略）。

零 Qt、零网络、零文件：这三块是"该不该说话"的全部判定，必须能脱离 GUI 单测。
策略用注入时钟与显式 now 驱动，不赌真实时间（AGENTS.md 时序纪律）。
"""
from __future__ import annotations

import pytest

from pet.web_watch.digest import (
    build_digest,
    content_hash,
    domain_allowed,
    domain_of,
    excerpt,
    match_domain_rules,
    page_kind,
    url_for_prompt,
)
from pet.web_watch.policy import (
    ACTION_COMMENT,
    ACTION_SUGGEST,
    DEFAULT_WEB_WATCH_CONFIG,
    Gates,
    WebWatchPolicy,
    effective_web_watch_config,
)
from pet.web_watch.protocol import parse_event, strip_url


# ---------------------------------------------------------------- 工具


def _event(**kwargs):
    payload = {"kind": "page_open", "url": "https://example.com/a", "title": "标题", "text": "正文" * 40}
    payload.update(kwargs)
    event = parse_event(payload)
    assert event is not None, payload
    return event


def _enabled_policy(**overrides):
    cfg = dict(DEFAULT_WEB_WATCH_CONFIG)
    cfg["enabled"] = True
    cfg.update(overrides)
    return WebWatchPolicy(cfg)


# ---------------------------------------------------------------- protocol


def test_parse_event_rejects_unknown_kind_and_non_http_url():
    assert parse_event({"kind": "nope", "url": "https://a.com"}) is None
    assert parse_event({"kind": "page_open", "url": "edge://settings"}) is None
    assert parse_event({"kind": "page_open", "url": "file:///C:/x.html"}) is None
    assert parse_event("not-a-dict") is None


def test_parse_event_drops_query_and_fragment_for_privacy():
    # 搜索词/追踪参数/登录 token 都可能出现在查询串里：一律不进摘要
    assert strip_url("https://a.com/s?q=secret&token=abc#frag") == "https://a.com/s"
    assert parse_event({"kind": "page_open", "url": "https://a.com/s?q=secret"}).url == "https://a.com/s"


def test_parse_event_truncates_and_sanitizes_fields():
    event = parse_event(
        {
            "kind": "page_update",
            "url": "https://a.com/",
            "title": "t" * 500,
            "text": "x" * 20000,
            "headings": ["h1", "", "h2"] * 5,
            "video_position": float("nan"),
            "video_paused": 1,
        }
    )
    assert len(event.title) == 300
    assert len(event.text) == 6000
    assert len(event.headings) == 8
    assert event.video_position == 0.0  # NaN 不得穿透成脏数值
    assert event.video_paused is True


# ---------------------------------------------------------------- digest


def test_domain_and_url_normalization():
    assert domain_of("https://WWW.Example.com/x?y=1") == "example.com"
    assert domain_of("not a url") == ""
    assert url_for_prompt("https://a.com/p?a=1") == "https://a.com/p"


def test_match_domain_rules_supports_subdomain_and_wildcards():
    assert match_domain_rules(["example.com"], "news.example.com") is True
    assert match_domain_rules(["*.example.com"], "news.example.com") is True
    assert match_domain_rules(["example.com"], "example.com.cn") is False
    assert match_domain_rules([], "example.com") is False
    assert match_domain_rules(["*"], "anything.com") is True


def test_domain_allowed_blacklist_wins_and_empty_whitelist_allows_all():
    assert domain_allowed("a.com", blacklist=[], whitelist=[]) is True
    assert domain_allowed("a.com", blacklist=["a.com"], whitelist=[]) is False
    assert domain_allowed("a.com", blacklist=["a.com"], whitelist=["a.com"]) is False
    assert domain_allowed("b.com", blacklist=["a.com"], whitelist=["a.com"]) is False
    assert domain_allowed("", blacklist=[], whitelist=[]) is False


def test_page_kind_classifies_known_sites():
    assert page_kind("bilibili.com") == "video"
    assert page_kind("github.com") == "code"
    assert page_kind("arxiv.org") == "paper"
    assert page_kind("jd.com") == "shopping"
    assert page_kind("zhihu.com") == "qa"
    assert page_kind("unknown.example") == "other"


def test_build_digest_excludes_query_and_carries_stable_hash():
    event = _event(url="https://news.example.com/p?token=secret", selection="选中的话")
    digest = build_digest(event)
    assert "secret" not in digest["url"]
    assert digest["domain"] == "news.example.com"
    assert digest["selection"] == "选中的话"
    assert digest["hash"] == build_digest(event)["hash"]
    assert digest["hash"] != build_digest(_event(text="别的正文"))["hash"]


def test_excerpt_collapses_whitespace_and_caps_length():
    assert excerpt("a\n\n  b\tc", 100) == "a b c"
    assert len(excerpt("x" * 100, 10)) == 10


# ---------------------------------------------------------------- policy 配置


@pytest.mark.parametrize(
    "raw,key,expected",
    [
        ({"dwell_seconds": -5}, "dwell_seconds", 0),
        ({"dwell_seconds": 9999}, "dwell_seconds", 600),
        ({"cooldown_minutes": 0.05}, "cooldown_minutes", 0.1),
        ({"daily_cap": 0}, "daily_cap", 1),
        ({"min_request_interval_seconds": 1}, "min_request_interval_seconds", 10),
        ({"port": 70000}, "port", 65535),
        ({"dwell_seconds": "abc"}, "dwell_seconds", 8),
        ({"unknown_key": 1}, "unknown_key", None),
    ],
)
def test_effective_config_clamps_and_ignores_unknown(raw, key, expected):
    eff = effective_web_watch_config(raw)
    if expected is None:
        assert key not in eff
    else:
        assert eff[key] == expected


def test_require_idle_false_neutralizes_idle_threshold():
    assert effective_web_watch_config({"require_idle": False, "min_idle_seconds": 30})["min_idle_seconds"] == 0
    assert effective_web_watch_config({"require_idle": True, "min_idle_seconds": 30})["min_idle_seconds"] == 30


def test_config_defaults_stay_in_sync_with_policy_defaults():
    """config.py 的落盘默认值与策略层有效默认值必须逐键同值。

    两处是刻意分开的（config 负责落盘/迁移，policy 负责运行时 clamp），但默认值
    漂移会让"设置页显示的初值"与"实际生效值"对不上——用等值断言把漂移打红。
    """
    from pet.config import _default_web_watch_data

    assert _default_web_watch_data() == DEFAULT_WEB_WATCH_CONFIG


def test_whitelist_and_blacklist_are_normalized():
    eff = effective_web_watch_config({"whitelist": [" a.com ", "", None, 3], "blacklist": "not-a-list"})
    assert eff["whitelist"] == ["a.com", "None", "3"]
    assert eff["blacklist"] == []


# ---------------------------------------------------------------- policy 触发


def test_disabled_by_default_never_triggers():
    policy = WebWatchPolicy(DEFAULT_WEB_WATCH_CONFIG)
    assert policy.observe(_event(), Gates(), now=100.0) is None
    assert policy.poll(Gates(), now=1000.0) is None


def test_dwell_triggers_comment_once_per_page():
    policy = _enabled_policy(dwell_seconds=8, min_text_chars=10)
    policy.observe(_event(), Gates(), now=100.0)
    assert policy.poll(Gates(), now=105.0) is None, "停留不足不得说话"
    decision = policy.poll(Gates(), now=109.0)
    assert decision is not None and decision.action == ACTION_COMMENT
    assert decision.reason == "page_dwell"
    assert policy.poll(Gates(), now=200.0) is None, "同一页只评论一次"


def test_release_lets_page_comment_again_after_rate_limit_refusal():
    policy = _enabled_policy(dwell_seconds=0, min_text_chars=10)
    decision = policy.observe(_event(), Gates(), now=100.0)
    assert decision is not None
    assert policy.poll(Gates(), now=101.0) is None
    policy.release(decision)  # 频控拒绝 → 撤销"已说话"标记
    assert policy.poll(Gates(), now=102.0) is not None


def test_content_growth_triggers_second_comment():
    policy = _enabled_policy(content_delta_chars=100, min_text_chars=10)
    policy.observe(_event(text="开篇" * 100), Gates(), now=100.0)
    assert policy.poll(Gates(), now=200.0) is not None  # 第一次（停留+评论）
    # 增量不足阈值：不得再说话
    assert policy.observe(_event(text="开篇" * 100 + "新增" * 10), Gates(), now=210.0) is None
    assert policy.poll(Gates(), now=211.0) is None
    # 增量越过阈值：说第二句（事件驱动，不必等下一次 poll）
    decision = policy.observe(_event(text="开篇" * 100 + "新增" * 60), Gates(), now=220.0)
    assert decision is not None
    assert decision.reason == "content_delta"


def test_selection_triggers_immediately_and_dedupes():
    policy = _enabled_policy(dwell_seconds=600)
    dec = policy.observe(_event(kind="selection", selection="这段话说得不对"), Gates(), now=100.0)
    assert dec is not None and dec.reason == "selection"
    assert policy.observe(_event(kind="selection", selection="这段话说得不对"), Gates(), now=101.0) is None
    assert policy.observe(_event(kind="selection", selection="换一段新的文字"), Gates(), now=102.0) is not None


def test_selection_too_short_is_ignored():
    policy = _enabled_policy(min_selection_chars=6)
    assert policy.observe(_event(kind="selection", selection="短"), Gates(), now=100.0) is None


def test_video_moment_triggers_once_and_respects_pause():
    policy = _enabled_policy(video_first_moment_seconds=20.0)
    assert policy.observe(_event(kind="video", video_position=5.0), Gates(), now=100.0) is None
    assert policy.observe(_event(kind="video", video_position=30.0, video_paused=True), Gates(), now=101.0) is None
    dec = policy.observe(_event(kind="video", video_position=30.0, video_paused=False), Gates(), now=102.0)
    assert dec is not None and dec.reason == "video_moment"
    assert policy.observe(_event(kind="video", video_position=60.0), Gates(), now=103.0) is None


def test_suggestion_requires_idle_and_suggestible_page_and_prior_comment():
    policy = _enabled_policy(
        min_text_chars=10,
        dwell_seconds=1,
        suggest_min_dwell_seconds=10,
        suggest_idle_seconds=30,
        suggest_min_text_chars=10,
    )
    policy.observe(_event(url="https://github.com/x/y", text="README 正文" * 30), Gates(), now=100.0)
    assert policy.poll(Gates(user_idle_seconds=0), now=120.0) is not None  # 先评论
    # 未闲置 → 不给建议
    assert policy.poll(Gates(user_idle_seconds=5), now=130.0) is None
    dec = policy.poll(Gates(user_idle_seconds=40), now=140.0)
    assert dec is not None and dec.action == ACTION_SUGGEST and dec.reason == "page_idle_suggestion"
    assert policy.poll(Gates(user_idle_seconds=99), now=200.0) is None, "同一页只建议一次"


def test_suggestion_skipped_for_video_pages():
    policy = _enabled_policy(
        min_text_chars=10,
        dwell_seconds=1,
        suggest_min_dwell_seconds=10,
        suggest_idle_seconds=10,
        suggest_min_text_chars=10,
    )
    policy.observe(_event(url="https://www.bilibili.com/video/BV1"), Gates(), now=100.0)
    assert policy.poll(Gates(user_idle_seconds=0), now=110.0) is not None
    assert policy.poll(Gates(user_idle_seconds=60), now=200.0) is None


@pytest.mark.parametrize(
    "gates",
    [
        Gates(visible=False),
        Gates(fullscreen=True),
    ],
)
def test_gates_block_observation(gates):
    policy = _enabled_policy(dwell_seconds=0)
    assert policy.observe(_event(), gates, now=100.0) is None
    assert policy.poll(gates, now=200.0) is None


def test_agent_busy_does_not_silence_by_default():
    """默认不因 agent 忙而静音（实机修正）。

    DSH agent 在工作时几乎恒为 working：若默认静音，用户跟 AI 对话的整段时间里
    网页互动都会没反应，表现为功能坏了。
    """
    policy = _enabled_policy(dwell_seconds=0, min_text_chars=10)
    assert policy.observe(_event(), Gates(agent_busy=True), now=100.0) is not None


def test_agent_busy_pauses_only_when_explicitly_enabled():
    policy = _enabled_policy(dwell_seconds=0, pause_when_agent_busy=True)
    assert policy.observe(_event(), Gates(agent_busy=True), now=100.0) is None
    assert policy.last_skip_reason == "agent_busy"
    assert policy.observe(_event(), Gates(agent_busy=False), now=101.0) is not None


def test_blocked_gate_still_records_page_so_it_can_speak_later():
    """被闸门拦下的一页必须仍然被记住，闸门放行后按停留补说。

    实测缺陷：守卫在记录页身份之前返回 → 事件整条丢弃 → agent 忙/桌宠隐藏期间
    打开的页面永远不会被评论（接收端 200、事件 204、日志毫无动静）。
    """
    policy = _enabled_policy(dwell_seconds=8, min_text_chars=10)
    assert policy.observe(_event(), Gates(visible=False), now=100.0) is None
    assert policy.snapshot()["domain"] == "example.com", "被拦下的页也要记住身份"
    # 桌宠可见后：停留已过 → 正常开口（无需重新来一条事件）
    decision = policy.poll(Gates(), now=120.0)
    assert decision is not None and decision.reason == "page_dwell"


def test_pending_selection_is_delivered_after_gate_clears():
    """闸门拦下的划词要在放行后补说，且仍以"划词"理由（最强信号不丢）。"""
    policy = _enabled_policy(dwell_seconds=600, min_text_chars=10, pause_when_agent_busy=True)
    assert policy.observe(_event(kind="selection", selection="这段被拦下的划词"), Gates(agent_busy=True), now=100.0) is None
    assert policy.last_skip_reason == "agent_busy"
    decision = policy.poll(Gates(agent_busy=False), now=101.0)
    assert decision is not None
    assert decision.reason == "selection"
    assert "这段被拦下的划词" in decision.digest["selection"]


def test_require_idle_gate_uses_user_idle_seconds():
    policy = _enabled_policy(dwell_seconds=0, require_idle=True, min_idle_seconds=30)
    assert policy.observe(_event(), Gates(user_idle_seconds=5), now=100.0) is None
    assert policy.last_skip_reason == "user_active"
    assert policy.observe(_event(), Gates(user_idle_seconds=45), now=101.0) is not None


def test_domain_blacklist_and_whitelist_gate_events():
    blocked = _enabled_policy(dwell_seconds=0, blacklist=["bank.example"])
    assert blocked.observe(_event(url="https://bank.example/x"), Gates(), now=100.0) is None
    assert blocked.last_skip_reason == "domain_blocked"

    only = _enabled_policy(dwell_seconds=0, whitelist=["github.com"])
    assert only.observe(_event(url="https://news.example.com/x"), Gates(), now=100.0) is None
    assert only.observe(_event(url="https://github.com/x"), Gates(), now=101.0) is not None


def test_page_leave_resets_state_so_next_page_can_speak_again():
    policy = _enabled_policy(dwell_seconds=0, min_text_chars=10)
    assert policy.observe(_event(url="https://a.com/1"), Gates(), now=100.0) is not None
    policy.observe(_event(url="https://a.com/1", kind="page_leave"), Gates(), now=101.0)
    assert policy.snapshot()["domain"] == ""
    assert policy.observe(_event(url="https://a.com/2"), Gates(), now=102.0) is not None


def test_snapshot_never_leaks_page_content():
    policy = _enabled_policy(dwell_seconds=0)
    policy.observe(_event(title="敏感标题", text="敏感正文" * 50), Gates(), now=100.0)
    snap = policy.snapshot()
    assert set(snap) == {"domain", "dwell_seconds", "commented", "suggested", "last_skip_reason"}
    assert "敏感" not in str(snap)


def test_make_digest_from_remembered_page_matches_event_digest():
    policy = _enabled_policy(dwell_seconds=5, min_text_chars=10)
    event = _event(url="https://a.com/p", title="标题", text="正文" * 50)
    policy.observe(event, Gates(), now=100.0)
    decision = policy.poll(Gates(), now=200.0)
    assert decision is not None
    assert decision.digest["title"] == "标题"
    assert decision.digest["hash"] == build_digest(event)["hash"]
