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
        self.hub = Accounts(self.temp.name)
        self.addCleanup(self.hub.close)
        login = patch.object(Session, 'login', side_effect=fake_login)
        self.login = login.start()
        self.addCleanup(login.stop)

    def person(self, name, cookie='', create=True):
        values={'email':name+'@example.test','password':'fixture-password'}
        if create: values['confirmation']='fixture-password'
        return self.hub.user_sign_in(cookie, values, create)

    def connect(self, cookie, username='shared', **kw):
        self.hub.sign_in(cookie, credentials(username=username, **kw))
        self.hub.browsers[cookie].future.result(10)
        return cookie

    def new(self,cookie):
        with self.hub.scope(cookie,require_account=True) as ws:
            identifier=ws.new(form())
        ws.future.result(15)
        self.assertEqual(ws.summary(identifier)['state'],'ready')
        return identifier,ws

    def test_separate_people_sharing_pacer_and_history_without_pacer(self):
        a,b=self.person('alice'),self.person('bob')
        self.assertEqual(self.login.call_count,0)
        self.connect(a);self.connect(b)
        identifier,ws=self.new(a)
        self.assertEqual(self.hub.list(b)['jobs'],[])
        with self.hub.scope(b) as bws,self.assertRaises(RocError):bws.summary(identifier)
        self.hub.disconnect(a)
        self.assertTrue(self.hub.status(a)['signedIn'])
        self.assertFalse(self.hub.status(a)['connected'])
        self.assertEqual(len(self.hub.list(a)['jobs']),1)
        self.hub.close();self.hub=Accounts(self.temp.name);self.addCleanup(self.hub.close)
        self.assertEqual(self.hub.list(a)['jobs'],[])
        a=self.person('alice',create=False)
        self.assertEqual(self.hub.list(a)['jobs'][0]['id'],identifier)
        self.assertFalse(self.hub.status(a)['connected'])

    def test_switching_pacer_never_switches_person(self):
        a=self.person('alice');self.connect(a)
        identifier,ws=self.new(a);view=self.hub.status(a)['viewId']
        self.connect(a,'another',password='wrong')
        self.assertEqual(self.hub.status(a)['viewId'],view)
        self.assertEqual(self.hub.list(a)['jobs'][0]['id'],identifier)
        self.connect(a,'another')
        self.assertEqual(self.hub.list(a)['jobs'][0]['id'],identifier)
        with self.assertRaises(RocError):self.hub.sign_in('',credentials())

    def test_same_person_two_browsers_capture_their_own_pacer_provider(self):
        a=self.person('alice');b=self.person('alice',create=False)
        self.connect(a,clientCode='first');self.connect(b,clientCode='second')
        entered,release=threading.Event(),threading.Event();seen=[]
        from roc.workspace import run_workflow
        def blocked(*args,**kw):
            entered.set();release.wait(10);seen.append(kw['session_provider']().client_code)
            return run_workflow(*args,**kw)
        with patch('roc.workspace.run_workflow',side_effect=blocked):
            with self.hub.scope(a) as ws:identifier=ws.new(form())
            self.assertTrue(entered.wait(10))
            try:
                with self.hub.scope(b) as other:self.assertIs(other,ws)
                with self.assertRaises(RocError):self.hub.sign_out(a)
            finally:release.set()
            ws.future.result(15)
        self.assertEqual(seen,['first'])
        self.assertEqual(self.hub.list(b)['jobs'][0]['id'],identifier)

    def test_password_reset_invalidates_sessions_and_cannot_grant_owner(self):
        a,b=self.person('alice'),self.person('bob')
        self.assertEqual(self.hub.status(a)['role'],'owner')
        self.assertEqual(self.hub.status(b)['role'],'member')
        job=self.hub.users.reset_request('alice@example.test')
        self.hub.users.reset({'token':job['token'],'password':'different','confirmation':'different'})
        self.assertFalse(self.hub.status(a)['signedIn'])
        self.assertTrue(self.hub.status(b)['signedIn'])
        with self.assertRaises(RocError):self.hub.users.reset({'token':job['token'],'password':'different','confirmation':'different'})
        with self.assertRaises(RocError):self.hub.user_sign_in('',{'email':'evil@example.test','password':'hello','confirmation':'hello','role':'owner'},True)
        raw=(Path(self.temp.name)/'users.sqlite3').read_bytes()
        self.assertNotIn(b'fixture-password',raw);self.assertNotIn(job['token'].encode(),raw)

    def test_failed_login_throttle_and_stale_tab(self):
        a=self.person('alice');view=self.hub.status(a)['viewId']
        anonymous=self.hub.sign_out(a,view)
        with self.assertRaisesRegex(RocError,'account changed'):
            self.hub.user_sign_in(anonymous,{'email':'alice@example.test','password':'fixture-password'},expected_view=view)
        for _ in range(6):
            with self.assertRaisesRegex(RocError,'not accepted'):self.hub.users.login({'email':'alice@example.test','password':'wrong'})
        with self.assertRaisesRegex(RocError,'Too many'):self.hub.users.login({'email':'alice@example.test','password':'wrong'})

    def test_legacy_import_is_explicit_owner_only_repeatable_and_preserves_source(self):
        from tests.test_surfer import prepared_workspace
        old_root=Path(self.temp.name)/'accounts'/account_key(self.temp.name,'shared')
        old,run=prepared_workspace(old_root);old.close()
        a,b=self.person('alice'),self.person('bob');self.connect(a);self.connect(b)
        self.assertEqual(self.hub.list(a)['jobs'],[])
        with self.assertRaises(RocError):self.hub.import_legacy(b)
        self.assertEqual(self.hub.import_legacy(a)['imported'],1)
        self.assertEqual(self.hub.import_legacy(a)['imported'],0)
        with self.hub.scope(a) as ws:
            self.assertEqual(len(ws.library.public()['files']),1)
            self.assertTrue(ws.surfer.case(run,'nysdc|1:24-cr-00001')['saved'])
        self.assertTrue((old_root/run/'saved.html').is_file())
        self.assertEqual(self.hub.list(b)['jobs'],[])

    def test_all_routes_scope_to_person_not_pacer_or_launcher_token(self):
        a,b=self.person('alice'),self.person('bob');self.connect(a);self.connect(b)
        identifier,ws=self.new(a)
        server,url=make_server(self.hub);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();parsed=urlsplit(url)
        def request(path,cookie='',value=None):
            client=http.client.HTTPConnection('127.0.0.1',parsed.port,timeout=10)
            headers={'X-ROC-Token':parsed.fragment,'X-ROC-View':self.hub.status(cookie)['viewId'],'Cookie':f'roc-session-{parsed.port}={cookie}'}
            if value is not None:headers['Content-Type']='application/json'
            try:
                client.request('GET' if value is None else 'POST',path,None if value is None else json.dumps(value),headers)
                res=client.getresponse();return res.status,res.read()
            finally:client.close()
        try:
            self.assertEqual(request(f'/api/runs/{identifier}',a)[0],200)
            for cookie in ('',b,'fake'):
                for route in ('','/documents','/document-bundle','/download/case-index.csv'):
                    self.assertEqual(request(f'/api/runs/{identifier}{route}',cookie)[0],400)
                self.assertEqual(request('/api/name-rules',cookie)[0],200 if cookie==b else 400)
            self.assertEqual(request('/api/account/import',b,{})[0],400)
            self.assertEqual(request('/api/account/forgot','',{'email':'alice@example.test'})[0],400)
        finally:server.shutdown();server.server_close();thread.join(10)

if __name__ == '__main__':unittest.main()
