import asyncio
import hashlib
import json
import threading
import time
from http.server import ThreadingHTTPServer

import httpx
import pytest

from app.plugin.infra.crawler.login_access import LoginAccess
from app.plugin.infra.crawler.login_service import LoginManager, handler
from app.plugin.infra.crawler.session_api import SessionAPI, SessionAPIError


class FixtureManager(LoginManager):
    calls = 0

    async def check(self, platform, account, *, force=False):
        self.calls += 1
        return {'state': 'valid', 'reason': 'verified'}

    async def command(self, action, platform, account):
        self.active = {'platform': platform, 'account': account, 'expires_at': time.time() + 900}


@pytest.mark.asyncio
async def test_state_cache_invalidation_and_owned_one_time_entry(tmp_path):
    manager = FixtureManager({'1688': {'name': '1688', 'domains': ['fixture.invalid']}}, tmp_path / 'auth')
    access = LoginAccess(tmp_path / 'key.json')
    api = SessionAPI(manager, access)
    body = {'platform': '1688', 'session_name': '采购账号'}
    assert (await api.dispatch('a', 'POST', '/v1/sessions/status', body))[1]['state'] == 'not_logged_in'
    manager.store('1688', '采购账号').save_verified({'cookies': [{'name': 'fixture', 'value': 'private', 'domain': 'fixture.invalid'}]})
    assert api.status('1688', '采购账号')['state'] == 'valid'
    code, job = await api.dispatch('a', 'POST', '/v1/sessions/check', {**body, 'force': True})
    assert code == 202
    await asyncio.gather(*api.tasks)
    assert api.status('1688', '采购账号')['state'] == 'valid'
    assert (await api.dispatch('a', 'POST', '/v1/sessions/check', body))[1]['cached']
    manager.store('1688', '采购账号').discard()
    assert api.status('1688', '采购账号')['state'] == 'not_logged_in'
    _, job = await api.dispatch('a', 'POST', '/v1/sessions/access', body)
    await asyncio.gather(*api.tasks)
    path = '/v1/sessions/operations/' + job['operation_id']
    with pytest.raises(SessionAPIError) as error:
        await api.dispatch('b', 'GET', path)
    assert error.value.status == 404
    assert 'ticket' not in (await api.dispatch('a', 'GET', path))[1]
    entry = (await api.dispatch('a', 'POST', path + '/entry', {}))[1]
    assert access.exchange(entry['access'])
    with pytest.raises(SessionAPIError):
        await api.dispatch('a', 'POST', path + '/entry', {})
    with pytest.raises(SessionAPIError):
        await api.dispatch('a', 'POST', '/v1/sessions/access', {**body, 'session_name': 'other'})


@pytest.mark.asyncio
async def test_real_http_service_auth_and_shared_sdk(tmp_path):
    from app.plugin.infra.crawler.collector_client import CollectorClient
    manager = FixtureManager({'1688': {'name': '1688', 'domains': ['fixture.invalid']}}, tmp_path / 'auth')
    access = LoginAccess(tmp_path / 'key.json')
    api = SessionAPI(manager, access)
    key = 'colsvc_' + 'a' * 40
    registry = tmp_path / 'clients.json'
    registry.write_text(json.dumps({'clients': [{'id': 'fixture', 'enabled': True, 'token_sha256': hashlib.sha256(key.encode()).hexdigest()}]}))
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler(manager, asyncio.get_running_loop(), access, api, registry))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{server.server_port}/v1'
    try:
        async with httpx.AsyncClient() as anonymous:
            assert (await anonymous.post(base + '/sessions/status', json={})).status_code == 401
        async with CollectorClient(base, key) as client:
            assert (await client.session_status('1688', '中文会话'))['saved'] is False
            operation = await client.login_access('1688', '中文会话')
            for _ in range(20):
                state = await client.session_operation(operation['operation_id'])
                if state['state'] != 'running':
                    break
                await asyncio.sleep(.01)
            assert state['state'] == 'completed'
            entry = await client.login_entry(operation['operation_id'])
            assert entry['url'].startswith(base.removesuffix('/v1') + '/entry#access=')
    finally:
        await api.close()
        await asyncio.to_thread(server.shutdown)
        server.server_close()
