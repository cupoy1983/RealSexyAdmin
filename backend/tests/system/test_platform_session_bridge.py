import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from app.plugin.infra.crawler.session_bridge import execute


async def test_access_reuses_matching_live_window_without_restarting():
    manager = SimpleNamespace(lock=asyncio.Lock(), active={"platform": "1688", "account": "default"}, command=AsyncMock(),
                              check=AsyncMock(return_value={"state": "verification_required", "reason": "verification_failed"}))
    result, entry = await execute(manager, SimpleNamespace(key="fixture", begin_window=Mock()), {"kind": "access", "platform": "1688", "account": "default"})
    assert result["reason"] == "verification_failed" and entry
    manager.command.assert_not_called()


async def test_different_active_account_does_not_get_entry():
    manager = SimpleNamespace(lock=asyncio.Lock(), active={"platform": "1688", "account": "other"}, command=AsyncMock())
    result, entry = await execute(manager, SimpleNamespace(key="fixture"), {"kind": "access", "platform": "1688", "account": "default"})
    assert result["reason"] == "window_busy" and entry is None
    manager.command.assert_not_called()


async def test_window_replaced_while_bridge_check_waits_does_not_issue_ticket():
    first = {'platform': '1688', 'account': 'first'}
    manager = SimpleNamespace(lock=asyncio.Lock(), active=first, command=AsyncMock())
    async def check(*args):
        manager.active = {'platform': '1688', 'account': 'second'}
        return {'state': 'unknown', 'reason': 'window_busy'}
    manager.check = check
    access = SimpleNamespace(key='fixture', begin_window=Mock())
    result, entry = await execute(manager, access, {'kind': 'access', 'platform': '1688', 'account': 'first'})
    assert result['reason'] == 'window_busy' and entry is None
    access.begin_window.assert_not_called()


async def test_same_named_replacement_is_not_the_original_window():
    manager = SimpleNamespace(lock=asyncio.Lock(), active={'platform': '1688', 'account': 'first'}, command=AsyncMock())
    async def check(*args):
        manager.active = {'platform': '1688', 'account': 'first'}
        return {'state': 'valid', 'reason': 'verified'}
    manager.check = check
    access = SimpleNamespace(key='fixture', begin_window=Mock())
    result, entry = await execute(manager, access, {'kind': 'access', 'platform': '1688', 'account': 'first'})
    assert result['reason'] == 'window_busy' and entry is None
    access.begin_window.assert_not_called()
