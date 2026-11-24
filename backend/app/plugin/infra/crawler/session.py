"""按调用方作用域保存有限寿命的游客会话；不持有业务对象或后台依赖。"""

import time
from pathlib import Path

from .state import read_private_json, write_private_json


class GuestSessionStore:
    def __init__(self, path: Path, scope: str, allowed_domains: set[str], ttl_seconds=21600, *, clock=time.time):
        self.path, self.scope, self.domains, self.ttl, self.clock = path, scope, allowed_domains, ttl_seconds, clock

    def _cookies(self, cookies):
        if not isinstance(cookies, list):
            raise ValueError("游客会话格式无效")
        return [cookie for cookie in cookies if isinstance(cookie, dict) and cookie.get("domain", "").lstrip(".") in self.domains]

    def load(self):
        saved = read_private_json(self.path)
        if not saved:
            return None
        if saved.get("scope") != self.scope or type(saved.get("expires_at")) not in (int, float) or saved["expires_at"] <= self.clock():
            self.discard()
            return None
        return {"cookies": self._cookies(saved["cookies"]), "origins": []}

    def save(self, storage_state):
        old = read_private_json(self.path)
        expires = old["expires_at"] if isinstance(old, dict) and old.get("scope") == self.scope and old.get("expires_at", 0) > self.clock() else self.clock() + self.ttl
        write_private_json(self.path, {"scope": self.scope, "expires_at": expires,
                                       "cookies": self._cookies(storage_state.get("cookies", []))})

    def discard(self):
        if self.path.is_symlink():
            raise ValueError("游客会话不能使用符号链接")
        self.path.unlink(missing_ok=True)
