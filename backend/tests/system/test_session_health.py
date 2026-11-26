import asyncio
import time
import pytest

from app.plugin.infra.crawler.auth_session import AuthSessionStore
from app.plugin.infra.crawler.login_access import LoginAccess
from app.plugin.infra.crawler.session_api import SessionAPI
from app.plugin.infra.crawler.session_health import SessionHealth
from app.plugin.infra.crawler.login_service import LoginManager


STATE = {'cookies': [{'name': 'fixture', 'value': 'fixture', 'domain': 'fixture.invalid'}], 'origins': []}


class Manager(LoginManager):
    calls = 0

    async def check(self, platform, account, *, force=False):
        self.calls += 1
        return {'state': 'valid', 'reason': 'verified'}


def setup(tmp_path):
    manager = Manager({'shop': {'name': 'Fixture', 'domains': ['fixture.invalid']}}, tmp_path / 'auth')
    manager.store('shop', 'main').save_verified(STATE)
    return manager, LoginAccess(tmp_path / 'key.json')


async def test_verified_login_survives_restart_and_twelve_hours_without_browser(tmp_path):
    manager, access = setup(tmp_path)
    now = [time.time()]
    api = SessionAPI(manager, access, clock=lambda: now[0])
    body = {'platform': 'shop', 'session_name': 'main'}
    now[0] += 43190
    restarted = SessionAPI(manager, access, clock=lambda: now[0])
    code, result = await restarted.dispatch('center', 'POST', '/v1/sessions/check', body)
    assert code == 200 and result['cached'] and result['result']['state'] == 'valid'
    assert manager.calls == 0 and not restarted.tasks
    now[0] += 20
    assert restarted.status('shop', 'main')['reason'] == 'check_required'


async def test_collection_anomaly_invalidates_cache_but_not_new_authorization(tmp_path):
    manager, access = setup(tmp_path)
    api = SessionAPI(manager, access)
    store = manager.store('shop', 'main')
    old = store.load_record()[1]['version']
    writer = SessionHealth(api.health.directory)
    writer.report(store.scope, old, 'verification_required', 'challenge')
    assert api.status('shop', 'main')['state'] == 'verification_required'
    assert api.status('shop', 'main')['saved']
    store.save_verified(STATE)
    writer.report(store.scope, old, 'login_required', 'session_missing')
    assert api.status('shop', 'main')['state'] == 'valid'


async def test_explicit_force_rechecks_and_refreshes_verified_state(tmp_path):
    manager, access = setup(tmp_path)
    api = SessionAPI(manager, access)
    code, result = await api.dispatch('center', 'POST', '/v1/sessions/check',
                                      {'platform': 'shop', 'session_name': 'main', 'force': True})
    assert code == 202
    await asyncio.gather(*api.tasks)
    assert manager.calls == 1 and api.status('shop', 'main')['state'] == 'valid'


def test_legacy_file_without_verified_time_does_not_gain_new_trust_window(tmp_path):
    from app.plugin.infra.crawler.state import write_private_json
    manager, access = setup(tmp_path)
    store = manager.store('shop', 'main')
    write_private_json(store.path, {'scope': store.scope, 'expires_at': time.time() + 43200, 'state': STATE})
    assert SessionAPI(manager, access).status('shop', 'main')['state'] == 'unknown'


async def test_native_and_legacy_checks_reuse_verified_state_without_starting_browser(tmp_path):
    from unittest.mock import AsyncMock
    from app.plugin.infra.crawler.native_login import NativeLoginManager
    sites = {'shop': {'name': 'Fixture', 'domains': ['fixture.invalid']}}
    native = NativeLoginManager(sites, tmp_path / 'auth')
    native.store('shop', 'main').save_verified(STATE)
    native.start_native = AsyncMock(side_effect=AssertionError('Must not launch a browser'))
    assert (await native.check('shop', 'main'))['state'] == 'valid'
    native.start_native.assert_not_awaited()
    legacy = LoginManager(sites, tmp_path / 'auth')
    assert (await legacy.check('shop', 'main'))['state'] == 'valid'


async def test_network_failure_does_not_become_valid_when_short_error_cache_expires(tmp_path):
    manager, access = setup(tmp_path)
    now = [time.time()]
    api = SessionAPI(manager, access, clock=lambda: now[0])
    store = manager.store('shop', 'main')
    api.health.report(store.scope, store.load_record()[1]['version'], 'network_error', 'network_failed')
    now[0] += 61
    result = api.status('shop', 'main')
    assert result['state'] == 'network_error' and not result['fresh']
    code, response = await api.dispatch('center', 'POST', '/v1/sessions/check',
                                        {'platform': 'shop', 'session_name': 'main'})
    assert code == 200 and response['cached'] and manager.calls == 0


@pytest.mark.parametrize('raised', [False, True])
@pytest.mark.parametrize('terminal,reason,expected', [
    ('needs_login', 'login_required', 'login_required'),
    ('needs_verification', 'challenge', 'verification_required'),
    ('needs_verification', 'access_denied', 'access_denied'),
    ('failed', 'network_error', 'network_error'),
])
async def test_product_terminal_updates_session_status_without_browser(tmp_path, monkeypatch, raised, terminal, reason, expected):
    import json
    from types import SimpleNamespace
    from app.plugin.infra.crawler.product_jobs import ali1688
    from app.plugin.infra.crawler.product_jobs.service import JobRunner
    from app.plugin.infra.crawler.product_jobs.store import JobStore

    manager = Manager({'1688': {'domains': ['1688.com']}}, tmp_path / 'auth')
    session = manager.store('1688', 'main')
    state = {'cookies': [{'name': 'fixture', 'value': 'fixture', 'domain': '1688.com'}], 'origins': []}
    session.save_verified(state)
    api = SessionAPI(manager, LoginAccess(tmp_path / 'key.json'))
    assert api.status('1688', 'main')['state'] == 'valid'
    sites = tmp_path / 'sites.json'
    sites.write_text(json.dumps(manager.sites))
    async def engine(*args, **kwargs):
        failure = ali1688.CollectionFailure(terminal, reason)
        if raised:
            raise failure
        return failure
    monkeypatch.setattr(ali1688, 'run_browser_job', engine)
    async def adapter(request, identifier):
        return await ali1688.collect(request, identifier, auth_dir=manager.directory,
            config_path=sites, diagnostics_dir=tmp_path / 'diagnostics', executable='/fixture', gate=SimpleNamespace())
    jobs = JobStore(tmp_path / 'jobs.sqlite3')
    request = {'platform': '1688', 'session_name': 'main', 'url': 'https://detail.1688.com/offer/123456789.html'}
    job, _ = jobs.submit('product', 'health-regression', request)
    await JobRunner(jobs, adapter).run_once()
    assert jobs.get('product', job['job_id'])['state'] == terminal
    code, status = await api.dispatch('product', 'POST', '/v1/sessions/status', {'platform': '1688', 'session_name': 'main'})
    assert code == 200 and status['state'] == expected and status['requires_attention']
    code, result = await api.dispatch('product', 'POST', '/v1/sessions/check', {'platform': '1688', 'session_name': 'main'})
    assert code == 200 and result['cached'] and manager.calls == 0
    session.save_verified(state)
    assert api.status('1688', 'main')['state'] == 'valid'
