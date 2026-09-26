"""Process-owned OS lock complements server generations after a parent crash.

There is no timeout-based takeover. The OS releases this lock only when its
owning worker closes the handle or exits, including across server restarts.
"""
import hashlib
import os
from pathlib import Path
import tempfile


class DeviceProcessLock:
    def __init__(self, identity, directory=None):
        directory = Path(directory or tempfile.gettempdir()) / 'oas-solana-device-locks'
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / (hashlib.sha256(identity.encode()).hexdigest() + '.lock')
        self.handle = None

    def acquire(self):
        if self.handle is not None:
            return True
        handle = self.path.open('a+b')
        try:
            if os.fstat(handle.fileno()).st_size == 0:
                handle.write(b'0')
                handle.flush()
            handle.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        self.handle = handle
        return True

    def release(self):
        if self.handle is None:
            return
        handle, self.handle = self.handle, None
        try:
            handle.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
