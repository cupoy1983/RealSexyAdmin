import json
import subprocess

import pytest
from pathlib import Path

SCRIPT = Path(__file__).parents[2] / 'app/plugin/infra/crawler/product_jobs/extract_1688.js'


def extract(data, scripts=None, window=None):
    harness = '''const fs=require('fs');global.location={pathname:'/offer/123456789.html'};const input=JSON.parse(fs.readFileSync(0,'utf8'));global.window=input.window||{__INIT_DATA:input.data};global.document={title:'Fixture supplier',scripts:input.scripts.map(textContent=>({textContent})),querySelector:s=>s==='h1'?{textContent:'Fixture supplier'}:null,querySelectorAll:()=>[]};process.stdout.write(JSON.stringify(eval('('+fs.readFileSync(process.argv[1],'utf8')+')')()));'''
    result=subprocess.run(['node','-e',harness,str(SCRIPT)],input=json.dumps({'data':data,'scripts':scripts or [],'window':window}),text=True,capture_output=True,check=True)
    return json.loads(result.stdout)


def test_supplier_heading_is_not_a_product_title():
    assert extract({})['title']==''


def module_page(price_type='rangePrice', offer_id='123456789'):
    return {'context': {'result': {'data': {
        'gallery': {'fields': {'offerId': offer_id, 'mainImage': ['https://cbu01.alicdn.com/product.jpg']}},
        'productTitle': {'fields': {'title': 'Fixture product', 'shopInfo': {'companyName': 'Fixture supplier'}}},
        'mainPrice': {'fields': {'priceModel': {'priceDisplayType': price_type, 'currentPrices': [
            {'beginAmount': '1', 'price': '15.00'}, {'beginAmount': '100', 'price': '12.00'}]}}},
        'productPackInfo': {'fields': {'unitWeight': 0.18}},
    }}}}


def test_module_template_reads_product_title_and_tiers():
    raw = extract({}, window=module_page())
    assert raw['offers'][0]['title'] == 'Fixture product'
    assert [p['price'] for p in raw['prices']] == ['15.00', '12.00']
    assert raw['images'] == ['https://cbu01.alicdn.com/product.jpg']


def test_module_sku_price_range_is_not_quantity_tiers():
    raw = extract({}, window=module_page('skuPrice'))
    assert raw['prices'] == []
    assert raw['price_range'] == {'min': '12.00', 'max': '15.00'}


def test_module_foreign_gallery_cannot_authorize_product_fields():
    raw = extract({}, window=module_page(offer_id='999'))
    assert not raw['offers'] and not raw['prices'] and not raw['images']


def test_module_root_sku_info_map_keeps_real_combinations_and_weights():
    window = module_page('skuPrice')
    modules = window['context']['result']['data']
    modules['Root'] = {'fields': {'dataJson': {'skuModel': {
        'skuProps': [{'prop': 'Color', 'value': [{'name': 'Blue'}, {'name': 'Red'}]}],
        'skuInfoMap': {'Blue&gt;M': {'skuId': 'sku-1', 'price': '15.00', 'canBookCount': 8}},
    }}}}
    modules['productPackInfo']['fields']['pieceWeightScale'] = {
        'pieceWeightScaleInfo': [{'skuId': 'sku-1', 'weight': '180'}],
        'columnList': [{'name': 'weight', 'label': 'Weight(g)'}],
    }
    raw = extract({}, window=window)
    assert len(raw['variants']) == 1
    assert raw['variants'][0]['spec_key'] == 'Blue>M'
    assert raw['variants'][0]['skuWeight'] == '180'
    assert raw['variants'][0]['weight_unit'] == 'g'
    assert raw['properties'][0]['values'][1]['name'] == 'Red'
    from app.plugin.infra.crawler.product_jobs.ali1688 import normalize
    request = {'url': 'https://detail.1688.com/offer/123456789.html'}
    result = normalize(raw, request, request['url'])
    assert result['field_sources']['images'] == 'page_data'
    assert result['price_range'] == {'min': '12.00', 'max': '15.00'}
    assert 'price_unavailable' not in result['warnings']
    assert result['weight_unit'] == 'kg'


