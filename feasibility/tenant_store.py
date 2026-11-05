"""Transactional, owner-partitioned mutable state for the local service.

No automatic input backups are made: explicit deletion is irreversible unless
the owner has separately exported data. Only explicit legacy migration retains
a read-only copy of the imported bytes. A failed cleanup remains tombstoned and
can be retried by the administrator's recovery janitor.
"""
from contextlib import ExitStack, contextmanager
from copy import deepcopy
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import time
import uuid

from feasibility.profile_store import ProfileStore, new_id

if os.name == 'nt':
    from feasibility import windows_storage

LOG = logging.getLogger(__name__)
MAX_JSON_BYTES = 2 * 1024 * 1024


class StoreError(ValueError):
    status = 400
    message = 'Invalid request'

    def __init__(self):
        super().__init__(self.message)


class NotFound(StoreError):
    status = 404
    message = 'Not found'


class Conflict(StoreError):
    status = 409
    message = 'Revision conflict; read the current snapshot and retry'


class Unauthorized(StoreError):
    status = 401
    message = 'Session unavailable'


def _json(value):
    if not isinstance(value, dict):
        raise StoreError()
    try:
        raw = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)
    except (ValueError, TypeError, RecursionError) as exc:
        raise StoreError() from exc
    if len(raw.encode()) > MAX_JSON_BYTES:
        raise StoreError()
    return raw


def _id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{32}', value):
        raise NotFound()
    return value


SCHEMA = '''
CREATE TABLE IF NOT EXISTS tenants (
 id TEXT PRIMARY KEY, created REAL NOT NULL, expires REAL,
 state TEXT NOT NULL DEFAULT 'active' CHECK(state IN ('active','deleting')));
CREATE TABLE IF NOT EXISTS sessions (
 token_hash TEXT PRIMARY KEY, tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
 expires REAL);
CREATE TABLE IF NOT EXISTS pairing_codes (
 code_hash TEXT PRIMARY KEY, tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
 expires REAL NOT NULL);
CREATE TABLE IF NOT EXISTS profiles (
 id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
 current_revision TEXT NOT NULL, name TEXT NOT NULL DEFAULT '', UNIQUE(id,tenant_id),
 FOREIGN KEY(id,tenant_id,current_revision) REFERENCES revisions(profile_id,tenant_id,sha) DEFERRABLE INITIALLY DEFERRED);
CREATE TABLE IF NOT EXISTS revisions (
 profile_id TEXT NOT NULL, tenant_id TEXT NOT NULL, sha TEXT NOT NULL, manifest_ref TEXT NOT NULL,
 PRIMARY KEY(profile_id,tenant_id,sha),
 FOREIGN KEY(profile_id,tenant_id) REFERENCES profiles(id,tenant_id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS scenarios (
 tenant_id TEXT NOT NULL, profile_id TEXT NOT NULL, revision TEXT NOT NULL, payload TEXT NOT NULL,
 PRIMARY KEY(tenant_id,profile_id,revision),
 FOREIGN KEY(profile_id,tenant_id,revision) REFERENCES revisions(profile_id,tenant_id,sha) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS defaults (
 tenant_id TEXT NOT NULL, profile_id TEXT NOT NULL, revision TEXT NOT NULL, payload TEXT NOT NULL,
 PRIMARY KEY(tenant_id,profile_id,revision),
 FOREIGN KEY(profile_id,tenant_id,revision) REFERENCES revisions(profile_id,tenant_id,sha) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS uploads (
 id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
 status TEXT NOT NULL, artifact_ref TEXT NOT NULL, sha256 TEXT NOT NULL, metadata TEXT NOT NULL,
 dedupe_key TEXT NOT NULL, UNIQUE(tenant_id,dedupe_key));
CREATE TABLE IF NOT EXISTS jobs (
 id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
 profile_id TEXT NOT NULL, profile_revision TEXT NOT NULL, scenario_hash TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('queued','running','complete','failed','cancelled')),
 result_ref TEXT, error TEXT, created REAL NOT NULL,
 FOREIGN KEY(profile_id,tenant_id,profile_revision) REFERENCES revisions(profile_id,tenant_id,sha) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS migrations (
 tenant_id TEXT NOT NULL, profile_id TEXT NOT NULL, digest TEXT NOT NULL, revision TEXT NOT NULL, backup_ref TEXT NOT NULL,
 PRIMARY KEY(tenant_id,profile_id),
 FOREIGN KEY(profile_id,tenant_id) REFERENCES profiles(id,tenant_id) ON DELETE CASCADE);
'''


def legacy_default_descriptions():
    """Optional explicit compatibility metadata; no private description in source.

    This is used only by the legacy local defaults reader. Tenant defaults carry
    their own records and never consult this external compatibility artifact.
    """
    store = ProfileStore()
    with store._directory(()) as fd:
        name = 'legacy-default-descriptions.json'
        if name not in os.listdir(fd):
            return {}
        raw = store._read(fd, name, 4096)
    value = json.loads(raw)
    if (not isinstance(value, dict) or set(value) != {'municipal_additional_tax'}
            or type(value['municipal_additional_tax']) is not str or len(value['municipal_additional_tax']) > 240):
        raise StoreError()
    return value


