import asyncio
import sys
import time
import threading
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import httpx

from app.plugin.infra.crawler.login_access import LoginAccess, ticket
from app.plugin.infra.crawler.login_service import LoginManager, handler
from app.plugin.infra.crawler.native_login import NativeLoginManager
from app.plugin.infra.crawler.session_api import SessionAPI


def browser_fixture(tmp_path, monkeypatch, kind):
    manager_type = NativeLoginManager if kind == 'native' else LoginManager
    login = manager_type({'fixture': {'name': 'fixture', 'url': 'https://fixture.invalid/',
                                     'domains': ['fixture.invalid'], 'success_selectors': ['#account']}}, tmp_path / 'auth')
    access = LoginAccess(tmp_path / 'key.json')
    login.access = access
    context, browser, page = AsyncMock(), AsyncMock(), AsyncMock()
    context.on = Mock()
    browser.new_context.return_value = context
    context.new_page.return_value = page
    context.storage_state.return_value = {'cookies': [{'name': 'fixture', 'value': 'fixture',
                                                     'domain': 'fixture.invalid', 'path': '/'}], 'origins': []}
    if kind == 'native':
        executable = tmp_path / 'fixture-browser'
        executable.write_text('fixture')
        monkeypatch.setenv('LOGIN_CHROME_EXECUTABLE', str(executable))
        process = Mock()
        process.poll.return_value = None
        process.wait.return_value = 0
        monkeypatch.setattr('app.plugin.infra.crawler.native_login.subprocess.Popen', Mock(return_value=process))
        async def attach():
            login.browser, login.context = browser, context
        login.attach = attach
    else:
        playwright = SimpleNamespace(chromium=SimpleNamespace(launch=AsyncMock(return_value=browser)))
        factory = SimpleNamespace(start=AsyncMock(return_value=playwright))
        monkeypatch.setitem(sys.modules, 'playwright.async_api', SimpleNamespace(async_playwright=lambda: factory))
    login.classify = AsyncMock(return_value={'state': 'valid', 'reason': 'verified'})
    return login, access, page


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['native', 'playwright'])
@pytest.mark.parametrize('action', ['cancel', 'confirm', 'expire'])
async def test_window_lifecycle_revokes_authorization(tmp_path, monkeypatch, kind, action):
    login, access, _ = browser_fixture(tmp_path, monkeypatch, kind)
    await login.command('start', 'fixture', 'first')
    cookie = 'collector_login=' + access.exchange(ticket(access.key))
    unused = ticket(access.key)
    if action == 'expire':
        login.active['expires_at'] = time.time() - 1
        task = asyncio.create_task(login.expire())
        try:
            for _ in range(30):
                if login.active is None:
                    break
                await asyncio.sleep(.1)
            assert login.active is None
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    else:
        await login.command(action, 'fixture', 'first')
    assert not access.authorized(cookie)
    with pytest.raises(ValueError):
        access.exchange(unused)
    if action == 'confirm':
        assert login.store('fixture', 'first').load() is not None
    await login.command('start', 'fixture', 'second')
    assert not access.authorized(cookie)
    await login.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['native', 'playwright'])
async def test_queued_old_browser_command_is_checked_inside_lock(tmp_path, monkeypatch, kind):
    login, access, _ = browser_fixture(tmp_path, monkeypatch, kind)
    await login.command('start', 'fixture', 'first')
    cookie = 'collector_login=' + access.exchange(ticket(access.key))
    await login.lock.acquire()
    task = asyncio.create_task(login.command('cancel', 'fixture', 'first', authorize=lambda: access.start_grant(cookie)))
    await asyncio.sleep(0)
    access.close_window()
    login.lock.release()
    with pytest.raises(ValueError):
        await task
    assert login.active is not None  # The rejected stale request did not cancel any window.
    await login.close()


@pytest.mark.asyncio
async def test_start_failure_revokes_opened_window(tmp_path, monkeypatch):
    login, access, page = browser_fixture(tmp_path, monkeypatch, 'playwright')
    management = 'collector_login=' + access.exchange(ticket(access.key))
    page.goto.side_effect = RuntimeError('fixture navigation failed')
    with pytest.raises(RuntimeError):
        await login.command('start', 'fixture', 'first')
    assert login.active is None and access.window is None
    assert not access.authorized(management)


