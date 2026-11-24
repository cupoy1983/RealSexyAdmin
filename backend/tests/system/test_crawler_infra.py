from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from app.plugin.infra.crawler.http import retry_after_seconds
from app.plugin.infra.crawler.interactive import interact
from app.plugin.infra.crawler.session import GuestSessionStore
from app.plugin.infra.crawler.traffic import TrafficGate


async def test_navigation_spacing_and_restart_cooldown(tmp_path):
    now, waits = [1000.0], []

    async def sleep(seconds):
        waits.append(seconds)
        now[0] += seconds

    path = tmp_path / "traffic.json"
    gate = TrafficGate(path, clock=lambda: now[0], sleep=sleep)
    await gate.navigate()
    await gate.navigate(8)
    assert waits == [8]
    gate.cooldown(30, "fixture_limited")
    restarted = TrafficGate(path, clock=lambda: now[0], sleep=sleep)
    assert restarted.remaining == 30
    await restarted.navigate()
    assert waits == [8, 30] and restarted.remaining == 0
    assert restarted.state["last_navigation"] == 1038


@pytest.mark.parametrize("body", ["[]", "{}", "null", '{"last_navigation":-1,"cooldown_until":0}', "broken"])
def test_corrupt_traffic_state_fails_closed(tmp_path, body):
    path = tmp_path / "traffic.json"
    path.write_text(body, encoding="utf-8")
    with pytest.raises(ValueError):
        TrafficGate(path)


def test_guest_state_bound_to_scope_domain_and_expiry(tmp_path):
    path, now = tmp_path / "guest.json", [10]
    store = GuestSessionStore(path, "fixture:US:10001", {"fixture.invalid"}, 60, clock=lambda: now[0])
    store.save({"cookies": [{"name": "region", "value": "fixture", "domain": ".fixture.invalid"},
                            {"name": "other", "domain": "another.invalid"}], "origins": [{"origin": "ignored"}]})
    assert store.load() == {"cookies": [{"name": "region", "value": "fixture", "domain": ".fixture.invalid"}], "origins": []}
    changed = GuestSessionStore(path, "fixture:US:90001", {"fixture.invalid"}, clock=lambda: now[0])
    assert changed.load() is None and not path.exists()
    store.save({"cookies": []})
    now[0] = 71
    assert store.load() is None and not path.exists()


def test_retry_after_seconds_or_http_date():
    now = datetime(2026, 1, 1, tzinfo=UTC)
    assert retry_after_seconds("120", now) == 120
    assert retry_after_seconds("Thu, 01 Jan 2026 00:01:00 GMT", now) == 60
    assert retry_after_seconds("invalid", now) is None
    assert retry_after_seconds("999999999", now) == 86400


async def test_interactions_only_dispatch_explicit_operator_command():
    page = AsyncMock()
    page.evaluate.return_value = False
    await interact(page, {"action": "click", "x": 10, "y": 20})
    page.mouse.click.assert_awaited_once_with(10, 20)
    await interact(page, {"action": "text", "secret": "fixture answer"})
    page.keyboard.insert_text.assert_awaited_once_with("fixture answer")
    await interact(page, {"action": "resume"})
    page.keyboard.press.assert_not_awaited()
