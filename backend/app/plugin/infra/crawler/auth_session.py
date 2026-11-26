"""授权会话的私有存储；平台登录成功判定由调用方负责。"""

import math
import time
from pathlib import Path
from urllib.parse import urlsplit

from .state import read_private_json, write_private_json


class AuthSessionStore:
    def __init__(self, path: Path, *, scope: str, allowed_domains: set[str], ttl_seconds=21600, clock=time.time):
        if not scope or not allowed_domains or not math.isfinite(ttl_seconds) or ttl_seconds <= 0:
            raise ValueError("授权会话需要明确的作用域、域名和有效期限")
        self.path, self.scope, self.domains = path, scope, frozenset(allowed_domains)
        self.ttl, self.clock = ttl_seconds, clock

    def _filter(self, state):
        cookies = [c for c in state.get("cookies", [])
                   if isinstance(c, dict) and c.get("domain", "").lstrip(".") in self.domains]
        origins = []
        for origin in state.get("origins", []):
            if not isinstance(origin, dict):
                continue
            url = urlsplit(origin.get("origin", ""))
            if url.scheme == "https" and url.hostname in self.domains and not url.username and url.port in {None, 443}:
                origins.append({k: origin[k] for k in ("origin", "localStorage", "indexedDB") if k in origin})
        return {"cookies": cookies, "origins": origins}

    def save_verified(self, state):
        """仅在调用方完成站点登录成功判定后调用，不自行判定凭据有效性。"""
        filtered = self._filter(state)
        if not filtered["cookies"] and not filtered["origins"]:
            raise ValueError("未取得允许域名的授权状态")
        write_private_json(self.path, {"scope": self.scope, "expires_at": self.clock() + self.ttl, "state": filtered})

    def load(self, *, discard_expired=True):
        saved = read_private_json(self.path, max_bytes=4 * 1024 * 1024)
        if not saved:
            return None
        if saved.get("scope") != self.scope:
            return None
        expiry = saved.get("expires_at")
        if type(expiry) not in (int, float) or not math.isfinite(expiry) or expiry <= self.clock():
            if discard_expired:
                self.discard()
            return None
        return self._filter(saved["state"])

    def discard(self):
        if self.path.is_symlink():
            raise ValueError("授权会话不能使用符号链接")
        self.path.unlink(missing_ok=True)
