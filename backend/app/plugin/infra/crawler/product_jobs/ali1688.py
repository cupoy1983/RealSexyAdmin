import asyncio
import os
import ipaddress
import json
import re
import socket
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlsplit

from ..auth_session import AuthSessionStore
from ..access_signals import inspect_access, classify_access
from ..engine import BrowserJobOptions, run_browser_job
from ..login_service import DEVICE
from ..diagnostics import capture_diagnostic
from ..session_health import SessionHealth

EXTRACT = Path(__file__).with_name('extract_1688.js').read_text(encoding='utf-8')
DOMAINS = {'1688.com', 'alibaba.com', 'alicdn.com', 'taobao.com', 'tbcdn.cn', 'alipay.com', 'aliyuncs.com', 'mmstat.com'}


class CollectionFailure(Exception):
    def __init__(self, state, reason, diagnostic=None):
        self.state, self.reason, self.diagnostic = state, reason, diagnostic
        super().__init__(reason)


def money(value):
    if value is None:
        return None
    text = str(value).strip().replace(',', '')
    if not re.fullmatch(r'\d+(?:\.\d{1,8})?', text):
        return None
    try:
        amount = Decimal(text)
        return str(amount) if amount.is_finite() and amount >= 0 else None
    except InvalidOperation:
        return None


def normalize(raw, request, final_url):
    source_id = re.search(r'/offer/(\d+)\.html', request['url'])[1]
    target = urlsplit(final_url)
    if target.scheme!='https' or target.hostname not in {'detail.1688.com','m.1688.com'} or target.path != f'/offer/{source_id}.html':
        raise CollectionFailure('failed', 'identity_mismatch')
    offers = raw.get('offers', [])
    matching = [o for o in offers if str(o.get('offerId', '')) == source_id]
    identifiers={str(o.get('offerId')) for o in offers if str(o.get('offerId','')).isdigit()}
    if identifiers and not matching:
        raise CollectionFailure('failed', 'identity_mismatch')
    offer = matching[0] if matching else next((o for o in offers if not o.get('offerId')), {})
    title = str(offer.get('subject') or offer.get('title') or raw.get('title') or '').strip()
    if not title or title in {'阿里巴巴', '1688', '阿里巴巴1688', '1688首页'}:
        raise CollectionFailure('failed', 'unsupported_page')
    prices = []
    for p in raw.get('prices', [])[:50]:
        price = money(p.get('price') if p.get('price') is not None else p.get('discountPrice'))
        minimum = p.get('beginAmount') or p.get('beginNumber') or p.get('startQuantity')
        if price is not None:
            prices.append({'min_quantity': int(minimum) if str(minimum).isdigit() else None, 'price': price,
                           'source': p.get('source', 'page_data')})
    images = []
    for image in raw.get('images', []):
        u = urlsplit(str(image))
        if u.scheme == 'https' and u.hostname and (u.hostname == 'alicdn.com' or u.hostname.endswith('.alicdn.com')) and image not in images:
            images.append(image)
    variants=[]
    for item in raw.get('variants', [])[:1000]:
        variants.append({'source_sku_id': item.get('skuId'), 'spec_key': item.get('spec_key'),
                         'price': money(item.get('price') if item.get('price') is not None else item.get('discountPrice')),
                         'available_quantity_raw': item.get('canBookCount'),
                         'weight_raw': item.get('skuWeight')})
    result={'result_version':1,'platform':'1688','source_id':source_id,'url':request['url'],'collected_at':time.time(),
            'title':title[:1000],'images':images,'currency':'CNY','price_tiers':prices,
            'sku':{'properties':raw.get('properties',[])[:30],'variants':variants},
            'weight_raw':raw.get('weight') or offer.get('skuWeight'),'missing_fields':[],
            'warnings':[],'field_sources':{'title':'page_data' if offer else 'dom','images':'dom','price_tiers':'page_data','sku':'page_data','currency':'platform_default'}}
    result['missing_fields']=[k for k,v in [('images',images),('price_tiers',prices),('sku',variants),('weight',result['weight_raw'])] if not v]
    if not prices:result['warnings'].append('price_unavailable')
    if not variants:result['warnings'].append('sku_unavailable')
    for key in ['price_tiers']:
        result[key]=list({json.dumps(p,sort_keys=True):p for p in result[key]}.values())
    for key in ['properties','variants']:
        result['sku'][key]=list({json.dumps(p,sort_keys=True):p for p in result['sku'][key]}.values())
    return result


capture = capture_diagnostic