def test_module_nested_offer_mismatch_does_not_use_page_models():
    window = module_page()
    window['context']['result']['data']['Root'] = {'fields': {'dataJson': {'offerId': '999'}}}
    raw = extract({}, window=window)
    assert not raw['offers'] and not raw['prices']


def test_offer_title_and_models():
    raw=extract({'offerInfoModel':{'offerId':'123456789','offerTitle':'Fixture product'},'priceModel':{'currentPrices':[{'price':'12.50'}]},'skuModel':{'skuMap':{'S':{'skuId':'fixture-sku','price':'12.50'}}}})
    assert raw['offers'][0].get('offerTitle')=='Fixture product'
    assert raw['prices'][0]['price']=='12.50'
    assert raw['variants'][0]['skuId']=='fixture-sku'


def test_unowned_prices_are_not_attached_to_matching_offer():
    raw=extract({'detail':{'offerInfoModel':{'offerId':'123456789','subject':'Fixture product'}},'other':{'currentPrices':[{'price':'999'}]}})
    assert raw['prices']==[]


def test_inline_current_offer_after_foreign_offer_uses_own_models():
    foreign={'offerInfoModel':{'offerId':'999','subject':'Other'},'priceModel':{'currentPrices':[{'price':'999'}]}}
    current={'offerInfoModel':{'offerId':'123456789','offerTitle':'Fixture product'},'priceModel':{'currentPrices':[{'price':'12.50'}]},'skuModel':{'skuMap':{'S':{'skuId':'fixture-sku'}}}}
    raw=extract({},['const a='+json.dumps(foreign)+'; const b='+json.dumps(current)+';'])
    assert raw['offers'][0]['offerTitle']=='Fixture product'
    assert [p['price'] for p in raw['prices']]==['12.50']
    assert raw['variants'][0]['skuId']=='fixture-sku'


def test_foreign_offer_nested_in_current_envelope_is_excluded():
    raw=extract({'offerInfoModel':{'offerId':'123456789','subject':'Fixture product'},'priceModel':{'currentPrices':[{'price':'12.50'}]},'other':{'offerInfoModel':{'offerId':'999'},'currentPrices':[{'price':'999'}]}})
    assert [p['price'] for p in raw['prices']]==['12.50']


def test_normalization_uses_offer_title_and_only_reports_successful_sources():
    from app.plugin.infra.crawler.product_jobs.ali1688 import normalize
    request={'url':'https://detail.1688.com/offer/123456789.html'}
    result=normalize(extract({'offerInfoModel':{'offerId':'123456789','offerTitle':'Fixture product'}}),request,request['url'])
    assert result['title']=='Fixture product'
    assert result['field_sources']['title']=='page_data'
    assert result['field_sources']['price_tiers'] is None
    assert result['field_sources']['sku'] is None


def test_supplier_only_page_is_not_successful_product():
    from app.plugin.infra.crawler.product_jobs.ali1688 import CollectionFailure, normalize
    request={'url':'https://detail.1688.com/offer/123456789.html'}
    with pytest.raises(CollectionFailure) as failure:
        normalize(extract({}),request,request['url'])
    assert failure.value.reason=='unsupported_page'


async def test_delayed_models_are_read_before_returning(monkeypatch):
    from app.plugin.infra.crawler.product_jobs import ali1688
    from unittest.mock import AsyncMock
    page=AsyncMock()
    complete={'offers':[{'offerId':'123456789'}],'prices':[{'price':'1'}],'variants':[{'skuId':'fixture'}]}
    page.evaluate.side_effect=[{'offers':[{'offerId':'123456789'}]},complete]
    sleep=AsyncMock()
    monkeypatch.setattr(ali1688.asyncio,'sleep',sleep)
    assert await ali1688.read_product_data(page)==complete
    assert page.evaluate.await_count==2
    sleep.assert_awaited_once_with(0.5)


async def test_missing_models_have_bounded_wait(monkeypatch):
    from app.plugin.infra.crawler.product_jobs import ali1688
    from unittest.mock import AsyncMock
    page=AsyncMock()
    page.evaluate.return_value={'offers':[],'prices':[],'variants':[]}
    monkeypatch.setattr(ali1688.asyncio,'sleep',AsyncMock())
    assert (await ali1688.read_product_data(page))['prices']==[]
    assert page.evaluate.await_count==9
