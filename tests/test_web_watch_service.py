# -*- coding: utf-8 -*-
"""web_watch 服务层契约测试：本地接收端、触发→冒泡全链路、启停与 AppShell 门控。

纪律（同 tests/test_voice_chime_service.py）：
- 外发请求全部 monkeypatch 打桩，绝不真连模型；
- 接收端用**真** 127.0.0.1 回环 + 端口 0（内核分配，免端口冲突 flake）；
- 不做固定 sleep 赌时序，一律「有界泵事件循环 + 宽预算轮询」。

覆盖的真实缺陷场景：
- 无令牌/错令牌的写入必须被拒（网页可以发 no-cors 请求，令牌是唯一边界）；
- 超大请求体不得进入解析（内存保护）；
- 分级冷却生效：第一页说了话之后，紧接着的第二页必须被频控拦住；
- dry-run 只记日志、绝不调模型（不烧额度）。
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QWidget

from pet import app as app_mod
from pet.app import AppShell
from pet.config import Config
from pet.web_watch import service as service_mod
from pet.web_watch.protocol import MAX_BODY_BYTES
from pet.web_watch.server import WebWatchServer
from pet.web_watch.service import WebWatchService

app = QApplication.instance() or QApplication([])

TOKEN = "test-token-0123456789abcdef"


def _pump(seconds: float) -> None:
    """有界泵事件循环：只让已排队的信号/定时器跑完，不赌时序。"""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)


def _wait_until(predicate, *, timeout: float = 10.0, interval: float = 0.02) -> bool:
    """宽预算轮询（CI 慢 runner 是本地数倍慢）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(interval)
    return bool(predicate())


def _post(port: int, payload: dict | str, *, token: str | None = TOKEN, raw: bytes | None = None):
    """发一条事件；返回 (status, body)。4xx 不抛，便于断言状态码。"""
    body = raw if raw is not None else (
        payload.encode("utf-8") if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    )
    request = urllib.request.Request(f"http://127.0.0.1:{port}/event", data=body, method="POST")
    request.add_header("Content-Type", "application/json")
    if token is not None:
        request.add_header("X-Pet-Token", token)
    try:
        with urllib.request.urlopen(request, timeout=5) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def _get(port: int, path: str = "/health"):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, {}


def _post_to(service: WebWatchService, payload: dict):
    """向服务自报的端口+令牌发事件。

    令牌必须取自服务本身：产品在未配置令牌时会在用户目录自动生成 32 位随机令牌
    （测试里的固定常量只用于直接构造 WebWatchServer 的用例）。
    """
    status = service.status()
    return _post(status["port"], payload, token=status["token"])


class _FakeTarget:
    """冒泡扇出面替身（公有钩子，禁跨模块访问窗口私有属性）。"""

    def __init__(self, *, visible: bool = True):
        self.bubbles: list[str] = []
        self.holds: list[float] = []
        self.synced: list[tuple[str, str]] = []
        self._visible = visible

    def isVisible(self) -> bool:  # noqa: N802 - 与 Qt 命名一致
        return self._visible

    def hold_bubble(self, seconds: float) -> None:
        self.holds.append(seconds)

    def show_bubble(self, text: str, duration_ms: int = 3200, **_kwargs) -> None:
        self.bubbles.append(text)

    def on_look_synced(self, marker: str, reply: str) -> None:
        self.synced.append((marker, reply))


