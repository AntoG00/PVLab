"""Owner-partitioned immutable profile storage; no active-runtime integration.

Owners are authenticated, server-generated IDs supplied by internal callers.
Traversal checks every component, including root ancestors. Published files
are flushed and installed without replacement before they can be referenced.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import stat
import sys
import uuid

from feasibility.profiles import (
    COMPILER_VERSION, MODEL_VERSION, PARSER_VERSION, SCHEMA_VERSION,
    HouseholdProfileV1, canonical_json, identifier, object_fields, parse_profile,
    profile_to_dict, strict_json, validate_ready,
)

if os.name == 'nt':
    from feasibility import windows_storage

KINDS = ('consumption', 'solar', 'weather', 'prices', 'thermal', 'dataset_manifest')
MAX_ARTIFACT_BYTES = 64 * 1024 * 1024
MAX_MANIFEST_BYTES = 2 * 1024 * 1024


def new_id():
    """Generate an opaque owner, profile or artifact ID on the server."""
    return uuid.uuid4().hex


def resolve_data_root(root=None):
    """Resolve an explicit/configured root; never store new profiles in a repo."""
    if root is None:
        root = os.environ.get('PV_DATA_ROOT')
    if root is None:
        if sys.platform == 'win32':
            base = Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local'))
        elif sys.platform == 'darwin':
            base = Path.home() / 'Library/Application Support'
        else:
            base = Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share'))
        root = base / 'pv-lab'
    path = Path(os.path.abspath(os.fspath(root)))
    if path == Path(path.anchor):
        raise ValueError('Profile root cannot be a filesystem root')
    repo = Path(__file__).absolute().parents[1]
    # Check Windows short-name aliases without using a resolved path for traversal.
    candidate = path.resolve() if os.name == 'nt' else path
    if os.name == 'nt':
        repo = repo.resolve()
    if candidate == repo or repo in candidate.parents or any(
            (parent / '.git').exists() for parent in (candidate, *candidate.parents)):
        raise ValueError('Profile root must be outside repositories')
    return path


@dataclass(frozen=True)
class ArtifactRef:
    id: str
    sha256: str
    kind: str
    size: int


@dataclass(frozen=True)
class RevisionRef:
    profile_id: str
    revision: str
    profile: HouseholdProfileV1
    artifacts: tuple[ArtifactRef, ...]


def _artifact(value):
    if isinstance(value, ArtifactRef):
        value = dict(id=value.id, sha256=value.sha256, kind=value.kind, size=value.size)
    object_fields(value, ('id', 'sha256', 'kind', 'size'))
    identifier(value['id'], 'artifact id', opaque=True)
    identifier(value['sha256'], 'artifact digest', digest=True)
    if type(value['kind']) is not str or value['kind'] not in KINDS:
        raise ValueError('Unsupported artifact kind')
    if type(value['size']) is not int or not 0 <= value['size'] <= MAX_ARTIFACT_BYTES:
        raise ValueError('Invalid artifact size')
    return ArtifactRef(**value)


class ProfileStore:
    def __init__(self, root=None):
        self.root = resolve_data_root(root)
        # Reject existing symlinks before mkdir; open each ancestor without following.
        with self._directory((), create=True):
            pass

    @contextmanager
    def _directory(self, parts, create=False):
        if os.name == 'nt':
            try:
                with windows_storage.directory(self.root, parts, create) as path:
                    yield path
            except OSError as exc:
                raise ValueError('Store path unavailable or unsafe') from exc
            return
        fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
        try:
            root_parts = self.root.parts[1:]
            for index, part in enumerate((*root_parts, *parts)):
                if part in ('', '.', '..') or '/' in part:
                    raise ValueError('Unsafe store path')
                try:
                    next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                except FileNotFoundError:
                    if not create:
                        raise
                    try:
                        os.mkdir(part, 0o700, dir_fd=fd)
                        os.fsync(fd)
                    except FileExistsError:
                        pass
                    next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = next_fd
                if index >= len(root_parts) - 1:
                    info = os.fstat(fd)
                    if info.st_uid != os.getuid() or info.st_mode & 0o077:
                        raise ValueError('Store directories must be owner-only')
            yield fd
        except OSError as exc:
            raise ValueError('Store path unavailable or unsafe') from exc
        finally:
            os.close(fd)

    @staticmethod
    def _owner(owner_id):
        return identifier(owner_id, 'owner id', opaque=True)

    @staticmethod
    @contextmanager
    def _open_read(fd, name):
        if os.name == 'nt':
            path = windows_storage.child(fd, name)
            with windows_storage.file(path), path.open('rb') as stream:
                yield stream
            return
        file_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        with os.fdopen(file_fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise ValueError('Unsafe stored file')
            yield stream

    @classmethod
    def _read(cls, fd, name, limit):
        try:
            with cls._open_read(fd, name) as stream:
                if os.fstat(stream.fileno()).st_size > limit:
                    raise ValueError('Stored file exceeds limit')
                raw = stream.read(limit + 1)
                if len(raw) > limit:
                    raise ValueError('Stored file exceeds limit')
                return raw
        except OSError as exc:
            raise ValueError('Stored file unavailable or unsafe') from exc

    @staticmethod
    def _write_once(fd, name, raw):
        """Flush a unique temp file, then publish atomically without replacement."""
        temp = '.tmp-' + new_id()
        if os.name == 'nt':
            destination = windows_storage.child(fd, name)
            temporary = windows_storage.child(fd, temp)
            stream = temporary.open('xb')
            try:
                with stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                windows_storage.move(temporary, destination)
            finally:
                temporary.unlink(missing_ok=True)
            return
        file_fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        try:
            with os.fdopen(file_fd, 'wb') as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.link(temp, name, src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
            os.fsync(fd)
        finally:
            os.unlink(temp, dir_fd=fd)

    def put_artifact(self, owner_id, content: bytes, kind) -> ArtifactRef:
        owner = self._owner(owner_id)
        if type(content) is not bytes or len(content) > MAX_ARTIFACT_BYTES:
            raise ValueError('Artifact requires bounded immutable bytes')
        ref = _artifact(dict(id=new_id(), sha256=hashlib.sha256(content).hexdigest(),
                             kind=kind, size=len(content)))
        with self._directory(('tenants', owner, 'uploads', ref.id), create=True) as fd:
            self._write_once(fd, 'content', content)
            self._write_once(fd, 'artifact.json', canonical_json(ref))
        return ref

    def _read_artifact(self, owner, ref):
        with self._directory(('tenants', owner, 'uploads', ref.id)) as fd:
            stored = _artifact(strict_json(self._read(fd, 'artifact.json', MAX_MANIFEST_BYTES)))
            raw = self._read(fd, 'content', MAX_ARTIFACT_BYTES)
        if stored != ref or len(raw) != ref.size or hashlib.sha256(raw).hexdigest() != ref.sha256:
            raise ValueError('Artifact digest/content mismatch')
        return raw

    def read_artifact(self, owner_id, ref):
        return self._read_artifact(self._owner(owner_id), _artifact(ref))

    def publish_revision(self, owner_id, profile, refs) -> RevisionRef:
        owner = self._owner(owner_id)
        # Reparse even dataclasses constructed by trusted internal callers.
        profile = parse_profile(profile_to_dict(profile))
        if not isinstance(refs, (tuple, list)) or len(refs) > 64:
            raise ValueError('Expected bounded artifact refs')
        refs = tuple(sorted((_artifact(x) for x in refs), key=lambda x: x.id))
        if len({x.id for x in refs}) != len(refs):
            raise ValueError('Duplicate artifact refs')
        for ref in refs:
            self._read_artifact(owner, ref)
        manifest = dict(schema_version=SCHEMA_VERSION, parser_version=PARSER_VERSION,
                        compiler_version=COMPILER_VERSION, model_version=MODEL_VERSION,
                        profile=profile_to_dict(profile), refs=list(refs))
        raw = canonical_json(manifest)
        if len(raw) > MAX_MANIFEST_BYTES:
            raise ValueError('Revision manifest too large')
        revision = hashlib.sha256(raw).hexdigest()
        with self._directory(('tenants', owner, 'profiles', profile.id, 'revisions', revision), create=True) as fd:
            try:
                self._write_once(fd, 'manifest.json', raw)
            except FileExistsError:
                if self._read(fd, 'manifest.json', MAX_MANIFEST_BYTES) != raw:
                    raise ValueError('Immutable revision conflict')
        return self.read_revision(owner, profile.id, revision)

    def read_revision(self, owner_id, profile_id, revision) -> RevisionRef:
        owner = self._owner(owner_id)
        identifier(profile_id, 'profile id', opaque=True)
        identifier(revision, 'revision', digest=True)
        with self._directory(('tenants', owner, 'profiles', profile_id, 'revisions', revision)) as fd:
            raw = self._read(fd, 'manifest.json', MAX_MANIFEST_BYTES)
        if hashlib.sha256(raw).hexdigest() != revision:
            raise ValueError('Revision digest/content mismatch')
        m = object_fields(strict_json(raw), ('schema_version', 'parser_version', 'compiler_version',
                                           'model_version', 'profile', 'refs'))
        if canonical_json(m) != raw:
            raise ValueError('Revision is not canonical')
        if (type(m['schema_version']) is not int or m['schema_version'] != SCHEMA_VERSION or
                (m['parser_version'], m['compiler_version'], m['model_version']) !=
                (PARSER_VERSION, COMPILER_VERSION, MODEL_VERSION)):
            raise ValueError('Unsupported revision contract versions')
        profile = parse_profile(m['profile'])
        if profile.id != profile_id:
            raise ValueError('Profile identity mismatch')
        if type(m['refs']) is not list or len(m['refs']) > 64:
            raise ValueError('Invalid revision refs')
        refs = tuple(_artifact(x) for x in m['refs'])
        if [x.id for x in refs] != sorted({x.id for x in refs}):
            raise ValueError('Unordered or duplicate revision refs')
        for ref in refs:
            self._read_artifact(owner, ref)
        return RevisionRef(profile_id, revision, profile, refs)

    def validate_ready(self, owner_id, profile_id, revision):
        """Always recheck ownership/content before validating readiness."""
        result = self.read_revision(owner_id, profile_id, revision)
        blobs = {r.id: self._read_artifact(self._owner(owner_id), r) for r in result.artifacts}
        manifests = [r for r in result.artifacts if r.kind == 'dataset_manifest']
        if len(manifests) != 1:
            raise ValueError('Draft not ready: one dataset_manifest required')
        m = strict_json(blobs[manifests[0].id])
        if type(m) is not dict or m.get('kind') != 'dataset_manifest':
            raise ValueError('Invalid dataset manifest content')
        validate_ready(result.profile, blobs)
        # Manifest component kind must agree with immutable artifact records too.
        by_id = {r.id: r for r in result.artifacts}
        for component in m['components']:
            other = by_id.get(component['artifact_id'])
            if other is None or other.kind != component['kind']:
                raise ValueError('Component kind mismatch')