class TenantStore:
    def __init__(self, root=None, *, worker_shutdown=None, clock=time.time):
        self.files = ProfileStore(root)
        self.root = self.files.root
        self.clock = clock
        self.worker_shutdown = worker_shutdown or (lambda tenant_id: None)
        self.db_path = self.root / 'tenants.sqlite3'
        with self.files._directory(()) as fd:
            if os.name == 'nt':
                try:
                    with self.db_path.open('xb'):
                        pass
                except FileExistsError:
                    pass
            else:
                self._initialize_posix_database(fd)
        with self._connection() as db:
            db.execute('PRAGMA journal_mode=WAL')
            version = db.execute('PRAGMA user_version').fetchone()[0]
            if version not in (0, 1, 2):
                raise StoreError()
            db.executescript(SCHEMA)
            with db:
                db.execute('BEGIN IMMEDIATE')
                cols = [r[1] for r in db.execute('PRAGMA table_info(profiles)').fetchall()]
                if 'name' not in cols:
                    db.execute("ALTER TABLE profiles ADD COLUMN name TEXT NOT NULL DEFAULT ''")
                db.execute('PRAGMA user_version=2')

    @staticmethod
    def _initialize_posix_database(fd):
        try:
            file_fd = os.open('tenants.sqlite3', os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600, dir_fd=fd)
            os.close(file_fd)
            os.fsync(fd)
        except FileExistsError:
            pass
        for name in ('tenants.sqlite3', 'tenants.sqlite3-wal', 'tenants.sqlite3-shm'):
            try:
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError:
                continue
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or info.st_mode & 0o077 or info.st_nlink != 1):
                raise StoreError()

    @staticmethod
    def _check_windows_database(directory):
        for suffix in ('', '-wal', '-shm', '-journal'):
            path = directory / ('tenants.sqlite3' + suffix)
            for attempt in range(5):
                try:
                    with windows_storage.file(path, mutable=True):
                        pass
                except FileNotFoundError:
                    if not suffix:
                        raise StoreError() from None
                except (OSError, ValueError) as exc:
                    # Windows can deny opens while SQLite deletes a sidecar.
                    if suffix and getattr(exc, 'winerror', None) == 5 and attempt < 4:
                        time.sleep(0.05)
                        continue
                    raise StoreError() from exc
                break

    @contextmanager
    def _connection(self):
        # Parent directories are validated without following symlinks each time.
        with ExitStack() as pinned:
            if os.name == 'nt':
                directory = pinned.enter_context(windows_storage.directory(self.root, ()))
                self._check_windows_database(directory)
                db = sqlite3.connect(self.db_path, timeout=10, isolation_level=None)
            else:
                with self.files._directory(()):
                    db = sqlite3.connect(self.db_path, timeout=10, isolation_level=None)
            try:
                db.row_factory = sqlite3.Row
                db.execute('PRAGMA foreign_keys=ON')
                db.execute('PRAGMA synchronous=FULL')
                yield db
            finally:
                db.close()

    @contextmanager
    def _transaction(self, *, write=False):
        with self._connection() as db:
            db.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def _tenant(self, db, tenant_id, *, deleting=False):
        _id(tenant_id)
        row = db.execute('SELECT * FROM tenants WHERE id=?', (tenant_id,)).fetchone()
        if (row is None or (not deleting and row['state'] != 'active')
                or (not deleting and row['expires'] is not None and row['expires'] <= self.clock())):
            raise NotFound()
        return row

    def _profile(self, db, tenant_id, profile_id):
        self._tenant(db, tenant_id)
        _id(profile_id)
        row = db.execute('SELECT * FROM profiles WHERE id=? AND tenant_id=?', (profile_id, tenant_id)).fetchone()
        if row is None:
            raise NotFound()
        return row

    @staticmethod
    def _audit(event, tenant_id, **opaque):
        # Callers pass server-generated IDs/status only; never request payloads.
        LOG.info('tenant_event', extra={'event': event, 'tenant_id': tenant_id, **opaque})

    def create_tenant(self, *, expires=None):
        if expires is not None and (type(expires) not in (int, float) or not self.clock() < expires < float('inf')):
            raise StoreError()
        tenant_id = new_id()
        with self._transaction(write=True) as db:
            db.execute('INSERT INTO tenants(id,created,expires) VALUES (?,?,?)', (tenant_id, self.clock(), expires))
            with self.files._directory(('tenants', tenant_id), create=True) as fd:
                self.files._write_once(fd, 'tenant-state.json', _json({'id': tenant_id}).encode())
        self._audit('created', tenant_id)
        return tenant_id

    def create_profile(self, tenant_id, *, name='', manifest_ref='', defaults=None):
        if not isinstance(name, str) or any(ord(c) < 32 or ord(c) == 127 for c in name):
            raise StoreError()
        name = name.strip()
        if len(name) > 120:
            raise StoreError()
        if not isinstance(manifest_ref, str) or len(manifest_ref) > 256 or any(ord(c) < 32 for c in manifest_ref):
            raise StoreError()
        defaults = {} if defaults is None else defaults
        _json(defaults)
        profile_id = new_id()
        revision = hashlib.sha256(uuid.uuid4().bytes).hexdigest()
        with self._transaction(write=True) as db:
            self._tenant(db, tenant_id)
            db.execute('INSERT INTO profiles (id, tenant_id, current_revision, name) VALUES (?,?,?,?)',
                       (profile_id, tenant_id, revision, name))
            self._insert_revision(db, tenant_id, profile_id, revision, manifest_ref, {}, defaults)
        return {'id': profile_id, 'revision': revision, 'name': name}

    @staticmethod
    def _insert_revision(db, tenant_id, profile_id, revision, manifest_ref, scenario, defaults):
        db.execute('INSERT INTO revisions VALUES (?,?,?,?)', (profile_id, tenant_id, revision, manifest_ref))
        db.execute('INSERT INTO scenarios VALUES (?,?,?,?)', (tenant_id, profile_id, revision, _json(scenario)))
        db.execute('INSERT INTO defaults VALUES (?,?,?,?)', (tenant_id, profile_id, revision, _json(defaults)))

    def _snapshot(self, db, tenant_id, profile_id, revision=None):
        profile = self._profile(db, tenant_id, profile_id)
        revision = profile['current_revision'] if revision is None else revision
        row = db.execute('''SELECT r.sha,r.manifest_ref,s.payload AS scenario,d.payload AS defaults
          FROM revisions r JOIN scenarios s ON s.tenant_id=r.tenant_id AND s.profile_id=r.profile_id AND s.revision=r.sha
          JOIN defaults d ON d.tenant_id=r.tenant_id AND d.profile_id=r.profile_id AND d.revision=r.sha
          WHERE r.tenant_id=? AND r.profile_id=? AND r.sha=?''', (tenant_id, profile_id, revision)).fetchone()
        if row is None:
            raise NotFound()
        return {'revision': row['sha'], 'scenario': json.loads(row['scenario']),
                'defaults': json.loads(row['defaults']), 'manifest_ref': row['manifest_ref']}

    def snapshot(self, tenant_id, profile_id, revision=None):
        with self._transaction() as db:
            return self._snapshot(db, tenant_id, profile_id, revision)

    def _publish(self, db, tenant_id, profile_id, expected_revision, scenario=None, defaults=None, manifest_ref=None):
        current = self._snapshot(db, tenant_id, profile_id)
        if expected_revision != current['revision']:
            raise Conflict()
        scenario = current['scenario'] if scenario is None else scenario
        defaults = current['defaults'] if defaults is None else defaults
        if manifest_ref is None:
            manifest_ref = current['manifest_ref']
        else:
            item = self._upload(db, tenant_id, manifest_ref)
            if item['metadata'].get('purpose') != 'profile_manifest':
                raise StoreError()
        content = _json({'parent': expected_revision, 'manifest_ref': manifest_ref,
                         'scenario': scenario, 'defaults': defaults})
        frozen = json.loads(content)
        scenario, defaults = frozen['scenario'], frozen['defaults']
        revision = hashlib.sha256(content.encode()).hexdigest()
        self._insert_revision(db, tenant_id, profile_id, revision, manifest_ref, scenario, defaults)
        changed = db.execute('UPDATE profiles SET current_revision=? WHERE id=? AND tenant_id=? AND current_revision=?',
                             (revision, profile_id, tenant_id, expected_revision)).rowcount
        if changed != 1:
            raise Conflict()
        return self._snapshot(db, tenant_id, profile_id)

    def publish(self, tenant_id, profile_id, expected_revision, *, scenario=None, defaults=None, manifest_ref=None):
        with self._transaction(write=True) as db:
            result = self._publish(db, tenant_id, profile_id, expected_revision, scenario, defaults, manifest_ref)
        self._audit('revision', tenant_id, profile_id=profile_id)
        return result

    def profiles(self, tenant_id):
        with self._transaction() as db:
            self._tenant(db, tenant_id)
            return [dict(r) for r in db.execute('SELECT id,name,current_revision AS revision FROM profiles WHERE tenant_id=? ORDER BY id', (tenant_id,))]

    def job_directory(self, tenant_id, job_id):
        with self._transaction() as db:
            self._tenant(db, tenant_id)
            _id(job_id)
            if db.execute('SELECT 1 FROM jobs WHERE tenant_id=? AND id=?', (tenant_id, job_id)).fetchone() is None:
                raise NotFound()
            with self.files._directory(('tenants', tenant_id, 'jobs', job_id), create=True):
                pass
        return self.root / 'tenants' / tenant_id / 'jobs' / job_id

    def start_job(self, tenant_id, job_id):
        with self._transaction(write=True) as db:
            self._tenant(db, tenant_id)
            _id(job_id)
            job = db.execute('SELECT * FROM jobs WHERE tenant_id=? AND id=?', (tenant_id, job_id)).fetchone()
            if job is None:
                raise NotFound()
            if job['state'] != 'queued' or self._profile(db, tenant_id, job['profile_id'])['current_revision'] != job['profile_revision']:
                raise Conflict()
            db.execute("UPDATE jobs SET state='running' WHERE tenant_id=? AND id=?", (tenant_id, job_id))

    def cancel_job(self, tenant_id, job_id):
        with self._transaction(write=True) as db:
            self._tenant(db, tenant_id)
            _id(job_id)
            if db.execute('SELECT 1 FROM jobs WHERE tenant_id=? AND id=?', (tenant_id, job_id)).fetchone() is None:
                raise NotFound()
            db.execute("UPDATE jobs SET state='cancelled',result_ref=NULL,error=NULL WHERE tenant_id=? AND id=?", (tenant_id, job_id))

    def recover_jobs(self):
        """Exclusive service startup: unfinished work cannot survive its workers."""
        with self._transaction(write=True) as db:
            return db.execute("UPDATE jobs SET state='failed',error='Computation interrupted' WHERE state IN ('queued','running')").rowcount

    def delete_profile(self, tenant_id, profile_id):
        with self._transaction(write=True) as db:
            self._profile(db, tenant_id, profile_id)
            if db.execute("SELECT 1 FROM jobs WHERE tenant_id=? AND profile_id=? AND state IN ('queued','running')",
                          (tenant_id, profile_id)).fetchone():
                raise Conflict()
            results = [r['result_ref'] for r in db.execute('SELECT result_ref FROM jobs WHERE tenant_id=? AND profile_id=? AND result_ref IS NOT NULL', (tenant_id, profile_id))]
            backups = [r['backup_ref'] for r in db.execute('SELECT backup_ref FROM migrations WHERE tenant_id=? AND profile_id=?', (tenant_id, profile_id))]
            job_ids = [r['id'] for r in db.execute('SELECT id FROM jobs WHERE tenant_id=? AND profile_id=?', (tenant_id, profile_id))]
            db.execute('DELETE FROM profiles WHERE tenant_id=? AND id=?', (tenant_id, profile_id))
        for ref in results:
            self._remove_directory(('tenants', tenant_id, 'results', _id(ref)))
        for ref in backups:
            self._remove_directory(('tenants', tenant_id, ref))
        for job_id in job_ids:
            self._remove_directory(('tenants', tenant_id, 'jobs', job_id))
        self._remove_directory(('tenants', tenant_id, 'catalogs', profile_id))

    def profile_catalog_path(self, tenant_id, profile_id):
        _id(tenant_id)
        _id(profile_id)
        with self._transaction() as db:
            self._profile(db, tenant_id, profile_id)
        with self.files._directory(('tenants', tenant_id, 'catalogs', profile_id), create=True):
            pass
        path = self.root / 'tenants' / tenant_id / 'catalogs' / profile_id
        return Path(windows_storage.native_path(path)) if os.name == 'nt' else path

    def add_session(self, tenant_id, token_hash, *, expires=None):
        if not isinstance(token_hash, str) or not re.fullmatch(r'[0-9a-f]{64}', token_hash):
            raise StoreError()
        if expires is not None and (type(expires) not in (int, float) or not self.clock() < expires < float('inf')):
            raise StoreError()
        with self._transaction(write=True) as db:
            self._tenant(db, tenant_id)
            db.execute('INSERT INTO sessions VALUES (?,?,?)', (token_hash, tenant_id, expires))

    def create_pairing_code(self, tenant_id, code_hash):
        if not isinstance(code_hash, str) or not re.fullmatch(r'[0-9a-f]{64}', code_hash):
            raise StoreError()
        with self._transaction(write=True) as db:
            self._tenant(db, tenant_id)
            db.execute('DELETE FROM pairing_codes WHERE expires<=?', (self.clock(),))
            db.execute('INSERT INTO pairing_codes VALUES (?,?,?)', (code_hash, tenant_id, self.clock() + 300))

    def consume_pairing_code(self, code_hash, session_hash):
        if not all(isinstance(v, str) and re.fullmatch(r'[0-9a-f]{64}', v) for v in (code_hash, session_hash)):
            raise Unauthorized()
        with self._transaction(write=True) as db:
            row = db.execute('SELECT tenant_id FROM pairing_codes WHERE code_hash=? AND expires>?',
                             (code_hash, self.clock())).fetchone()
            if row is None:
                raise Unauthorized()
            tenant_id = row['tenant_id']
            self._tenant(db, tenant_id)
            db.execute('DELETE FROM pairing_codes WHERE code_hash=?', (code_hash,))
            db.execute('INSERT INTO sessions VALUES (?,?,NULL)', (session_hash, tenant_id))
            return tenant_id

    def resolve_session(self, token_hash):
        with self._transaction() as db:
            row = db.execute('''SELECT s.tenant_id FROM sessions s JOIN tenants t ON t.id=s.tenant_id
                WHERE s.token_hash=? AND t.state='active' AND (s.expires IS NULL OR s.expires>?)
                AND (t.expires IS NULL OR t.expires>?)''', (token_hash, self.clock(), self.clock())).fetchone()
            if row is None:
                raise Unauthorized()
            return row['tenant_id']

    def revoke_session(self, tenant_id, token_hash):
        with self._transaction(write=True) as db:
            self._tenant(db, tenant_id)
            db.execute('DELETE FROM sessions WHERE token_hash=? AND tenant_id=?', (token_hash, tenant_id))

    def put_upload(self, tenant_id, content, metadata, *, dedupe_scope=''):
        if not isinstance(content, bytes) or not 0 < len(content) <= 64 * 1024 * 1024:
            raise StoreError()
        meta = _json(metadata)
        digest = hashlib.sha256(content).hexdigest()
        dedupe = hashlib.sha256(_json({'sha256': digest, 'scope': dedupe_scope}).encode()).hexdigest()
        upload_id = new_id()
        parts = ('tenants', tenant_id, 'uploads', upload_id)
        created = False
        try:
            with self._transaction(write=True) as db:
                self._tenant(db, tenant_id)
                old = db.execute('SELECT id FROM uploads WHERE tenant_id=? AND dedupe_key=?', (tenant_id, dedupe)).fetchone()
                if old:
                    return self._upload(db, tenant_id, old['id']), True
                created = True
                with self.files._directory(parts, create=True) as fd:
                    self.files._write_once(fd, 'content', content)
                db.execute('INSERT INTO uploads VALUES (?,?,?,?,?,?,?)',
                           (upload_id, tenant_id, 'ready', upload_id, digest, meta, dedupe))
                result = self._upload(db, tenant_id, upload_id)
        except BaseException:
            if created:
                self._remove_directory(parts)
            raise
        self._audit('upload', tenant_id, upload_id=upload_id)
        return result, False

    def _upload(self, db, tenant_id, upload_id):
        self._tenant(db, tenant_id)
        _id(upload_id)
        row = db.execute('SELECT * FROM uploads WHERE tenant_id=? AND id=?', (tenant_id, upload_id)).fetchone()
        if row is None:
            raise NotFound()
        return {'id': row['id'], 'status': row['status'], 'sha256': row['sha256'],
                'metadata': json.loads(row['metadata'])}

    def upload(self, tenant_id, upload_id):
        with self._transaction() as db:
            return self._upload(db, tenant_id, upload_id)

    def uploads(self, tenant_id):
        with self._transaction() as db:
            self._tenant(db, tenant_id)
            return [self._upload(db, tenant_id, row['id']) for row in
                    db.execute('SELECT id FROM uploads WHERE tenant_id=? ORDER BY id', (tenant_id,))]

    def read_upload(self, tenant_id, upload_id):
        with self._transaction() as db:
            item = self._upload(db, tenant_id, upload_id)
            row = db.execute('SELECT artifact_ref FROM uploads WHERE tenant_id=? AND id=?', (tenant_id, upload_id)).fetchone()
            ref = _id(row['artifact_ref'])
            with self.files._directory(('tenants', tenant_id, 'uploads', ref)) as fd:
                raw = self.files._read(fd, 'content', 64 * 1024 * 1024)
            if hashlib.sha256(raw).hexdigest() != item['sha256']:
                raise StoreError()
            return raw

    def update_upload(self, tenant_id, upload_id, *, included):
        if type(included) is not bool:
            raise StoreError()
        with self._transaction(write=True) as db:
            item = self._upload(db, tenant_id, upload_id)
            item['metadata']['included'] = included
            db.execute('UPDATE uploads SET metadata=? WHERE tenant_id=? AND id=?',
                       (_json(item['metadata']), tenant_id, upload_id))
            return self._upload(db, tenant_id, upload_id)

    def create_job(self, tenant_id, profile_id, revision, scenario_hash):
        if not isinstance(scenario_hash, str) or not re.fullmatch(r'[0-9a-f]{64}', scenario_hash):
            raise StoreError()
        job_id = new_id()
        with self._transaction(write=True) as db:
            current = self._snapshot(db, tenant_id, profile_id)
            if current['revision'] != revision:
                raise Conflict()
            db.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?)',
                       (job_id, tenant_id, profile_id, revision, scenario_hash, 'queued', None, None, self.clock()))
        return self.job(tenant_id, job_id)

    def job(self, tenant_id, job_id):
        with self._transaction() as db:
            self._tenant(db, tenant_id)
            _id(job_id)
            row = db.execute('SELECT * FROM jobs WHERE tenant_id=? AND id=?', (tenant_id, job_id)).fetchone()
            if row is None:
                raise NotFound()
            return dict(row)

    def fail_job(self, tenant_id, job_id, error=None, failure_info=None):
        # Arbitrary exceptions, paths and payloads never become API error text.
        self.job(tenant_id, job_id)
        with self._transaction(write=True) as db:
            self._tenant(db, tenant_id)
            db.execute("UPDATE jobs SET state='failed',error='Computation failed' WHERE tenant_id=? AND id=? AND state IN ('queued','running')",
                       (tenant_id, job_id))
        if isinstance(error, dict) and failure_info is None:
            failure_info = error
        if failure_info and isinstance(failure_info, dict):
            try:
                with self.files._directory(('tenants', tenant_id, 'jobs', job_id), create=True) as fd:
                    safe_diag = {
                        k: failure_info[k] for k in (
                            'job_id', 'operation', 'failure_type', 'error_code',
                            'error_name', 'model_build_id', 'version', 'traceback', 'timestamp'
                        ) if k in failure_info
                    }
                    self.files._write_once(fd, 'diagnostic.json', _json(safe_diag).encode())
            except Exception:
                pass
        return self.job(tenant_id, job_id)

    def _remove_directory(self, parts):
        if os.name == 'nt':
            try:
                with windows_storage.directory(self.root, parts[:-1]) as directory:
                    windows_storage.remove_tree(windows_storage.child(directory, parts[-1]))
            except FileNotFoundError:
                pass
            return
        # shutil.rmtree uses fd-based traversal and refuses top-level symlinks.
        try:
            if not (self.root.joinpath(*parts[:-1])).exists():
                return
            with self.files._directory(parts[:-1]) as fd:
                name = parts[-1]
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if stat.S_ISLNK(info.st_mode):
                    os.unlink(name, dir_fd=fd)
                else:
                    shutil.rmtree(name, dir_fd=fd)
                os.fsync(fd)
        except FileNotFoundError:
            pass

    def publish_result(self, tenant_id, job_id, result):
        raw = _json(result).encode()
        result_id = new_id()
        parts = ('tenants', tenant_id, 'results', result_id)
        created = False
        try:
            with self._transaction(write=True) as db:
                self._tenant(db, tenant_id)
                _id(job_id)
                job = db.execute('SELECT * FROM jobs WHERE tenant_id=? AND id=?', (tenant_id, job_id)).fetchone()
                if job is None:
                    raise NotFound()
                profile = self._profile(db, tenant_id, job['profile_id'])
                if profile['current_revision'] != job['profile_revision'] or job['state'] not in ('queued', 'running'):
                    raise Conflict()
                created = True
                with self.files._directory(parts, create=True) as fd:
                    self.files._write_once(fd, 'result.json', raw)
                db.execute("UPDATE jobs SET state='complete',result_ref=?,error=NULL WHERE tenant_id=? AND id=?",
                           (result_id, tenant_id, job_id))
        except BaseException:
            if created:
                self._remove_directory(parts)
            raise
        return self.job(tenant_id, job_id)

    def read_result(self, tenant_id, job_id):
        with self._transaction() as db:
            self._tenant(db, tenant_id)
            _id(job_id)
            job = db.execute('SELECT * FROM jobs WHERE tenant_id=? AND id=?', (tenant_id, job_id)).fetchone()
            if job is None or job['result_ref'] is None:
                raise NotFound()
            if self._profile(db, tenant_id, job['profile_id'])['current_revision'] != job['profile_revision']:
                raise Conflict()
            with self.files._directory(('tenants', tenant_id, 'results', _id(job['result_ref']))) as fd:
                return json.loads(self.files._read(fd, 'result.json', MAX_JSON_BYTES))

    def delete_tenant(self, tenant_id):
        with self._transaction(write=True) as db:
            self._tenant(db, tenant_id, deleting=True)
            db.execute("UPDATE tenants SET state='deleting' WHERE id=?", (tenant_id,))
            db.execute('DELETE FROM sessions WHERE tenant_id=?', (tenant_id,))
            db.execute("UPDATE jobs SET state='cancelled',result_ref=NULL,error=NULL WHERE tenant_id=?", (tenant_id,))
        # Fail closed if shutdown/cleanup fails. Recovery retries this tombstone.
        self.worker_shutdown(tenant_id)
        self._remove_directory(('tenants', tenant_id))
        with self._transaction(write=True) as db:
            db.execute("DELETE FROM tenants WHERE id=? AND state='deleting'", (tenant_id,))
        self._audit('deleted', tenant_id)

    def janitor(self):
        """Administrator-only recovery; never expire persistent active sessions."""
        with self._transaction() as db:
            deleting = [r['id'] for r in db.execute("SELECT id FROM tenants WHERE state='deleting'")]
        for tenant_id in deleting:
            self.delete_tenant(tenant_id)
        removed = 0
        with self._transaction(write=True) as db:
            # This lock also serializes every upload publisher against cleanup.
            tenants = db.execute("SELECT id FROM tenants WHERE state='active'").fetchall()
            active_ids = {t['id'] for t in tenants}
            with self.files._directory(('tenants',), create=True) as fd:
                candidates = os.listdir(fd)
            for owner in candidates:
                if owner in active_ids or not re.fullmatch(r'[0-9a-f]{32}', owner):
                    continue
                with self.files._directory(('tenants', owner)) as fd:
                    if 'tenant-state.json' not in os.listdir(fd):
                        continue
                    marker = self.files._read(fd, 'tenant-state.json', 256)
                if marker == _json({'id': owner}).encode():
                    self._remove_directory(('tenants', owner))
                    removed += 1
            for tenant in tenants:
                owner = tenant['id']
                known = {'uploads': {r['artifact_ref'] for r in db.execute('SELECT artifact_ref FROM uploads WHERE tenant_id=?', (owner,))},
                         'results': {r['result_ref'] for r in db.execute('SELECT result_ref FROM jobs WHERE tenant_id=? AND result_ref IS NOT NULL', (owner,))}}
                backups = {r['backup_ref'] for r in db.execute('SELECT backup_ref FROM migrations WHERE tenant_id=?', (owner,))}
                with self.files._directory(('tenants', owner), create=True) as tenant_fd:
                    names = os.listdir(tenant_fd)
                for name in names:
                    if name not in backups and re.fullmatch(r'migration-[0-9a-f]{32}|\.stage-[0-9a-f]{32}', name):
                        self._remove_directory(('tenants', owner, name))
                        removed += 1
                for kind in ('uploads', 'results', 'quarantine', 'cache'):
                    if kind not in names:
                        continue
                    with self.files._directory(('tenants', owner, kind)) as data_fd:
                        children = os.listdir(data_fd)
                    for name in children:
                        orphan = kind in known and name not in known[kind] and re.fullmatch(r'[0-9a-f]{32}', name)
                        if orphan or re.fullmatch(r'\.stage-[0-9a-f]{32}', name):
                            self._remove_directory(('tenants', owner, kind, name))
                            removed += 1
        return {'deleted_tenants': len(deleting), 'orphan_uploads': removed}

    def migrate_legacy(self, tenant_id, profile_id, expected_revision, *, scenario_path, defaults_path, apply=False):
        """Explicit local adapter only. Dry-run has no writes; sources stay intact."""
        initial = self.snapshot(tenant_id, profile_id)
        blobs = []
        for path in (scenario_path, defaults_path):
            with Path(path).open('rb') as stream:
                raw = stream.read(MAX_JSON_BYTES + 1)
            if len(raw) > MAX_JSON_BYTES:
                raise StoreError()
            blobs.append(raw)
        try:
            scenario, defaults = [json.loads(raw) for raw in blobs]
            _json(scenario)
            _json(defaults)
            if (not isinstance(scenario.get('settings'), dict) or not isinstance(scenario.get('quote'), dict)
                    or not isinstance(defaults.get('overrides'), dict)):
                raise StoreError()
            # The caller first supplies this profile's explicit factory inputs.
            # Never import another household's global SPEC as an implicit base.
            factory = initial['defaults'].get('factory')
            if not isinstance(factory, dict) or set(defaults['overrides']) - set(factory):
                raise StoreError()
            defaults = {'factory': factory, 'overrides': defaults['overrides']}
        except (ValueError, TypeError) as exc:
            raise StoreError() from exc
        digest = hashlib.sha256(b'\0'.join(blobs)).hexdigest()
        report = {'digest': digest, 'scenario_fields': len(scenario['settings']),
                  'default_overrides': len(defaults['overrides']), 'applied': False}
        if not apply:
            return report
        backup = ('tenants', tenant_id, 'migration-' + new_id())
        created = False
        try:
            with self._transaction(write=True) as db:
                self._profile(db, tenant_id, profile_id)
                if db.execute('SELECT 1 FROM migrations WHERE tenant_id=? AND profile_id=?', (tenant_id, profile_id)).fetchone():
                    raise Conflict()
                created = True
                with self.files._directory(backup, create=True) as fd:
                    for index, raw in enumerate(blobs):
                        name = f'{index}.json'
                        self.files._write_once(fd, name, raw)
                        if os.name == 'nt':
                            windows_storage.child(fd, name).chmod(stat.S_IREAD)
                        else:
                            os.chmod(name, 0o400, dir_fd=fd, follow_symlinks=False)
                result = self._publish(db, tenant_id, profile_id, expected_revision, scenario, defaults)
                db.execute('INSERT INTO migrations VALUES (?,?,?,?,?)', (tenant_id, profile_id, digest, result['revision'], backup[-1]))
        except BaseException:
            if created:
                self._remove_directory(backup)
            raise
        return {**report, 'applied': True, 'revision': result['revision']}


