"""Credential, lifecycle and receipt boundaries with synthetic authentication only."""
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

from roc.common import RocError, read_json, write_json
from roc.connection import BrowserConnection
from roc.desktop import existing_url
from roc.interface import make_server
from roc.pacer import Session, SessionExpired, SignInError
from roc.workspace import Workspace
from tests.test_workspace import FakeCourt, form, record, response


def credentials(**overrides):
    return {"username":"test-user", "password":"test-password-only", "otp":"987654", "clientCode":"test-client", "redact":True} | overrides


class ConnectionTests(unittest.TestCase):
    def test_no_terminal_fallback_and_acknowledgment_is_required(self):
        connection = BrowserConnection()
        with patch("roc.pacer.Session.prompt", side_effect=AssertionError("No console fallback")):
            with self.assertRaisesRegex(RocError, "Connect PACER"):
                connection.get()
        for bad in (credentials(redact=False), credentials(username=""), credentials(password=[]), credentials(unexpected="value")):
            with self.assertRaises(RocError):
                connection.prepare(bad)
        self.assertFalse(connection.connecting)

    def test_failed_login_keeps_no_secret_and_retries_only_when_requested(self):
        connection = BrowserConnection()
        session = Session("test-session-only")
        with patch.object(Session, "login", side_effect=[SignInError("13"), session]) as login:
            fields = connection.prepare(credentials())
            connection.authenticate(fields)
            self.assertEqual(fields, {})
            self.assertFalse(connection.status()["connected"])
            self.assertIn("did not accept", connection.status()["message"])
            self.assertEqual(login.call_count, 1)
            fields = connection.prepare(credentials(password="corrected-test-password"))
            connection.authenticate(fields)
            self.assertEqual(login.call_count, 2)
            self.assertEqual(login.call_args.args, ("test-user","corrected-test-password","987654","test-client",True))
            self.assertIs(connection.get(), session)
            self.assertEqual(fields, {})
            status = json.dumps(connection.status())
            self.assertNotIn("test-session-only", status)
            self.assertNotIn("corrected-test-password", status)
            self.assertNotIn("987654", status)
        connection.disconnect()
        self.assertFalse(connection.status()["connected"])

    def test_overlapping_attempts_refused_and_late_login_discarded_after_stop(self):
        connection = BrowserConnection()
        fields = connection.prepare(credentials())
        with self.assertRaises(RocError):
            connection.prepare(credentials())
        entered, release = threading.Event(), threading.Event()
        def login_once(*args):
            entered.set()
            if not release.wait(10):
                raise AssertionError('test sign-in timed out')
            return Session('late-test-token')
        with patch.object(Session, "login", side_effect=login_once) as login:
            worker = threading.Thread(target=connection.authenticate, args=(fields,))
            worker.start()
            self.assertTrue(entered.wait(10))
            connection.stop()
            release.set()
            worker.join(10)
            self.assertEqual(login.call_count, 1)
        self.assertIsNone(connection.session)
        self.assertFalse(connection.status()["connected"])

    def test_stop_before_queued_signin_starts_sends_nothing(self):
        connection = BrowserConnection()
        fields = connection.prepare(credentials())
        connection.stop()
        with patch.object(Session, 'login') as login:
            connection.authenticate(fields)
            login.assert_not_called()
        self.assertEqual(fields, {})

    def test_errors_do_not_leak_unexpected_exception_details(self):
        connection = BrowserConnection()
        with patch.object(Session, "login", side_effect=ValueError("test-password-only 987654")):
            connection.authenticate(connection.prepare(credentials()))
        self.assertNotIn("test-password-only", json.dumps(connection.status()))
        self.assertNotIn("987654", json.dumps(connection.status()))

    def test_expired_api_session_cannot_be_reused_and_pending_charge_is_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            from roc.store import RunStore
            session = Session("expired", requester=lambda *args: (_ for _ in ()).throw(SessionExpired("Sign in again.")))
            connection = BrowserConnection()
            connection.session = session
            with RunStore(folder, 100) as store:
                with self.assertRaises(SessionExpired):
                    session.search_page({"lastName":"Lawyer"},0,store)
                self.assertEqual(store.ledger["transactions"][0]["state"], "pending")
            self.assertFalse(connection.status()["connected"])
            with self.assertRaises(RocError):
                connection.get()

    def test_reconnect_preserves_selected_dockets_receipts_and_stays_idle_until_resume(self):
        calls = []
        def request(*args):
            calls.append(True)
            return response([record()])
        with tempfile.TemporaryDirectory() as folder, contextlib.redirect_stdout(io.StringIO()):
            ws = Workspace(folder)
            try:
                ws.connection.session = Session("first", requester=request)
                identifier = ws.new(form())
                ws.future.result(10)
                key = ws.cases(identifier)[0]["key"]
                FakeCourt.bought, FakeCourt.fail_key = [], key
                with patch("roc.cli.CourtRetriever", FakeCourt):
                    ws.act(identifier,"retrieve",{"keys":[key]})
                    ws.future.result(10)
                    before = ws.manifest(identifier)
                    before_receipts = ws.ledger(identifier)
                    with patch.object(Session,"login",return_value=Session("replacement-test-token",requester=request)):
                        ws.sign_in(credentials())
                        ws.future.result(10)
                    self.assertEqual(ws.manifest(identifier), before)
                    self.assertEqual(ws.ledger(identifier), before_receipts)
                    self.assertEqual(calls, [True])
                    self.assertEqual(FakeCourt.bought, [])
                    FakeCourt.fail_key = None
                    ws.act(identifier,"resume")
                    ws.future.result(10)
                    self.assertEqual(FakeCourt.bought,[key])
                    self.assertEqual(calls,[True])
                saved = ''.join(p.read_text(encoding='utf-8') for p in Path(folder).rglob('*.json'))
                for secret in ('test-password-only','replacement-test-token','987654'):
                    self.assertNotIn(secret,saved)
            finally:
                FakeCourt.fail_key = None
                ws.close()


