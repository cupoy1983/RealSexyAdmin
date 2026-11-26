"""Shared Collector session operations; all mutable state belongs to its asyncio loop."""
import asyncio
import re
import time
from uuid import uuid4

from .login_access import ticket


class SessionAPIError(Exception):
    def __init__(self, status, reason):
        self.status, self.reason = status, reason


class SessionAPI:
    def __init__(self, manager, access, *, clock=time.time):
        self.manager, self.access, self.clock = manager, access, clock
        self.cache, self.operations = {}, {}
        self.tasks = set()
        self.busy = False

    def scope(self, body):
        if not isinstance(body, dict) or set(body) - {'platform', 'session_name', 'force'}:
            raise SessionAPIError(422, 'invalid_request')
        platform, name = body.get('platform'), body.get('session_name')
        if not isinstance(platform, str) or platform not in self.manager.sites or not isinstance(name, str):
            raise SessionAPIError(422, 'invalid_scope')
        if not re.fullmatch(r'[a-zA-Z0-9_\u3400-\u4dbf\u4e00-\u9fff-]{1,48}', name):
            raise SessionAPIError(422, 'invalid_scope')
        if type(body.get('force', False)) is not bool:
            raise SessionAPIError(422, 'invalid_request')
        return platform, name

    def fingerprint(self, platform, name):
        store = self.manager.store(platform, name)
        if not store.load():
            return None
        stat = store.path.stat()
        return stat.st_mtime_ns, stat.st_size

    def status(self, platform, name):
        fingerprint = self.fingerprint(platform, name)
        cached = self.cache.get((platform, name))
        fresh = bool(cached and cached['fingerprint'] == fingerprint and self.clock() - cached['checked_at'] < 60
                     and (fingerprint is not None or cached['state'] != 'valid'))
        return {'platform': platform, 'session_name': name, 'saved': fingerprint is not None,
                'state': cached['state'] if fresh else ('unknown' if fingerprint else 'not_logged_in'),
                'reason': cached['reason'] if fresh else ('check_required' if fingerprint else 'no_saved_state'),
                'checked_at': cached['checked_at'] if cached else None, 'fresh': fresh}

    async def dispatch(self, owner, method, path, body=None):
        now = self.clock()
        self.operations = {k: v for k, v in self.operations.items() if now - v['created_at'] < 600}
        self.cache = {k: v for k, v in self.cache.items() if now - v['checked_at'] < 600}
        match = re.fullmatch(r'/v1/sessions/operations/([a-f0-9]{32})(/entry)?', path)
        if match:
            operation = self.operations.get(match[1])
            if not operation or operation['owner'] != owner:
                raise SessionAPIError(404, 'not_found')
            if match[2] and method == 'POST':
                token = operation.get('ticket')
                if not token or operation['state'] != 'completed' or now >= operation['entry_deadline']:
                    raise SessionAPIError(409, 'entry_unavailable')
                del operation['ticket']
                return 200, {'access': token, 'expires_at': operation['entry_deadline']}
            if not match[2] and method == 'GET':
                return 200, {k: v for k, v in operation.items() if k not in {'owner', 'ticket'}}
            raise SessionAPIError(404, 'not_found')
        if method != 'POST' or path not in {'/v1/sessions/status', '/v1/sessions/check', '/v1/sessions/access'}:
            raise SessionAPIError(404, 'not_found')
        platform, name = self.scope(body)
        if path.endswith('/status'):
            return 200, self.status(platform, name)
        kind = path.rsplit('/', 1)[1]
        if kind == 'check' and not body.get('force'):
            status = self.status(platform, name)
            if status['fresh']:
                return 200, {'cached': True, 'result': status, 'operation_id': None}
        if self.busy or len(self.operations) >= 128:
            raise SessionAPIError(409 if self.busy else 429, 'window_busy' if self.busy else 'queue_full')
        active = self.manager.active
        if active and (active['platform'], active['account']) != (platform, name):
            raise SessionAPIError(409, 'window_busy')
        identifier = uuid4().hex
        operation = {'operation_id': identifier, 'owner': owner, 'kind': kind, 'platform': platform,
                     'session_name': name, 'state': 'running', 'created_at': now}
        self.operations[identifier] = operation
        self.busy = True
        task = asyncio.create_task(self.run(operation))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return 202, {'operation_id': identifier, 'cached': False}

    async def run(self, operation):
        platform, name = operation['platform'], operation['session_name']
        try:
            async with asyncio.timeout(75):
                if operation['kind'] == 'check':
                    result = await self.manager.check(platform, name)
                    self.cache[(platform, name)] = {**result, 'checked_at': self.clock(),
                                                   'fingerprint': self.fingerprint(platform, name)}
                    operation['result'] = self.status(platform, name)
                else:
                    if not self.manager.active:
                        await self.manager.command('start', platform, name)
                    active = self.manager.active
                    if not active or (active['platform'], active['account']) != (platform, name):
                        raise SessionAPIError(409, 'window_busy')
                    operation['ticket'] = ticket(self.access.key)
                    operation['entry_deadline'] = int(operation['ticket'].split('.')[0])
                operation['state'] = 'completed'
        except asyncio.CancelledError:
            operation.update(state='failed', reason='process_interrupted')
            raise
        except TimeoutError:
            operation.update(state='failed', reason='timeout')
        except Exception:
            operation.update(state='failed', reason='operation_failed')
        finally:
            self.busy = False

    async def close(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
