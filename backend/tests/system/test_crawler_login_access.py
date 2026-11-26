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
