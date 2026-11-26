"""SSH 内签发短时入口；一次兑换成浏览器会话，不向网页暴露服务密钥。"""

import argparse
import hashlib
import hmac
import secrets
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
        self.key = secrets.token_hex(32)
        self.used, self.sessions = {}, {}
        write_private_json(path, {"key": self.key})

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
            self.sessions[session] = now + 3600
            return session

    def authorized(self, header):
        cookie = SimpleCookie()
        try:
            cookie.load(header or "")
            value = cookie["collector_login"].value
        except Exception:
            return False
        with self.lock:
            return self.sessions.get(value, 0) > self.clock()


ENTRY_HTML = b'''<!doctype html><meta charset="utf-8"><title>Collector login</title>
<p id="message">Connecting...</p><script>
const access=new URLSearchParams(location.hash.slice(1)).get('access');
history.replaceState(null,'','/entry');
fetch('/access/exchange',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({access})})
.then(async r=>{if(!r.ok)throw Error((await r.json()).error);location.replace('/')})
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
