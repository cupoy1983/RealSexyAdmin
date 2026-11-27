import hashlib
import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from .schema import JobError, REASONS, TERMINAL
from .lifecycle import publish, prune, scope


class JobStore:
    def __init__(self, path: Path, *, capacity=1000, retention_seconds=604800, clock=time.time):
        self.path, self.capacity, self.retention, self.clock = Path(path), capacity, retention_seconds, clock
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.path.is_symlink():
            raise ValueError("执行队列不能为符号链接")
        with self.connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute('BEGIN IMMEDIATE')
            db.execute("""CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, idem TEXT NOT NULL, fingerprint TEXT NOT NULL,
                request TEXT NOT NULL, state TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL,
                reason TEXT, diagnostic TEXT, result TEXT, UNIQUE(owner, idem))""")
            db.execute("CREATE INDEX IF NOT EXISTS jobs_queue ON jobs(state,created,id)")
            db.execute('CREATE TABLE IF NOT EXISTS superseded_results (job_id TEXT PRIMARY KEY, replacement TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS current_products (scope TEXT PRIMARY KEY, job_id TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS artifact_cleanup (kind TEXT, identifier TEXT, PRIMARY KEY(kind,identifier))')
            db.execute('CREATE TABLE IF NOT EXISTS product_result_schema (id INTEGER PRIMARY KEY CHECK(id=1), version INTEGER NOT NULL)')
            version = db.execute('SELECT version FROM product_result_schema WHERE id=1').fetchone()
            if version and version['version'] > 2:
                raise ValueError('结果索引版本高于当前程序，不能降级启动')
            if not version or version['version'] < 2:
                db.execute('DELETE FROM current_products')
                rows = db.execute("SELECT rowid AS sequence,* FROM jobs WHERE state='succeeded' AND result IS NOT NULL ORDER BY rowid").fetchall()
                for row in rows:
                    if json.loads(row['request']).get('save_mode', 'overwrite') == 'overwrite':
                        result = publish(db, row, json.loads(row['result']))
                        db.execute('UPDATE jobs SET result=? WHERE id=?', (json.dumps(result, ensure_ascii=False) if result is not None else None, row['id']))
                db.execute('INSERT OR REPLACE INTO product_result_schema VALUES (1,2)')
        self.path.chmod(0o600)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def submit(self, owner, key, request):
        request = {**request, 'save_mode': request.get('save_mode', 'overwrite')}
        body = json.dumps(request, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        fingerprint = hashlib.sha256(body.encode()).hexdigest()
        now = self.clock()
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            prune(db, now-self.retention)
            old = db.execute("SELECT * FROM jobs WHERE owner=? AND idem=?", (owner, key)).fetchone()
            if old:
                old_request = json.loads(old['request'])
                old_request.setdefault('save_mode', 'overwrite')
                if old_request != request:
                    raise JobError(409, "idempotency_conflict")
                return self.metadata(old), True
            if db.execute("SELECT COUNT(*) FROM jobs WHERE state IN ('queued','running')").fetchone()[0] >= self.capacity:
                raise JobError(429, "queue_full")
            identifier = uuid4().hex
            db.execute("INSERT INTO jobs (id,owner,idem,fingerprint,request,state,created,updated,reason,diagnostic,result) VALUES (?,?,?,?,?,'queued',?,?,NULL,NULL,NULL)",
                       (identifier, owner, key, fingerprint, body, now, now))
            row = db.execute("SELECT * FROM jobs WHERE id=?", (identifier,)).fetchone()
            return self.metadata(row), False

    def metadata(self, row, *, db=None):
        if db is None:
            with self.connection() as connection:
                return self.metadata(row, db=connection)
        request = json.loads(row["request"])
        replacement = db.execute('SELECT replacement FROM superseded_results WHERE job_id=?', (row['id'],)).fetchone()
        return {"job_id": row["id"], **request, "state": row["state"], "created_at": row["created"],
                "updated_at": row["updated"], "error_code": row["reason"], "diagnostic_id": row["diagnostic"],
                "result_ready": row["state"] == "succeeded" and row['result'] is not None,
                "superseded_by": replacement['replacement'] if replacement else None}

    def current_product(self, owner, reference):
        key = json.dumps((owner, reference['platform'], reference['url']), ensure_ascii=False)
        with self.connection() as db:
            db.execute('BEGIN')
            row = db.execute('SELECT j.* FROM current_products c JOIN jobs j ON j.id=c.job_id WHERE c.scope=? AND j.owner=?', (key, owner)).fetchone()
            if row is None or row['state'] != 'succeeded' or row['result'] is None:
                raise JobError(404, 'product_not_found')
            if scope(row) != (owner, reference['platform'], reference['url']):
                raise JobError(404, 'product_not_found')
            return {'job': self.metadata(row, db=db), 'result': json.loads(row['result'])}

    def get(self, owner, identifier, *, result=False):
        with self.connection() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=? AND owner=?", (identifier, owner)).fetchone()
            if row is None:
                raise JobError(404, "job_not_found")
            if not result:
                return self.metadata(row)
            if row["state"] != "succeeded":
                raise JobError(409, "result_not_ready", {"state": row["state"], "error_code": row["reason"]})
            replacement = db.execute('SELECT replacement FROM superseded_results WHERE job_id=?', (identifier,)).fetchone()
            if replacement:
                raise JobError(410, 'result_superseded', {'superseded_by': replacement['replacement']})
            return json.loads(row["result"])

    def claim(self):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM jobs WHERE state='queued' ORDER BY created,id LIMIT 1").fetchone()
            if row is None:
                return None
            db.execute("UPDATE jobs SET state='running',updated=? WHERE id=?", (self.clock(), row["id"]))
            return {"job_id": row["id"], "owner": row['owner'], "request": json.loads(row["request"])}

    def finish(self, identifier, state, *, result=None, reason=None, diagnostic=None):
        if state not in TERMINAL or (reason is not None and reason not in REASONS):
            raise ValueError("任务结束状态无效")
        if state == "succeeded" and not isinstance(result, dict):
            raise ValueError("成功任务必须有结果")
        data = json.dumps(result, ensure_ascii=False, allow_nan=False) if state == "succeeded" else None
        if data and len(data.encode()) > 1024*1024:
            raise ValueError("商品结果超过上限")
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT rowid AS sequence,* FROM jobs WHERE id=?', (identifier,)).fetchone()
            if not row or row['state'] != 'running':
                raise ValueError('任务已结束或不在执行中')
            if state == 'succeeded':
                result = publish(db, row, result)
                data = json.dumps(result, ensure_ascii=False, allow_nan=False) if result is not None else None
                if data and len(data.encode()) > 1024*1024:
                    raise ValueError('合并商品结果超过上限')
            changed = db.execute("UPDATE jobs SET state=?,updated=?,reason=?,diagnostic=?,result=? WHERE id=? AND state='running'",
                                 (state, self.clock(), reason, diagnostic, data, identifier)).rowcount
            if changed != 1:
                raise ValueError("任务已结束或不在执行中")

    def maintain(self):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            prune(db, self.clock()-self.retention)

    def recover(self):
        with self.connection() as db:
            return db.execute("UPDATE jobs SET state='failed',reason='process_interrupted',updated=? WHERE state='running'",
                              (self.clock(),)).rowcount
