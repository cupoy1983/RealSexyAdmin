"""仅供 SSH 转发保护的独立登录容器使用；无业务数据库或后台依赖。"""

import asyncio
import contextlib
import json
import os
import re
import signal
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from .auth_session import AuthSessionStore
from .access_signals import inspect_access
from .login_access import ENTRY_HTML, LoginAccess
from .state import write_private_json

DEVICE = "chromium-desktop-zh-CN-v1"


class LoginManager:
    def __init__(self, sites, directory):
        self.sites, self.directory = sites, Path(directory)
        self.lock = asyncio.Lock()
        self.browser = self.context = None
        self.active = None
        self.events = []
        self.message = "请选择平台并开始登录"

    def store(self, platform, account):
        if platform not in self.sites or not re.fullmatch(r"[a-zA-Z0-9_\u3400-\u4dbf\u4e00-\u9fff-]{1,48}", account):
            raise ValueError("平台或会话名无效")
        site = self.sites[platform]
        return AuthSessionStore(self.directory / platform / (account + ".json"),
                                scope=f"{platform}:{account}:{DEVICE}", allowed_domains=set(site["domains"]),
                                ttl_seconds=site.get("ttl_seconds", 21600))

    def status(self):
        sessions = {}
        for platform in self.sites:
            sessions[platform] = []
            directory = self.directory / platform
            if directory.is_symlink() or not directory.is_dir():
                continue
            for path in sorted(directory.glob("*.json")):
                if path.is_symlink() or not re.fullmatch(r"[a-zA-Z0-9_\u3400-\u4dbf\u4e00-\u9fff-]{1,48}", path.stem):
                    continue
                try:
                    if self.store(platform, path.stem).load():
                        sessions[platform].append(path.stem)
                except (OSError, ValueError, KeyError, TypeError):
                    continue
        return {"platforms": [{"id": k, "name": v["name"]} for k, v in self.sites.items()],
                "active": self.active, "message": self.message, "sessions": sessions}

    def event(self, kind, **details):
        safe = {key: value for key, value in details.items() if key in {"host", "resource", "error", "status"}}
        self.events.append({"time": time.time(), "kind": kind, **safe})
        self.events = self.events[-200:]
        if self.active and self.active.get("diagnostic_ref"):
            location = self.directory.parent / "private" / "login-diagnostics" / (self.active["diagnostic_ref"] + ".json")
            try:
                write_private_json(location, {"version": 1, "platform": self.active["platform"], "events": self.events})
            except OSError:
                import logging
                logging.getLogger(__name__).warning("登录诊断写入失败，请检查私有目录空间与权限")

    def instrument(self, context):
        def failed(request):
            error = re.search(r"net::ERR_[A-Z_]+", request.failure or "")
            self.event("request_failed", host=urlsplit(request.url).hostname,
                       resource=request.resource_type, error=error.group(0) if error else "request_failed")
        def response(value):
            if value.status >= 400:
                self.event("http_error", host=urlsplit(value.url).hostname, status=value.status)
        context.on("requestfailed", failed)
        context.on("response", response)

    async def classify(self, context, platform, *, reveal=False):
        site = self.sites[platform]
        for page in context.pages:
            target = urlsplit(page.url)
            if target.scheme != "https" or target.hostname not in site["domains"]:
                continue
            signal = await inspect_access(page, site=site)
            if signal['state'] in {'verification_required', 'login_required', 'network_error'}:
                return {k: signal[k] for k in ('state', 'reason')}
            if signal['state'] == 'access_denied':
                return {'state': 'unknown', 'reason': signal['reason']}
            if target.hostname in site["success_hosts"]:
                if reveal:
                    for selector in site.get("success_hover_selectors", []):
                        try:
                            await page.locator(selector).first.hover(timeout=5000)
                            await asyncio.sleep(0.3)
                        except Exception:
                            pass
                for selector in site["success_selectors"]:
                    if await page.locator(selector).first.is_visible():
                        return {"state": "valid", "reason": "verified"}
            if target.hostname in site.get("login_hosts", []):
                return {"state": "login_required", "reason": "login_redirect"}
        return {"state": "unknown", "reason": "unrecognized_page"}

    async def check(self, platform, account):
        async with self.lock:
            store = self.store(platform, account)
            if self.active:
                if (self.active["platform"], self.active["account"]) != (platform, account):
                    return {"state": "unknown", "reason": "window_busy"}
                return await self.classify(self.context, platform)
            saved = store.load()
            if not saved:
                return {"state": "not_logged_in", "reason": "no_saved_state"}
            from playwright.async_api import async_playwright
            try:
                async with async_playwright() as runtime:
                    browser = await runtime.chromium.launch(headless=False)
                    try:
                        context = await browser.new_context(storage_state=saved, locale="zh-CN", viewport={"width": 1280, "height": 720})
                        site = self.sites[platform]
                        async def route(request_route):
                            request = request_route.request
                            target = urlsplit(request.url)
                            if request.is_navigation_request() and (target.scheme != "https" or target.hostname not in site["domains"]):
                                await request_route.abort()
                            else:
                                await request_route.continue_()
                        await context.route("**/*", route)
                        page = await context.new_page()
                        await page.goto(site.get("check_url", site["url"]), wait_until="domcontentloaded", timeout=30000)
                        return await self.classify(context, platform, reveal=True)
                    finally:
                        await browser.close()
            except Exception:
                return {"state": "network_error", "reason": "network_failed"}

    async def close(self):
        try:
            if self.browser is not None:
                await self.browser.close()
        finally:
            self.event("window_closed")
            self.browser = self.context = self.active = None

    async def command(self, action, platform, account):
        async with self.lock:
            store = self.store(platform, account)
            if self.active and self.active["expires_at"] <= time.time():
                await self.close()
                self.message = "登录窗口已超时关闭，未保存本次状态"
            if action == "revoke":
                if self.active and self.active["platform"] == platform and self.active["account"] == account:
                    await self.close()
                store.discard()
                self.message = "已删除本机保存状态；平台端会话如需撤销，请在平台账号设置中退出设备"
                return self.status()
            if action == "cancel":
                await self.close()
                self.message = "已取消，未保存本次登录"
                return self.status()
            if action == "start":
                if self.active:
                    raise ValueError("已有活动登录，请先完成或取消")
                site = self.sites[platform]
                from playwright.async_api import async_playwright
                if not hasattr(self, "playwright"):
                    self.playwright = await async_playwright().start()
                try:
                    self.browser = await self.playwright.chromium.launch(headless=False)
                    self.context = await self.browser.new_context(locale="zh-CN", viewport={"width": 1280, "height": 720})
                    self.instrument(self.context)

                    async def route(request_route):
                        request = request_route.request
                        target = urlsplit(request.url)
                        if request.is_navigation_request() and (target.scheme != "https" or target.hostname not in site["domains"]):
                            self.event("navigation_denied", host=target.hostname)
                            await request_route.abort()
                        else:
                            await request_route.continue_()

                    await self.context.route("**/*", route)
                    self.events = []
                    self.active = {"platform": platform, "account": account, "expires_at": time.time() + 900, "diagnostic_ref": uuid4().hex}
                    page = await self.context.new_page()
                    await page.goto(site["url"], wait_until="domcontentloaded", timeout=60000)
                except BaseException as error:
                    await self.close()
                    if isinstance(error, asyncio.CancelledError):
                        raise
                    raise RuntimeError("浏览器启动或平台访问失败，请检查 Collector 网络") from None
                self.message = "请在下方专用浏览器中自行登录，完成后点击校验并保存"
            elif action == "confirm":
                if not self.active or (platform, account) != (self.active["platform"], self.active["account"]):
                    raise ValueError("没有对应的活动登录")
                site = self.sites[platform]
                verified = (await self.classify(self.context, platform, reveal=True))["state"] == "valid"
                if not verified:
                    raise ValueError("未检测到平台登录成功标志；请回到平台首页并展开账号菜单，或检查站点适配，当前状态未保存")
                store.save_verified(await self.context.storage_state(indexed_db=True))
                await self.close()
                self.message = "已校验并保存专用会话；采集时仍需检查平台是否接受该会话"
            else:
                raise ValueError("未知操作")
            return self.status()

    async def expire(self):
        while True:
            await asyncio.sleep(2)
            async with self.lock:
                if self.active and self.active["expires_at"] <= time.time():
                    await self.close()
                    self.message = "登录窗口已超时关闭，未保存本次状态"


