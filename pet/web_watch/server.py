# -*- coding: utf-8 -*-
"""本地回环 HTTP 接收端（标准库，后台守护线程）。

浏览器扩展把页面事件 `POST /event` 到这里；本模块**只做**校验、计数与入队，
绝不触碰 Qt 对象、绝不写文件、绝不打印页面内容（隐私）。

安全口径：
- 只绑 `127.0.0.1`（不监听外部网卡）；
- 共享令牌鉴权：请求头 `X-Pet-Token`，或 `?token=`（便于手工 curl 调试）；
- 请求体上限 `protocol.MAX_BODY_BYTES`，超出直接 413，不解析；
- 令牌比较用 `hmac.compare_digest`（常量时间）。

注意：网页可以发起"无需读取响应"的跨站请求，所以**不能**用 CORS 当作安全边界；
真正的边界是令牌。
"""
from __future__ import annotations

import hmac
import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit

from .protocol import MAX_BODY_BYTES, parse_event

log = logging.getLogger("dsh-pet-standalone")

DEFAULT_HOST = "127.0.0.1"


class _Handler(BaseHTTPRequestHandler):
    server_version = "dsh-pet-web-watch"
    sys_version = ""
    protocol_version = "HTTP/1.0"  # 每次响应即关连接：不引入 keep-alive 状态机

    # 静音默认访问日志：URL 里可能带页面参数，且噪声大
    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        return

    # ---- 工具 ----------------------------------------------------------

    def _send(self, code: int, payload: dict | None = None) -> None:
        body = b"" if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        # 扩展的 service worker 有 host_permissions 时不需要 CORS；
        # 这几行只为"用 curl/网页控制台手工调试"时不至于被浏览器拦掉。
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Pet-Token")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _token_ok(self) -> bool:
        expected = getattr(self.server, "pet_token", "") or ""
        provided = self.headers.get("X-Pet-Token", "") or ""
        if not provided:
            query = parse_qs(urlsplit(self.path).query)
            provided = (query.get("token") or [""])[0]
        if not expected:
            # 没配令牌 = 拒绝一切写入请求（宁可不可用，也不留一个本机后门）
            return False
        return hmac.compare_digest(str(provided), str(expected))

    # ---- HTTP 方法 -----------------------------------------------------

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._send(204)

    def do_GET(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        if path == "/health":
            # 只回"在不在"，不回任何秘密；扩展与设置页用它显示连通状态
            self._send(200, {"ok": True, "name": getattr(self.server, "pet_name", "")})
            return
        self._send(404, {"ok": False, "error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        if path not in ("/event", "/ping"):
            self._send(404, {"ok": False, "error": "not_found"})
            return
        if not self._token_ok():
            self.server.pet_stats["rejected_auth"] += 1
            self._send(401, {"ok": False, "error": "bad_token"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            self._send(400, {"ok": False, "error": "empty_body"})
            return
        if length > MAX_BODY_BYTES:
            self.server.pet_stats["rejected_big"] += 1
            self._send(413, {"ok": False, "error": "too_large"})
            return

        if path == "/ping":
            self._read_body(length)  # 丢弃内容：ping 只验令牌
            self.server.pet_stats["pings"] += 1
            self._send(200, {"ok": True, "name": getattr(self.server, "pet_name", "")})
            return

        raw = self._read_body(length)
        if raw is None:
            self._send(400, {"ok": False, "error": "bad_body"})
            return
        try:
            payload = json.loads(raw.decode("utf-8", "replace"))
        except Exception:
            self.server.pet_stats["rejected_bad"] += 1
            self._send(400, {"ok": False, "error": "bad_json"})
            return
        event = parse_event(payload)
        if event is None:
            self.server.pet_stats["rejected_bad"] += 1
            self._send(400, {"ok": False, "error": "bad_event"})
            return
        sink: Callable[[Any], None] | None = getattr(self.server, "pet_sink", None)
        self.server.pet_stats["received"] += 1
        self.server.pet_stats["last_event_ts"] = time.time()
        if sink is not None:
            try:
                sink(event)  # 服务侧只做线程安全入队，绝不在此线程碰 Qt
            except Exception:
                log.warning("web_watch: 事件入队失败", exc_info=True)
        self._send(204)

    def _read_body(self, length: int) -> bytes | None:
        try:
            return self.rfile.read(length)
        except Exception:
            return None


class WebWatchServer:
    """线程化的本地接收端；`start()` / `stop()` 幂等。"""

    def __init__(
        self,
        sink: Callable[[Any], None],
        *,
        host: str = DEFAULT_HOST,
        port: int = 0,
        token: str = "",
        pet_name: str = "",
    ) -> None:
        self._host = host
        self._port = int(port)
        self._token = str(token or "")
        self._pet_name = str(pet_name or "")
        self._sink = sink
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.bind_error = ""
        self.stats: dict[str, Any] = {
            "received": 0,
            "rejected_auth": 0,
            "rejected_bad": 0,
            "rejected_big": 0,
            "pings": 0,
            "last_event_ts": 0.0,
        }

    # ---- 生命周期 ------------------------------------------------------

    def start(self) -> bool:
        """绑定并起线程；端口占用等失败记在 `bind_error` 并返回 False（不抛）。"""
        if self._httpd is not None:
            return True
        try:
            httpd = ThreadingHTTPServer((self._host, self._port), _Handler)
        except OSError as exc:
            self.bind_error = str(exc)
            log.warning("web_watch: 本地接收端绑定失败 %s:%s（%s）", self._host, self._port, exc)
            return False
        httpd.daemon_threads = True
        httpd.pet_sink = self._sink  # type: ignore[attr-defined]
        httpd.pet_token = self._token  # type: ignore[attr-defined]
        httpd.pet_name = self._pet_name  # type: ignore[attr-defined]
        httpd.pet_stats = self.stats  # type: ignore[attr-defined]
        self._httpd = httpd
        self._port = int(httpd.server_address[1])
        self._thread = threading.Thread(target=httpd.serve_forever, name="web-watch-server", daemon=True)
        self._thread.start()
        log.info("web_watch: 本地接收端已启动 http://%s:%s", self._host, self._port)
        return True

    def stop(self) -> None:
        """幂等停止：关 socket + 等线程退出（有超时，绝不阻塞退出流程）。"""
        httpd, thread = self._httpd, self._thread
        self._httpd = None
        self._thread = None
        if httpd is not None:
            try:
                httpd.shutdown()
            except Exception:
                pass
            try:
                httpd.server_close()
            except Exception:
                pass
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)

    # ---- 访问器 --------------------------------------------------------

    @property
    def is_running(self) -> bool:
        return self._httpd is not None

    @property
    def port(self) -> int:
        return self._port

    @property
    def host(self) -> str:
        return self._host

    def set_token(self, token: str) -> None:
        self._token = str(token or "")
        if self._httpd is not None:
            self._httpd.pet_token = self._token  # type: ignore[attr-defined]

    def set_sink(self, sink: Callable[[Any], None]) -> None:
        self._sink = sink
        if self._httpd is not None:
            self._httpd.pet_sink = sink  # type: ignore[attr-defined]

    def set_pet_name(self, name: str) -> None:
        self._pet_name = str(name or "")
        if self._httpd is not None:
            self._httpd.pet_name = self._pet_name  # type: ignore[attr-defined]
