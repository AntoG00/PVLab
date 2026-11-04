"""Exclusive locks on persistent sidecar files."""
from contextlib import contextmanager
import os
from pathlib import Path

if os.name == 'nt':
    import errno
    import msvcrt
    import time
else:
    import fcntl


@contextmanager
def file_lock(path, thread_lock):
    lock_path = Path(str(path) + '.lock')
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with thread_lock, lock_path.open('a+b') as lock_file:
        with exclusive_lock(lock_file.fileno()):
            yield


@contextmanager
def exclusive_lock(fd):
    """Lock an open sidecar descriptor until the context exits."""
    if os.name == 'nt':
        # Windows can lock a byte past EOF, so the sidecar need not be changed.
        os.lseek(fd, 0, os.SEEK_SET)
        while True:
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                break
            except InterruptedError:
                continue
            except OSError as exc:
                if exc.errno != errno.EACCES:
                    raise
                time.sleep(0.05)
        try:
            yield
        finally:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