def handler(manager, loop, access=None, session_api=None, registry=None):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def reply(self, status, body):
            data = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def shared_request(self):
            from .service_auth import authenticate, ServiceAuthError
            from .session_api import SessionAPIError
            try:
                self.connection.settimeout(10)
                if session_api is None or not registry:
                    return self.reply(503, {'code': 503, 'msg': 'service_unavailable', 'data': None})
                size = int(self.headers.get('Content-Length', '0')) if self.command == 'POST' else 0
                if self.command == 'POST' and not 0 < size <= 4096:
                    raise SessionAPIError(422, 'invalid_request')
                raw = self.rfile.read(size) if size else b''
                owner = authenticate(registry, self.headers.get('Authorization'))
                body = None
                if self.command == 'POST':
                    if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                        raise SessionAPIError(422, 'invalid_request')
                    body = json.loads(raw)
                future = asyncio.run_coroutine_threadsafe(session_api.dispatch(owner, self.command, self.path, body), loop)
                try:
                    status, data = future.result(timeout=5)
                except TimeoutError:
                    future.cancel()
                    raise
                return self.reply(status, {'code': 0, 'msg': 'success', 'data': data})
            except ServiceAuthError:
                return self.reply(401, {'code': 401, 'msg': 'unauthorized', 'data': None})
            except SessionAPIError as error:
                return self.reply(error.status, {'code': error.status, 'msg': error.reason, 'data': None})
            except (ValueError, TypeError, UnicodeError):
                return self.reply(422, {'code': 422, 'msg': 'invalid_request', 'data': None})
            except Exception:
                return self.reply(503, {'code': 503, 'msg': 'service_unavailable', 'data': None})

        def do_GET(self):
            if self.path.startswith('/v1/sessions/'):
                return self.shared_request()
            if self.path == "/diagnostics":
                return self.reply(200, {"active": manager.active, "events": manager.events})
            if self.path == "/entry" and access is not None:
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(ENTRY_HTML)
                return
            if self.path == "/authorize" and access is not None:
                return self.reply(200 if access.authorized(self.headers.get("Cookie")) else 401, {})
            if self.path != "/status":
                return self.reply(404, {"error": "not_found"})

            async def status():
                return manager.status()

            self.reply(200, asyncio.run_coroutine_threadsafe(status(), loop).result(timeout=5))

        def do_POST(self):
            if self.path.startswith('/v1/sessions/'):
                return self.shared_request()
            host, origin = self.headers.get("Host"), self.headers.get("Origin")
            scheme = self.headers.get("X-Forwarded-Proto", "http")
            if scheme not in {"http", "https"} or not host or origin != scheme + "://" + host or self.headers.get("Content-Type") != "application/json":
                return self.reply(403, {"error": "请求来源无效"})
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 4096:
                    raise ValueError("请求大小无效")
                body = json.loads(self.rfile.read(size))
                if self.path == "/exchange" and access is not None:
                    session = access.exchange(body.get("access"))
                    self.send_response(200)
                    secure = "; Secure" if scheme == "https" else ""
                    self.send_header("Set-Cookie", f"collector_login={session}; Path=/; HttpOnly; SameSite=Strict; Max-Age=3600{secure}")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if self.path != "/command" or not isinstance(body, dict):
                    raise ValueError("请求无效")
                future = asyncio.run_coroutine_threadsafe(manager.command(body["action"], body["platform"], body["account"]), loop)
                try:
                    result = future.result(timeout=75)
                except TimeoutError:
                    future.cancel()
                    raise
            except (ValueError, KeyError) as error:
                return self.reply(400, {"error": str(error)})
            except Exception:
                return self.reply(503, {"error": "登录操作失败，请刷新状态后检查，未确认前不要重复提交"})
            self.reply(200, result)
    return Handler


