"""Personal ROC workspaces with separately connected PACER sessions."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import hashlib
import hmac
from pathlib import Path
import secrets
import shutil
import re
import time

from .common import RocError
from .connection import BrowserConnection
from .locking import ProcessLock
from .workspace import Workspace
from .users import Users


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
        self.email = ''
        self.role = ''
        self.version = 0
        self.expires = time.time() + 12 * 3600
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
        self._process_lock = ProcessLock(self.process_lock, 'This workspace is already open. Stop its existing ROC process before reopening.').acquire()
        self.lock = threading.RLock()
        self.close_lock = threading.Lock()
        self.pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix='roc-account-login')
        self.browsers = {}
        self.workspaces = {}
        self.guests = []
        self.ai_credentials = ai_credentials
        self.users = Users(self.root)
        self.reset_sender = None
        self.stopping = self.closed = self.lock_released = False

    def _workspace(self, context):
        if context.owner:
            return self.workspaces[context.owner]
        return context.guest

    def _check(self):
        if self.stopping:
            raise RocError('ROC is stopping. Reopen it before signing in.')

    def _context(self, cookie):
        context = self.browsers.get(cookie)
        if context and context.owner:
            user = self.users.get(context.owner)
            if not user or user['version'] != context.version or time.time() > context.expires:
                # Do not invalidate an in-flight request's own session object.
                self.browsers.pop(cookie, None)
                return None
        return context

    def user_sign_in(self, cookie, values, create=False, expected_view=None):
        with self.lock:
            self._check()
            self._check_view(cookie, expected_view)
            old = self._context(cookie)
            self._idle(old)
            allowed = {'email', 'password', 'confirmation'} if create else {'email', 'password'}
            if set(values) - allowed:
                raise RocError('Unexpected account fields.')
            user = self.users.create(values) if create else self.users.login(values)
            if user['id'] not in self.workspaces:
                self.workspaces[user['id']] = Workspace(self.root / 'users' / user['id'], ai_credentials=self.ai_credentials)
            if old:
                old.connection.disconnect()
                self.browsers.pop(cookie, None)
            new_cookie, context = self._new_context()
            context.owner, context.email, context.role, context.version = user['id'], user['email'], user['role'], user['version']
            return new_cookie

    def sign_out(self, cookie, expected_view=None):
        with self.lock:
            self._check_view(cookie, expected_view)
            old = self._context(cookie)
            self._idle(old)
            if old:
                old.connection.disconnect()
            self.browsers.pop(cookie, None)
            return self._new_context()[0]

    def reset_password(self, values):
        with self.lock:
            owner = self.users.reset(values)
            if owner in self.workspaces:
                self.workspaces[owner].pause_event.set()
            for cookie, context in list(self.browsers.items()):
                if context.owner == owner:
                    context.connection.stop()
                    self.browsers.pop(cookie,None)

    def import_legacy(self, cookie, expected_view=None):
        """Explicit owner-only copy after PACER verification; never move/delete history."""
        with self.lock:
            self._check_view(cookie, expected_view)
            context = self._context(cookie)
            if not context or context.role != 'owner' or not context.connection.status()['connected']:
                raise RocError('The ROC owner must connect the original PACER account before importing its old workspace.')
            self._idle(context)
            source = self.root / 'accounts' / account_key(self.root, context.username)
            target = self._workspace(context).root
            count = 0
            for manifest in source.glob('*/workspace.json'):
                identity = manifest.parent.name
                if not re.fullmatch('[a-f0-9]{32}', identity) or (target / identity).exists():
                    continue
                temp = target / ('import-' + identity)
                if temp.exists():
                    raise RocError('An earlier import was interrupted. Its files are preserved; ask the owner to inspect it.')
                shutil.copytree(manifest.parent, temp, ignore=shutil.ignore_patterns('.run.lock', '*.tmp'))
                temp.rename(target / identity)
                count += 1
            if (source/'name-rules.json').is_file() and not (target/'name-rules.json').exists():
                shutil.copy2(source/'name-rules.json',target/'name-rules.json')
            return {'imported': count}

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
            context = self._context(cookie)
            if not context or not context.owner:
                raise RocError('Sign in to ROC before connecting PACER.')
            self._idle(context)
            fields = context.connection.prepare(values)
            username = fields['username']
            context.authenticating = True
            context.future = self.pool.submit(self._authenticate, context, username, fields)
            return cookie

    def _authenticate(self, context, username, fields):
        context.connection.authenticate(fields)
        with self.lock:
            context.authenticating = False
            if self.stopping or context not in self.browsers.values():
                context.connection.stop()
                return
            if not context.connection.status()['connected']:
                return
            context.username = username

    def disconnect(self, cookie, expected_view=None):
        with self.lock:
            self._check_view(cookie, expected_view)
            context = self._context(cookie)
            self._idle(context)
            if context:
                context.connection.disconnect()
                context.username = ''
            return cookie

    def status(self, cookie):
        with self.lock:
            context = self._context(cookie)
            state = context.connection.status() if context else BrowserConnection().status()
            if context and context.authenticating:
                state.update(connecting=True, connected=False)
            return {**state, 'signedIn': bool(context and context.owner),
                    'username': context.username if context and context.owner else '',
                    'email': context.email if context else '', 'role': context.role if context else '',
                    'personalAccounts': True, 'recoveryAvailable': self.reset_sender is not None,
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
                raise RocError('Sign in to ROC to access your saved searches.')
            if mutating and context.authenticating:
                raise RocError('Wait for PACER sign-in to finish.')
            ws = self._workspace(context)
            if not ws:
                raise RocError('Sign in to ROC to access your saved searches.')
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
                self._process_lock.release()
                self.lock_released = True
