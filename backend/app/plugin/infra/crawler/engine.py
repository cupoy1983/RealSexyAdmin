"""Crawlee＋Playwright 单入口运行层；站点及业务处理由回调提供。"""

import math
import tempfile
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class BrowserJobOptions:
    executable: str | None = None
    headless: bool = False
    locale: str = "en-US"
    storage_state: dict | None = None
    launch_args: tuple[str, ...] = ("--no-proxy-server",)
    navigation_timeout_seconds: float = 180
    handler_timeout_seconds: float = 600

    def __post_init__(self):
        for value in (self.navigation_timeout_seconds, self.handler_timeout_seconds):
            if isinstance(value, bool) or not math.isfinite(value) or value <= 0:
                raise ValueError("浏览器任务时限必须是正的有限秒数")
        if not self.locale.strip():
            raise ValueError("浏览器 locale 不能为空")


def check_runtime():
    try:
        from crawlee.crawlers import PlaywrightCrawler  # noqa: F401
        from playwright.async_api import async_playwright  # noqa: F401
    except ImportError as error:
        raise RuntimeError("未安装 Crawlee＋Playwright 运行依赖，请安装共享采集运行环境") from error


async def run_browser_job(url: str, handle, *, prepare=None, on_error=None, options: BrowserJobOptions | None = None) -> Any:
    """在独立队列中执行一个入口；返回业务回调结果，始终清理所属队列。"""
    check_runtime()
    from crawlee import ConcurrencySettings, Request
    from crawlee.configuration import Configuration
    from crawlee.crawlers import PlaywrightCrawler
    from crawlee.events import LocalEventManager
    from crawlee.storage_clients import MemoryStorageClient
    from crawlee.storages import RequestQueue

    options = options or BrowserJobOptions()
    outcome, completed, failure = None, False, None
    with tempfile.TemporaryDirectory(prefix="crawler-job-") as directory:
        configuration = Configuration(storage_dir=directory)
        storage = MemoryStorageClient()
        queue = await RequestQueue.open(name=f"crawler-job-{uuid4().hex}", configuration=configuration, storage_client=storage)
        primary_error = None
        try:
            crawler = PlaywrightCrawler(
                configuration=configuration, event_manager=LocalEventManager(), storage_client=storage,
                request_manager=queue,
                concurrency_settings=ConcurrencySettings(min_concurrency=1, max_concurrency=1, desired_concurrency=1),
                browser_type="chromium", headless=options.headless, fingerprint_generator=None, use_incognito_pages=True,
                browser_launch_options={"args": list(options.launch_args), **({"executable_path": options.executable} if options.executable else {})},
                browser_new_context_options={"locale": options.locale, "extra_http_headers": {},
                                             **({"storage_state": options.storage_state} if options.storage_state else {})},
                goto_options={"wait_until": "domcontentloaded"},
                navigation_timeout=timedelta(seconds=options.navigation_timeout_seconds),
                request_handler_timeout=timedelta(seconds=options.handler_timeout_seconds),
                max_requests_per_crawl=1, max_request_retries=0, max_session_rotations=0,
                use_session_pool=False, retry_on_blocked=False, configure_logging=False,
            )
            if prepare is not None:
                crawler.pre_navigation_hook(prepare)

            @crawler.router.default_handler
            async def dispatch(context):
                nonlocal outcome, completed
                outcome = await handle(context)
                completed = True

            @crawler.failed_request_handler
            async def failed(context, error):
                nonlocal outcome, completed, failure
                if on_error is None:
                    failure = error
                    return
                outcome = await on_error(context, error)
                completed = True

            await crawler.run([Request.from_url(url)])
            if failure is not None:
                raise failure
            if not completed:
                raise RuntimeError("Crawlee 未调用处理器或失败处理器，未产生任务结果")
            return outcome
        except BaseException as error:
            primary_error = error
            raise
        finally:
            try:
                await queue.drop()
            except Exception:
                if primary_error is None:
                    raise
