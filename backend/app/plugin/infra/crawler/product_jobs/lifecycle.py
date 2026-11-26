"""Product result retention. Never touches selection batches or authorization files."""
import json
import re
from pathlib import Path

FIELDS = ('title', 'images', 'price_tiers', 'weight_raw', 'weight_unit', 'price_range', 'currency')


def present(value):
    if isinstance(value, dict):
        return any(present(v) for v in value.values())
    return value is not None and value != '' and value != []


def merge_result(old, new, observed):
    result = dict(new)
    times = dict(old.get('field_collected_at', {}))
    sources = dict(new.get('field_sources', {}))
    for field in FIELDS:
        if present(new.get(field)):
            times[field] = observed
        elif present(old.get(field)):
            result[field] = old[field]
            times[field] = times.get(field, old.get('collected_at'))
            if field in old.get('field_sources', {}):
                sources[field] = old['field_sources'][field]
    result['field_collected_at'] = times
    result['field_sources'] = sources
    sku = dict(new.get('sku') or {})
    for field in ('properties', 'variants'):
        key = 'sku.' + field
        if present(sku.get(field)):
            times[key] = observed
        elif present((old.get('sku') or {}).get(field)):
            sku[field] = old['sku'][field]
            times[key] = times.get(key, old.get('collected_at'))
            if 'sku' in old.get('field_sources', {}):
                sources.setdefault('sku', old['field_sources']['sku'])
    if sku:
        result['sku'] = sku
    values = {'images': result.get('images'), 'price_tiers': result.get('price_tiers'),
              'sku': (result.get('sku') or {}).get('variants'), 'weight': result.get('weight_raw')}
    result['missing_fields'] = [f for f in new.get('missing_fields', []) if not present(values.get(f))]
    warnings = list(new.get('warnings', []))
    if present(values['sku']) and 'sku_unavailable' in warnings:
        warnings.remove('sku_unavailable')
    if (present(values['price_tiers']) or present(result.get('price_range'))
            or any(v.get('price') is not None for v in (result.get('sku') or {}).get('variants', []))):
        warnings = [w for w in warnings if w != 'price_unavailable']
    result['warnings'] = warnings
    return result


def scope(row):
    request = json.loads(row['request'])
    return (row['owner'], request['platform'], request['url'], request['session_name'])


def enqueue(db, row):
    db.execute('INSERT OR IGNORE INTO artifact_cleanup VALUES (?,?)', ('result', row['id']))
    if row['diagnostic']:
        db.execute('INSERT OR IGNORE INTO artifact_cleanup VALUES (?,?)', ('diagnostic', row['diagnostic']))


def publish(db, row, result):
    if json.loads(row['request']).get('save_mode', 'overwrite') == 'history':
        return merge_result({}, result, result.get('collected_at', row['updated']))
    key = json.dumps(scope(row), ensure_ascii=False)
    current = db.execute('SELECT job_id FROM current_products WHERE scope=?', (key,)).fetchone()
    previous = db.execute('SELECT rowid AS sequence,* FROM jobs WHERE id=?', (current['job_id'],)).fetchone() if current else None
    if previous and previous['sequence'] > row['sequence']:
        db.execute('INSERT OR REPLACE INTO superseded_results VALUES (?,?)', (row['id'], previous['id']))
        enqueue(db, row)
        return None
    result = merge_result(json.loads(previous['result']) if previous and previous['result'] else {}, result,
                          result.get('collected_at', row['updated']))
    # Scope checks are in Python to support pre-existing JSON request records.
    older = db.execute('SELECT rowid AS sequence,* FROM jobs WHERE rowid < ? AND state NOT IN (\'queued\',\'running\')', (row['sequence'],)).fetchall()
    for item in older:
        if scope(item) == scope(row) and json.loads(item['request']).get('save_mode', 'overwrite') == 'overwrite':
            db.execute('UPDATE jobs SET result=NULL WHERE id=?', (item['id'],))
            db.execute('INSERT OR REPLACE INTO superseded_results VALUES (?,?)', (item['id'], row['id']))
            enqueue(db, item)
    db.execute('INSERT OR REPLACE INTO current_products VALUES (?,?)', (key, row['id']))
    return result


def prune(db, before):
    rows = db.execute("SELECT * FROM jobs WHERE state NOT IN ('queued','running') AND updated < ? AND id NOT IN (SELECT job_id FROM current_products)", (before,)).fetchall()
    for row in rows:
        if json.loads(row['request']).get('save_mode', 'overwrite') == 'history' and row['state'] == 'succeeded':
            continue
        enqueue(db, row)
        db.execute('DELETE FROM superseded_results WHERE job_id=?', (row['id'],))
        db.execute('DELETE FROM jobs WHERE id=?', (row['id'],))


def clean_files(store, results_dir, diagnostics_dir):
    with store.connection() as db:
        pending = db.execute('SELECT kind,identifier FROM artifact_cleanup').fetchall()
    for row in pending:
        kind, identifier = row['kind'], row['identifier']
        if not re.fullmatch('[a-f0-9]{32}', identifier):
            continue
        root = results_dir if kind == 'result' else diagnostics_dir
        if root is None:
            continue
        root = Path(root)
        try:
            if root.is_symlink() or root.resolve() != root.absolute():
                continue
            target = root / (identifier + '.json' if kind == 'result' else identifier)
            if target.is_symlink():
                continue
            if kind == 'result':
                target.unlink(missing_ok=True)
            elif target.exists():
                allowed = {'metadata.json', 'reason.md', 'reason.json', 'page.html', 'page.png', 'manifest.json'}
                children = list(target.iterdir())
                if any(p.name not in allowed or p.is_symlink() or not p.is_file() for p in children):
                    continue
                for path in children:
                    path.unlink()
                target.rmdir()
        except OSError:
            continue
        with store.connection() as db:
            db.execute('DELETE FROM artifact_cleanup WHERE kind=? AND identifier=?', (kind, identifier))
