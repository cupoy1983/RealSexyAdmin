import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.plugin.infra.crawler.session_bridge import execute


async def test_access_reuses_matching_live_window_without_restarting():
    manager = SimpleNamespace(lock=asyncio.Lock(), active={"platform": "1688", "account": "default"}, command=AsyncMock(),
                              check=AsyncMock(return_value={"state": "verification_required", "reason": "verification_failed"}))
    result, entry = await execute(manager, SimpleNamespace(key="fixture"), {"kind": "access", "platform": "1688", "account": "default"})
    assert result["reason"] == "verification_failed" and entry
    manager.command.assert_not_called()


async def test_different_active_account_does_not_get_entry():
    manager = SimpleNamespace(lock=asyncio.Lock(), active={"platform": "1688", "account": "other"}, command=AsyncMock())
    result, entry = await execute(manager, SimpleNamespace(key="fixture"), {"kind": "access", "platform": "1688", "account": "default"})
    assert result["reason"] == "window_busy" and entry is None
    manager.command.assert_not_called()
