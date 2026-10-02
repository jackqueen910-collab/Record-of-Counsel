"""Account UX acceptance against the production Accounts server, without PACER access."""
import contextlib
import io
import os
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

from roc.accounts import Accounts
from roc.interface import make_server
from roc.pacer import Session
from tests.test_accounts import fake_login


@unittest.skipUnless(os.environ.get('ROC_BROWSER_TESTS') == '1', 'Set ROC_BROWSER_TESTS=1')
class AccountBrowserTests(unittest.TestCase):
    def test_signin_owned_search_switch_signout_expiry_and_two_tabs(self):
        from playwright.sync_api import sync_playwright, expect
        with tempfile.TemporaryDirectory() as folder, contextlib.redirect_stdout(io.StringIO()), \
                patch.object(Session, 'login', side_effect=fake_login) as login:
            hub = Accounts(folder)
            server, url = make_server(hub)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            errors, external = [], []
            try:
                with sync_playwright() as pw:
                    browser = pw.chromium.launch(headless=True)
                    contexts = [browser.new_context(viewport={'width':1440, 'height':1100}) for _ in range(2)]
                    for context in contexts:
                        def route(r):
                            if urlsplit(r.request.url).netloc == urlsplit(url).netloc:
                                r.continue_()
                            else:
                                external.append(r.request.url)
                                r.abort()
                        context.route('**/*', route)
                    page, other = [ctx.new_page() for ctx in contexts]
                    for p in (page, other):
                        p.on('pageerror', lambda exc: errors.append(str(exc)))
                        p.goto(url)
                        expect(p.locator('#runs')).to_contain_text('after you sign in with PACER')
                    def submit_auth(p, name, password='fixture-password'):
                        p.locator('#auth-username').fill(name)
                        p.locator('#auth-password').fill(password)
                        p.locator('#auth-redact').check()
                        p.locator('#auth-submit').click()
                    # A requested search survives first sign-in; no account registration.
                    page.get_by_label('First name', exact=True).fill('Jordan')
                    page.get_by_label('Last name', exact=True).fill('Lawyer')
                    page.locator('input[name=budget]').fill('1.00')
                    page.locator('#search-submit').click()
                    submit_auth(page, 'alice')
                    expect(page.locator('#run-status')).to_have_text('READY', timeout=15000)
                    expect(page.locator('#connection-status')).to_have_text('alice · PACER connected')
                    expect(page.locator('#runs .run-link')).to_have_count(1)
                    first = page.evaluate('currentId')
                    self.assertIsNotNone(first)
                    self.assertEqual(login.call_count, 1)
                    expect(other.locator('#runs .run-link')).to_have_count(0)
                    other.locator('#connect-pacer').click()
                    submit_auth(other, 'bob')
                    expect(other.locator('#connection-status')).to_have_text('bob · PACER connected', timeout=10000)
                    expect(other.locator('#runs .run-link')).to_have_count(0)
                    denied = other.evaluate('(id)=>api(`/api/runs/${id}`).then(()=>false,()=>true)', first)
                    self.assertTrue(denied)
                    # Expiring PACER only removes purchasing access, not ROC history.
                    alice = next(c for c in hub.browsers.values() if c.username == 'alice')
                    alice.connection.session.usable = False
                    page.evaluate('refresh()')
                    expect(page.locator('#connection-status')).to_have_text('alice · Saved searches available')
                    expect(page.locator('#disconnect-pacer')).to_be_visible()
                    page.locator('#connect-pacer').click()
                    submit_auth(page, 'alice', 'wrong')
                    expect(page.locator('#auth-message')).to_contain_text('did not accept', timeout=10000)
                    expect(page.locator('#runs .run-link')).to_have_count(1)
                    submit_auth(page, 'alice')
                    expect(page.locator('#auth-dialog')).not_to_be_visible(timeout=10000)
                    expect(page.locator('#runs .run-link')).to_have_count(1)
                    # A second tab shares the browser account but has a stale-view guard.
                    tab = contexts[0].new_page()
                    tab.on('pageerror', lambda exc: errors.append(str(exc)))
                    tab.goto(url)
                    expect(tab.locator('#runs .run-link')).to_have_count(1)
                    tab.locator('#runs .run-link').click()
                    expect(tab.locator('#run-name')).to_have_text('Jordan Lawyer')
                    tab.evaluate('clearInterval(pollTimer)')
                    tab.locator('#open-documents').click()
                    expect(tab.locator('#documents-dialog')).to_be_visible()
                    page.locator('#switch-account').click()
                    submit_auth(page, 'bob')
                    expect(page.locator('#connection-status')).to_have_text('bob · PACER connected', timeout=10000)
                    expect(page.locator('#runs .run-link')).to_have_count(0)
                    expect(page.locator('#run-view')).not_to_be_visible()
                    self.assertEqual(page.evaluate('currentId'), None)
                    # A stale tab cannot accidentally apply an action in the newly selected account.
                    self.assertTrue(tab.evaluate('api("/api/demo",{}).then(()=>false,e=>e.message.includes("account changed"))'))
                    tab.evaluate('refresh()')
                    expect(tab.locator('#documents-dialog')).not_to_be_visible()
                    self.assertEqual(tab.evaluate('[currentId,docRun,docState,analysisQuote,purchaseQuote]'), [None]*5)
                    expect(tab.locator('#case-rows')).to_be_empty()
                    expect(tab.locator('#run-name')).to_be_empty()
                    expect(tab.locator('#search-view')).to_be_visible()
                    # Returning to Alice recovers exactly her search; sign-out clears it again.
                    page.locator('#switch-account').click()
                    submit_auth(page, 'alice')
                    expect(page.locator('#runs .run-link')).to_have_count(1, timeout=10000)
                    page.locator('#runs .run-link').click()
                    expect(page.locator('#run-name')).to_have_text('Jordan Lawyer')
                    page.locator('#disconnect-pacer').click()
                    expect(page.locator('#runs')).to_contain_text('after you sign in with PACER')
                    expect(page.locator('#runs .run-link')).to_have_count(0)
                    expect(page.locator('#run-name')).to_be_empty()
                    expect(page.locator('#auth-username')).to_have_value('')
                    expect(page.locator('#case-rows')).to_be_empty()
                    self.assertEqual(login.call_count, 6)
                    self.assertEqual(errors, [])
                    self.assertEqual(external, [])
                    # Optional inspection artifact, kept outside the repository.
                    artifact = os.environ.get('ROC_ACCOUNT_SCREENSHOT')
                    if artifact:
                        page.screenshot(path=artifact, full_page=True)
                    browser.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(10)
                hub.close()

    def test_anonymous_demo_and_failed_first_login_never_reveal_account_history(self):
        from playwright.sync_api import sync_playwright, expect
        with tempfile.TemporaryDirectory() as folder, contextlib.redirect_stdout(io.StringIO()), \
                patch.object(Session, 'login', side_effect=fake_login) as login:
            hub = Accounts(folder)
            server, url = make_server(hub)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with sync_playwright() as pw:
                    browser = pw.chromium.launch(headless=True)
                    page = browser.new_page()
                    page.route('**/*', lambda r: r.continue_() if urlsplit(r.request.url).netloc == urlsplit(url).netloc else r.abort())
                    page.goto(url)
                    page.locator('#demo').click()
                    expect(page.locator('#run-status')).to_have_text('READY', timeout=15000)
                    expect(page.locator('#runs')).to_contain_text('after you sign in with PACER')
                    self.assertEqual(login.call_count, 0)
                    page.locator('#connect-pacer').click()
                    page.locator('#auth-username').fill('alice')
                    page.locator('#auth-password').fill('wrong')
                    page.locator('#auth-redact').check()
                    page.locator('#auth-submit').click()
                    expect(page.locator('#auth-message')).to_contain_text('did not accept', timeout=10000)
                    expect(page.locator('#runs .run-link')).to_have_count(0)
                    expect(page.locator('#run-view')).not_to_be_visible()
                    self.assertEqual(len(hub.workspaces), 0)
                    self.assertEqual(login.call_count, 1)
                    browser.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(10)
                hub.close()
