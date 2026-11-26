import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.plugin.infra.crawler import state
from app.plugin.infra.crawler.access_signals import classify_access
from app.plugin.infra.crawler.diagnostics import capture_diagnostic, MAX_HTML_BYTES


def test_atomic_failure_preserves_old_file_and_removes_temporary(tmp_path, monkeypatch):
    path = tmp_path / 'state.json'
    state.write_private_json(path, {'version': 1})
    def fail(*args):
        raise OSError('fixture replace failure')
    monkeypatch.setattr(state.os, 'replace', fail)
    with pytest.raises(OSError):
        state.write_private_json(path, {'version': 2})
    assert state.read_private_json(path) == {'version': 1}
    assert sorted(p.name for p in tmp_path.iterdir()) == ['state.json']


def test_concurrent_atomic_writers_produce_complete_json(tmp_path):
    path = tmp_path / 'state.json'
    with ThreadPoolExecutor(max_workers=6) as executor:
        list(executor.map(lambda i: state.write_private_json(path, {'value': str(i) * 1000}), range(12)))
    assert len(state.read_private_json(path)['value']) in {1000, 2000}
    assert list(tmp_path.glob('*.tmp')) == []


async def test_diagnostic_without_page_has_reason_and_manifest(tmp_path):
    reference = await capture_diagnostic(None, tmp_path, 'access_denied')
    directory = tmp_path / reference
    reason = json.loads((directory / 'reason.json').read_text())
    assert reason['capture_errors'] == ['browser_unavailable']
    manifest = json.loads((directory / 'manifest.json').read_text())
    assert [f['path'] for f in manifest['files']] == ['reason.json']


async def test_shared_page_evidence_limits_html_and_masks_image(tmp_path):
    page = SimpleNamespace(evaluate=AsyncMock(return_value={'html': '中' * MAX_HTML_BYTES, 'url': 'https://fixture.invalid/?token=private'}),
                           locator=lambda selector: selector, screenshot=AsyncMock(return_value=b'fixture-png'))
    reference = await capture_diagnostic(page, tmp_path, 'challenge')
    directory = tmp_path / reference
    assert (directory / 'page.html').stat().st_size == MAX_HTML_BYTES
    reason = json.loads((directory / 'reason.json').read_text())
    assert reason['html_truncated'] and reason['url'] == 'https://fixture.invalid/'
    assert 'mask' in page.screenshot.call_args.kwargs
    assert not list(directory.glob('*.tmp'))


@pytest.mark.parametrize('status', [401, 403, 429])
def test_http_restriction_is_neither_login_failure_nor_captcha(status):
    result = classify_access(status=status, login=True)
    assert result['state'] == 'access_denied'
    assert result['evidence'] == [f'http_{status}']


def test_explicit_challenge_precedes_login_and_unknown_is_not_valid():
    assert classify_access(url='https://detail.1688.com/_____tmd_____/punish', login=True)['state'] == 'verification_required'
    assert classify_access(status=403, challenge=True)['state'] == 'verification_required'
    assert classify_access(login=True)['state'] == 'login_required'
    assert classify_access()['state'] == 'unknown'
