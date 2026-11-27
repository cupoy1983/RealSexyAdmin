"""第三方共享请求不受启动终端残留代理影响。"""

import asyncio
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from app.plugin.infra.integration.http import request_json


def test_request_json_ignores_unreachable_environment_proxy(monkeypatch):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = b'{"ok": true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    # 占用但不监听，保证代理端口不可连接，避免依赖本机固定端口状态。
    with socket.socket() as dead_proxy, ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        dead_proxy.bind(("127.0.0.1", 0))
        proxy = f"http://127.0.0.1:{dead_proxy.getsockname()[1]}"
        for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
            monkeypatch.setenv(name, proxy)
        for name in ("NO_PROXY", "no_proxy"):
            monkeypatch.setenv(name, "")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result = asyncio.run(request_json("GET", f"http://127.0.0.1:{server.server_port}/", "测试"))
            assert result == {"ok": True}
        finally:
            server.shutdown()
            thread.join(timeout=5)
