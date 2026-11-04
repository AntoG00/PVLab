"""Win32 primitives for private household storage on local ACL-capable volumes."""
from contextlib import ExitStack, contextmanager
import ctypes as c
from ctypes import wintypes as w
import os
from pathlib import PureWindowsPath
import stat


def _api(dll, name, result, *args):
    function = getattr(dll, name)
    function.restype = result
    function.argtypes = args
    return function


_kernel = c.WinDLL('kernel32', use_last_error=True)
_security = c.WinDLL('advapi32', use_last_error=True)
P = c.c_void_p
PP = c.POINTER(P)
_close = _api(_kernel, 'CloseHandle', w.BOOL, w.HANDLE)
_free = _api(_kernel, 'LocalFree', P, P)
_process = _api(_kernel, 'GetCurrentProcess', w.HANDLE)
_open_token = _api(_security, 'OpenProcessToken', w.BOOL, w.HANDLE, w.DWORD, PP)
_token_info = _api(_security, 'GetTokenInformation', w.BOOL,
                   w.HANDLE, c.c_int, P, w.DWORD, c.POINTER(w.DWORD))
_sid_string = _api(_security, 'ConvertSidToStringSidW', w.BOOL, P, PP)
_equal_sid = _api(_security, 'EqualSid', w.BOOL, P, P)
_get_security = _api(_security, 'GetSecurityInfo', w.DWORD,
                     w.HANDLE, c.c_int, w.DWORD, PP, PP, PP, PP, PP)
_get_ace = _api(_security, 'GetAce', w.BOOL, P, w.DWORD, PP)
_parse_descriptor = _api(_security, 'ConvertStringSecurityDescriptorToSecurityDescriptorW',
                         w.BOOL, w.LPCWSTR, w.DWORD, PP, P)
_create_directory = _api(_kernel, 'CreateDirectoryW', w.BOOL, w.LPCWSTR, P)
_create_file = _api(_kernel, 'CreateFileW', w.HANDLE,
                    w.LPCWSTR, w.DWORD, w.DWORD, P, w.DWORD, w.DWORD, w.HANDLE)
_move_file = _api(_kernel, 'MoveFileExW', w.BOOL, w.LPCWSTR, w.LPCWSTR, w.DWORD)
_drive_type = _api(_kernel, 'GetDriveTypeW', w.UINT, w.LPCWSTR)
_query_device = _api(_kernel, 'QueryDosDeviceW', w.DWORD, w.LPCWSTR, w.LPWSTR, w.DWORD)

READ_CONTROL = 0x20000
READ_ATTRIBUTES = 0x80
READ_DATA_OR_LIST_DIRECTORY = 1
# FILE_WRITE_DATA, FILE_WRITE_ATTRIBUTES, WRITE_DAC, WRITE_OWNER, GENERIC_WRITE and GENERIC_ALL
ANCESTOR_REPARSE_ACCESS = 0x500c0102
ADMINISTRATORS = 'S-1-5-32-544'
SYSTEM_OWNERS = {
    'S-1-5-18',  # Local System
    ADMINISTRATORS,
    'S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464',  # TrustedInstaller
}
SHARE_READ, SHARE_WRITE, SHARE_DELETE = 1, 2, 4
OPEN_EXISTING = 3
OPEN_REPARSE_POINT = 0x00200000
BACKUP_SEMANTICS = 0x02000000
OWNER_AND_DACL = 5
FILE_ALL_ACCESS = 0x1f01ff
INHERIT_TO_FILES_AND_DIRS = 3


class _FileInfo(c.Structure):
    _fields_ = [('attributes', w.DWORD), ('created', w.FILETIME),
                ('accessed', w.FILETIME), ('modified', w.FILETIME),
                ('volume', w.DWORD), ('size_high', w.DWORD), ('size_low', w.DWORD),
                ('links', w.DWORD), ('index_high', w.DWORD), ('index_low', w.DWORD)]


_file_info = _api(_kernel, 'GetFileInformationByHandle', w.BOOL,
                  w.HANDLE, c.POINTER(_FileInfo))


