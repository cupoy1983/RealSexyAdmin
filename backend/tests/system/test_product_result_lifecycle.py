import json
from pathlib import Path

import pytest

from app.plugin.infra.crawler.product_jobs.store import JobStore
from app.plugin.infra.crawler.product_jobs.schema import JobError, JobRequest
from app.plugin.infra.crawler.product_jobs.lifecycle import clean_files

REQUEST = {'platform': '1688', 'url': 'https://detail.1688.com/offer/123.html', 'session_name': 'main'}


def task(store, key, **changes):
    job, _ = store.submit('product', key, {**REQUEST, **changes})
    assert store.claim()['job_id'] == job['job_id']
    return job['job_id']


def test_overwrite_replaces_payload_keeps_idempotency_and_cleans_files(tmp_path):
    store = JobStore(tmp_path / 'jobs.db')
    old = task(store, 'old-task')
    store.finish(old, 'succeeded', result={'title': 'old', 'images': ['https://fixture.invalid/image']})
    failed = task(store, 'failed-task')
    diagnostic = 'a' * 32
    store.finish(failed, 'needs_verification', reason='challenge', diagnostic=diagnostic)
    results = tmp_path / 'results'; results.mkdir()
    (results / (old + '.json')).write_text('old')
    diagnostics = tmp_path / 'diagnostics'; evidence = diagnostics / diagnostic; evidence.mkdir(parents=True)
    (evidence / 'reason.json').write_text('{}')
    new = task(store, 'new-task')
    store.finish(new, 'succeeded', result={'title': 'new', 'images': [], 'missing_fields': ['images'], 'collected_at': 123})
    result = store.get('product', new, result=True)
    assert result['title'] == 'new' and result['images'] == ['https://fixture.invalid/image']
    assert result['missing_fields'] == [] and result['field_collected_at']['title'] == 123
    with pytest.raises(JobError) as error:
        store.get('product', old, result=True)
    assert error.value.status == 410 and error.value.data['superseded_by'] == new
    assert store.submit('product', 'old-task', REQUEST)[0]['job_id'] == old
    clean_files(store, results, diagnostics)
    assert not (results / (old + '.json')).exists() and not evidence.exists()


def test_failure_and_late_success_cannot_replace_newer_result(tmp_path):
    store = JobStore(tmp_path / 'jobs.db')
    older = task(store, 'older-task')
    newer = task(store, 'newer-task')
    store.finish(newer, 'succeeded', result={'title': 'new'})
    store.finish(older, 'succeeded', result={'title': 'late-old'})
    assert store.get('product', newer, result=True)['title'] == 'new'
    assert store.get('product', older)['superseded_by'] == newer
    failed = task(store, 'failure-task')
    store.finish(failed, 'failed', reason='timeout')
    assert store.get('product', newer, result=True)['title'] == 'new'


def test_history_scope_and_expiry_do_not_delete_current_data(tmp_path):
    now = [1000]
    store = JobStore(tmp_path / 'jobs.db', clock=lambda: now[0])
    history = task(store, 'history-task', save_mode='history')
    store.finish(history, 'succeeded', result={'title': 'history'})
    old = task(store, 'old-task')
    store.finish(old, 'succeeded', result={'title': 'old'})
    other = task(store, 'other-account', session_name='other')
    store.finish(other, 'succeeded', result={'title': 'other'})
    new = task(store, 'new-task')
    store.finish(new, 'succeeded', result={'title': 'new'})
    now[0] += 604801
    store.maintain()
    assert store.get('product', history, result=True)['title'] == 'history'
    assert store.get('product', other, result=True)['title'] == 'other'
    assert store.get('product', new, result=True)['title'] == 'new'
    with pytest.raises(JobError):
        store.get('product', old)


def test_legacy_request_replay_and_schema_validation(tmp_path):
    store = JobStore(tmp_path / 'jobs.db')
    job, _ = store.submit('product', 'legacy-key', REQUEST)
    with store.connection() as db:
        db.execute('UPDATE jobs SET request=? WHERE id=?', (json.dumps(REQUEST), job['job_id']))
    assert store.submit('product', 'legacy-key', REQUEST)[1]
    assert JobRequest.model_validate(REQUEST).save_mode == 'overwrite'
    with pytest.raises(ValueError):
        JobRequest.model_validate({**REQUEST, 'save_mode': 'delete_all'})


def test_cleanup_refuses_unknown_diagnostic_contents(tmp_path):
    store = JobStore(tmp_path / 'jobs.db')
    root = tmp_path / 'diagnostics'; folder = root / ('a' * 32); folder.mkdir(parents=True)
    (folder / 'unknown.txt').write_text('keep')
    with store.connection() as db:
        db.execute('INSERT INTO artifact_cleanup VALUES (?,?)', ('diagnostic', 'a' * 32))
    clean_files(store, tmp_path / 'results', root)
    assert (folder / 'unknown.txt').read_text() == 'keep'
    with store.connection() as db:
        assert db.execute('SELECT count(*) FROM artifact_cleanup').fetchone()[0] == 1


def test_partial_sku_preserves_old_variants_and_their_time():
    from app.plugin.infra.crawler.product_jobs.lifecycle import merge_result
    old = {'sku': {'properties': ['old'], 'variants': [{'price': '10'}]}, 'collected_at': 10}
    new = {'sku': {'properties': ['new'], 'variants': []}, 'missing_fields': ['sku'], 'collected_at': 20}
    result = merge_result(old, new, 20)
    assert result['sku'] == {'properties': ['new'], 'variants': [{'price': '10'}]}
    assert result['field_collected_at']['sku.properties'] == 20
    assert result['field_collected_at']['sku.variants'] == 10


async def test_export_failure_does_not_turn_success_into_failure(tmp_path, monkeypatch):
    from app.plugin.infra.crawler.product_jobs import service
    store = JobStore(tmp_path / 'jobs.db')
    job, _ = store.submit('product', 'export-key', REQUEST)
    async def adapter(*args):
        return {'title': 'fixture'}
    def broken(*args):
        raise ValueError('Unsafe output path')
    monkeypatch.setattr(service, 'write_private_json', broken)
    assert await service.JobRunner(store, adapter, results_dir=tmp_path / 'results').run_once()
    assert store.get('product', job['job_id'], result=True)['title'] == 'fixture'


def test_other_owner_and_failed_diagnostic_retention(tmp_path):
    now = [1000]
    store = JobStore(tmp_path / 'jobs.db', clock=lambda: now[0])
    old = task(store, 'old-task')
    store.finish(old, 'succeeded', result={'title': 'product'})
    other, _ = store.submit('other', 'other-key', REQUEST)
    store.claim(); store.finish(other['job_id'], 'succeeded', result={'title': 'other'})
    failed = task(store, 'failed-task')
    diagnostic = 'b' * 32
    store.finish(failed, 'failed', reason='timeout', diagnostic=diagnostic)
    root = tmp_path / 'diagnostics'; folder = root / diagnostic; folder.mkdir(parents=True)
    (folder / 'reason.json').write_text('{}')
    now[0] += 604801
    store.maintain(); clean_files(store, tmp_path / 'results', root)
    assert not folder.exists()
    assert store.get('product', old, result=True)['title'] == 'product'
    assert store.get('other', other['job_id'], result=True)['title'] == 'other'
