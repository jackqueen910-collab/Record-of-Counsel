"""Production account routing; all authentication/search responses are synthetic."""
import contextlib
import http.client
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

from roc.accounts import Accounts, account_key
from roc.common import RocError
from roc.interface import make_server
from roc.pacer import Session, SignInError
from tests.test_browser_connection import credentials
from tests.test_workspace import form, record, response


def fake_login(username, password, otp, client_code, redact):
    if password == 'wrong':
        raise SignInError('13')
    return Session('synthetic-token-' + username, client_code, requester=lambda *args: response([record()]))


class AccountTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        output = contextlib.redirect_stdout(io.StringIO())
        output.__enter__()
        self.addCleanup(output.__exit__, None, None, None)
        login = patch.object(Session, 'login', side_effect=fake_login)
        self.login = login.start()
        self.addCleanup(login.stop)
        self.hub = Accounts(self.temp.name)
        self.addCleanup(self.hub.close)

    def sign_in(self, username, cookie='', **kw):
        cookie = self.hub.sign_in(cookie, credentials(username=username, **kw))
        self.hub.browsers[cookie].future.result(10)
        return cookie

    def new(self, cookie):
        with self.hub.scope(cookie, require_account=True) as ws:
            identifier = ws.new(form())
        ws.future.result(15)
        self.assertEqual(ws.summary(identifier)['state'], 'ready')
        return identifier, ws

    def test_verified_identity_owns_history_and_restart_requires_signin(self):
        a = self.sign_in('alice')
        identifier, ws = self.new(a)
        b = self.sign_in('bob')
        self.assertEqual(self.hub.list('')['jobs'], [])
        self.assertEqual(self.hub.list(b)['jobs'], [])
        with self.hub.scope(b) as bws, self.assertRaises(RocError):
            bws.summary(identifier)
        self.hub.close()
        self.hub = Accounts(self.temp.name)
        self.addCleanup(self.hub.close)
        self.assertEqual(self.hub.list(a)['jobs'], [])  # Process restart forgets every cookie.
        a2 = self.sign_in('alice')
        self.assertEqual([j['id'] for j in self.hub.list(a2)['jobs']], [identifier])
        self.assertEqual(ws.root.name, account_key(self.temp.name, 'alice'))
        self.assertNotEqual(account_key(self.temp.name, 'Alice'), ws.root.name)
        saved = ''.join(p.read_text(encoding='utf-8') for p in Path(self.temp.name).rglob('*.json'))
        for private in ('alice', 'test-password-only', '987654', 'synthetic-token-alice'):
            self.assertNotIn(private, saved)

    def test_expiry_and_failed_reconnect_keep_verified_history_but_switch_failure_clears_it(self):
        a = self.sign_in('alice')
        identifier, ws = self.new(a)
        self.hub.browsers[a].connection.session.usable = False
        self.assertTrue(self.hub.status(a)['signedIn'])
        self.assertFalse(self.hub.status(a)['connected'])
        old_view = self.hub.status(a)['viewId']
        retry = self.sign_in('alice', a, password='wrong')
        self.assertEqual(self.hub.status(retry)['viewId'], old_view)
        self.assertTrue(self.hub.status(retry)['signedIn'])
        self.assertEqual(len(self.hub.list(retry)['jobs']), 1)
        self.assertEqual(self.hub.list(a)['jobs'], [])
        switched = self.sign_in('bob', retry, password='wrong')
        self.assertFalse(self.hub.status(switched)['signedIn'])
        self.assertEqual(self.hub.list(switched)['jobs'], [])
        self.assertEqual(self.hub.list(retry)['jobs'], [])
        self.assertEqual(self.login.call_count, 3)
        self.assertEqual(len(self.hub.workspaces), 1)

    def test_same_account_shares_history_without_swapping_inflight_provider(self):
        a = self.sign_in('shared', clientCode='first-client')
        b = self.sign_in('shared', clientCode='second-client')
        entered, release = threading.Event(), threading.Event()
        seen = []
        from roc.workspace import run
        def blocked_run(*args, **kw):
            entered.set()
            if not release.wait(10):
                raise AssertionError('Timed out waiting for second browser')
            seen.append(kw['session_provider']().client_code)
            return run(*args, **kw)
        with patch('roc.workspace.run', side_effect=blocked_run):
            with self.hub.scope(a, require_account=True) as ws:
                identifier = ws.new(form())
            self.assertTrue(entered.wait(10))
            try:
                with self.hub.scope(b) as bws:
                    self.assertIs(ws, bws)
                    self.assertEqual(bws.provider().client_code, 'second-client')
                with self.assertRaises(RocError):
                    self.hub.disconnect(a)  # Let the in-flight request settle first.
            finally:
                release.set()
            ws.future.result(15)
        self.assertEqual(seen, ['first-client'])
        self.assertEqual(self.hub.list(b)['jobs'][0]['id'], identifier)
        anonymous = self.hub.disconnect(a)
        self.assertEqual(self.hub.list(anonymous)['jobs'], [])
        self.assertTrue(self.hub.status(b)['connected'])
        self.assertEqual(len(self.hub.list(b)['jobs']), 1)

    def test_views_cookie_rotation_and_anonymous_demo_are_isolated(self):
        guest, identifier = self.hub.demo('')
        self.hub.browsers[guest].guest.future.result(15)
        self.assertEqual(self.login.call_count, 0)
        self.assertEqual(self.hub.list('')['jobs'], [])
        a = self.sign_in('alice', guest)
        self.assertEqual(self.hub.list(a)['jobs'], [])  # Guest work is never auto-assigned.
        view = self.hub.status(a)['viewId']
        new_cookie = self.hub.disconnect(a, view)
        with self.assertRaisesRegex(RocError, 'account changed'):
            self.hub.sign_in(new_cookie, credentials(), view)
        with self.assertRaises(RocError):
            with self.hub.scope(a):
                self.fail('Revoked browser cookie was accepted')

    def test_stop_discards_late_authentication_without_creating_account_files(self):
        entered, release = threading.Event(), threading.Event()
        def slow(*args):
            entered.set()
            release.wait(10)
            return fake_login(*args)
        self.login.side_effect = slow
        cookie = self.hub.sign_in('', credentials())
        self.assertTrue(entered.wait(10))
        self.hub.request_stop()
        release.set()
        self.hub.browsers[cookie].future.result(10)
        self.assertFalse(self.hub.status(cookie)['signedIn'])
        self.assertFalse(self.hub.status(cookie)['connected'])
        self.assertFalse((Path(self.temp.name) / 'accounts').exists())

    def test_all_http_routes_use_cookie_and_account_view_not_just_launcher_token(self):
        a = self.sign_in('alice')
        identifier, ws = self.new(a)
        b = self.sign_in('bob')
        server, url = make_server(self.hub)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        parsed = urlsplit(url)
        def request(path, cookie='', value=None, view=None):
            client = http.client.HTTPConnection('127.0.0.1', parsed.port, timeout=10)
            headers = {'X-ROC-Token':parsed.fragment, 'X-ROC-View':self.hub.status(cookie)['viewId'] if view is None else view,
                       'Cookie':f'roc-session-{parsed.port}={cookie}'}
            if value is not None:
                headers['Content-Type'] = 'application/json'
            try:
                client.request('GET' if value is None else 'POST', path, None if value is None else json.dumps(value), headers)
                res = client.getresponse()
                return res.status, res.read(), res.getheader('Set-Cookie')
            finally:
                client.close()
        try:
            self.assertEqual(json.loads(request('/api/runs')[1])['jobs'], [])
            self.assertEqual(request(f'/api/runs/{identifier}', a)[0], 200)
            self.assertEqual(request(f'/api/runs/{identifier}/download/case-index.csv', a)[0], 200)
            for cookie in ('', b, 'invented-cookie'):
                for route in ('', '/documents', '/document-bundle', '/download/case-index.csv'):
                    self.assertEqual(request(f'/api/runs/{identifier}{route}', cookie)[0], 400)
                for route in ('quote', 'retrieve', 'resume', 'export', 'budget', 'documents-analysis-quote', 'documents-purchase-quote', 'documents-analyze', 'documents-download'):
                    self.assertEqual(request(f'/api/runs/{identifier}/{route}', cookie, {})[0], 400)
            self.assertEqual(request('/api/name-rules')[0], 400)
            self.assertEqual(request('/api/name-rules', a)[0], 200)
            self.assertEqual(request('/api/runs', '', form())[0], 400)
            self.assertEqual(request('/api/name-rules', a, view='stale-view')[0], 400)
            self.assertEqual(request('/api/connection/sign-in', a, credentials(), view='stale-view')[0], 400)
            code, raw, cookie_header = request('/api/connection/disconnect', a, {})
            self.assertEqual(code, 200)
            self.assertIn('HttpOnly', cookie_header)
            self.assertIn('SameSite=Strict', cookie_header)
            self.assertNotIn('synthetic-token', cookie_header)
            self.assertEqual(request(f'/api/runs/{identifier}', a)[0], 400)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(10)


if __name__ == '__main__':
    unittest.main()
