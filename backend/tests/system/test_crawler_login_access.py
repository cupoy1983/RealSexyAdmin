import pytest

from app.plugin.infra.crawler.login_access import LoginAccess, ticket


def test_ticket_single_use_and_session_expiry(tmp_path):
    now = [1000]
    access = LoginAccess(tmp_path / "key.json", clock=lambda: now[0])
    value = ticket(access.key, now[0])
    session = access.exchange(value)
    assert access.authorized(f"collector_login={session}")
    with pytest.raises(ValueError):
        access.exchange(value)
    assert not access.authorized("collector_login=unknown")
    now[0] = 4600
    assert not access.authorized(f"collector_login={session}")


def test_expired_forged_and_restart_tickets_rejected(tmp_path):
    access = LoginAccess(tmp_path / "key.json", clock=lambda: 1000)
    for value in (ticket(access.key, 900), ticket("wrong", 1000), "bad"):
        with pytest.raises(ValueError):
            access.exchange(value)
    old = ticket(access.key, 1000)
    restarted = LoginAccess(tmp_path / "key.json", clock=lambda: 1000)
    with pytest.raises(ValueError):
        restarted.exchange(old)


def test_window_change_revokes_cookie_unused_ticket_and_limits_expiry(tmp_path):
    now = [1000]
    access = LoginAccess(tmp_path / 'key.json', clock=lambda: now[0])
    first = {'platform': '1688', 'account': 'first', 'expires_at': 1900}
    access.begin_window(first)
    cookie = 'collector_login=' + access.exchange(ticket(access.key, now[0]))
    unused = ticket(access.key, now[0])
    access.begin_window(first)  # Same live window, another entry request.
    assert access.authorized(cookie)
    now[0] = 1900
    assert not access.authorized(cookie)
    access.close_window()
    access.begin_window({'platform': '1688', 'account': 'second', 'expires_at': 2800})
    assert not access.authorized(cookie)
    with pytest.raises(ValueError):
        access.exchange(unused)


def test_management_cookie_only_transferred_for_initiating_browser(tmp_path):
    access = LoginAccess(tmp_path / 'key.json')
    first = 'collector_login=' + access.exchange(ticket(access.key))
    other = 'collector_login=' + access.exchange(ticket(access.key))
    grant = access.start_grant(first)
    access.begin_window({'platform': '1688', 'account': 'first', 'expires_at': access.clock() + 900})
    access.continue_start(grant)
    assert access.authorized(first)
    assert not access.authorized(other)
    access.close_window()
    assert not access.authorized(first)
    with pytest.raises(ValueError):
        access.continue_start(grant)
