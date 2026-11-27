import asyncio
import hashlib
import http.client
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer

import pytest
from pydantic import ValidationError

from app.plugin.infra.crawler.product_jobs.api import handler
from app.plugin.infra.crawler.product_jobs.ali1688 import CollectionFailure, normalize
from app.plugin.infra.crawler.product_jobs.schema import JobError, JobRequest
from app.plugin.infra.crawler.product_jobs.service import JobRunner
from app.plugin.infra.crawler.product_jobs.store import JobStore

REQUEST={'platform':'1688','url':'https://detail.1688.com/offer/123456789.html','session_name':'default'}
KEY_A='colsvc_'+'a'*40
KEY_B='colsvc_'+'b'*40


@pytest.fixture
def store(tmp_path):return JobStore(tmp_path/'jobs.sqlite3')


@pytest.fixture
def api(store,tmp_path):
    registry=tmp_path/'clients.json'
    registry.write_text(json.dumps({'clients':[{'id':name,'enabled':True,'token_sha256':hashlib.sha256(key.encode()).hexdigest()}
        for name,key in [('product',KEY_A),('other',KEY_B)]]}))
    server=ThreadingHTTPServer(('127.0.0.1',0),handler(store,registry))
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    def call(method,path,body=None,*,key=KEY_A,idem='fixture-unique-001'):
        conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
        headers={'Authorization':'Bearer '+key,'Content-Type':'application/json','Idempotency-Key':idem}
        conn.request(method,'/v1/'+path,json.dumps(body) if body is not None else None,headers)
        response=conn.getresponse();result=(response.status,json.loads(response.read()));conn.close();return result
    yield call
    server.shutdown();server.server_close();thread.join(timeout=3)


@pytest.mark.parametrize('url',[
    'http://detail.1688.com/offer/123.html','https://127.0.0.1/offer/123.html',
    'https://detail.1688.com.evil.invalid/offer/123.html','https://user:pass@detail.1688.com/offer/123.html',
    'https://detail.1688.com:22/offer/123.html','file:///etc/passwd',
])
def test_rejects_arbitrary_urls(url):
    with pytest.raises(ValidationError):JobRequest.model_validate({**REQUEST,'url':url})


def test_url_canonicalization_and_required_session():
    assert JobRequest.model_validate({**REQUEST,'url':'https://m.1688.com/offer/123456789.html?tracking=fixture'}).url==REQUEST['url']
    with pytest.raises(ValidationError):JobRequest.model_validate({**REQUEST,'session_name':''})


def test_http_auth_idempotency_owner_and_result_state(api,store):
    assert api('GET','capabilities',key='invalid')[0]==401
    assert api('GET','capabilities')[1]['data']['platforms']==['1688']
    code,body=api('POST','jobs',REQUEST);assert code==202
    identifier=body['data']['job_id']
    code,replay=api('POST','jobs',REQUEST);assert code==200 and replay['data']['job_id']==identifier
    assert api('POST','jobs',{**REQUEST,'session_name':'other'})[0]==409
    assert api('GET','jobs/'+identifier,key=KEY_B)[0]==404
    assert api('GET','jobs/'+identifier+'/result')[0]==409
    assert store.claim()['job_id']==identifier
    store.finish(identifier,'succeeded',result={'title':'fixture','result_version':1})
    assert api('GET','jobs/'+identifier+'/result')[1]['data']['title']=='fixture'
    assert api('GET','jobs/'+identifier)[1]['data']['result_ready'] is True


def test_invalid_body_and_platform_rejected(api):
    assert api('POST','jobs',{**REQUEST,'platform':'unknown'})[0]==422
    assert api('POST','jobs',{**REQUEST,'session_name':''})[0]==422
    assert api('POST','jobs',REQUEST,idem='')[0]==422
    assert api('POST','jobs',{**REQUEST,'caller_id':'other'})[0]==422


