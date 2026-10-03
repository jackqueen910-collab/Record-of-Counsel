"""Process-held locks, with conservative compatibility for older PID markers.

The guard file is permanent: unlinking a POSIX locked file permits two owners
of different inodes. The OS releases the guard on exit, including crashes.
"""
import json
import os
from pathlib import Path

from .common import RocError, write_json


def process_may_exist(pid):
    if pid <= 0:
        return True
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return ctypes.get_last_error() != 87  # Invalid PID; access denied is not proof of death.
        try:
            code = wintypes.DWORD()
            return not kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


class ProcessLock:
    def __init__(self, path, message):
        self.path = Path(path)
        self.message = message
        self.stream = None

    def acquire(self):
        guard = self.path.with_name(self.path.name + '.guard')
        stream = guard.open('a+b')
        try:
            if os.name == 'nt':
                import msvcrt
                # Windows byte-range locks may extend beyond EOF.
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            stream.close()
            raise RocError(self.message) from None
        try:
            if self.path.exists():
                raw = self.path.read_text(encoding='utf-8').strip()
                try:
                    owner = json.loads(raw)
                except ValueError:
                    owner = None
                if isinstance(owner, int):
                    if process_may_exist(owner):
                        raise RocError(self.message)
                elif not isinstance(owner, dict) or owner.get('protocol') != 'roc-os-lock-v1':
                    raise RocError('Unrecognized lock marker; confirm its owner before repairing it.')
            write_json(self.path, {'protocol': 'roc-os-lock-v1', 'pid': os.getpid()})
        except BaseException:
            stream.close()
            raise
        self.stream = stream
        return self

    def release(self):
        if self.stream is not None:
            try:
                self.path.unlink(missing_ok=True)
            finally:
                self.stream.close()
                self.stream = None
