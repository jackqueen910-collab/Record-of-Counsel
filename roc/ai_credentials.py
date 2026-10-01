"""Owner-managed ROC account credentials; no browser or repository secrets."""
import ctypes
import os

from .common import RocError

ENV_NAME = 'ROC_ANTHROPIC_API_KEY'
TARGET = 'RecordOfCounsel/Anthropic'
MAX_KEY_BYTES = 2048


def validate_key(key):
    if (not isinstance(key, str) or not key or not key.isascii()
            or len(key) > MAX_KEY_BYTES or any(ord(c) < 33 or ord(c) > 126 for c in key)):
        raise RocError('The ROC account key is invalid. Ask the owner to update AI setup.')
    return key


class _FileTime(ctypes.Structure):
    _fields_ = [('low', ctypes.c_uint32), ('high', ctypes.c_uint32)]


class _Credential(ctypes.Structure):
    _fields_ = [('Flags', ctypes.c_uint32), ('Type', ctypes.c_uint32),
                ('TargetName', ctypes.c_wchar_p), ('Comment', ctypes.c_wchar_p),
                ('LastWritten', _FileTime), ('CredentialBlobSize', ctypes.c_uint32),
                ('CredentialBlob', ctypes.POINTER(ctypes.c_ubyte)), ('Persist', ctypes.c_uint32),
                ('AttributeCount', ctypes.c_uint32), ('Attributes', ctypes.c_void_p),
                ('TargetAlias', ctypes.c_wchar_p), ('UserName', ctypes.c_wchar_p)]


class WindowsCredentialStore:
    """One generic credential for the current Windows user, persisted locally.

    Official API contract: https://learn.microsoft.com/windows/win32/api/wincred/
    The optional target supports isolated tests; production always uses TARGET.
    """
    def __init__(self, target=TARGET):
        if os.name != 'nt':
            raise RocError('Owner setup uses Windows Credential Manager. Other hosts can configure ROC_ANTHROPIC_API_KEY on the backend.')
        self.target = target
        self.api = ctypes.WinDLL('Advapi32.dll', use_last_error=True)
        pointer = ctypes.POINTER(_Credential)
        self.api.CredReadW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.POINTER(pointer)]
        self.api.CredReadW.restype = ctypes.c_int
        self.api.CredWriteW.argtypes = [pointer, ctypes.c_uint32]
        self.api.CredWriteW.restype = ctypes.c_int
        self.api.CredDeleteW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32]
        self.api.CredDeleteW.restype = ctypes.c_int
        self.api.CredFree.argtypes = [ctypes.c_void_p]
        self.api.CredFree.restype = None

    def read(self):
        pointer = ctypes.POINTER(_Credential)()
        if not self.api.CredReadW(self.target, 1, 0, ctypes.byref(pointer)):
            if ctypes.get_last_error() == 1168:  # ERROR_NOT_FOUND
                return ''
            raise RocError('ROC could not read its Windows credential. Ask the owner to check AI setup.')
        try:
            value = pointer.contents
            if not value.CredentialBlob or not 0 < value.CredentialBlobSize <= MAX_KEY_BYTES:
                raise RocError('The saved ROC credential is invalid. Ask the owner to update AI setup.')
            raw = ctypes.string_at(value.CredentialBlob, value.CredentialBlobSize)
            try:
                return validate_key(raw.decode('ascii'))
            except UnicodeDecodeError:
                raise RocError('The saved ROC credential is invalid. Ask the owner to update AI setup.') from None
        finally:
            self.api.CredFree(pointer)

    def save(self, key):
        raw = validate_key(key).encode('ascii')
        blob = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
        value = _Credential(Type=1, TargetName=self.target, Comment='ROC shared Anthropic account',
                            CredentialBlobSize=len(raw), CredentialBlob=blob,
                            Persist=2, UserName='ROC')  # CRED_PERSIST_LOCAL_MACHINE, current user only.
        if not self.api.CredWriteW(ctypes.byref(value), 0):
            raise RocError('Windows could not save the ROC credential. No plaintext fallback was written.')

    def remove(self):
        if not self.api.CredDeleteW(self.target, 1, 0) and ctypes.get_last_error() != 1168:
            raise RocError('Windows could not remove the ROC credential.')


class ProjectAIKey:
    def __init__(self, store=None, environ=None):
        self.store = store
        self.environ = os.environ if environ is None else environ

    def load(self):
        # Only the explicitly ROC-scoped setting can override the vault. Never
        # silently borrow a generic Anthropic key belonging to another project.
        if ENV_NAME in self.environ:
            return validate_key(self.environ[ENV_NAME])
        store = self.store
        if store is None:
            if os.name != 'nt':
                return ''
            store = WindowsCredentialStore()
        key = store.read()
        return validate_key(key) if key else ''

    def require(self):
        key = self.load()
        if not key:
            raise RocError('ROC account setup is needed. Ask the owner to configure AI once; no analysis was submitted.')
        return key

    def status(self):
        try:
            configured = bool(self.load())
            return {'configured': configured, 'connectionMessage':
                    'ROC account configured. Validated on first analysis.' if configured else
                    'AI setup needed. The ROC owner must configure the shared account once.'}
        except RocError as exc:
            return {'configured': False, 'connectionMessage': str(exc)}