def test_concurrent_idempotency_and_queue_limit(tmp_path):
    store=JobStore(tmp_path/'queue.sqlite3',capacity=1)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results=list(pool.map(lambda _:store.submit('product','one-key-123',REQUEST),range(8)))
    assert len({r[0]['job_id'] for r in results})==1
    assert sum(not r[1] for r in results)==1
    with pytest.raises(JobError) as error:store.submit('product','second-key',REQUEST)
    assert error.value.status==429


def test_restart_marks_running_interrupted_and_retains_queued(store):
    first,_=store.submit('product','first-key-001',REQUEST)
    second,_=store.submit('product','second-key-002',REQUEST)
    claimed=store.claim()
    other=second if claimed['job_id']==first['job_id'] else first
    reopened=JobStore(store.path);assert reopened.recover()==1
    assert reopened.get('product',claimed['job_id'])['error_code']=='process_interrupted'
    assert reopened.claim()['job_id']==other['job_id']


async def test_real_http_to_runner_and_result(api,store):
    identifier=api('POST','jobs',REQUEST)[1]['data']['job_id']
    async def adapter(request,job_id):
        assert job_id==identifier and request['session_name']=='default'
        return {'result_version':1,'title':'Fixture product'}
    assert await JobRunner(store,adapter).run_once()
    assert api('GET','jobs/'+identifier+'/result')[1]['data']['title']=='Fixture product'


def test_current_product_api_needs_no_login_or_new_job(api, store):
    reference = {key: REQUEST[key] for key in ('platform', 'url')}
    assert api('POST', 'products/current', reference)[0] == 404
    identifier = api('POST', 'jobs', {**REQUEST, 'session_name': 'actual-source'})[1]['data']['job_id']
    store.claim()
    store.finish(identifier, 'succeeded', result={'title': 'shared product'})
    response, body = api('POST', 'products/current', {**reference, 'url': 'https://m.1688.com/offer/123456789.html?tracking=fixture'})
    assert response == 200
    assert body['data']['job']['session_name'] == 'actual-source'
    assert body['data']['result']['title'] == 'shared product'
    assert api('POST', 'products/current', reference, key=KEY_B)[0] == 404
    assert api('POST', 'products/current', reference, key='')[0] == 401
    assert api('POST', 'products/current', {**reference, 'session_name': 'not-needed'})[0] == 422
    assert api('POST', 'products/current', {**reference, 'url': 'https://127.0.0.1/private'})[0] == 422
    assert store.claim() is None
    with store.connection() as db:
        assert db.execute('SELECT count(*) FROM jobs').fetchone()[0] == 1


async def test_login_and_timeout_failures_are_terminal_without_fake_results(store):
    first,_=store.submit('product','login-key-001',REQUEST)
    async def missing(*args):raise CollectionFailure('needs_login','session_missing')
    await JobRunner(store,missing).run_once()
    assert store.get('product',first['job_id'])['state']=='needs_login'
    second,_=store.submit('product','timeout-key-001',REQUEST)
    async def slow(*args):await asyncio.sleep(10)
    await JobRunner(store,slow,timeout=0.01).run_once()
    assert store.get('product',second['job_id'])['error_code']=='timeout'
    with pytest.raises(JobError):store.get('product',first['job_id'],result=True)


async def test_cancel_marks_interrupted(store):
    job,_=store.submit('product','cancel-key-001',REQUEST)
    started=asyncio.Event()
    async def slow(*args):started.set();await asyncio.sleep(10)
    task=asyncio.create_task(JobRunner(store,slow).run_once());await started.wait();task.cancel()
    with pytest.raises(asyncio.CancelledError):await task
    assert store.get('product',job['job_id'])['error_code']=='process_interrupted'