class TenantState:
    """Explicit adapter; a caller supplies validated documents, never global data."""
    def __init__(self, store, tenant_id, profile_id):
        self.store, self.tenant_id, self.profile_id = store, tenant_id, profile_id

    def get_state(self, revision=None):
        snap = self.store.snapshot(self.tenant_id, self.profile_id)
        if revision == snap['revision']:
            return {'revision': revision, 'unchanged': True}
        return self._response(snap)

    @staticmethod
    def _response(snap):
        document = snap['defaults']
        defaults = deepcopy(document.get('factory', {}))
        for name, entry in document.get('overrides', {}).items():
            if name in defaults and isinstance(entry, dict) and 'value' in entry:
                defaults[name] = deepcopy(entry['value'])
        records = [{'name': name, 'value': value, 'source': 'Profile input'} for name, value in defaults.items()]
        return {'revision': snap['revision'], 'settings': {**defaults, **snap['scenario'].get('settings', {})},
                'quote': snap['scenario'].get('quote', {'line_items': []}), 'defaults': defaults, 'records': records}

    def update_state(self, payload):
        if not isinstance(payload, dict) or set(payload) - {'revision', 'settings', 'quote', 'replace'}:
            raise StoreError()
        if not isinstance(payload.get('revision'), str):
            raise Conflict()
        if type(payload.get('replace', False)) is not bool:
            raise StoreError()
        snap = self.store.snapshot(self.tenant_id, self.profile_id)
        scenario = deepcopy(snap['scenario'])
        for key in ('settings', 'quote'):
            if key in payload:
                _json(payload[key])
                scenario[key] = payload[key] if payload.get('replace') else {**scenario.get(key, {}), **payload[key]}
            elif payload.get('replace'):
                raise StoreError()
        result = self.store.publish(self.tenant_id, self.profile_id, payload['revision'], scenario=scenario)
        return self._response(result)


