import asyncio
import http.client
import json
import threading
from http.server import ThreadingHTTPServer
from unittest.mock import AsyncMock

import pytest

from app.plugin.infra.crawler.auth_session import AuthSessionStore
from app.plugin.infra.crawler.login_service import DEVICE, LoginManager, handler


def state():
    return {"cookies": [{"name": "session", "value": "fixture", "domain": ".shop.invalid", "path": "/"},
                        {"name": "other", "value": "fixture", "domain": "other.invalid", "path": "/"}],
            "origins": [{"origin": "https://shop.invalid", "localStorage": [{"name": "auth", "value": "fixture"}],
                         "indexedDB": [{"name": "fixture"}]}, {"origin": "https://other.invalid", "localStorage": []}]}


def test_auth_state_roundtrip_scope_expiry_and_revoke(tmp_path):
    now = [1000]
    path = tmp_path / "auth.json"
    store = AuthSessionStore(path, scope="shop:a:device", allowed_domains={"shop.invalid"}, ttl_seconds=60, clock=lambda: now[0])
    store.save_verified(state())
    saved = store.load()
    assert len(saved["cookies"]) == len(saved["origins"]) == 1
    assert saved["origins"][0]["localStorage"][0]["name"] == "auth"
    assert saved["origins"][0]["indexedDB"] == [{"name": "fixture"}]
    other = AuthSessionStore(path, scope="shop:b:device", allowed_domains={"shop.invalid"}, clock=lambda: now[0])
    assert other.load() is None and path.exists()
    now[0] = 1060
    assert store.load() is None and not path.exists()
    store.save_verified(state())
    store.discard()
    assert not path.exists()


def test_auth_store_rejects_empty_allowed_state(tmp_path):
    store = AuthSessionStore(tmp_path / "auth.json", scope="fixture", allowed_domains={"allowed.invalid"})
    with pytest.raises(ValueError):
        store.save_verified(state())
    assert not store.path.exists()


def manager(tmp_path):
    return LoginManager({"shop": {"name": "Fixture", "url": "https://shop.invalid/", "domains": ["shop.invalid"],
                                  "success_hosts": ["shop.invalid"], "success_selectors": ["#signed-in"]}}, tmp_path)


async def test_confirm_requires_platform_evidence_and_matching_account(tmp_path):
    login = manager(tmp_path)
    login.active = {"platform": "shop", "account": "default", "expires_at": float("inf")}
    page = AsyncMock()
    page.url = "https://shop.invalid/"
    locator = AsyncMock()
    page.locator = lambda _: locator
    login.context = AsyncMock()
    login.context.pages = [page]
    login.browser = AsyncMock()
    with pytest.raises(ValueError, match="对应"):
        await login.command("confirm", "shop", "other")
    locator.first = locator
    locator.is_visible.return_value = False
    with pytest.raises(ValueError, match="成功标志"):
        await login.command("confirm", "shop", "default")
    assert login.store("shop", "default").load() is None
    locator.is_visible.return_value = True
    login.context.storage_state.return_value = state()
    await login.command("confirm", "shop", "default")
    saved = login.store("shop", "default").load()
    assert saved and login.active is None
    assert login.store("shop", "default").scope == f"shop:default:{DEVICE}"


async def test_expired_login_cannot_be_saved(tmp_path):
    login = manager(tmp_path)
    login.active = {"platform": "shop", "account": "default", "expires_at": 0}
    login.browser = AsyncMock()
    with pytest.raises(ValueError):
        await login.command("confirm", "shop", "default")
    assert login.active is None and login.store("shop", "default").load() is None


async def test_account_path_traversal_and_unknown_platform_rejected(tmp_path):
    login = manager(tmp_path)
    for platform, account in [("shop", "../other"), ("unknown", "default")]:
        with pytest.raises(ValueError):
            await login.command("start", platform, account)


async def test_cross_origin_command_cannot_revoke_saved_session(tmp_path):
    login = manager(tmp_path)
    store = login.store("shop", "default")
    store.save_verified(state())
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler(login, asyncio.get_running_loop()))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]

    def request(origin):
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            connection.request("POST", "/command", json.dumps({"action": "revoke", "platform": "shop", "account": "default"}),
                               headers={"Content-Type": "application/json", "Origin": origin})
            response = connection.getresponse()
            response.read()
            return response.status
        finally:
            connection.close()

    try:
        assert await asyncio.to_thread(request, "https://untrusted.invalid") == 403
        assert store.load() is not None
        assert await asyncio.to_thread(request, f"http://127.0.0.1:{port}") == 200
        assert store.load() is None
    finally:
        await asyncio.to_thread(server.shutdown)
        server.server_close()


def test_private_login_diagnostics_omit_request_contents(tmp_path):
    login = manager(tmp_path / "auth")
    login.active = {"platform": "shop", "account": "default", "diagnostic_ref": "a" * 32}
    login.event("request_failed", host="shop.invalid", error="net::ERR_FAILED", url="https://shop.invalid/?secret=fixture", headers={"Cookie": "fixture"})
    saved = json.loads((tmp_path / "private/login-diagnostics" / ("a" * 32 + ".json")).read_text())
    assert saved["events"][0]["host"] == "shop.invalid"
    assert "url" not in saved["events"][0] and "headers" not in saved["events"][0]
