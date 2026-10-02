"""PACER-verified browser identities and isolated local account workspaces.

PACER passwords/tokens and browser identities stay in memory. Only an HMAC
of the successfully authenticated username names a persistent account folder.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import hashlib
import hmac
import os
from pathlib import Path
import secrets

from .common import RocError
from .connection import BrowserConnection
from .workspace import Workspace


def account_key(root, username):
    username = username.strip()
    if not username:
        raise RocError('A PACER username is required.')
    path = Path(root) / 'account-identity.key'
    try:
        with path.open('xb') as stream:
            stream.write(secrets.token_bytes(32))
    except FileExistsError:
        pass
    key = path.read_bytes()
    if len(key) != 32:
        raise RocError('ROC account identity storage needs repair. Existing searches were not changed.')
    # PACER does not return a stable account ID or documented canonical username.
    # Never merge case variants on an unverified assumption about account identity.
    return hmac.new(key, username.encode('utf-8'), hashlib.sha256).hexdigest()


class BrowserAccount:
    def __init__(self):
        self.connection = BrowserConnection()
        self.owner = ''
        self.username = ''
        self.guest = None
        self.future = None
        self.authenticating = False
        self.view = secrets.token_urlsafe(18)


class Accounts:
    def __init__(self, root, ai_credentials=None):
        import threading
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.process_lock = self.root / '.workspace.lock'
        try:
            fd = os.open(self.process_lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, 'w') as stream:
                stream.write(str(os.getpid()))
        except FileExistsError:
            raise RocError('This workspace is already open. Stop its existing ROC process before reopening.') from None
        self.lock = threading.RLock()
        self.close_lock = threading.Lock()
        self.pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix='roc-account-login')
        self.browsers = {}
        self.workspaces = {}
        self.guests = []
        self.ai_credentials = ai_credentials
        self.stopping = self.closed = self.lock_released = False

    def _workspace(self, context):
        if context.owner:
            return self.workspaces[context.owner]
        return context.guest

    def _check(self):
        if self.stopping:
            raise RocError('ROC is stopping. Reopen it before signing in.')

    def _context(self, cookie):
        return self.browsers.get(cookie)

    def _new_context(self):
        key = secrets.token_urlsafe(32)
        context = BrowserAccount()
        self.browsers[key] = context
        return key, context

    def _idle(self, context):
        if context:
            if context.authenticating:
                raise RocError('Wait for the current sign-in attempt to finish.')
            ws = self._workspace(context)
            if ws:
                with ws.lock:
                    ws.idle()

    def _check_view(self, cookie, expected):
        if expected is not None and not secrets.compare_digest(expected, self.status(cookie)['viewId']):
            raise RocError('The account changed in another tab. Refresh before continuing.')

    def sign_in(self, cookie, values, expected_view=None):
        with self.lock:
            self._check()
            self._check_view(cookie, expected_view)
            old = self._context(cookie)
            self._idle(old)
            context = BrowserAccount()
            fields = context.connection.prepare(values)
            username = fields['username']
            # A reconnect preserves the existing ROC identity even if PACER is
            # unavailable. Switching usernames hides the old account immediately.
            if old and old.owner and old.username == username:
                context.owner, context.username, context.view = old.owner, old.username, old.view
            if old:
                old.connection.disconnect()
                self.browsers.pop(cookie, None)
            new_cookie = secrets.token_urlsafe(32)
            self.browsers[new_cookie] = context
            context.authenticating = True
            context.future = self.pool.submit(self._authenticate, context, username, fields)
            return new_cookie

    def _authenticate(self, context, username, fields):
        context.connection.authenticate(fields)
        with self.lock:
            context.authenticating = False
            if self.stopping or context not in self.browsers.values():
                context.connection.stop()
                return
            if not context.connection.status()['connected']:
                return
            try:
                owner = account_key(self.root, username)
                if owner not in self.workspaces:
                    self.workspaces[owner] = Workspace(self.root / 'accounts' / owner, ai_credentials=self.ai_credentials)
                if context.owner != owner:
                    context.view = secrets.token_urlsafe(18)
                context.owner, context.username = owner, username
            except Exception:
                context.connection.disconnect()
                context.connection.message = 'PACER accepted sign-in, but ROC could not open this account’s saved searches. No search was submitted.'

    def disconnect(self, cookie, expected_view=None):
        with self.lock:
            self._check_view(cookie, expected_view)
            context = self._context(cookie)
            self._idle(context)
            if context:
                context.connection.disconnect()
                self.browsers.pop(cookie, None)
            return self._new_context()[0]

    def status(self, cookie):
        with self.lock:
            context = self._context(cookie)
            state = context.connection.status() if context else BrowserConnection().status()
            if context and context.authenticating:
                state.update(connecting=True, connected=False)
            return {**state, 'signedIn': bool(context and context.owner),
                    'username': context.username if context and context.owner else '',
                    'viewId': context.view if context else 'signed-out',
                    'accountMode': True}

    def list(self, cookie):
        with self.lock:
            context = self._context(cookie)
            ws = self._workspace(context) if context else None
            state = ws.list() if ws else {'active': None, 'jobs': [], 'nameRulesRevision': None,
                'docketBudgetVersion': 2, 'documentGrabberVersion': 3, 'clientReportVersion': 1}
            return state | {'connection': self.status(cookie), 'stopping': self.stopping, 'closed': self.closed,
                            'accountWorkspaceVersion': 1, 'searchModesVersion': 1, 'additionalNamesVersion': 1}

    @contextmanager
    def scope(self, cookie, mutating=False, require_account=False, expected_view=None):
        # Freeze the browser-to-account binding for the complete request. A
        # concurrent sign-out/switch cannot redirect it to another workspace.
        with self.lock:
            self._check_view(cookie, expected_view)
            context = self._context(cookie)
            if not context or (require_account and not context.owner):
                raise RocError('Sign in with PACER to access your saved searches.')
            if mutating and context.authenticating:
                raise RocError('Wait for PACER sign-in to finish.')
            ws = self._workspace(context)
            if not ws:
                raise RocError('Sign in with PACER to access your saved searches.')
            with ws.lock:
                # Each operation captures this browser's own PACER connection.
                # Sharing history never swaps an in-flight request's credentials.
                ws.provider = context.connection.get
                yield ws

    def demo(self, cookie, expected_view=None):
        with self.lock:
            self._check()
            self._check_view(cookie, expected_view)
            context = self._context(cookie)
            if not context:
                cookie, context = self._new_context()
            self._idle(context)
            if not context.owner and not context.guest:
                context.guest = Workspace(self.root / 'guests' / secrets.token_hex(16), ai_credentials=self.ai_credentials)
                self.guests.append(context.guest)
            with self.scope(cookie, mutating=True) as ws:
                identifier = ws.new(demo=True)
            return cookie, identifier

    def request_stop(self):
        with self.lock:
            self.stopping = True
            for context in self.browsers.values():
                context.connection.stop()
            for ws in [*self.workspaces.values(), *self.guests]:
                ws.request_stop()

    def close(self, release_lock=True):
        with self.close_lock:
            if not self.closed:
                self.request_stop()
                self.pool.shutdown(wait=True)
                for ws in [*self.workspaces.values(), *self.guests]:
                    ws.close()
                self.closed = True
            if release_lock and not self.lock_released:
                self.process_lock.unlink(missing_ok=True)
                self.lock_released = True