async def main():
    sites = json.loads(Path(os.environ.get("LOGIN_SITES", "/config/sites.json")).read_text())
    for site in sites.values():
        target = urlsplit(site["url"])
        if target.scheme != "https" or target.hostname not in site["domains"] or not site["success_selectors"]:
            raise ValueError("站点登录配置不完整")
    manager_type = LoginManager
    if os.environ.get("LOGIN_BROWSER_MODE") == "native":
        from .native_login import NativeLoginManager
        manager_type = NativeLoginManager
    manager = manager_type(sites, os.environ.get("LOGIN_STATE_DIR", "/data/auth"))
    loop = asyncio.get_running_loop()
    access = LoginAccess()
    from .session_api import SessionAPI
    sessions = SessionAPI(manager, access)
    registry = os.environ.get('COLLECTOR_SERVICE_REGISTRY', '/data/service-clients.json')
    server = ThreadingHTTPServer(("127.0.0.1", 6081), handler(manager, loop, access, sessions, registry))
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    stop = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    timer = asyncio.create_task(manager.expire())
    bridge = None
    if os.environ.get("LOGIN_API_URL"):
        from .session_bridge import serve
        bridge = asyncio.create_task(serve(manager, access))
        def bridge_done(task):
            if not task.cancelled() and task.exception() is not None:
                import logging
                logging.getLogger(__name__).error("平台会话后台连接已停止，请核对连接和机器授权；本地窗口仍可使用")
        bridge.add_done_callback(bridge_done)
    try:
        await stop.wait()
    finally:
        await sessions.close()
        if bridge is not None:
            bridge.cancel()
            with contextlib.suppress(asyncio.CancelledError, RuntimeError, ValueError):
                await bridge
        timer.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await timer
        await manager.close()
        if hasattr(manager, "playwright"):
            await manager.playwright.stop()
        await asyncio.to_thread(server.shutdown)


if __name__ == "__main__":
    asyncio.run(main())
