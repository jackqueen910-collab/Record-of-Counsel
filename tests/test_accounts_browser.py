"""Personal account, manual docket and library browser acceptance. No external traffic."""
import contextlib
import io
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

from roc.accounts import Accounts
from roc.interface import make_server
from roc.pacer import Session
from tests.test_accounts import fake_login
from tests.test_surfer import prepare_run
from tests.test_document_grabber import FakeTransport

@unittest.skipUnless(os.environ.get('ROC_BROWSER_TESTS')=='1','Set ROC_BROWSER_TESTS=1')
class AccountBrowserTests(unittest.TestCase):
    def test_personal_accounts_docket_purchase_library_and_shared_pacer_isolation(self):
        from playwright.sync_api import sync_playwright,expect
        with tempfile.TemporaryDirectory() as folder,contextlib.redirect_stdout(io.StringIO()),patch.object(Session,'login',side_effect=fake_login) as login:
            hub=Accounts(folder);server,url=make_server(hub);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            errors=[];external=[];transport=FakeTransport()
            try:
                with patch('roc.document_download.CourtDocumentHTTP',return_value=transport),patch('roc.claude.request_claude',side_effect=AssertionError('No AI')),sync_playwright() as pw:
                    browser=pw.chromium.launch(headless=True)
                    contexts=[browser.new_context(viewport={'width':1440,'height':1050}) for _ in range(2)]
                    def route(r):
                        if urlsplit(r.request.url).netloc==urlsplit(url).netloc:r.continue_()
                        else:external.append(r.request.url);r.abort()
                    for ctx in contexts:ctx.route('**/*',route)
                    a,b=[ctx.new_page() for ctx in contexts]
                    for page in (a,b):page.on('pageerror',lambda e:errors.append(str(e)));page.goto(url)
                    def signup(page,email):
                        page.locator('#user-login').click();page.locator('#user-create').click()
                        page.locator('#user-email').fill(email);page.locator('#user-password').fill('fixture-password');page.locator('#user-confirm').fill('fixture-password');page.locator('#user-submit').click()
                        expect(page.locator('#user-dialog')).not_to_be_visible()
                        expect(page.locator('#connection-status')).to_contain_text(email)
                    def connect(page):
                        page.locator('#connect-pacer').click();page.locator('#auth-username').fill('shared')
                        page.locator('#auth-password').fill('pacer-fixture');page.locator('#auth-redact').check();page.locator('#auth-submit').click()
                        expect(page.locator('#auth-dialog')).not_to_be_visible(timeout=10000)
                    signup(a,'alice@example.test');signup(b,'bob@example.test')
                    self.assertEqual(login.call_count,0)
                    ws=next(w for k,w in hub.workspaces.items() if hub.users.get(k)['email']=='alice@example.test')
                    run=prepare_run(ws);a.evaluate('refresh()')
                    expect(a.locator('#runs .run-link')).to_have_count(1);expect(b.locator('#runs .run-link')).to_have_count(0)
                    a.locator('#runs .run-link').click();a.locator('#open-surfer').click()
                    expect(a.locator('#surfer-entries tr')).to_have_count(6)
                    expect(a.locator('#surfer-party-list')).to_contain_text('Another Defendant')
                    a.locator('#surfer-filter').fill('sentencing');expect(a.locator('#surfer-entries')).to_contain_text('No docket entries')
                    a.locator('#surfer-filter').fill('');a.locator('#surfer-entries button').first.click()
                    expect(a.locator('#surfer-purchase-summary')).to_contain_text('$3.00')
                    expect(a.locator('#surfer-confirm')).to_be_disabled();self.assertEqual(len(transport.requests),0)
                    a.locator('#surfer-cap').fill('3');a.locator('#surfer-confirm').click()
                    expect(a.locator('#auth-dialog')).to_be_visible()
                    a.locator('#auth-username').fill('shared');a.locator('#auth-password').fill('pacer-fixture');a.locator('#auth-redact').check();a.locator('#auth-submit').click()
                    expect(a.locator('#surfer-dialog')).not_to_be_visible(timeout=15000)
                    expect(a.locator('#run-status')).to_have_text('READY',timeout=15000)
                    self.assertEqual(len(transport.requests),2)
                    a.locator('#my-files').click();expect(a.locator('#files-list .file-item')).to_have_count(2)
                    a.locator('#files-list input[type=checkbox]').first.check()
                    with a.expect_download() as event:a.locator('#files-download').click()
                    self.assertEqual(event.value.suggested_filename,'ROC-My-Files.zip')
                    a.get_by_label('Close My Files',exact=True).click()
                    connect(b);self.assertEqual(login.call_count,2)
                    b.locator('#my-files').click();expect(b.locator('#files-list')).to_contain_text('No purchases yet')
                    b.get_by_label('Close My Files',exact=True).click()
                    self.assertTrue(b.evaluate('(id)=>api(`/api/runs/${id}/docket?caseKey=nysdc%7C1%3A24-cr-00001`).then(()=>false,()=>true)',run))
                    a.locator('#disconnect-pacer').click();expect(a.locator('#connection-status')).to_contain_text('Saved searches available')
                    a.locator('#open-surfer').click();expect(a.locator('#surfer-entries')).to_contain_text('Open saved PDF')
                    self.assertEqual(len(transport.requests),2)
                    artifact=Path('runs/account-surfer-preview.png');artifact.parent.mkdir(exist_ok=True);a.screenshot(path=str(artifact),full_page=True)
                    a.get_by_label('Close Docket Surfer',exact=True).click()
                    a.locator('#switch-account').click();expect(a.locator('#runs')).to_contain_text('after you sign in to ROC')
                    a.locator('#user-login').click();a.locator('#user-email').fill('alice@example.test');a.locator('#user-password').fill('wrong');a.locator('#user-submit').click()
                    expect(a.locator('#user-message')).to_contain_text('not accepted')
                    a.locator('#user-password').fill('fixture-password');a.locator('#user-submit').click();expect(a.locator('#runs .run-link')).to_have_count(1)
                    self.assertEqual(login.call_count,2)
                    self.assertEqual(errors,[]);self.assertEqual(external,[]);browser.close()
            finally:server.shutdown();server.server_close();thread.join(10);hub.close()
