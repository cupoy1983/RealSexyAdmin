"""Shared backend-only Collector client. No browser or business-framework imports."""
from urllib.parse import urlsplit

from .product_jobs.client import ProductJobsClient


class CollectorClient(ProductJobsClient):
    async def session_status(self, platform, session_name):
        return await self.request('POST', 'sessions/status', json={'platform': platform, 'session_name': session_name})

    async def check_session(self, platform, session_name, *, force=False):
        return await self.request('POST', 'sessions/check', json={'platform': platform, 'session_name': session_name, 'force': force})

    async def login_access(self, platform, session_name):
        return await self.request('POST', 'sessions/access', json={'platform': platform, 'session_name': session_name})

    @staticmethod
    def operation_id(value):
        import re
        if not re.fullmatch('[a-f0-9]{32}', value):
            raise ValueError('操作标识无效')
        return value

    async def session_operation(self, operation_id):
        return await self.request('GET', 'sessions/operations/' + self.operation_id(operation_id))

    async def login_entry(self, operation_id, *, browser_origin=None):
        # Validate origin before consuming the one-time ticket.
        base = urlsplit(str(self.client.base_url))
        origin = browser_origin or f'{base.scheme}://{base.netloc}'
        target = urlsplit(origin)
        if (target.scheme != 'https' and not (target.scheme == 'http' and target.hostname in {'127.0.0.1', 'localhost', '::1'})
                or not target.hostname or target.username or target.password or target.path not in {'', '/'} or target.query or target.fragment):
            raise ValueError('浏览器入口必须为可信 HTTPS 或回环 HTTP origin')
        entry = await self.request('POST', 'sessions/operations/' + self.operation_id(operation_id) + '/entry', json={})
        return {'url': origin.rstrip('/') + '/entry#access=' + entry['access'], 'expires_at': entry['expires_at']}
