"""Local, append-only evidence uploads; never alters active calculator inputs."""
import datetime as dt
import hashlib
import json
import os
import re
import tempfile
import threading
import time
import uuid
from pathlib import Path

try:
    from .evidence_formats import validate_dataset, iter_pvgis_rows
    from .evidence_priority import build_coverage
except ImportError:
    from evidence_formats import validate_dataset, iter_pvgis_rows
    from evidence_priority import build_coverage

MAX_FILE_BYTES = 64 * 1024 * 1024
UPLOAD_ROOT = Path(__file__).resolve().parent / 'data' / 'evidence_uploads'
LOCK = threading.RLock()
SOURCES = {'pvgis6', 'pvgis53', 'google'}
SUFFIXES = {'.csv', '.json', '.tif', '.tiff'}
_coverage_cache = {}


def _atomic_new(path, content):
    """Publish a new file atomically without replacing an existing artifact."""
    descriptor, temporary = tempfile.mkstemp(prefix='.upload-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except OSError as error:
            import errno
            if error.errno not in (errno.EPERM, errno.EOPNOTSUPP, errno.EXDEV):
                raise
            # Removable filesystems may lack hard links. Linux's exclusive
            # rename still publishes atomically and refuses to replace files.
            import ctypes
            libc = ctypes.CDLL(None, use_errno=True)
            rename = getattr(libc, 'renameat2', None)
            if rename is None:
                raise OSError('This filesystem cannot publish uploads without replacing files') from error
            rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
            rename.restype = ctypes.c_int
            if rename(-100, os.fsencode(temporary), -100, os.fsencode(path), 1) != 0:
                code = ctypes.get_errno()
                raise OSError(code, os.strerror(code), str(path))
    finally:
        Path(temporary).unlink(missing_ok=True)


def _root(root):
    return Path(UPLOAD_ROOT if root is None else root).resolve()


def _validate_upload(filename, content, source, series_label=''):
    if source not in SOURCES:
        raise ValueError('Choose PVGIS 6, PVGIS 5.3, or Google Solar')
    if not isinstance(filename, str) or not filename or len(filename) > 240:
        raise ValueError('A filename of at most 240 characters is required')
    if any(ord(char) < 32 for char in filename) or '/' in filename or '\\' in filename:
        raise ValueError('Filename must be a plain name, without directory components')
    suffix = Path(filename).suffix.lower()
    if suffix not in SUFFIXES:
        raise ValueError('Supported files: CSV, JSON, TIF, TIFF')
    if not isinstance(content, bytes) or not 0 < len(content) <= MAX_FILE_BYTES:
        raise ValueError('File must be nonempty and no larger than 64 MiB')
    if not isinstance(series_label, str) or len(series_label) > 80 or any(ord(c) < 32 for c in series_label):
        raise ValueError('Series label must be at most 80 characters, without control characters')
    series_label = series_label.strip()
    if source.startswith('pvgis') and not series_label:
        raise ValueError('Enter a roof plane / series label for PVGIS files')
    summary = validate_dataset(filename, content)
    if source.startswith('pvgis') and summary.get('provider') != 'PVGIS':
        raise ValueError('The selected PVGIS upload area requires a PVGIS dataset')
    if source == 'google' and summary.get('provider') not in ('Google Solar', 'GoogleSolar', 'Raster'):
        raise ValueError('The Google Solar area accepts Solar JSON or TIFF layers')
    detected = summary.get('pvgis_version')
    if detected in ('6', '5.3') and source.startswith('pvgis'):
        if detected != ('6' if source == 'pvgis6' else '5.3'):
            raise ValueError('Detected PVGIS version does not match the selected upload area')
    return summary, suffix, series_label


def save_upload(filename, content, source, series_label='', root=None):
    """Explicit legacy local file adapter; tenant service uses EvidenceStore."""
    summary, suffix, series_label = _validate_upload(filename, content, source, series_label)
    digest = hashlib.sha256(content).hexdigest()
    identity = hashlib.sha256(json.dumps([digest, source, series_label.casefold()]).encode()).hexdigest()
    base = _root(root)
    with LOCK:
        base.mkdir(parents=True, exist_ok=True)
        directory = base / identity
        if directory.is_symlink():
            raise ValueError('Upload storage path must not be a symbolic link')
        directory.mkdir(exist_ok=True)
        metadata_path = directory / 'metadata.json'
        if metadata_path.exists():
            old = json.loads(metadata_path.read_text())
            allowed_names = {'source' + extension for extension in SUFFIXES}
            if old.get('stored_filename') not in allowed_names or old.get('id') != identity:
                raise ValueError('Invalid upload inventory metadata')
            old_path = directory / old['stored_filename']
            if old_path.is_symlink() or hashlib.sha256(old_path.read_bytes()).hexdigest() != digest:
                raise ValueError('Stored file failed its integrity check; original upload retained')
            return {'dataset': old, 'duplicate': True}
        stored_filename = 'source' + suffix
        destination = directory / stored_filename
        if destination.exists():
            if destination.is_symlink() or hashlib.sha256(destination.read_bytes()).hexdigest() != digest:
                raise ValueError('Existing upload artifact differs; no file was replaced')
        else:
            _atomic_new(destination, content)
        record = {'id': identity, 'sha256': digest, 'source': source, 'filename': filename,
                  'stored_filename': stored_filename, 'series_label': series_label,
                  'size_bytes': len(content), 'uploaded_at': dt.datetime.now(dt.timezone.utc).isoformat(),
                  'summary': summary, 'active_calculator_data_modified': False}
        _atomic_new(metadata_path, json.dumps(record, allow_nan=False).encode())
        _coverage_cache.clear()
        return {'dataset': record, 'duplicate': False}


def inventory(root=None):
    base = _root(root)
    with LOCK:
        records = []
        if base.exists():
            for directory in sorted(base.iterdir()):
                if not re.fullmatch(r'[0-9a-f]{64}', directory.name) or directory.is_symlink():
                    continue
                metadata = directory / 'metadata.json'
                if metadata.is_file() and not metadata.is_symlink():
                    record = json.loads(metadata.read_text())
                    if record.get('id') != directory.name or record.get('stored_filename') not in {'source' + s for s in SUFFIXES}:
                        raise ValueError('Invalid upload inventory metadata')
                    records.append(record)
        included = {}
        for event_path in sorted((base / 'selection_events').glob('*.json')):
            event = json.loads(event_path.read_text())
            included[event['id']] = event['included']
        for record in records:
            record['included'] = included.get(record['id'], True)
        signature = (str(base), tuple((r['id'], r['sha256'], r['included']) for r in records))
        if signature not in _coverage_cache:
            def load_rows(record):
                raw = (base / record['id'] / record['stored_filename']).read_bytes()
                if hashlib.sha256(raw).hexdigest() != record['sha256']:
                    raise ValueError('Stored dataset failed its integrity check')
                return iter_pvgis_rows(record['filename'], raw)
            _coverage_cache[signature] = build_coverage([r for r in records if r['included']], load_rows)
        return {'datasets': records, 'max_file_bytes': MAX_FILE_BYTES,
                'coverage': _coverage_cache[signature], 'active_calculator_data_modified': False}


def set_inclusion(body, root=None):
    identifier, included = body.get('id'), body.get('included')
    if not isinstance(identifier, str) or not re.fullmatch(r'[0-9a-f]{64}', identifier):
        raise ValueError('A valid dataset identifier is required')
    if not isinstance(included, bool):
        raise ValueError('included must be a boolean')
    base = _root(root)
    directory = base / identifier
    if directory.is_symlink() or not (directory / 'metadata.json').is_file():
        raise ValueError('Dataset not found')
    with LOCK:
        events = base / 'selection_events'
        events.mkdir(parents=True, exist_ok=True)
        event = {'id': identifier, 'included': included}
        path = events / f'{time.time_ns():020d}-{uuid.uuid4().hex}.json'
        _atomic_new(path, json.dumps(event).encode())
        _coverage_cache.clear()
    return {'id': identifier, 'included': included, 'active_calculator_data_modified': False}


def receive_upload(handler, root=None):
    """Read only a bounded, explicitly framed raw upload from the local UI."""
    if handler.headers.get('Transfer-Encoding'):
        raise ValueError('Chunked uploads are not supported')
    if handler.headers.get('Content-Type', '').split(';')[0].strip().lower() != 'application/octet-stream':
        raise ValueError('Upload the raw file as application/octet-stream')
    length = int(handler.headers.get('Content-Length', '0'))
    if not 0 < length <= MAX_FILE_BYTES:
        raise ValueError('File must be nonempty and no larger than 64 MiB')
    from urllib.parse import unquote
    filename = unquote(handler.headers.get('X-Dataset-Filename', ''), errors='strict')
    series_label = unquote(handler.headers.get('X-Dataset-Series', ''), errors='strict')
    source = handler.headers.get('X-Dataset-Source', '')
    content = handler.rfile.read(length)
    if len(content) != length:
        raise ValueError('Upload was incomplete; no dataset was stored')
    return save_upload(filename, content, source, series_label, root)


class EvidenceStore:
    """Tenant-owned preview evidence with database-resolved opaque upload IDs.

    No upload activates model data. TIFF stays a local preview artifact. The
    injected root is server-owned and must match this tenant's storage root.
    """
    def __init__(self, store, tenant_id, root):
        self.store, self.tenant_id = store, tenant_id
        with store._transaction() as db:
            store._tenant(db, tenant_id)
        self.root = Path(root)
        if self.root != store.root / 'tenants' / tenant_id:
            raise ValueError('Invalid evidence root')

    @staticmethod
    def _record(item):
        return {**item['metadata'], 'id': item['id'], 'sha256': item['sha256'],
                'active_calculator_data_modified': False}

    def save_upload(self, filename, content, source, series_label=''):
        summary, suffix, series_label = _validate_upload(filename, content, source, series_label)
        metadata = {'purpose': 'evidence', 'filename': filename, 'source': source, 'series_label': series_label,
                    'summary': summary, 'included': True, 'size_bytes': len(content),
                    'uploaded_at': dt.datetime.now(dt.timezone.utc).isoformat()}
        # Dedupe only within the authenticated tenant and the same evidence role.
        scope = json.dumps([source, series_label.casefold()])
        item, duplicate = self.store.put_upload(self.tenant_id, content, metadata, dedupe_scope=scope)
        return {'dataset': self._record(item), 'duplicate': duplicate}

    def inventory(self):
        records = [self._record(item) for item in self.store.uploads(self.tenant_id)
                   if item['metadata'].get('purpose') == 'evidence']
        def rows(record):
            return iter_pvgis_rows(record['filename'], self.store.read_upload(self.tenant_id, record['id']))
        return {'datasets': records, 'max_file_bytes': MAX_FILE_BYTES,
                'coverage': build_coverage([r for r in records if r['included']], rows),
                'active_calculator_data_modified': False}

    def set_inclusion(self, body):
        if not isinstance(body, dict) or set(body) != {'id', 'included'}:
            raise ValueError('Expected id and included')
        item = self.store.update_upload(self.tenant_id, body['id'], included=body['included'])
        return {'id': item['id'], 'included': item['metadata']['included'], 'active_calculator_data_modified': False}

    def download(self, upload_id):
        return self.store.read_upload(self.tenant_id, upload_id)