def _page(**overrides) -> dict:
    payload = {
        "kind": "page_open",
        "url": "https://github.com/example/repo",
        "title": "示例仓库",
        "text": "README 正文" * 40,
        "headings": ["安装", "用法"],
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------- 接收端


def test_server_accepts_token_event_and_counts():
    got: list = []
    server = WebWatchServer(got.append, port=0, token=TOKEN, pet_name="深深")
    assert server.start() is True
    try:
        status, _ = _post(server.port, _page())
        assert status == 204
        assert _wait_until(lambda: len(got) == 1)
        assert got[0].kind == "page_open"
        assert server.stats["received"] == 1
    finally:
        server.stop()


def test_server_rejects_missing_or_wrong_token():
    got: list = []
    server = WebWatchServer(got.append, port=0, token=TOKEN)
    assert server.start() is True
    try:
        assert _post(server.port, _page(), token=None)[0] == 401
        assert _post(server.port, _page(), token="wrong")[0] == 401
        _pump(0.2)
        assert got == [], "鉴权失败的请求绝不能进决策链"
        assert server.stats["rejected_auth"] == 2
    finally:
        server.stop()


def test_server_without_token_refuses_everything():
    """没配令牌 = 拒绝一切写入：不给本机留后门。"""
    server = WebWatchServer(lambda _e: None, port=0, token="")
    assert server.start() is True
    try:
        assert _post(server.port, _page(), token="anything")[0] == 401
    finally:
        server.stop()


def test_server_rejects_oversized_and_bad_payloads():
    got: list = []
    server = WebWatchServer(got.append, port=0, token=TOKEN)
    assert server.start() is True
    try:
        assert _post(server.port, None, raw=b"x" * (MAX_BODY_BYTES + 1))[0] == 413
        assert _post(server.port, "{not json")[0] == 400
        assert _post(server.port, {"kind": "nope", "url": "https://a.com"})[0] == 400
        assert _post(server.port, {"kind": "page_open", "url": "edge://settings"})[0] == 400
        _pump(0.2)
        assert got == []
        assert server.stats["received"] == 0
    finally:
        server.stop()


def test_health_endpoint_needs_no_token_and_hides_secrets():
    server = WebWatchServer(lambda _e: None, port=0, token=TOKEN, pet_name="深深")
    assert server.start() is True
    try:
        status, body = _get(server.port)
        assert status == 200 and body["ok"] is True and body["name"] == "深深"
        assert TOKEN not in json.dumps(body)
        assert _get(server.port, "/nope")[0] == 404
    finally:
        server.stop()


def test_server_stop_is_idempotent_and_frees_port():
    server = WebWatchServer(lambda _e: None, port=0, token=TOKEN)
    assert server.start() is True
    port = server.port
    server.stop()
    server.stop()  # 幂等
    assert server.is_running is False
    again = WebWatchServer(lambda _e: None, port=port, token=TOKEN)
    assert again.start() is True, "停掉后同一端口必须能立刻重新绑定"
    again.stop()


# ---------------------------------------------------------------- 服务门控


def _service(tmp_path, *, target=None, **overrides) -> tuple[WebWatchService, _FakeTarget, Config]:
    config = Config(base=tmp_path)
    cfg = dict(config.get("web_watch") or {})
    cfg.update(overrides)
    config.set("web_watch", cfg)
    fake = target if target is not None else _FakeTarget()
    service = WebWatchService(config, fake)
    service._test_config = config  # type: ignore[attr-defined]
    return service, fake, config


def test_service_disabled_binds_nothing(tmp_path):
    service, _fake, _cfg = _service(tmp_path, enabled=False)
    try:
        assert service.is_running() is False
        assert service.status()["port"] == 0
    finally:
        service.stop()


def test_service_enabled_binds_loopback_and_creates_token_file(tmp_path):
    service, _fake, config = _service(tmp_path, enabled=True, port=0)
    try:
        assert service.is_running() is True
        status = service.status()
        assert status["running"] is True
        assert status["port"] > 0
        token = status["token"]
        assert len(token) == 32
        token_file = config.dir / service_mod.TOKEN_FILE_NAME
        assert token_file.is_file() and token_file.read_text(encoding="utf-8").strip() == token
    finally:
        service.stop()
    assert service.is_running() is False


def test_service_end_to_end_bubbles_once_then_rate_limits(tmp_path, monkeypatch):
    """真链路：HTTP → 决策 → (打桩的)模型调用 → 气泡；第二页被频控拦住。"""
    calls: list = []

    def _fake_request(messages, provider, **kwargs):
        calls.append(messages)
        return "这仓库的 README 写得挺实在"

    monkeypatch.setattr(service_mod, "post_text_request", _fake_request)
    service, fake, _cfg = _service(
        tmp_path, enabled=True, port=0, dwell_seconds=0, min_text_chars=10, pre_cue=False
    )
    try:
        port = service.status()["port"]
        assert _post_to(service, _page())[0] == 204
        assert _wait_until(lambda: bool(fake.bubbles)), "触发后必须回到 GUI 线程冒泡"
        assert fake.bubbles == ["这仓库的 README 写得挺实在"]
        assert len(calls) == 1
        # 分级冷却：另一页紧接着发来，仍受最小请求间隔约束，不得再说
        assert _post_to(service, _page(url="https://zhihu.com/question/1", title="问题"))[0] == 204
        _pump(1.5)
        assert len(fake.bubbles) == 1, "最小请求间隔内不得连说两句"
        assert service.status()["spoken"] == 1
        assert fake.holds, "冒泡前应先占位，防自言自语顶掉"
        assert fake.synced and fake.synced[0][0].startswith("[网页评论]")
    finally:
        service.stop()


def test_service_dry_run_logs_without_calling_model(tmp_path, monkeypatch):
    def _boom(*_args, **_kwargs):
        raise AssertionError("dry-run 绝不能调用模型")

    monkeypatch.setattr(service_mod, "post_text_request", _boom)
    service, fake, _cfg = _service(tmp_path, enabled=True, dry_run=True, port=0, dwell_seconds=0, min_text_chars=10)
    try:
        assert _post_to(service, _page())[0] == 204
        assert _wait_until(lambda: service.status()["dry_run_hits"] >= 1)
        _pump(0.4)
        assert fake.bubbles == []
        assert service.status()["spoken"] == 0
    finally:
        service.stop()


def test_service_ignores_blacklisted_domain(tmp_path, monkeypatch):
    monkeypatch.setattr(service_mod, "post_text_request", lambda *_a, **_k: "不该出现")
    service, fake, _cfg = _service(
        tmp_path, enabled=True, port=0, dwell_seconds=0, min_text_chars=10, blacklist=["github.com"]
    )
    try:
        assert _post_to(service, _page())[0] == 204
        _pump(1.2)
        assert fake.bubbles == []
    finally:
        service.stop()


def test_service_speaks_when_enabled(tmp_path, monkeypatch):
    spoken: list[str] = []
    monkeypatch.setattr(service_mod, "post_text_request", lambda *_a, **_k: "这是一句评论")
    config = Config(base=tmp_path)
    cfg = dict(config.get("web_watch") or {})
    cfg.update({"enabled": True, "port": 0, "dwell_seconds": 0, "min_text_chars": 10, "speak_enabled": True})
    config.set("web_watch", cfg)
    fake = _FakeTarget()
    service = WebWatchService(config, fake, on_speak=spoken.append)
    try:
        assert _post_to(service, _page())[0] == 204
        assert _wait_until(lambda: bool(spoken))
        assert spoken == ["这是一句评论"]
    finally:
        service.stop()


def test_service_invisible_pet_stays_silent(tmp_path, monkeypatch):
    monkeypatch.setattr(service_mod, "post_text_request", lambda *_a, **_k: "不该出现")
    service, fake, _cfg = _service(tmp_path, target=_FakeTarget(visible=False), enabled=True, port=0, dwell_seconds=0)
    try:
        assert _post_to(service, _page())[0] == 204
        _pump(1.2)
        assert fake.bubbles == [], "桌宠隐藏时不得冒泡"
    finally:
        service.stop()


def test_service_status_never_leaks_page_content(tmp_path):
    service, _fake, _cfg = _service(tmp_path, enabled=True, port=0)
    try:
        _post_to(service, _page(title="敏感标题", text="敏感正文" * 30))
        _pump(0.6)
        status = service.status()
        assert "敏感" not in json.dumps(status, ensure_ascii=False)
        assert set(status) >= {"enabled", "running", "port", "token", "received", "page"}
    finally:
        service.stop()


def test_rate_limit_refusal_backs_off_instead_of_polling_every_tick(tmp_path, monkeypatch):
    """频控拒绝后必须本地退避。

    实测缺陷背景：``ProactiveLimiter.try_acquire()`` 在拒绝时**不盖时间戳**，若服务
    每个 1s tick 都重问一遍，就是 1Hz 的跨进程文件锁 + 读盘（纯浪费）。本用例把
    try_acquire 换成计数器，断言 3 秒内只被问一次。

    这里用**瞬时**原因（冷却中）：它走"记住这条、稍后补说"的路径。整天性的原因
    （熔断 / 到量）走另一条路——见 ``test_breaker_refusal_does_not_spin``。
    """
    monkeypatch.setattr(service_mod, "post_text_request", lambda *_a, **_k: "不该出现")
    calls: list[int] = []
    service, fake, _cfg = _service(tmp_path, enabled=True, port=0, dwell_seconds=0, min_text_chars=10)
    try:
        monkeypatch.setattr(
            service.limiter,
            "try_acquire",
            lambda **kwargs: (calls.append(1), (False, "cooldown_active"))[1],
        )
        assert _post_to(service, _page())[0] == 204
        _pump(3.0)
        assert len(calls) == 1, f"退避期内不得反复询问频控（实际 {len(calls)} 次）"
        assert fake.bubbles == []
        assert service.status()["last_reason"].startswith("rate_limited")
        assert service._pending_decision is not None, "瞬时原因应保留这条、稍后补说"
    finally:
        service.stop()


# ---------------------------------------------------------------- AppShell 接线


def _start_shell(tmp_path, monkeypatch):
    """真 AppShell.start()，只把"起桌宠"那一层置空（对齐后台服务门控测试）。"""
    config = Config(base=tmp_path)
    assert config.save()
    shell = AppShell(app, config)
    shell.instance._create_ui = lambda cid: None
    shell.instance._apply_spawn_offset = lambda: None
    shell.instance._sync_animation_prewarm = lambda: None
    shell.instance._refresh_chat_windows = lambda: None
    shell._apply_balance_timer = lambda: None
    shell._sync_dynamic_island = lambda: None
    shell._install_session_watcher = lambda: None
    shell._sync_todo_service = lambda: None
    shell._sync_chime_service = lambda: None
    shell._sync_festival_service = lambda: None
    monkeypatch.setattr(app_mod.QTimer, "singleShot", lambda *a, **k: None)
    shell.start()
    return shell


def test_appshell_default_config_creates_no_service(tmp_path, monkeypatch):
    shell = _start_shell(tmp_path, monkeypatch)
    try:
        assert shell.web_watch_service is None, "默认关闭时不得创建服务、不得占端口"
    finally:
        shell._on_about_to_quit()


def test_appshell_sync_starts_and_stops_service(tmp_path, monkeypatch):
    shell = _start_shell(tmp_path, monkeypatch)
    try:
        cfg = dict(shell.config.get("web_watch") or {})
        cfg.update({"enabled": True, "port": 0})
        shell.config.set("web_watch", cfg)
        shell._sync_web_watch_service()
        service = shell.web_watch_service
        assert service is not None and service.is_running() is True, "开了必须真启动（不是只有对象）"
        cfg["enabled"] = False
        shell.config.set("web_watch", cfg)
        shell._sync_web_watch_service()
        assert shell.web_watch_service is None
        assert service.is_running() is False, "关了必须释放端口"
    finally:
        shell._on_about_to_quit()


def test_appshell_wanted_gate_survives_dirty_config(tmp_path, monkeypatch):
    shell = _start_shell(tmp_path, monkeypatch)
    try:
        shell.config.data["web_watch"] = "broken"
        assert shell._web_watch_wanted() is False
        shell._sync_web_watch_service()  # 脏配置不得抛
        assert shell.web_watch_service is None
    finally:
        shell._on_about_to_quit()


def test_external_config_change_syncs_web_watch_service(tmp_path, monkeypatch):
    """独立设置进程保存后，热加载路径必须同步接收端启停。

    实测缺口：用户的设置页跑在独立进程（``settings_process_isolation=true``），
    改设置只写 config.json，靠 ``_apply_external_config_change`` 合并进运行期；
    该路径最初没有 web_watch，结果"设置里开了、接收端不启动、也不生成令牌"，
    用户侧表现为功能坏了（重启才生效）。本用例钉死这条通道。
    """
    shell = _start_shell(tmp_path, monkeypatch)
    try:
        assert shell.web_watch_service is None
        cfg = dict(shell.config.get("web_watch") or {})
        cfg.update({"enabled": True, "port": 0, "dry_run": True})
        shell.config.set("web_watch", cfg)
        shell._apply_external_config_change()
        service = shell.web_watch_service
        assert service is not None and service.is_running() is True, "热加载必须真启动接收端"
        cfg["enabled"] = False
        shell.config.set("web_watch", cfg)
        shell._apply_external_config_change()
        assert shell.web_watch_service is None
        assert service.is_running() is False
    finally:
        shell._on_about_to_quit()


def test_status_snapshot_file_records_pipeline_stages(tmp_path, monkeypatch):
    """状态快照必须回答用户三问：读到了吗 / 何时读的 / 处理到哪一步。

    实测背景：没有这份快照时，用户只能看到"什么都没发生"——接收端在跑、事件也收下了，
    却无法区分"扩展没发出来""被策略跳过""频控拦下""模型弃权"这四种完全不同的情况。
    """
    monkeypatch.setattr(service_mod, "post_text_request", lambda *_a, **_k: "一条评论")
    service, _fake, config = _service(tmp_path, enabled=True, port=0, dwell_seconds=0, min_text_chars=10)
    try:
        assert _post_to(service, _page())[0] == 204
        status_path = config.dir / service_mod.STATUS_FILE_NAME
        assert _wait_until(lambda: status_path.is_file(), timeout=8.0)
        assert _wait_until(lambda: json.loads(status_path.read_text(encoding="utf-8")).get("received", 0) >= 1)
        data = json.loads(status_path.read_text(encoding="utf-8"))
        assert data["last_event_kind"] == "page_open"
        assert data["last_event_domain"] == "github.com"
        assert data["last_event_text_len"] > 0
        assert data["last_decision"].startswith("comment/")
        assert data["last_dispatch"], "派发阶段也必须留痕（否则无法区分'没说话'与'说了但失败'）"
        assert data["port"] == service.status()["port"]
    finally:
        service.stop()


def test_status_snapshot_records_skip_reason_when_not_speaking(tmp_path):
    """没说话时必须留下原因（否则用户只能猜）。"""
    service, _fake, config = _service(tmp_path, enabled=True, port=0, dwell_seconds=600)
    try:
        assert _post_to(service, _page())[0] == 204
        status_path = config.dir / service_mod.STATUS_FILE_NAME
        assert _wait_until(
            lambda: status_path.is_file()
            and "waiting_dwell" in json.loads(status_path.read_text(encoding="utf-8")).get("last_skip", "")
        ), "状态快照里应记录跳过原因（含机器可读的原因码）"
    finally:
        service.stop()


def test_pipeline_stages_are_logged(tmp_path, monkeypatch, caplog):
    """三段日志（收到事件 / 暂不说话 / 派发）是用户排查的唯一入口，用 caplog 钉住。"""
    monkeypatch.setattr(service_mod, "post_text_request", lambda *_a, **_k: "一条评论")
    service, _fake, _cfg = _service(tmp_path, enabled=True, port=0, dwell_seconds=0, min_text_chars=10)
    try:
        with caplog.at_level("INFO", logger="dsh-pet-standalone"):
            assert _post_to(service, _page())[0] == 204
            assert _wait_until(
                lambda: any("收到事件" in record.getMessage() for record in caplog.records)
            ), "必须留下'收到了什么'的日志"
            assert _wait_until(
                lambda: any("派发模型请求" in record.getMessage() for record in caplog.records)
            ), "必须留下'已发出请求'的日志"
        text = "\n".join(record.getMessage() for record in caplog.records)
        assert "收到事件 #1" in text
        assert "站点=github.com" in text
        assert "正文=" in text
    finally:
        service.stop()


def test_settings_save_does_not_clobber_untouched_fields(tmp_path):
    """设置页保存不得回滚"用户没碰过"的字段（实测缺陷）。

    复现：设置页是独立进程，Config 是打开那一刻的快照。用户只是去关了「语音朗读」
    然后保存，结果把运行期已经生效的端口改动（8765 → 8755）连同 dry-run 一起写回旧值，
    桌宠随即改绑旧端口 → 扩展"突然又连不上"。
    """
    from pet import settings_web_watch as page

    config = Config(base=tmp_path)
    cfg = dict(config.get("web_watch") or {})
    cfg.update({"enabled": True, "port": 8765, "dry_run": False, "speak_enabled": True})
    config.set("web_watch", cfg)
    assert config.save()
    dialog = _FakeDialog(config)
    try:
        page.create_web_watch_controls(dialog)  # 打开：快照 = {port: 8765, dry_run: False, speak: True}

        # 模拟"运行期被改过"：磁盘上已经是 8755 + dry_run True（热加载生效中的值）
        disk = json.loads(Path(config.path).read_text(encoding="utf-8"))
        disk["web_watch"]["port"] = 8755
        disk["web_watch"]["dry_run"] = True
        Path(config.path).write_text(json.dumps(disk, ensure_ascii=False, indent=2), encoding="utf-8")

        # 用户只动了「语音朗读」→ 关掉，然后保存
        dialog.ww_speak_check.setChecked(False)
        page.save_web_watch_settings(dialog)

        after = json.loads(Path(config.path).read_text(encoding="utf-8"))["web_watch"]
        assert after["speak_enabled"] is False, "用户改过的字段必须生效"
        assert after["port"] == 8755, "没碰过的端口必须服从磁盘最新值，不能被回滚成 8765"
        assert after["dry_run"] is True, "没碰过的模式同理"
    finally:
        dialog.deleteLater()


def test_settings_save_applies_fields_the_user_changed(tmp_path):
    """碰过的字段以界面为准（对照组，防止上面的修复矫枉过正）。"""
    from pet import settings_web_watch as page

    config = Config(base=tmp_path)
    cfg = dict(config.get("web_watch") or {})
    cfg.update({"enabled": True, "port": 8765})
    config.set("web_watch", cfg)
    assert config.save()
    dialog = _FakeDialog(config)
    try:
        page.create_web_watch_controls(dialog)
        dialog.ww_port_spin.setValue(9310)
        dialog.ww_dwell_spin.setValue(42)
        page.save_web_watch_settings(dialog)
        after = json.loads(Path(config.path).read_text(encoding="utf-8"))["web_watch"]
        assert after["port"] == 9310
        assert after["dwell_seconds"] == 42
    finally:
        dialog.deleteLater()


def test_new_page_bypasses_page_cooldown(tmp_path, monkeypatch):
    """换到新页面时必须请求"跳过同页冷却"（实测：新页被 30s 冷却压住 → 用户觉得迟钝）。"""
    monkeypatch.setattr(service_mod, "post_text_request", lambda *_a, **_k: "评论")
    service, _fake, _cfg = _service(
        tmp_path, enabled=True, port=0, dwell_seconds=0, min_text_chars=10, pre_cue=False, cooldown_minutes=0.5
    )
    seen: list[bool] = []
    real_try = service.limiter.try_acquire

    def spy(**kwargs):
        seen.append(bool(kwargs.get("ignore_cooldown")))
        return real_try(**kwargs)

    try:
        monkeypatch.setattr(service.limiter, "try_acquire", spy)
        assert _post_to(service, _page(url="https://a.example/x"))[0] == 204
        assert _wait_until(lambda: seen, timeout=8.0)
        assert seen[0] is True, "第一页就该免同页冷却"
        # 换到 B 页：即便同页冷却（30s）还没过，也必须带 ignore_cooldown=True 去问
        assert _post_to(service, _page(url="https://b.example/y", title="B页"))[0] == 204
        assert _wait_until(lambda: len(seen) >= 2, timeout=8.0)
        assert seen[-1] is True, "换到新页面必须跳过同页冷却"
    finally:
        service.stop()


def test_rate_limited_decision_is_remembered_and_retried(tmp_path, monkeypatch):
    """被频控拦下时不丢、也不每秒重算：记住这条，退避后再补说。"""
    monkeypatch.setattr(service_mod, "post_text_request", lambda *_a, **_k: "补说的评论")
    monkeypatch.setattr(service_mod, "REFUSAL_BACKOFF_SECONDS", 2.0)
    service, fake, _cfg = _service(
        tmp_path, enabled=True, port=0, dwell_seconds=0, min_text_chars=10, pre_cue=False
    )
    calls: list[int] = []
    allow = {"value": False}

    def gated(**kwargs):
        calls.append(1)
        return (True, "ok") if allow["value"] else (False, "cooldown_active")

    try:
        monkeypatch.setattr(service.limiter, "try_acquire", gated)
        assert _post_to(service, _page())[0] == 204
        assert _wait_until(lambda: len(calls) == 1, timeout=8.0)
        _pump(0.9)
        assert len(calls) == 1, "退避期内不得反复询问频控（修复前是 1Hz 重算）"
        assert fake.bubbles == []
        assert service.status()["last_dispatch"].startswith("rate_limited")
        # 放行后：不必等用户再操作，退避到点就补说这一条
        allow["value"] = True
        assert _wait_until(lambda: bool(fake.bubbles), timeout=8.0), "冷却结束后应自动补说"
        assert fake.bubbles == ["补说的评论"]
    finally:
        service.stop()


def test_pending_decision_dropped_when_user_moved_on(tmp_path, monkeypatch):
    """冷却结束时用户已经离开那一页 → 丢弃待说内容（不说过期的话）。"""
    monkeypatch.setattr(service_mod, "post_text_request", lambda *_a, **_k: "不该出现")
    monkeypatch.setattr(service_mod, "REFUSAL_BACKOFF_SECONDS", 1.0)
    service, fake, _cfg = _service(
        tmp_path, enabled=True, port=0, dwell_seconds=0, min_text_chars=10, pre_cue=False
    )
    allow = {"value": False}
    try:
        monkeypatch.setattr(
            service.limiter,
            "try_acquire",
            lambda **kwargs: (True, "ok") if allow["value"] else (False, "cooldown_active"),
        )
        assert _post_to(service, _page(url="https://a.example/one"))[0] == 204
        assert _wait_until(lambda: "rate_limited" in service.status()["last_dispatch"], timeout=8.0)
        # 用户翻页走了（页身份被清空）→ 退避到点时应丢弃这条待说内容
        service._policy.reset()
        allow["value"] = True
        assert _wait_until(lambda: service.status()["last_dispatch"].startswith("dropped"), timeout=8.0)
        assert fake.bubbles == []
    finally:
        service.stop()


def test_event_arrival_wakes_idle_timer(tmp_path, monkeypatch):
    """事件到达要立刻处理：空闲退避到 5s 时，不能白等下一次心跳。"""
    monkeypatch.setattr(service_mod, "post_text_request", lambda *_a, **_k: "评论")
    service, _fake, _cfg = _service(
        tmp_path, enabled=True, port=0, dwell_seconds=0, min_text_chars=10, pre_cue=False
    )
    try:
        _pump(1.0)
        service._retune_interval()
        assert service._timer.interval() == service_mod.TICK_IDLE_MS, "还没有页面时是 5s 空闲心跳"
        before = service.status()["received"]
        assert _post_to(service, _page())[0] == 204
        assert _wait_until(lambda: service.status()["received"] == before + 1, timeout=3.0)
        assert service._timer.interval() == service_mod.TICK_ACTIVE_MS, "事件到达应把心跳拉回 1s"
    finally:
        service.stop()


def test_pre_cue_bubble_shows_before_reply(tmp_path, monkeypatch):
    """开了「先说一句」时，先冒"让我看看……"再等模型（体感延迟）。"""
    monkeypatch.setattr(service_mod, "post_text_request", lambda *_a, **_k: "真正的评论")
    service, fake, _cfg = _service(
        tmp_path, enabled=True, port=0, dwell_seconds=0, min_text_chars=10, pre_cue=True
    )
    try:
        assert _post_to(service, _page())[0] == 204
        assert _wait_until(lambda: len(fake.bubbles) >= 2, timeout=10.0)
        assert fake.bubbles[0] == "让我看看……"
        assert fake.bubbles[-1] == "真正的评论"
    finally:
        service.stop()


def test_limiter_floors_are_lowered_for_web_watch(tmp_path):
    """频控下限必须按 web_watch 的区间走。

    实测缺陷：`ProactiveLimiter` 内部用共享的 `effective_proactive_config`，其中
    `min_request_interval_seconds` 硬地板是 30s（主动识屏给免费视觉模型定的）。
    web_watch 配 12s 会被顶回 30s —— 换页时仍然"慢半拍"。这里钉死下限可注入，
    同时保证主动识屏的默认下限（30s / 0.5 分）没被动过。
    """
    from pet.proactive_limiter import ProactiveLimiter

    service, _fake, config = _service(
        tmp_path, enabled=True, port=0, min_request_interval_seconds=12, cooldown_minutes=0.5
    )
    try:
        assert service.limiter.cfg["min_request_interval_seconds"] == 12
        assert abs(service.limiter.cfg["cooldown_minutes"] - 0.5) < 1e-9
    finally:
        service.stop()

    # 主动识屏（不传下限）：仍按手册的 30s / 0.5 分钳制
    legacy = ProactiveLimiter(config.dir / "legacy_state.json", {"min_request_interval_seconds": 12, "cooldown_minutes": 0.1})
    assert legacy.cfg["min_request_interval_seconds"] == 30
    assert abs(legacy.cfg["cooldown_minutes"] - 0.5) < 1e-9


def test_empty_reply_does_not_trip_breaker(tmp_path, monkeypatch):
    """空回复（模型有响应但正文为空）不得计入连续失败、不得熔断掉一整天。

    实测缺陷（2026-10-06）：思考型模型把 max_tokens 花在 reasoning 上 → 正文为空
    → 旧代码把它当故障 → 3 次即当日熔断 → 之后整个下午一句话都不说，
    而用户只看到"它不说话了"（熔断触发时旧代码也没有任何日志）。
    """
    from pet.web_watch.llm import EmptyReplyError

    def _empty(*_a, **_k):
        raise EmptyReplyError("模型没说话（空回复）")

    monkeypatch.setattr(service_mod, "post_text_request", _empty)
    service, fake, _cfg = _service(
        tmp_path, enabled=True, port=0, dwell_seconds=0, min_text_chars=10, pre_cue=False
    )
    # 频控放行（本用例要验的是"空回复不累计失败"，不是在测频控节流）
    monkeypatch.setattr(service.limiter, "try_acquire", lambda **kwargs: (True, "ok"))
    try:
        for _ in range(4):  # 够触发 3 次熔断的次数
            assert _post_to(service, _page())[0] == 204
            assert _wait_until(lambda: "empty_reply" in service.status()["last_dispatch"], timeout=8.0)
            service._policy.reset()
        assert service.limiter.allow()[0] is True, "空回复不得把熔断打开"
        assert service.limiter._load_state().get("consecutive_failures", 0) == 0
        assert fake.bubbles == []
    finally:
        service.stop()


def test_breaker_pause_is_minutes_not_a_day(tmp_path):
    """web_watch 的熔断按时长计（默认 10 分钟），到点自动恢复；主动识屏仍是当日熔断。"""
    from pet.proactive_limiter import ProactiveLimiter

    now = {"t": 1_000_000.0}
    lim = ProactiveLimiter(
        tmp_path / "s.json", {"daily_cap": 100},
        clock=lambda: now["t"], today=lambda: "2026-10-06",
        breaker_cooldown_minutes=10.0,
    )
    assert lim.record_failure() is False
    assert lim.record_failure() is False
    assert lim.record_failure() is True, "第 3 次连续失败应触发熔断"
    assert lim.allow()[1] == "paused_by_circuit_breaker"
    assert 500 < lim.breaker_remaining() <= 600
    now["t"] += 601  # 10 分钟后
    assert lim.allow()[0] is True, "按分钟计的熔断到点必须自动恢复"
    assert lim.breaker_remaining() == 0

    # 主动识屏（不传时长）：仍是"当日熔断"，不会自动恢复
    legacy = ProactiveLimiter(tmp_path / "p.json", {"daily_cap": 100}, clock=lambda: now["t"], today=lambda: "2026-10-06")
    for _ in range(3):
        legacy.record_failure()
    assert legacy.allow()[1] == "paused_by_circuit_breaker"
    assert legacy.breaker_remaining() == float("inf")
    legacy.clear_breaker()
    # 注意：清熔断不等于立刻放行——最小请求间隔/冷却仍在（这是防刷底线），
    # 这里只断言"熔断这个原因已经不在了"。
    assert legacy.allow()[1] != "paused_by_circuit_breaker"


def test_breaker_refusal_does_not_spin(tmp_path, monkeypatch):
    """熔断/到量时不留待说内容、不每 10 秒重问（旧行为把日志刷了一整天）。"""
    monkeypatch.setattr(service_mod, "post_text_request", lambda *_a, **_k: "不该出现")
    service, fake, _cfg = _service(
        tmp_path, enabled=True, port=0, dwell_seconds=0, min_text_chars=10, pre_cue=False
    )
    calls: list[int] = []
    monkeypatch.setattr(
        service.limiter, "try_acquire",
        lambda **kwargs: (calls.append(1), (False, "paused_by_circuit_breaker"))[1],
    )
    monkeypatch.setattr(service.limiter, "breaker_remaining", lambda: 600.0)
    try:
        assert _post_to(service, _page())[0] == 204
        assert _wait_until(lambda: len(calls) == 1, timeout=8.0)
        _pump(2.0)
        assert len(calls) == 1, "熔断期间不得反复重问"
        assert service._pending_decision is None, "熔断期间不该留待说内容"
        assert service.status()["last_dispatch"].startswith("paused")
        assert fake.bubbles == []
    finally:
        service.stop()


def test_state_file_with_bom_is_not_treated_as_corrupt(tmp_path):
    """带 BOM 的状态文件不得被当成"损坏"而静默重建（实测：手改清熔断时计数被归零）。"""
    from pet.proactive_limiter import ProactiveLimiter

    path = tmp_path / "s.json"
    path.write_text('\ufeff{"date": "2026-10-06", "count": 42}', encoding="utf-8")
    lim = ProactiveLimiter(path, {"daily_cap": 100}, clock=lambda: 1_000_000.0, today=lambda: "2026-10-06")
    assert lim._load_state()["count"] == 42, "BOM 不该让计数归零"


# ---------------------------------------------------------------- 设置页契约


class _FakeDialog(QWidget):
    """设置页替身：本域控件全在 settings_web_watch 里自建，只需 config + include_ai。"""

    def __init__(self, config, *, include_ai: bool = True):
        super().__init__()
        self.config = config
        self.include_ai = include_ai


def test_settings_rows_and_round_trip(tmp_path):
    from pet import settings_web_watch as page

    config = Config(base=tmp_path)
    dialog = _FakeDialog(config)
    try:
        page.create_web_watch_controls(dialog)
        rows = page.build_web_watch_rows(dialog)
        assert rows, "有聊天能力时必须产生设置行"
        names = [row.objectName() for row in rows]
        assert all(name.startswith("settingRow_web_watch_") for name in names), names
        assert len(names) == len(set(names)), "行 id 不得重复"

        # 改控件 → 保存 → 落进 config（round trip）
        dialog.ww_enabled_check.setChecked(True)
        dialog.ww_port_spin.setValue(9310)
        dialog.ww_dwell_spin.setValue(12)
        dialog.ww_cooldown_spin.setValue(3.5)
        dialog.ww_blacklist_edit.setPlainText("mail.example.com\n\n  bank.example  ")
        dialog.ww_whitelist_edit.setPlainText("github.com")
        page.save_web_watch_settings(dialog)
        saved = config.get("web_watch")
        assert saved["enabled"] is True
        assert saved["port"] == 9310
        assert saved["dwell_seconds"] == 12
        assert abs(saved["cooldown_minutes"] - 3.5) < 1e-6
        assert saved["blacklist"] == ["mail.example.com", "bank.example"]
        assert saved["whitelist"] == ["github.com"]
        # 未在 UI 暴露的键（含未来新增的高级键）不得被保存抹掉
        assert "excerpt_chars" in saved
    finally:
        dialog.deleteLater()


def test_settings_rows_absent_without_chat(tmp_path):
    from pet import settings_web_watch as page

    dialog = _FakeDialog(Config(base=tmp_path), include_ai=False)
    try:
        page.create_web_watch_controls(dialog)
        assert page.build_web_watch_rows(dialog) == [], "无聊天能力时整组不出现"
        assert getattr(dialog, "ww_enabled_check", None) is None
    finally:
        dialog.deleteLater()


def test_settings_probe_reports_offline_instead_of_raising(tmp_path):
    from pet import settings_web_watch as page

    # 无人监听的端口：必须返回可读结论（False + 说明），不得抛异常
    ok, text = page.probe(1, "token")
    assert ok is False and text
    config = Config(base=tmp_path)
    assert page.current_token(config) == ""
    assert page.today_count(config) == 0
    assert page.extension_dir().name == "edge-web-watch"
