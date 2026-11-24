"""只用共享层的真实浏览器验收；页面全部由内存路由提供，不访问目标站。"""

import asyncio
import json
import logging
import shutil
import sys
import tempfile
from pathlib import Path


async def verify():
    from app.plugin.infra.crawler.engine import BrowserJobOptions, run_browser_job

    handled = []

    async def prepare(context):
        async def fixture(route):
            await route.fulfill(status=200, content_type="text/html", body="<title>Offline fixture</title><h1>Offline fixture</h1>")
        await context.page.route("**/*", fixture)

    async def handle(context):
        handled.append(await context.page.title())
        return {"title": handled[-1], "batch": len(handled)}

    options = BrowserJobOptions(headless=True, navigation_timeout_seconds=30, handler_timeout_seconds=30)
    for number in (1, 2):
        result = await run_browser_job("https://fixture.invalid/repeated", handle, prepare=prepare, options=options)
        assert result == {"title": "Offline fixture", "batch": number}

    async def refuse(context):
        async def fixture(route):
            await route.fulfill(status=503, body="Offline fixture unavailable")
        await context.page.route("**/*", fixture)

    async def failed(context, error):
        original = getattr(error, "original_error", error)
        return {"status": getattr(original, "status_code", None)}

    result = await run_browser_job("https://fixture.invalid/unavailable", handle, prepare=refuse, on_error=failed, options=options)
    assert result == {"status": 503}, result
    assert handled == ["Offline fixture", "Offline fixture"]
    assert not any(name.startswith(("app.core", "app.config", "app.plugin.module_", "fastapi", "sqlalchemy")) for name in sys.modules)
    print(json.dumps({"offline_fixture": True, "shared_engine": "Crawlee+Playwright", "repeated_jobs": 2,
                      "failure_status": 503, "business_dependencies": False}))


def main():
    # 只打包共享包，验证过程不通过仓库后台 app/__init__.py 引入应用工厂。
    source = Path(__file__).resolve().parent.parent
    with tempfile.TemporaryDirectory(prefix="crawler-smoke-") as temporary:
        root = Path(temporary)
        target = root / "app/plugin/infra/crawler"
        target.mkdir(parents=True)
        for file in source.glob("*.py"):
            shutil.copyfile(file, target / file.name)
        (target.parent / "__init__.py").write_text("", encoding="utf-8")
        sys.path.insert(0, str(root))
        logging.getLogger("crawlee").setLevel(logging.ERROR)
        asyncio.run(verify())


if __name__ == "__main__":
    main()
