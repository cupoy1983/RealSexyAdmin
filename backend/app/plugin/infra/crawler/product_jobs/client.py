"""For a trusted business backend; never put the service key in frontend code."""
from pathlib import Path
from urllib.parse import urlsplit
import json

import httpx


class ProductJobsClient:
    def __init__(self, base_url, service_key, *, transport=None):
        target=urlsplit(base_url)
        if target.scheme!='https' and not (target.scheme=='http' and target.hostname in {'127.0.0.1','localhost','::1'}):
            raise ValueError('采集 API 需要 HTTPS，开发 HTTP 仅限回环 SSH 转发')
        if not target.hostname or target.username or target.password or target.query or target.fragment:
            raise ValueError('采集 API 地址无效')
        self.client=httpx.AsyncClient(base_url=base_url.rstrip('/')+'/',headers={'Authorization':'Bearer '+service_key},
                                     timeout=15,trust_env=False,transport=transport)

    @classmethod
    def from_file(cls,path,**kwargs):
        location=Path(path)
        if location.is_symlink():raise ValueError('服务凭据不能为符号链接')
        value=json.loads(location.read_text(encoding='utf-8'))
        return cls(value['api_url'],value['api_key'],**kwargs)

    async def request(self,method,path,**kwargs):
        response=await self.client.request(method,path,**kwargs)
        response.raise_for_status()
        body=response.json()
        if body.get('code')!=0:raise RuntimeError('采集服务拒绝请求')
        return body['data']

    async def capabilities(self):return await self.request('GET','capabilities')

    async def submit(self,*,platform,url,session_name,idempotency_key,save_mode='overwrite'):
        if save_mode not in {'overwrite', 'history'}:
            raise ValueError('采集保存模式无效')
        return await self.request('POST','jobs',json={'platform':platform,'url':url,'session_name':session_name,'save_mode':save_mode},
                                  headers={'Idempotency-Key':idempotency_key})

    async def status(self,job_id):
        import re
        if not re.fullmatch('[a-f0-9]{32}',job_id):raise ValueError('任务 ID 无效')
        return await self.request('GET','jobs/'+job_id)

    async def result(self,job_id):
        import re
        if not re.fullmatch('[a-f0-9]{32}',job_id):raise ValueError('任务 ID 无效')
        return await self.request('GET','jobs/'+job_id+'/result')

    async def aclose(self):await self.client.aclose()

    async def __aenter__(self):return self

    async def __aexit__(self,*args):await self.aclose()