class _SecurityAttributes(c.Structure):
    _fields_ = [('length', w.DWORD), ('descriptor', P), ('inherit_handle', w.BOOL)]


class _Acl(c.Structure):
    _fields_ = [('revision', w.BYTE), ('reserved', w.BYTE), ('size', w.WORD),
                ('ace_count', w.WORD), ('reserved2', w.WORD)]


class _AllowAce(c.Structure):
    _fields_ = [('type', w.BYTE), ('flags', w.BYTE), ('size', w.WORD),
                ('mask', w.DWORD), ('sid_start', w.DWORD)]


def _check(result):
    if not result:
        raise c.WinError(c.get_last_error())


@contextmanager
def _user_sid():
    token = P()
    _check(_open_token(_process(), 8, c.byref(token)))  # TOKEN_QUERY
    try:
        size = w.DWORD()
        _token_info(token, 1, None, 0, c.byref(size))  # TokenUser
        if not size.value:
            raise c.WinError(c.get_last_error())
        buffer = c.create_string_buffer(size.value)
        _check(_token_info(token, 1, buffer, size, c.byref(size)))
        yield c.cast(buffer, PP)[0]  # TOKEN_USER starts with the SID pointer.
    finally:
        _close(token)


def _sid_text(sid):
    text = P()
    _check(_sid_string(sid, c.byref(text)))
    try:
        return c.wstring_at(text)
    finally:
        _free(text)


def native_path(path):
    """Use the extended local path syntax for Win32 filesystem calls."""
    value = os.path.abspath(path)
    if value.startswith('\\\\?\\') or value.startswith('\\\\'):
        return value
    return '\\\\?\\' + value


def _private_mkdir(path, sid):
    user = _sid_text(sid)
    descriptor = P()
    try:
        # A protected DACL applies at creation, including when the parent is shared.
        _check(_parse_descriptor(f'O:{user}D:P(A;OICI;FA;;;{user})', 1,
                                 c.byref(descriptor), None))
        attributes = _SecurityAttributes(c.sizeof(_SecurityAttributes), descriptor, False)
        _check(_create_directory(native_path(path), c.byref(attributes)))
    finally:
        _free(descriptor)


def mkdir_private(path):
    """Create one new directory with an owner-only inheritable ACL."""
    with _user_sid() as sid:
        _private_mkdir(path, sid)


def _check_access(handle, sid, *, directory, private):
    owner, acl, descriptor = P(), P(), P()
    error = _get_security(handle, 1, OWNER_AND_DACL, c.byref(owner), None,
                          c.byref(acl), None, c.byref(descriptor))  # SE_FILE_OBJECT
    if error:
        raise c.WinError(error)
    try:
        if not owner or not acl:
            raise ValueError('Unsafe store ownership or permissions')
        if not _equal_sid(owner, sid):
            owner_sid = _sid_text(owner)
            # Elevated tokens can assign ownership of newly created files to
            # Administrators. The file DACL must still grant only the user SID.
            if private:
                trusted_owner = not directory and owner_sid == ADMINISTRATORS
            else:
                trusted_owner = owner_sid in SYSTEM_OWNERS
            if not trusted_owner:
                raise ValueError('Unsafe store ownership or permissions')
        inheritable = False
        for index in range(_Acl.from_address(acl.value).ace_count):
            entry = P()
            _check(_get_ace(acl, index, c.byref(entry)))
            ace = _AllowAce.from_address(entry.value)
            if not private and (ace.type == 1 or ace.flags & 8):  # Deny or inherit-only
                continue
            if ace.type != 0:
                raise ValueError('Unsupported store access rule')
            trustee = entry.value + _AllowAce.sid_start.offset
            current_user = _equal_sid(trustee, sid)
            if private:
                if not current_user:
                    raise ValueError('Store objects must be owner-only')
                if ace.flags & 0x0f == INHERIT_TO_FILES_AND_DIRS and ace.mask == FILE_ALL_ACCESS:
                    inheritable = True
            elif (ace.mask & ANCESTOR_REPARSE_ACCESS and not current_user
                  and _sid_text(trustee) not in SYSTEM_OWNERS | {'S-1-3-4'}):
                # Attribute writes can set reparse points even on a pinned directory.
                # OWNER RIGHTS applies only to the already validated owner.
                raise ValueError('Unsafe store ancestor permissions')
        if private and directory and not inheritable:
            raise ValueError('Store directories must pass owner-only access to new children')
    finally:
        _free(descriptor)


