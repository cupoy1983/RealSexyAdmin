import time
import pytest

from app.plugin.infra.crawler.login_service import LoginManager
from app.plugin.infra.crawler.state import write_private_json


def test_chinese_session_roundtrip_across_login_and_task_schema(tmp_path):
    from app.plugin.infra.crawler.product_jobs.schema import JobRequest
    name = '采购账号-1688'
    login = LoginManager({'1688': {'name': '1688', 'domains': ['shop.invalid']}}, tmp_path)
    login.store('1688', name).save_verified({'cookies': [{'name': 'fixture', 'value': 'fixture', 'domain': 'shop.invalid', 'path': '/'}], 'origins': []})
    assert name in login.status()['sessions']['1688']
    assert login.store('1688', name).load() is not None
    assert JobRequest(platform='1688', url='https://detail.1688.com/offer/123.html', session_name=name).session_name == name


@pytest.mark.parametrize('name', ['../采购', '采购/账号', '采购\\账号', '采购.账号', '采购 账号', '采购\n', '采' * 49])
def test_session_names_reject_unsafe_paths_consistently(tmp_path, name):
    from app.plugin.infra.crawler.product_jobs.schema import JobRequest
    login = LoginManager({'1688': {'name': '1688', 'domains': []}}, tmp_path)
    with pytest.raises(ValueError):
        login.store('1688', name)
    with pytest.raises(ValueError):
        JobRequest(platform='1688', url='https://detail.1688.com/offer/123.html', session_name=name)


def test_status_lists_only_loadable_sessions_without_credentials(tmp_path):
    login = LoginManager({'shop': {'name': 'Fixture', 'domains': ['shop.invalid']}}, tmp_path)
    state = {'cookies': [{'name': 'fixture', 'value': 'secret-fixture', 'domain': 'shop.invalid', 'path': '/'}], 'origins': []}
    login.store('shop', 'backup').save_verified(state)
    login.store('shop', 'main').save_verified(state)
    write_private_json(tmp_path / 'shop/wrong.json', {'scope': 'other', 'expires_at': time.time()+100, 'state': state})
    (tmp_path / 'shop/broken.json').write_text('broken')
    result = login.status()
    assert result['sessions'] == {'shop': ['backup', 'main']}
    assert 'secret-fixture' not in str(result)


def test_empty_saved_sessions_do_not_invent_default_record(tmp_path):
    login = LoginManager({'shop': {'name': 'Fixture', 'domains': ['shop.invalid']}}, tmp_path)
    assert login.status()['sessions'] == {'shop': []}


def test_readonly_expired_session_returns_missing_without_unlink(tmp_path):
    from app.plugin.infra.crawler.auth_session import AuthSessionStore
    now=[1000]
    path=tmp_path/'fixture.json'
    store=AuthSessionStore(path,scope='fixture',allowed_domains={'shop.invalid'},ttl_seconds=1,clock=lambda:now[0])
    store.save_verified({'cookies':[{'name':'fixture','value':'fixture','domain':'shop.invalid','path':'/'}],'origins':[]})
    now[0]=1002
    assert store.load(discard_expired=False) is None and path.exists()
    assert store.load() is None and not path.exists()
