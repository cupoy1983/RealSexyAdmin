"""Dedicated native Chrome login; CDP stays inside the login container."""

import asyncio
import os
import re
import subprocess
import time
from pathlib import Path
from uuid import uuid4

from .login_service import LoginManager
from .diagnostics import capture_diagnostic
from .state import write_private_json


class NativeLoginManager(LoginManager):
    def __init__(self, sites, directory):
        super().__init__(sites, directory)
        self.native = None
        self.profiles = self.directory.parent / "native-profiles"

    def profile(self, platform, account):
        self.store(platform, account)  # Validate before constructing any path.
        for path in (self.profiles, self.profiles / platform, self.profiles / platform / account):
            if path.is_symlink():
                raise ValueError("专用浏览器目录不能为符号链接")
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
        return self.profiles / platform / account

    async def attach(self):
        if self.context is not None:
            return
        from playwright.async_api import async_playwright
        if not hasattr(self, "playwright"):
            self.playwright = await async_playwright().start()
        last = None
        for _ in range(30):
            try:
                self.browser = await self.playwright.chromium.connect_over_cdp(
                    "http://127.0.0.1:9222", timeout=1500)
                self.context = self.browser.contexts[0]
                self.instrument(self.context)
                return
            except Exception as error:
                last = error
                if self.native is None or self.native.poll() is not None:
                    break
                await asyncio.sleep(0.2)
        raise RuntimeError("专用浏览器连接失败") from last

    async def start_native(self, platform, account):
        if self.closing:
            raise RuntimeError('登录窗口正在关闭')
        profile = self.profile(platform, account)
        executable = Path(os.environ.get("LOGIN_CHROME_EXECUTABLE", "/opt/google/chrome/chrome"))
        if not executable.is_file():
            raise RuntimeError("稳定版 Chrome 未安装")
        try:
            self.native = subprocess.Popen([
                str(executable), "--no-sandbox", "--no-first-run",
                "--user-data-dir=" + str(profile), "--window-size=1280,720", "--lang=zh-CN",
                "--remote-debugging-address=127.0.0.1", "--remote-debugging-port=9222",
                self.sites[platform]["url"],
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.active = {"platform": platform, "account": account,
                           "expires_at": time.time() + 900, "diagnostic_ref": uuid4().hex}
            if self.access is not None:
                self.access.begin_window(self.active)
            self.events = []
            await self.attach()
        except BaseException:
            await self.close()
            raise

    async def close(self):
        self._closing_count += 1
        native, self.native = self.native, None
        try:
            await super().close()
        finally:
            try:
                if native is not None and native.poll() is None:
                    native.terminate()
                    try:
                        await asyncio.to_thread(native.wait, timeout=5)
                    except subprocess.TimeoutExpired:
                        native.kill()
                        await asyncio.to_thread(native.wait)
            finally:
                self._closing_count -= 1

    async def check(self, platform, account, *, force=False):
        async with self.lock:
            if not force and not self.active:
                cached = self.cached_check(platform, account)
                if cached:
                    return cached
            store = self.store(platform, account)
            if self.active:
                if (self.active["platform"], self.active["account"]) != (platform, account):
                    return {"state": "unknown", "reason": "window_busy"}
                result = await self.classify(self.context, platform)
                if result["state"] == "valid":
                    # A manually logged-in profile is not an exported business session yet.
                    return {"state": "login_required", "reason": "manual_login"}
                return self.observed_check(platform, account, result)
            if not store.load():
                return {"state": "not_logged_in", "reason": "no_saved_state"}
            stage, page = 'browser_start', None
            try:
                await self.start_native(platform, account)
                # Restored startup tabs can close while Chrome finishes session recovery.
                # This check owns its page instead of racing a restored target.
                page = await self.context.new_page()
                site = self.sites[platform]
                stage = 'navigation'
                await page.goto(site.get("check_url") or site["url"], wait_until="domcontentloaded", timeout=30000)
                stage = 'recognition'
                result = await self.classify(self.context, platform, reveal=True, target_page=page)
                if result['state'] == 'valid':
                    stage = 'save_state'
                    store.save_verified(await self.context.storage_state(indexed_db=True))
                return self.observed_check(platform, account, result)
            except Exception as error:
                reason = 'network_failed' if stage == 'navigation' else ('browser_start_failed' if stage == 'browser_start' else 'check_failed')
                code = re.search(r'net::ERR_[A-Z0-9_]+', str(error))
                try:
                    root = self.directory.parent / 'private' / 'session-checks'
                    reference = await asyncio.wait_for(capture_diagnostic(page, root, reason), timeout=4)
                    write_private_json(root / (uuid4().hex + '.json'), {
                        'platform': platform, 'stage': stage, 'error_type': type(error).__name__,
                        'network_code': code.group(0) if code else None, 'diagnostic_id': reference, 'observed_at': time.time()})
                except Exception:
                    pass
                return self.observed_check(platform, account, {
                    "state": "network_error" if stage == 'navigation' else 'unknown', "reason": reason})
            finally:
                await self.close()

    async def command(self, action, platform, account, *, authorize=None):
        if action == "start":
            async with self.lock:
                if self.closing:
                    raise ValueError('登录窗口正在关闭，请稍后重新申请入口')
                if authorize is not None:
                    authorize()
                self.store(platform, account)
                if self.active and self.active["expires_at"] <= time.time():
                    await self.close()
                if self.active:
                    raise ValueError("已有活动登录，请先完成或取消")
                await self.start_native(platform, account)
                self.message = "请自行登录，完成后点击校验并保存；中文输入按 Ctrl＋空格"
                return self.status()
        if action == "revoke":
            async with self.lock:
                if self.closing:
                    raise ValueError('登录窗口正在关闭，请稍后重新申请入口')
                if authorize is not None:
                    authorize()
                store = self.store(platform, account)
                if self.active and (self.active["platform"], self.active["account"]) == (platform, account):
                    await self.close()
                store.discard()
                profile = self.profile(platform, account)
                if profile.exists():
                    private = self.directory.parent / "private" / "revoked-profiles"
                    private.mkdir(parents=True, exist_ok=True, mode=0o700)
                    profile.rename(private / uuid4().hex)
                self.message = "已撤销采集端授权并隔离旧 profile；平台设备退出请在平台操作"
                return self.status()
        return await super().command(action, platform, account, authorize=authorize)