class ConnectionHttpTests(unittest.TestCase):
    def test_authentication_guard_explicit_attempt_and_graceful_stop(self):
        with tempfile.TemporaryDirectory() as folder, contextlib.redirect_stdout(io.StringIO()):
            ws = Workspace(folder)
            server, url = make_server(ws)
            server_thread = threading.Thread(target=server.serve_forever, daemon=True)
            server_thread.start()
            token = urlsplit(url).fragment
            def request(path, value=None, extra=None):
                conn = http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=10)
                try:
                    conn.request('GET' if value is None else 'POST',path,None if value is None else json.dumps(value),
                        {'Content-Type':'application/json','X-ROC-Token':token, **(extra or {})})
                    reply=conn.getresponse()
                    return reply.status,json.loads(reply.read())
                finally:
                    conn.close()
            try:
                with patch.object(Session,'login',side_effect=[SignInError('13'),Session('private-test-token')]) as login:
                    self.assertEqual(request('/api/connection/sign-in',credentials(),{'Origin':'https://foreign.example'})[0],400)
                    self.assertEqual(request('/api/connection/sign-in',credentials(),{'X-ROC-Token':''})[0],400)
                    self.assertEqual(login.call_count,0)
                    self.assertEqual(request('/api/connection/sign-in',credentials())[0],200)
                    ws.future.result(10)
                    self.assertFalse(request('/api/connection')[1]['connected'])
                    self.assertEqual(login.call_count,1)
                    request('/api/connection/sign-in',credentials(password='corrected-password'))
                    ws.future.result(10)
                    self.assertTrue(request('/api/connection')[1]['connected'])
                    self.assertEqual(request('/api/runs')[1]['jobs'],[])
                    write_json(Path(folder)/'interface-connection.json',{'url':url})
                    self.assertEqual(existing_url(folder),url)
                    self.assertNotIn('private-test-token',json.dumps(request('/api/connection')))
                    request('/api/connection/disconnect',{})
                    self.assertFalse(request('/api/connection')[1]['connected'])
                self.assertEqual(request('/api/stop',{}, {'Origin':'https://foreign.example'})[0],400)
                self.assertFalse(ws.stopping)
                self.assertEqual(request('/api/stop',{})[0],200)
                server_thread.join(8)
                self.assertFalse(server_thread.is_alive())
                self.assertTrue(ws.closed)
                self.assertIsNone(ws.connection.session)
            finally:
                server.shutdown()
                server.server_close()
                server_thread.join(10)
                ws.close()

    def test_stop_settles_paid_response_and_prevents_next_page(self):
        entered, release = threading.Event(), threading.Event()
        calls=[]
        def request(*args):
            calls.append(True)
            entered.set()
            if not release.wait(10):
                raise AssertionError('test request timed out')
            return response([record()],0,False,2)
        with tempfile.TemporaryDirectory() as folder, contextlib.redirect_stdout(io.StringIO()):
            ws=Workspace(folder)
            try:
                ws.connection.session=Session('test',requester=request)
                identifier=ws.new(form())
                self.assertTrue(entered.wait(10))
                ws.request_stop()
                with self.assertRaises(RocError):
                    ws.new(form())
                with self.assertRaises(RocError):
                    ws.sign_in(credentials())
                release.set()
                ws.future.result(10)
                ws.close()
                self.assertEqual(calls,[True])
                self.assertEqual(ws.receipts(identifier)['spentCents'],10)
                self.assertEqual(ws.receipts(identifier)['pendingCount'],0)
                self.assertEqual(ws.manifest(identifier)['state'],'stopped')
            finally:
                release.set()
                ws.close()

    def test_launcher_never_follows_nonlocal_saved_urls(self):
        with tempfile.TemporaryDirectory() as folder:
            write_json(Path(folder)/'interface-connection.json',{'url':'https://foreign.example/#'+'a'*43})
            with patch('roc.desktop.http.client.HTTPConnection') as http:
                self.assertIsNone(existing_url(folder))
                http.assert_not_called()
