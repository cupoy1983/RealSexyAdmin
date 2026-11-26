import asyncio

import fakeredis.aioredis
import pytest

from app.plugin.infra.crawler.session_broker import SessionBroker, SessionConflict


@pytest.fixture
def broker():
    return SessionBroker(fakeredis.aioredis.FakeRedis(decode_responses=True))


async def test_coalescing_concurrent_claim_and_cache(broker):
    await broker.poll("office", ["1688"])
    first, second = await asyncio.gather(*(broker.enqueue("office", "1688", "default", "check", 1) for _ in range(2)))
    assert first["operation_id"] == second["operation_id"]
    jobs = await asyncio.gather(*(broker.poll("office", ["1688"]) for _ in range(2)))
    assert sum(job is not None for job in jobs) == 1
    job = next(job for job in jobs if job)
    await broker.complete("office", job["operation_id"], job["secret"], "valid", "verified")
    assert (await broker.status("office", "1688", "default"))["state"] == "valid"
    assert (await broker.enqueue("office", "1688", "default", "check", 1))["cached"]
    with pytest.raises(SessionConflict):
        await broker.complete("office", job["operation_id"], job["secret"], "login_required", "login_redirect")


async def test_worker_identity_owner_and_one_time_entry(broker):
    await broker.poll("office", ["1688"])
    queued = await broker.enqueue("office", "1688", "default", "access", 1)
    job = await broker.poll("office", ["1688"])
    with pytest.raises(SessionConflict):
        await broker.complete("other", job["operation_id"], job["secret"], "valid", "verified")
    await broker.complete("office", job["operation_id"], job["secret"], "verification_required", "challenge", "fixture-entry")
    with pytest.raises(SessionConflict):
        await broker.operation(queued["operation_id"], 2, claim_entry=True)
    assert "secret" not in await broker.operation(queued["operation_id"], 1)
    claims = await asyncio.gather(*(broker.operation(queued["operation_id"], 1, claim_entry=True) for _ in range(2)))
    assert sum("secret" in result for result in claims) == 1


async def test_observation_generation_and_stale_valid_state(broker):
    now = [1000]
    broker.clock = lambda: now[0]
    await broker.observe("office", "1688", "default", 0, "valid", "verified")
    with pytest.raises(SessionConflict):
        await broker.observe("office", "1688", "default", 0, "login_required", "login_redirect")
    now[0] += 61
    result = await broker.status("office", "1688", "default")
    assert result["state"] == "unknown" and result["last_known_state"] == "valid" and not result["fresh"]
    with pytest.raises(SessionConflict):
        await broker.observe("office", "1688", "default", 1, "valid", "verified", observed_at=800)


async def test_expired_operation_not_replayed_and_old_result_rejected(broker):
    now = [1000]
    broker.clock = lambda: now[0]
    await broker.poll("office", ["1688"])
    first = await broker.enqueue("office", "1688", "default", "access", 1)
    job = await broker.poll("office", ["1688"])
    now[0] += 181
    assert await broker.poll("office", ["1688"]) is None
    assert (await broker.operation(first["operation_id"], 1))["state"] == "expired"
    await broker.enqueue("office", "1688", "default", "check", 1)
    with pytest.raises(SessionConflict):
        await broker.complete("office", job["operation_id"], job["secret"], "valid", "verified")