async def collect(request, job_id, *, auth_dir, config_path, diagnostics_dir, executable, gate):
    sites=json.loads(Path(config_path).read_text())
    site=sites['1688']
    store=AuthSessionStore(Path(auth_dir)/'1688'/(request['session_name']+'.json'),
                           scope=f"1688:{request['session_name']}:{DEVICE}",allowed_domains=set(site['domains']))
    record=store.load_record(discard_expired=False)
    if not record:
        raise CollectionFailure('needs_login','session_missing')
    state, metadata = record
    health = SessionHealth(os.environ.get('COLLECTOR_SESSION_HEALTH_DIR', str(Path(auth_dir).parent / 'session-health')))
    observation = health.read(store.scope, metadata['version'])
    if observation:
        failure_state = {'not_logged_in': 'needs_login', 'login_required': 'needs_login', 'verification_required': 'needs_verification',
                         'access_denied': 'needs_verification'}.get(observation['state'], 'failed')
        reason = {'not_logged_in': 'session_missing', 'login_required': 'login_required',
                  'verification_required': 'challenge', 'access_denied': 'access_denied',
                  'network_error': 'network_error'}.get(observation['state'], 'session_check_failed')
        if observation['reason'] == 'page_changing':
            reason = 'page_changing'
        raise CollectionFailure(failure_state, reason)

    def report_failure(error):
        observed = None
        if error.state == 'needs_login':
            observed = 'login_required'
        elif error.state == 'needs_verification':
            observed = 'access_denied' if error.reason == 'access_denied' else 'verification_required'
        elif error.reason in {'network_error', 'timeout', 'collection_interrupted'}:
            observed = 'network_error'
        elif error.reason == 'page_changing':
            observed = 'unknown'
        if observed:
            health.report(store.scope, metadata['version'], observed, error.reason)
    cache={}
    async def allowed(host):
        if not host or not any(host==d or host.endswith('.'+d) for d in DOMAINS):return False
        if host not in cache:
            try:
                addresses=await asyncio.to_thread(socket.getaddrinfo,host,443,type=socket.SOCK_STREAM)
                cache[host]=bool(addresses) and all(ipaddress.ip_address(a[4][0]).is_global for a in addresses)
            except OSError:cache[host]=False
        return cache[host]
    async def prepare(context):
        async def route(r):
            u=urlsplit(r.request.url)
            ok=u.scheme=='https' and u.port in {None,443} and not u.username and await allowed(u.hostname)
            if r.request.is_navigation_request():
                ok=ok and (u.hostname in set(site['domains']) or (u.hostname or '').endswith('.1688.com'))
            if ok:await r.continue_()
            else:await r.abort()
        await context.page.context.route('**/*',route)
    async def handle(context):
        page=context.page
        response=getattr(context,'response',None)
        status=getattr(response,'status',getattr(response,'status_code',None))
        http_signal = classify_access(status=status)
        if http_signal['state'] == 'access_denied':
            gate.cooldown(300,'access_denied')
            raise CollectionFailure('needs_verification','access_denied',await capture(page,diagnostics_dir,'access_denied'))
        if http_signal['state'] == 'network_error':
            raise CollectionFailure('failed','network_error',await capture(page,diagnostics_dir,'network_error'))
        try:
            await page.locator('h1,meta[property="og:title"],input[type="password"],.nc-container,#baxia-dialog-content').first.wait_for(state='attached',timeout=12000)
        except Exception:pass
        body=(await page.locator('body').inner_text())[:5000]
        signal = await inspect_access(page, site={**site, 'challenge_selectors': list(dict.fromkeys([*site.get('challenge_selectors', []), '.nc-container,#baxia-dialog-content,#nocaptcha']))}, status=status, include_password=True)
        if signal['reason'] == 'page_changing':
            raise CollectionFailure('failed', 'page_changing', await capture(page, diagnostics_dir, 'page_changing'))
        if signal['state'] in {'verification_required', 'access_denied'}:
            reason = 'challenge' if signal['state'] == 'verification_required' else 'access_denied'
            gate.cooldown(300, reason)
            raise CollectionFailure('needs_verification', reason, await capture(page, diagnostics_dir, reason))
        if signal['state'] == 'login_required':
            raise CollectionFailure('needs_login','login_required',await capture(page,diagnostics_dir,'login_required'))
        if any(text in body for text in ['该商品不存在','商品已下架','您访问的页面不存在']):
            raise CollectionFailure('failed','unsupported_page',await capture(page,diagnostics_dir,'unsupported_page'))
        try:return normalize(await page.evaluate(EXTRACT),request,page.url)
        except CollectionFailure as error:
            error.diagnostic=await capture(page,diagnostics_dir,error.reason)
            raise
    async def on_error(context,error):
        for _ in range(10):
            original=getattr(error,'original_error',error)
            if original is error:break
            error=original
        if isinstance(error,CollectionFailure):return error
        if classify_access(status=getattr(error,'status_code',None))['state'] == 'access_denied':
            gate.cooldown(300,'access_denied')
            return CollectionFailure('needs_verification','access_denied',await capture(getattr(context,'page',None),diagnostics_dir,'access_denied'))
        return CollectionFailure('failed','network_error',await capture(getattr(context,'page',None),diagnostics_dir,'network_error'))
    try:
        result=await run_browser_job(request['url'],handle,prepare=prepare,on_error=on_error,
            options=BrowserJobOptions(executable=executable,locale='zh-CN',viewport={'width':1280,'height':720},
                storage_state=state,launch_args=('--no-sandbox',),navigation_timeout_seconds=60,handler_timeout_seconds=90))
    except asyncio.CancelledError:
        report_failure(CollectionFailure('failed', 'collection_interrupted'))
        raise
    if isinstance(result,CollectionFailure):
        report_failure(result)
        raise result
    return result