def child(directory, name):
    # Win32 normalizes dots/spaces, recognizes device names and supports ADS.
    if (not name or name[-1] in ' .' or any(ch in name for ch in '\\/:*?"<>|\0')
            or PureWindowsPath(name).is_reserved()):
        raise ValueError('Unsafe store path')
    return directory / name


@contextmanager
def _open(path, *, directory, sid, private=True, mutable=False):
    share = SHARE_READ
    if directory is not False:
        share |= SHARE_WRITE  # Child creation and rename need directory write sharing.
    if mutable:
        share |= SHARE_WRITE | SHARE_DELETE
    access = READ_CONTROL | READ_ATTRIBUTES
    if not mutable:
        access |= READ_DATA_OR_LIST_DIRECTORY  # Participate in sharing checks.
    handle = _create_file(native_path(path), access, share, None, OPEN_EXISTING,
                          OPEN_REPARSE_POINT | BACKUP_SEMANTICS, None)
    if handle == P(-1).value:
        raise c.WinError(c.get_last_error())
    try:
        info = _FileInfo()
        _check(_file_info(handle, c.byref(info)))
        is_directory = bool(info.attributes & stat.FILE_ATTRIBUTE_DIRECTORY)
        if (info.attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT
                or (directory is not None and is_directory != directory)
                or (not is_directory and info.links != 1)):
            raise ValueError('Unsafe store path')
        _check_access(handle, sid, directory=is_directory, private=private)
        yield info
    finally:
        _close(handle)


@contextmanager
def directory(root, parts, create=False):
    """Pin every traversed directory against renames until the operation ends."""
    path = type(root)(root.anchor)
    if len(root.drive) != 2 or root.drive[1] != ':' or _drive_type(str(path)) not in (2, 3):
        raise ValueError('Store requires a local drive')
    device = c.create_unicode_buffer(32768)
    _check(_query_device(root.drive, device, len(device)))
    if not device.value.startswith('\\Device\\'):
        raise ValueError('Store requires a directly mounted drive')
    with _user_sid() as sid, ExitStack() as handles:
        handles.enter_context(_open(path, directory=True, sid=sid, private=False))
        for index, name in enumerate((*root.parts[1:], *parts), 1):
            path = child(path, name)
            private = index >= len(root.parts) - 1
            try:
                handles.enter_context(_open(path, directory=True, sid=sid, private=private))
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    _private_mkdir(path, sid)
                except FileExistsError:
                    pass
                handles.enter_context(_open(path, directory=True, sid=sid, private=private))
        yield type(root)(native_path(path))


@contextmanager
def file(path, *, mutable=False):
    with _user_sid() as sid, _open(path, directory=False, sid=sid, mutable=mutable):
        yield


def move(source, destination, *, replace=False):
    # Same-directory rename with write-through and optional replacement.
    flags = 8 | int(replace)  # MOVEFILE_WRITE_THROUGH | MOVEFILE_REPLACE_EXISTING
    _check(_move_file(native_path(source), native_path(destination), flags))


def remove_tree(path):
    """Remove entries from a pinned private tree, rejecting every reparse tag."""
    with _user_sid() as sid:
        _remove_tree(type(path)(native_path(path)), sid)


def _remove_tree(path, sid):
    with _open(path, directory=None, sid=sid) as info:
        directory = info.attributes & stat.FILE_ATTRIBUTE_DIRECTORY
        if directory:
            for entry in path.iterdir():
                try:
                    _remove_tree(child(path, entry.name), sid)
                except FileNotFoundError:
                    continue
    if directory:
        path.rmdir()
    else:
        if info.attributes & stat.FILE_ATTRIBUTE_READONLY:
            path.chmod(stat.S_IWRITE)
        path.unlink()