def test_result_does_not_invent_prices_or_accept_redirected_identity():
    result=normalize({'title':'Fixture','prices':[],'images':['file:///private'],'offers':[]},REQUEST,REQUEST['url'])
    assert result['price_tiers']==[] and 'price_tiers' in result['missing_fields'] and result['images']==[]
    with pytest.raises(CollectionFailure):normalize({'title':'Fixture'},REQUEST,'https://detail.1688.com/offer/999.html')
    with pytest.raises(CollectionFailure):normalize({'title':'Fixture','offers':[{'offerId':'999'}]},REQUEST,REQUEST['url'])


async def test_crawlee_failure_callback_returns_sentinel_instead_of_raising(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from app.plugin.infra.crawler.auth_session import AuthSessionStore
    from app.plugin.infra.crawler.login_service import DEVICE
    from app.plugin.infra.crawler.product_jobs import ali1688
    auth=tmp_path/'auth'
    AuthSessionStore(auth/'1688/default.json',scope='1688:default:'+DEVICE,allowed_domains={'1688.com'}).save_verified(
        {'cookies':[{'name':'fixture','value':'fixture','domain':'1688.com','path':'/'}],'origins':[]})
    config=tmp_path/'sites.json';config.write_text(json.dumps({'1688':{'domains':['1688.com']}}))
    failure=CollectionFailure('needs_verification','challenge','a'*32)
    async def engine(url,handle,*,prepare,on_error,options):
        try:outcome=await on_error(SimpleNamespace(page=None),SimpleNamespace(original_error=failure))
        except CollectionFailure:pytest.fail('Raising inside Crawlee failure callback hides the business classification')
        assert outcome is failure
        return outcome
    monkeypatch.setattr(ali1688,'run_browser_job',engine)
    with pytest.raises(CollectionFailure) as error:
        await ali1688.collect(REQUEST,'fixture',auth_dir=auth,config_path=config,diagnostics_dir=tmp_path/'diagnostics',
                              executable='/fixture',gate=SimpleNamespace())
    assert error.value.state=='needs_verification' and error.value.diagnostic=='a'*32
    from app.plugin.infra.crawler.session_health import SessionHealth
    current = AuthSessionStore(auth/'1688/default.json', scope=f'1688:default:{DEVICE}', allowed_domains={'1688.com'})
    observation = SessionHealth(tmp_path/'session-health').read(current.scope, current.load_record()[1]['version'])
    assert observation['state'] == 'verification_required' and observation['reason'] == 'challenge'


@pytest.mark.parametrize('state,reason,expected', [
    ('unknown', 'check_failed', 'session_check_failed'),
    ('network_error', 'network_failed', 'network_error'),
    ('unknown', 'page_changing', 'page_changing'),
])
async def test_session_health_reasons_remain_valid_product_job_results(tmp_path, monkeypatch, state, reason, expected):
    from app.plugin.infra.crawler.product_jobs import ali1688
    from app.plugin.infra.crawler.auth_session import AuthSessionStore
    from app.plugin.infra.crawler.login_service import DEVICE
    from app.plugin.infra.crawler.session_health import SessionHealth
    from app.plugin.infra.crawler.product_jobs.schema import REASONS
    auth = tmp_path/'auth'
    session = AuthSessionStore(auth/'1688/default.json', scope=f'1688:default:{DEVICE}', allowed_domains={'1688.com'})
    session.save_verified({'cookies': [{'name': 'fixture', 'value': 'fixture', 'domain': '1688.com'}], 'origins': []})
    SessionHealth(tmp_path/'session-health').report(session.scope, session.load_record()[1]['version'], state, reason)
    config=tmp_path/'sites.json'
    config.write_text(json.dumps({'1688': {'domains': ['1688.com']}}))
    with pytest.raises(CollectionFailure) as failure:
        await ali1688.collect(REQUEST, 'fixture', auth_dir=auth, config_path=config,
                              diagnostics_dir=tmp_path/'diagnostics', executable='/fixture', gate=None)
    assert failure.value.reason == expected and expected in REASONS
