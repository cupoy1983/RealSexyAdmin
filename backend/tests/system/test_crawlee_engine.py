import asyncio
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.plugin.infra.crawler.engine import BrowserJobOptions, run_browser_job


@pytest.fixture
def runtime(monkeypatch):
    queues, crawlers, names = [], [], []

    class RequestQueue:
        @staticmethod
        async def open(**kwargs):
            names.append(kwargs["name"])
            queue = SimpleNamespace(drop=AsyncMock())
            queues.append(queue)
            return queue

    class Crawler:
        def __init__(self, **kwargs):
            self.options, self.prepare, self.handler, self.failed = kwargs, None, None, None
            self.router = SimpleNamespace(default_handler=self.register)
            crawlers.append(self)

        def register(self, callback):
            self.handler = callback
            return callback

        def pre_navigation_hook(self, callback):
            self.prepare = callback
            return callback

        def failed_request_handler(self, callback):
            self.failed = callback
            return callback

        async def run(self, requests):
            context = SimpleNamespace(page=object(), request=requests[0])
            try:
                if self.prepare:
                    await self.prepare(context)
                await self.handler(context)
            except Exception as error:
                await self.failed(context, error)

    modules = {
        "crawlee": {"ConcurrencySettings": lambda **kw: kw, "Request": SimpleNamespace(from_url=lambda url: url)},
        "crawlee.configuration": {"Configuration": lambda **kw: kw},
        "crawlee.crawlers": {"PlaywrightCrawler": Crawler},
        "crawlee.events": {"LocalEventManager": object},
        "crawlee.storage_clients": {"MemoryStorageClient": object},
        "crawlee.storages": {"RequestQueue": RequestQueue},
        "playwright": {},
        "playwright.async_api": {"async_playwright": object},
    }
    for name, values in modules.items():
        module = ModuleType(name)
        module.__dict__.update(values)
        monkeypatch.setitem(sys.modules, name, module)
    return SimpleNamespace(queues=queues, crawlers=crawlers, names=names)


async def test_same_entry_runs_twice_with_isolated_queues_and_safe_defaults(runtime):
    handler = AsyncMock(side_effect=[{"batch": 1}, {"batch": 2}])
    assert await run_browser_job("https://fixture.invalid/", handler) == {"batch": 1}
    assert await run_browser_job("https://fixture.invalid/", handler) == {"batch": 2}
    assert len(set(runtime.names)) == 2
    assert runtime.crawlers[0].options["storage_client"] is not runtime.crawlers[1].options["storage_client"]
    for queue, crawler in zip(runtime.queues, runtime.crawlers, strict=True):
        queue.drop.assert_awaited_once()
        assert crawler.options["request_manager"] is queue
        assert crawler.options["max_request_retries"] == 0
        assert crawler.options["max_session_rotations"] == 0
        assert crawler.options["retry_on_blocked"] is False


async def test_prepare_failure_returns_business_failure_and_releases_queue(runtime):
    prepare = AsyncMock(side_effect=RuntimeError("fixture setup failure"))
    handler = AsyncMock()
    failed = AsyncMock(return_value={"state": "blocked"})
    assert await run_browser_job("https://fixture.invalid/", handler, prepare=prepare, on_error=failed) == {"state": "blocked"}
    handler.assert_not_awaited()
    assert str(failed.await_args.args[1]) == "fixture setup failure"
    runtime.queues[0].drop.assert_awaited_once()


async def test_untranslated_failure_is_raised_and_none_is_a_valid_success(runtime):
    with pytest.raises(RuntimeError, match="fixture failure"):
        await run_browser_job("https://fixture.invalid/", AsyncMock(side_effect=RuntimeError("fixture failure")))
    runtime.queues[0].drop.assert_awaited_once()
    assert await run_browser_job("https://fixture.invalid/", AsyncMock(return_value=None)) is None
    runtime.queues[1].drop.assert_awaited_once()


async def test_cancelled_handler_releases_its_queue(runtime):
    with pytest.raises(asyncio.CancelledError):
        await run_browser_job("https://fixture.invalid/", AsyncMock(side_effect=asyncio.CancelledError))
    runtime.queues[0].drop.assert_awaited_once()


@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan"), False])
def test_invalid_timeout_rejected_without_installing_browser(value):
    with pytest.raises(ValueError):
        BrowserJobOptions(navigation_timeout_seconds=value)
