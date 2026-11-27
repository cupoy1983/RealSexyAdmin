import json

import httpx
import pytest

from app.plugin.infra.crawler.product_jobs.client import ProductJobsClient


async def test_backend_sdk_uses_relative_paths_service_auth_and_idempotency(tmp_path):
    calls=[]
    def route(request):
        calls.append(request)
        return httpx.Response(202 if request.method=='POST' else 200,json={'code':0,'msg':'success','data':{'job_id':'a'*32}})
    file=tmp_path/'service.json'
    file.write_text(json.dumps({'api_url':'http://127.0.0.1:18126/collector/v1','api_key':'colsvc_'+'x'*40}))
    async with ProductJobsClient.from_file(file,transport=httpx.MockTransport(route)) as client:
        await client.submit(platform='1688',url='https://detail.1688.com/offer/123.html',session_name='main',idempotency_key='fixture-key')
        await client.status('a'*32);await client.result('a'*32)
        with pytest.raises(ValueError):await client.status('../other')
    assert [r.url.path for r in calls]==['/collector/v1/jobs','/collector/v1/jobs/'+'a'*32,'/collector/v1/jobs/'+'a'*32+'/result']
    assert calls[0].headers['Idempotency-Key']=='fixture-key'
    assert all(r.headers['Authorization']=='Bearer colsvc_'+'x'*40 for r in calls)


def test_backend_sdk_refuses_unencrypted_remote_api():
    with pytest.raises(ValueError):ProductJobsClient('http://collector.invalid/collector/v1','fixture')


async def test_current_product_sdk_reads_without_a_login_session():
    from app.plugin.infra.crawler.collector_client import CollectorClient
    calls = []
    def route(request):
        calls.append(request)
        return httpx.Response(200, json={'code': 0, 'data': {'job': {'session_name': 'actual-source'}, 'result': {'title': 'shared'}}})
    async with CollectorClient('http://127.0.0.1/collector/v1', 'fixture', transport=httpx.MockTransport(route)) as client:
        current = await client.current_product(platform='1688', url='https://detail.1688.com/offer/123.html')
    assert current['job']['session_name'] == 'actual-source'
    assert calls[0].url.path == '/collector/v1/products/current'
    assert json.loads(calls[0].content) == {'platform': '1688', 'url': 'https://detail.1688.com/offer/123.html'}
