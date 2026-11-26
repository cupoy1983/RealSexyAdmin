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
    login.start_native = AsyncMock()
    login.classify = AsyncMock(return_value={'state': 'valid', 'reason': 'verified'})
    login.close = AsyncMock()
    assert (await login.check('shop', 'a'))['state'] == 'valid'
    fresh.goto.assert_awaited_once()
    old.goto.assert_not_awaited()
