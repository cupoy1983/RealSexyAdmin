"""Crawlee＋Playwright 单入口运行层；站点及业务处理由回调提供。"""

import math
import re
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Literal
from uuid import uuid4


@dataclass(frozen=True)
class BrowserJobOptions:
    executable: str | None = None
    headless: bool = False
    locale: str = "en-US"
    storage_state: dict | None = field(default=None, repr=False)
    launch_args: tuple[str, ...] = ("--no-proxy-server",)
    navigation_timeout_seconds: float = 180
    handler_timeout_seconds: float = 600
    browser_type: Literal["chromium", "firefox", "webkit"] = "chromium"
    user_agent: str | None = None
    viewport: dict[str, int] | None = None
    is_mobile: bool | None = None
    has_touch: bool | None = None
    device_scale_factor: float | None = None
    extra_http_headers: dict[str, str] | None = field(default=None, repr=False)

    def __post_init__(self):
        for value in (self.navigation_timeout_seconds, self.handler_timeout_seconds):
            if isinstance(value, bool) or not math.isfinite(value) or value <= 0:
                raise ValueError("浏览器任务时限必须是正的有限秒数")
        if not self.locale.strip():
            raise ValueError("浏览器 locale 不能为空")
        if self.browser_type not in {"chromium", "firefox", "webkit"}:
            raise ValueError("browser_type 仅支持 chromium、firefox、webkit")
        if self.user_agent is not None:
            if not isinstance(self.user_agent, str) or not self.user_agent.strip() or len(self.user_agent) > 1024 or any(c in self.user_agent for c in "\r\n\0"):
                raise ValueError("user_agent 必须是非空单行字符串")
            if any(arg.startswith("--user-agent") for arg in self.launch_args):
                raise ValueError("请只通过 user_agent 设置 UA，不要同时提供 --user-agent")
        if self.viewport is not None and (not isinstance(self.viewport, Mapping) or set(self.viewport) != {"width", "height"}
                                          or any(type(n) is not int or not 1 <= n <= 16384 for n in self.viewport.values())):
            raise ValueError("viewport 需要 1～16384 的整数 width 和 height")
        for name in ("is_mobile", "has_touch"):
            value = getattr(self, name)
            if value is not None and type(value) is not bool:
                raise ValueError(f"{name} 必须是布尔值")
        if self.browser_type == "firefox" and self.is_mobile is not None:
            raise ValueError("Firefox 不支持 is_mobile，请省略该参数")
        if self.device_scale_factor is not None and (type(self.device_scale_factor) not in (int, float)
                or not math.isfinite(self.device_scale_factor) or self.device_scale_factor <= 0):
            raise ValueError("device_scale_factor 必须是正的有限数值")
        if self.extra_http_headers is not None:
            if not isinstance(self.extra_http_headers, Mapping):
                raise ValueError("extra_http_headers 必须是字符串映射")
            for name, value in self.extra_http_headers.items():
                if not isinstance(name, str) or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name) or not isinstance(value, str) or any(c in value for c in "\r\n\0"):
                    raise ValueError("HTTP 请求头名称或值无效")
                if name.lower() == "user-agent":
                    raise ValueError("请通过 user_agent 配置 UA，不要放入 extra_http_headers")


def _context_options(options):
    context = {"locale": options.locale, "extra_http_headers": dict(options.extra_http_headers or {})}
    if options.storage_state:
        context["storage_state"] = options.storage_state
    for name in ("user_agent", "viewport", "is_mobile", "has_touch", "device_scale_factor"):
        value = getattr(options, name)
        if value is not None:
            context[name] = dict(value) if name == "viewport" else value
    return context


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
                browser_type=options.browser_type, headless=options.headless, fingerprint_generator=None, use_incognito_pages=True,
                browser_launch_options={"args": list(options.launch_args), **({"executable_path": options.executable} if options.executable else {})},
                browser_new_context_options=_context_options(options),
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