class TenantDefaults:
    """Revision-bound defaults adapter with explicit, profile-owned factory data.

    A successful save/reset invalidates this adapter's expected revision; callers
    obtain a fresh adapter to make another edit. No worker snapshot is mutated.
    """
    def __init__(self, store, tenant_id, profile_id, expected_revision=None):
        self.store, self.tenant_id, self.profile_id = store, tenant_id, profile_id
        self.snapshot = store.snapshot(tenant_id, profile_id)
        self.expected_revision = self.snapshot['revision'] if expected_revision is None else expected_revision
        self.document = deepcopy(self.snapshot['defaults'])
        if not isinstance(self.document.get('factory'), dict) or not isinstance(self.document.get('overrides', {}), dict):
            raise Conflict()

    def overrides(self):
        return deepcopy(self.document.get('overrides', {}))

    def effective(self):
        result = deepcopy(self.document['factory'])
        for name, entry in self.overrides().items():
            if name in result and isinstance(entry, dict) and 'value' in entry:
                result[name] = deepcopy(entry['value'])
        return result

    def records(self):
        return [{'name': name, 'value': value,
                 'status': self.overrides().get(name, {}).get('status', 'proposed'),
                 'source': 'Profile input', 'owner': 'user',
                 'factory_default': deepcopy(self.document['factory'][name])}
                for name, value in self.effective().items()]

    def _group(self, name):
        from feasibility.assumptions import BATTERY_SETTINGS, SCHEDULE_SETTINGS
        if name not in self.document['factory']:
            raise StoreError()
        group = BATTERY_SETTINGS if name in BATTERY_SETTINGS else SCHEDULE_SETTINGS if name in SCHEDULE_SETTINGS else (name,)
        return tuple(key for key in group if key in self.document['factory'])

    def save(self, name, value, validator, *, battery_settings=None, schedule_settings=None):
        group = self._group(name)
        base = self.effective()
        candidate = {name: value}
        for context in (battery_settings, schedule_settings):
            if context is not None:
                _json(context)
                if set(context) - set(group):
                    raise StoreError()
                candidate.update(context)
        candidate[name] = value
        normalized = validator(candidate, base=deepcopy(base), supplied=set(candidate))
        _json(normalized)
        overrides = self.overrides()
        for key in group:
            if key not in normalized:
                raise StoreError()
            overrides[key] = {'value': deepcopy(normalized[key]), 'status': 'provided',
                              'owner': 'user', 'factory_default': deepcopy(self.document['factory'][key])}
        document = {**self.document, 'overrides': overrides}
        self.store.publish(self.tenant_id, self.profile_id, self.expected_revision, defaults=document)
        return deepcopy(overrides[name])

    def reset(self, name):
        overrides = self.overrides()
        for key in self._group(name):
            overrides.pop(key, None)
        self.store.publish(self.tenant_id, self.profile_id, self.expected_revision,
                           defaults={**self.document, 'overrides': overrides})
        return True
