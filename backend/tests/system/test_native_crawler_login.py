from unittest.mock import AsyncMock

import pytest

from app.plugin.infra.crawler.native_login import NativeLoginManager


def manager(tmp_path):
    return NativeLoginManager({"shop": {"name": "Fixture", "url": "https://shop.invalid/",
        "domains": ["shop.invalid"], "success_hosts": ["shop.invalid"],
        "success_selectors": ["#signed-in"], "challenge_selectors": ["#challenge"]}}, tmp_path / "auth")


def test_native_profiles_are_isolated_and_validated(tmp_path):
    login = manager(tmp_path)
    a, b = login.profile("shop", "a"), login.profile("shop", "b")
    assert a != b and a.is_dir() and b.is_dir()
    with pytest.raises(ValueError):
        login.profile("shop", "../a")


async def test_native_revoke_removes_export_and_quarantines_only_own_profile(tmp_path):
    login = manager(tmp_path)
    a, b = login.profile("shop", "a"), login.profile("shop", "b")
    login.store("shop", "a").save_verified({"cookies": [
        {"name": "fixture", "value": "fixture", "domain": "shop.invalid", "path": "/"}], "origins": []})
    await login.command("revoke", "shop", "a")
    assert login.store("shop", "a").load() is None
    assert not a.exists() and b.exists()
    assert len(list((tmp_path / "private/revoked-profiles").iterdir())) == 1


async def test_native_confirm_rejects_challenge_even_when_signed_in_marker_visible(tmp_path):
    login = manager(tmp_path)
    login.active = {"platform": "shop", "account": "a", "expires_at": float("inf")}
    page = AsyncMock()
    page.url = "https://shop.invalid/"
    page.frames = [page]
    locator = AsyncMock()
    locator.first = locator
    locator.is_visible.return_value = True
    page.locator = lambda _: locator
    page.get_by_text = lambda *args, **kwargs: locator
    login.context = AsyncMock()
    login.context.pages = [page]
    with pytest.raises(ValueError, match="成功标志"):
        await login.command("confirm", "shop", "a")
    assert login.store("shop", "a").load() is None
    login.context.storage_state.assert_not_awaited()


async def test_success_menu_is_revealed_only_for_explicit_verification(tmp_path):
    login = manager(tmp_path)
    login.sites['shop']['success_hover_selectors'] = ['#account-menu']
    visible = [False]
    trigger, marker = AsyncMock(), AsyncMock()
    trigger.first, marker.first = trigger, marker
    async def reveal(**kwargs):
        visible[0] = True
    trigger.hover.side_effect = reveal
    marker.is_visible.side_effect = lambda: visible[0]
    page = AsyncMock()
    page.url, page.frames = 'https://shop.invalid/', []
    page.locator = lambda selector: trigger if selector == '#account-menu' else marker
    context = AsyncMock()
    context.pages = [page]
    assert (await login.classify(context, 'shop'))['state'] == 'unknown'
    trigger.hover.assert_not_awaited()
    assert (await login.classify(context, 'shop', reveal=True))['state'] == 'valid'
    trigger.hover.assert_awaited_once()


async def test_saved_check_does_not_reuse_closed_startup_tab(tmp_path):
    login = manager(tmp_path)
    login.store('shop', 'a').save_verified({'cookies': [
        {'name': 'fixture', 'value': 'fixture', 'domain': 'shop.invalid', 'path': '/'}], 'origins': []})
    old, fresh = AsyncMock(), AsyncMock()
    old.goto.side_effect = RuntimeError('Startup tab has closed')
    login.context = AsyncMock()
    login.context.pages = [old]
    login.context.new_page.return_value = fresh
    login.context.storage_state.return_value = {'cookies': [
        {'name': 'fixture', 'value': 'fixture', 'domain': 'shop.invalid', 'path': '/'}], 'origins': []}
    login.start_native = AsyncMock()
    login.classify = AsyncMock(return_value={'state': 'valid', 'reason': 'verified'})
    login.close = AsyncMock()
    assert (await login.check('shop', 'a', force=True))['state'] == 'valid'
    fresh.goto.assert_awaited_once()
    old.goto.assert_not_awaited()
    login.classify.assert_awaited_once_with(login.context, 'shop', reveal=True, target_page=fresh)


async def test_browser_start_failure_is_not_reported_as_platform_network_failure(tmp_path):
    login = manager(tmp_path)
    login.store('shop', 'a').save_verified({'cookies': [
        {'name': 'fixture', 'value': 'fixture', 'domain': 'shop.invalid'}], 'origins': []})
    login.start_native = AsyncMock(side_effect=RuntimeError('Fixture browser startup failure'))
    login.close = AsyncMock()
    result = await login.check('shop', 'a', force=True)
    assert result == {'state': 'unknown', 'reason': 'browser_start_failed'}
    assert list((tmp_path / 'private' / 'session-checks').glob('*.json'))
    login.close.assert_awaited_once()


async def test_visible_success_marker_skips_legacy_menu_hover(tmp_path):
    login = manager(tmp_path)
    login.sites['shop']['challenge_selectors'] = []
    login.sites['shop']['success_hover_selectors'] = ['#old-menu']
    trigger, marker = AsyncMock(), AsyncMock()
    trigger.first, marker.first = trigger, marker
    marker.is_visible.return_value = True
    page = AsyncMock()
    page.url, page.frames = 'https://shop.invalid/', []
    page.locator = lambda selector: trigger if selector == '#old-menu' else marker
    context = AsyncMock()
    context.pages = [page]
    assert (await login.classify(context, 'shop', reveal=True))['state'] == 'valid'
    trigger.hover.assert_not_awaited()


async def test_unstable_frame_scan_cannot_be_overridden_by_success_marker(tmp_path, monkeypatch):
    import app.plugin.infra.crawler.login_service as module
    login = manager(tmp_path)
    page = AsyncMock()
    page.url = 'https://shop.invalid/'
    context = AsyncMock()
    context.pages = [page]
    monkeypatch.setattr(module, 'inspect_access', AsyncMock(return_value={
        'state': 'unknown', 'reason': 'page_changing', 'evidence': ['detached_frame']}))
    result = await login.classify(context, 'shop', reveal=True)
    assert result == {'state': 'unknown', 'reason': 'page_changing'}
    page.locator.assert_not_called()
