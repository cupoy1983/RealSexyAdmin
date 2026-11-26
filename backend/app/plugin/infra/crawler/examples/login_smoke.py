"""离线合成登录验收：无站点账号、无外网请求，不连接业务数据库。"""

import asyncio
import json
import time
from tempfile import TemporaryDirectory

from app.plugin.infra.crawler.login_service import LoginManager


async def main():
    from playwright.async_api import async_playwright

    sites = {"fixture": {"name": "Offline", "domains": ["login.invalid"], "success_hosts": ["login.invalid"],
                         "success_selectors": ["#signed-in"], "challenge_selectors": ["#challenge"], "url": "https://login.invalid/"}}
    html = """<a id='signed-in'>Sign out</a><script>
    localStorage.setItem('fixture-auth','accepted');
    document.cookie='fixture=session; Secure; SameSite=Lax; path=/';
    </script>"""
    with TemporaryDirectory() as directory:
        print("SMOKE_STAGE=launch", flush=True)
        manager = LoginManager(sites, directory)
        async with async_playwright() as playwright:
            manager.browser = await playwright.chromium.launch(headless=False)
            manager.context = await manager.browser.new_context()
            await manager.context.route("**/*", lambda route: route.fulfill(content_type="text/html", body=html))
            page = await manager.context.new_page()
            await page.goto("https://login.invalid/")
            print("SMOKE_STAGE=fixture_loaded", flush=True)
            assert (await manager.classify(manager.context, "fixture"))["state"] == "valid"
            await page.set_content('<div id="challenge">验证失败，点击框体重试(error:muhair)</div>')
            result = await manager.classify(manager.context, "fixture")
            assert result == {"state": "verification_required", "reason": "verification_failed"}
            await page.set_content(html)
            manager.active = {"platform": "fixture", "account": "default", "expires_at": time.time() + 60}
            await manager.command("confirm", "fixture", "default")
            print("SMOKE_STAGE=saved", flush=True)
            saved = manager.store("fixture", "default").load()
            assert saved["cookies"][0]["name"] == "fixture"
            browser = await playwright.chromium.launch(headless=False)
            try:
                context = await browser.new_context(storage_state=saved)
                await context.route("**/*", lambda route: route.fulfill(content_type="text/html", body="<h1>restored</h1>"))
                page = await context.new_page()
                await page.goto("https://login.invalid/")
                assert await page.evaluate("localStorage.getItem('fixture-auth')") == "accepted"
                assert "fixture=session" in await page.evaluate("document.cookie")
                print("SMOKE_STAGE=restored", flush=True)
            finally:
                await browser.close()
            await manager.command("revoke", "fixture", "default")
            assert manager.store("fixture", "default").load() is None
    print(json.dumps({"offline": True, "login_verified": True, "cookie_and_localstorage_restored": True, "revoked": True}))


if __name__ == "__main__":
    asyncio.run(asyncio.wait_for(main(), timeout=90))
