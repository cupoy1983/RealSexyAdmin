"""SSH 内签发短时入口；一次兑换成浏览器会话，不向网页暴露服务密钥。"""

import argparse
import hashlib
import hmac
import math
import secrets
import socket
import threading
import time
from http.cookies import SimpleCookie
from pathlib import Path

from .state import read_private_json, write_private_json

KEY_PATH = Path("/data/login-access-key.json")


def ticket(key, now=None):
    body = f"{int(time.time() if now is None else now) + 90}.{secrets.token_hex(16)}"
    return body + "." + hmac.new(key.encode(), body.encode(), hashlib.sha256).hexdigest()


class LoginAccess:
    def __init__(self, path=KEY_PATH, *, clock=time.time):
        self.clock, self.lock = clock, threading.Lock()
        self.path = path
        self.key = secrets.token_hex(32)
        self.used, self.sessions = {}, {}
        self.window = None
        self.window_id = secrets.token_hex(16)
        self.epoch = 0
        self.connections = set()
        self.continued = set()
        write_private_json(path, {"key": self.key})

    def _rotate(self):
        self.key = secrets.token_hex(32)
        self.window_id = secrets.token_hex(16)
        self.epoch += 1
        self.used.clear()
        self.sessions.clear()
        self.continued.clear()
        for connection in self.connections:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        self.connections.clear()
        write_private_json(self.path, {"key": self.key})

    def begin_window(self, active):
        with self.lock:
            if (not isinstance(active, dict) or not active.get('platform') or not active.get('account')
                    or type(active.get('expires_at')) not in (int, float)
                    or not math.isfinite(active['expires_at']) or active['expires_at'] <= self.clock()):
                raise ValueError('没有有效的人工登录窗口')
            if self.window is active:
                return
            self._rotate()
            self.window = active
            active['window_id'] = self.window_id

    def close_window(self):
        with self.lock:
            if self.window is not None:
                self._rotate()
                self.window = None

    @staticmethod
    def cookie_value(header):
        cookie = SimpleCookie()
        try:
            cookie.load(header or '')
            return cookie['collector_login'].value
        except Exception:
            return None

    def start_grant(self, header, *, scope=None, window_id=None):
        with self.lock:
            value = self.cookie_value(header)
            if self.sessions.get(value, 0) <= self.clock():
                raise ValueError('窗口授权已失效，请重新申请入口')
            if window_id is not None and window_id != self.window_id:
                raise ValueError('窗口标识已失效，请重新申请入口')
            if self.window and scope is not None and scope != (self.window['platform'], self.window['account']):
                raise ValueError('不能操作其他平台或会话的窗口')
            return value, self.epoch

    def continue_start(self, grant):
        with self.lock:
            value, epoch = grant
            if not self.window or epoch + 1 != self.epoch or grant in self.continued:
                raise ValueError('窗口授权已失效，请重新申请入口')
            self.sessions[value] = min(self.clock() + 3600, self.window['expires_at'])
            self.continued.add(grant)

    def register_connections(self, header, *connections, window_id):
        with self.lock:
            value = self.cookie_value(header)
            if not self.window or window_id != self.window_id or self.sessions.get(value, 0) <= self.clock():
                raise ValueError('窗口授权已失效')
            self.connections.update(connections)
            return value, self.epoch

    def forward(self, lease, connection, data):
        # Serialize the final authorization check and write with window rotation.
        with self.lock:
            value, epoch = lease
            if epoch != self.epoch or not self.window or self.sessions.get(value, 0) <= self.clock():
                raise ValueError('窗口授权已失效')
            connection.sendall(data)

    def connection_live(self, lease):
        with self.lock:
            value, epoch = lease
            return bool(epoch == self.epoch and self.window and self.sessions.get(value, 0) > self.clock())

    def unregister_connections(self, *connections):
        with self.lock:
            self.connections.difference_update(connections)

    def browser_window(self, header):
        with self.lock:
            if self.sessions.get(self.cookie_value(header), 0) <= self.clock():
                raise ValueError('窗口授权已失效')
            return self.window_id

    def authorized_window(self, header, window_id):
        with self.lock:
            return bool(window_id and window_id == self.window_id
                        and self.sessions.get(self.cookie_value(header), 0) > self.clock())

    def exchange(self, value):
        with self.lock:
            now = self.clock()
            self.used = {k: v for k, v in self.used.items() if v > now}
            self.sessions = {k: v for k, v in self.sessions.items() if v > now}
            if not isinstance(value, str) or len(value) > 256:
                raise ValueError("入口无效")
            try:
                expiry, nonce, signature = value.split(".")
                deadline = int(expiry)
            except (ValueError, TypeError):
                raise ValueError("入口无效") from None
            expected = hmac.new(self.key.encode(), f"{expiry}.{nonce}".encode(), hashlib.sha256).hexdigest()
            if not now < deadline <= now + 90 or value in self.used or not hmac.compare_digest(signature, expected):
                raise ValueError("入口已过期或已使用，请通过 SSH 重新打开")
            self.used[value] = deadline
            session = secrets.token_urlsafe(32)
            self.sessions[session] = min(now + 3600, self.window['expires_at']) if self.window else now + 3600
            return session

    def authorized(self, header):
        value = self.cookie_value(header)
        with self.lock:
            return self.sessions.get(value, 0) > self.clock()


ENTRY_HTML = b'''<!doctype html><meta charset="utf-8"><title>Collector login</title>
<p id="message">Connecting...</p><script>
const access=new URLSearchParams(location.hash.slice(1)).get('access');
history.replaceState(null,'','/entry');
fetch('/access/exchange',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({access})})
.then(async r=>{const data=await r.json();if(!r.ok)throw Error(data.error);sessionStorage.setItem('collector_window_id',data.window_id);location.replace('/')})
.catch(e=>document.getElementById('message').textContent=e.message);
</script>'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=18120)
    args = parser.parse_args()
    key = read_private_json(KEY_PATH)["key"]
    print(f"http://127.0.0.1:{args.port}/entry#access={ticket(key)}")


if __name__ == "__main__":
    main()
