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
    service, fake, _cfg = _service(tmp_path, enabled=True, port=0, dwell_seconds=0, min_text_chars=10)
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
    """
    monkeypatch.setattr(service_mod, "post_text_request", lambda *_a, **_k: "不该出现")
    calls: list[int] = []
    service, fake, _cfg = _service(tmp_path, enabled=True, port=0, dwell_seconds=0, min_text_chars=10)
    try:
        monkeypatch.setattr(service.limiter, "try_acquire", lambda: (calls.append(1), (False, "daily_cap_reached"))[1])
        assert _post_to(service, _page())[0] == 204
        _pump(3.0)
        assert len(calls) == 1, f"退避期内不得反复询问频控（实际 {len(calls)} 次）"
        assert fake.bubbles == []
        assert service.status()["last_reason"].startswith("rate_limited")
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
