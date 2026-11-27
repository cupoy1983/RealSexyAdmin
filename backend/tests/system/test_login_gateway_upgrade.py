from pathlib import Path
import shutil
import subprocess

import pytest

from app.plugin.infra.crawler.login_deploy.update_gateway import upgrade, upgrade_page


ROOT = Path(__file__).parents[2] / 'app/plugin/infra/crawler/login_deploy'


def test_upgrade_preserves_extra_routes_and_is_idempotent():
    current = (ROOT / 'nginx.conf').read_text(encoding='utf-8')
    original = current.replace('proxy_pass http://127.0.0.1:6081/vnc;', 'proxy_pass http://127.0.0.1:6080/websockify;')
    original += '\n# existing product API includes and Chinese environment retained\n'
    result = upgrade(original)
    assert 'proxy_pass http://127.0.0.1:6081/vnc;' in result
    assert result.endswith('# existing product API includes and Chinese environment retained\n')
    assert upgrade(result) == result


def test_upgrade_rejects_unrecognized_or_bypass_route():
    current = (ROOT / 'nginx.conf').read_text(encoding='utf-8')
    for unsafe in (current.replace('/viewer/websockify', '/unknown'),
                   current.replace('proxy_set_header Upgrade "";', 'proxy_set_header Upgrade $http_upgrade;')):
        with pytest.raises(ValueError):
            upgrade(unsafe)


def test_existing_chinese_clipboard_route_is_guarded_and_preserved():
    current = (ROOT / 'nginx.conf').read_text(encoding='utf-8')
    original = current.replace('location /control/ {',
        'location /text/ { proxy_pass http://127.0.0.1:6082/; proxy_set_header Host $http_host; }\n        location /control/ {')
    result = upgrade(original)
    assert 'location /text/ { proxy_pass http://127.0.0.1:6081/;' in result
    assert upgrade(result) == result


def test_page_upgrade_preserves_chinese_and_does_not_duplicate():
    current = (ROOT / 'index.html').read_text(encoding='utf-8')
    assert upgrade_page(current) == current
    assert 'collectorSyncViewer(state)' in current
    assert "url.searchParams.set('path','viewer/websockify?window='+current)" in current
    assert 'collectorWindowHeaders()' in current
    assert '会话名' in current
    with pytest.raises(ValueError):
        upgrade_page('<html>unknown layout</html>')


@pytest.mark.skipif(shutil.which('node') is None, reason='Node is required for login-page JavaScript execution')
def test_late_refresh_during_start_does_not_revoke_the_new_page():
    script = r"""
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync(process.argv[1],'utf8');
const refresh=html.split('\n').find(line=>line.startsWith('async function refresh(){'));
let calls=0,ended=0,resolve;
const context={collectorWindowFinished:false,collectorStarting:true,collectorWindowId:'first',
 collectorWindowHeaders:()=>({}),collectorEndWindow:()=>{ended++},message:{},render:()=>{},
 fetch:()=>{calls++;return new Promise(done=>{resolve=done})}};
vm.createContext(context);vm.runInContext(refresh,context);
(async()=>{
 await context.refresh();assert.equal(calls,0);
 context.collectorStarting=false;const pending=context.refresh();
 context.collectorStarting=true;resolve({status:401});await pending;assert.equal(ended,0);
 context.collectorStarting=false;const old=context.refresh();
 context.collectorWindowId='second';resolve({status:401});await old;assert.equal(ended,0);
})().catch(e=>{console.error(e);process.exitCode=1});
"""
    subprocess.run([shutil.which('node'), '-e', script, str(ROOT / 'index.html')],
                   check=True, capture_output=True, text=True, timeout=10)