@pytest.mark.asyncio
async def test_http_start_transfers_only_callers_cookie_and_cancel_revokes_it(tmp_path, monkeypatch):
    login, access, _ = browser_fixture(tmp_path, monkeypatch, 'playwright')
    transfers = []
    class ClipboardConnection:
        def __init__(self, host, port, timeout):
            assert (host, port) == ('127.0.0.1', 6082)
        def request(self, *args, **kwargs):
            assert login.lock.locked()
            transfers.append(args)
        def getresponse(self):
            return SimpleNamespace(status=200, read=lambda size: b'{"ok": true}')
        def close(self):
            pass
    monkeypatch.setattr('app.plugin.infra.crawler.login_service.http.client.HTTPConnection', ClipboardConnection)
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler(login, asyncio.get_running_loop(), access))
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    try:
        async with httpx.AsyncClient(base_url=base, trust_env=False, headers={'Origin': base}) as caller, \
                httpx.AsyncClient(base_url=base, trust_env=False, headers={'Origin': base}) as other:
            for client in (caller, other):
                response = await client.post('/exchange', json={'access': ticket(access.key)})
                assert response.status_code == 200
                client.headers['X-Collector-Window'] = response.json()['window_id']
            body = {'action': 'start', 'platform': 'fixture', 'account': 'first'}
            response = await caller.post('/command', json=body)
            assert response.status_code == 200
            first_window = response.json()['active']['window_id']
            caller.headers['X-Collector-Window'] = first_window
            assert (await caller.get('/authorize')).status_code == 200
            assert (await caller.post('/clipboard', json={'text': '中文测试'})).status_code == 200
            assert len(transfers) == 1
            assert (await other.get('/authorize')).status_code == 401
            assert (await caller.post('/command', json={**body, 'action': 'revoke', 'account': 'other'})).status_code == 400
            assert login.active['account'] == 'first'
            assert (await caller.post('/command', json={**body, 'action': 'cancel'})).status_code == 200
            assert (await caller.get('/authorize')).status_code == 401
            await login.command('start', 'fixture', 'first')
            response = await caller.post('/exchange', json={'access': ticket(access.key)})
            assert response.status_code == 200
            # Old tab has a fresh browser-wide Cookie but retains its own window ID.
            assert (await caller.get('/status')).status_code == 401
            assert (await caller.post('/clipboard', json={'text': '不应发送'})).status_code == 400
            assert len(transfers) == 1
            assert (await caller.post('/command', json={**body, 'action': 'cancel'})).status_code == 400
            assert login.active is not None
            caller.headers['X-Collector-Window'] = response.json()['window_id']
            assert (await caller.get('/status')).status_code == 200
    finally:
        await login.close()
        await asyncio.to_thread(server.shutdown)
        server.server_close()
        thread.join(timeout=3)


@pytest.mark.asyncio
async def test_access_cannot_reauthorize_a_window_while_browser_is_closing(tmp_path, monkeypatch):
    login, access, _ = browser_fixture(tmp_path, monkeypatch, 'playwright')
    api = SessionAPI(login, access)
    await login.command('start', 'fixture', 'first')
    unused = ticket(access.key)
    entered, release = asyncio.Event(), asyncio.Event()
    async def slow_close():
        entered.set()
        await release.wait()
    login.browser.close.side_effect = slow_close
    close_task = asyncio.create_task(login.close())
    await entered.wait()
    try:
        _, operation = await api.dispatch('fixture', 'POST', '/v1/sessions/access',
                                          {'platform': 'fixture', 'session_name': 'first'})
        await asyncio.gather(*api.tasks)
        outcome = (await api.dispatch('fixture', 'GET', '/v1/sessions/operations/' + operation['operation_id']))[1]
        assert outcome['state'] == 'failed' and outcome['reason'] == 'window_busy'
        assert access.window is None
    finally:
        release.set()
        await close_task
        await api.close()
    with pytest.raises(ValueError):
        access.exchange(unused)
