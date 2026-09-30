"""Browser acceptance for the interface, served locally with all external traffic blocked."""
import contextlib
import io
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

from roc.common import write_json
from roc.interface import make_server
from roc.workspace import Workspace


@unittest.skipUnless(os.environ.get("ROC_BROWSER_TESTS") == "1", "Set ROC_BROWSER_TESTS=1 for local browser tests")
class InterfaceBrowserTests(unittest.TestCase):
    def test_browser_signin_cancel_retry_connect_continue_and_stop_without_terminal(self):
        from playwright.sync_api import sync_playwright, expect
        from roc.pacer import Session, SignInError
        from tests.test_workspace import response, record
        searches = []
        def request(*args):
            searches.append(True)
            return response([record()])
        session = Session('fixture-only-session',requester=request)
        with tempfile.TemporaryDirectory() as folder, contextlib.redirect_stdout(io.StringIO()), \
                patch.object(Session,'login',side_effect=[SignInError('13'),session,session]) as login, \
                patch.object(Session,'prompt',side_effect=AssertionError('No terminal prompt')):
            ws=Workspace(folder)
            server,url=make_server(ws)
            thread=threading.Thread(target=server.serve_forever,daemon=True)
            thread.start()
            try:
                with sync_playwright() as pw:
                    browser=pw.chromium.launch(headless=True)
                    page=browser.new_page(viewport={'width':1440,'height':1120})
                    page.route('**/*',lambda route: route.continue_() if urlsplit(route.request.url).netloc == urlsplit(url).netloc else route.abort())
                    page.goto(url)
                    page.get_by_label('First name',exact=True).fill('Jordan')
                    page.get_by_label('Last name',exact=True).fill('Lawyer')
                    page.locator('input[name="budget"]').fill('1.00')
                    page.locator('#search-submit').click()
                    expect(page.locator('#auth-dialog')).to_be_visible()
                    self.assertEqual(searches,[])
                    page.locator('#auth-password').fill('cancelled-fixture-password')
                    page.locator('#auth-otp').fill('654321')
                    page.get_by_role('button',name='Close sign-in').click()
                    expect(page.locator('#auth-password')).to_have_value('')
                    expect(page.locator('#auth-otp')).to_have_value('')
                    self.assertEqual(ws.list()['jobs'],[])
                    page.locator('#connect-pacer').click()
                    page.locator('#auth-username').fill('fixture-user')
                    page.locator('#auth-password').fill('incorrect-fixture-password')
                    page.locator('#auth-otp').fill('123456')
                    page.get_by_role('button',name='Hide password').click()
                    expect(page.locator('#auth-password')).to_have_attribute('type','password')
                    page.get_by_role('button',name='Show password').click()
                    expect(page.locator('#auth-password')).to_have_attribute('type','text')
                    page.locator('#auth-submit').click()  # Required acknowledgment prevents submission.
                    self.assertEqual(login.call_count,0)
                    page.locator('#auth-redact').check()
                    page.locator('#auth-submit').click()
                    expect(page.locator('#auth-message')).to_contain_text('did not accept',timeout=10000)
                    expect(page.locator('#auth-password')).to_have_value('incorrect-fixture-password')
                    self.assertEqual(login.call_count,1)
                    page.locator('#auth-password').fill('correct-fixture-password')
                    page.locator('#auth-otp').fill('111222')
                    page.locator('#auth-submit').click()
                    expect(page.locator('#connection-status')).to_have_text('PACER connected',timeout=10000)
                    expect(page.locator('#auth-dialog')).not_to_be_visible()
                    expect(page.locator('#auth-password')).to_have_value('')
                    self.assertEqual(searches,[])  # Standalone Connect does not run the previously cancelled search.
                    page.locator('#disconnect-pacer').click()
                    expect(page.locator('#connection-status')).to_have_text('Sign-in required')
                    page.locator('#search-submit').click()
                    expect(page.locator('#auth-submit')).to_have_text('Connect and continue')
                    page.locator('#auth-password').fill('correct-fixture-password')
                    page.locator('#auth-redact').check()
                    page.locator('#auth-submit').click()
                    expect(page.locator('#run-status')).to_have_text('READY',timeout=15000)
                    self.assertEqual(searches,[True])
                    self.assertEqual(login.call_count,3)
                    for key in ('password','otp'):
                        expect(page.locator('#auth-'+key)).to_have_value('')
                    saved=''.join(p.read_text(encoding='utf-8') for p in Path(folder).rglob('*.json'))
                    for secret in ('incorrect-fixture-password','correct-fixture-password','fixture-only-session','111222'):
                        self.assertNotIn(secret,saved)
                    page.locator('#stop-roc').click()
                    page.locator('#confirm-stop').click()
                    expect(page.locator('#notice')).to_contain_text('ROC has stopped',timeout=15000)
                    self.assertTrue(ws.closed)
                    self.assertIsNone(ws.connection.session)
                    browser.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(10)
                ws.close()

    def test_search_form_with_fake_api_requires_separate_docket_confirmation(self):
        from playwright.sync_api import sync_playwright, expect
        from roc.pacer import Session
        from tests.test_workspace import FakeCourt, response, record
        requests = []
        def request(url, payload, headers):
            requests.append(payload)
            return response([record(seq=1), record(seq=2)])
        FakeCourt.bought, FakeCourt.fail_key = [], None
        with tempfile.TemporaryDirectory() as folder, contextlib.redirect_stdout(io.StringIO()), patch("roc.cli.CourtRetriever", FakeCourt):
            ws = Workspace(folder, lambda: Session("fictional", requester=request))
            ws.connection.session = Session("fictional", requester=request)
            server, url = make_server(ws)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with sync_playwright() as pw:
                    browser = pw.chromium.launch(headless=True)
                    page = browser.new_page(viewport={"width":1440,"height":1100})
                    page.route("**/*", lambda route: route.continue_() if urlsplit(route.request.url).netloc == urlsplit(url).netloc else route.abort())
                    page.goto(url)
                    page.get_by_label("First name", exact=True).fill("Jordan")
                    page.get_by_label("Last name", exact=True).fill("Lawyer")
                    page.locator('textarea[name="aliases"]').fill("Jordan A. Lawyer")
                    page.get_by_text("Courts & filing dates", exact=False).click()
                    page.get_by_label("Filed on or after").fill("2020-01-01")
                    page.locator('#court-select').select_option("nysdc")
                    page.locator('input[name="budget"]').fill("3.10")
                    page.get_by_role("button", name="Search cases").click()
                    expect(page.locator('#run-status')).to_have_text("READY", timeout=15000)
                    self.assertEqual(requests, [{"firstName":"Jordan", "lastName":"Lawyer", "partyType":"aty",
                        "courtCase":{"courtId":["nysdc"],"dateFiledFrom":"2020-01-01"}}])
                    self.assertEqual(FakeCourt.bought, [])
                    page.get_by_role("button", name="Select this page").click()
                    page.get_by_role("button", name="Review docket selection").click()
                    expect(page.locator('#quote-cost')).to_have_text("$6.00")
                    expect(page.locator('#confirm-retrieve')).to_be_disabled()
                    page.get_by_role("button", name="Close preview").click()
                    page.get_by_role("button", name="Clear selection").click()
                    page.get_by_role("checkbox", name="Select 1:24-cr-00002").check()
                    page.get_by_role("button", name="Review docket selection").click()
                    expect(page.locator('#quote-cost')).to_have_text("$3.00")
                    self.assertEqual(FakeCourt.bought, [])
                    page.locator('#confirm-retrieve').click()
                    expect(page.locator('#stat-dockets')).to_have_text("1", timeout=15000)
                    expect(page.locator('#stat-spent')).to_have_text("$3.10")
                    self.assertEqual(FakeCourt.bought, ["nysdc|1:24-cr-00002"])
                    self.assertEqual(len(requests), 1)
                    browser.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(10)
                ws.close()

    def test_demo_preview_enrich_filter_details_download_and_reopen(self):
        from playwright.sync_api import sync_playwright, expect
        with tempfile.TemporaryDirectory() as folder, contextlib.redirect_stdout(io.StringIO()), \
                patch("roc.pacer.Session.prompt", side_effect=AssertionError("No PACER login")), \
                patch("roc.pacer.request_json", side_effect=AssertionError("No PACER requests")):
            ws = Workspace(folder)
            server, url = make_server(ws)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with sync_playwright() as pw:
                    browser = pw.chromium.launch(headless=True)
                    context = browser.new_context(viewport={"width":1440,"height":1050}, accept_downloads=True)
                    allowed = urlsplit(url).netloc
                    blocked = []
                    def route(request):
                        if urlsplit(request.request.url).netloc == allowed:
                            request.continue_()
                        else:
                            blocked.append(request.request.url)
                            request.abort()
                    context.route("**/*", route)
                    page = context.new_page()
                    errors = []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.goto(url)
                    expect(page.get_by_role("heading", name="Follow the lawyer. Find the cases.")).to_be_visible()
                    page.get_by_role("button", name="Open free demo").click()
                    expect(page.locator("#run-status")).to_have_text("READY", timeout=15000)
                    expect(page.locator("#stat-dockets")).to_have_text("0")
                    expect(page.locator("#case-rows tr")).to_have_count(1)
                    page.get_by_role("checkbox", name="Select 1:24-cr-00001").check()
                    page.get_by_role("button", name="Review docket selection").click()
                    expect(page.locator("#quote-cost")).to_have_text("$0.00")
                    expect(page.get_by_role("button", name="Retrieve these dockets")).to_be_enabled()
                    expect(page.locator("#stat-dockets")).to_have_text("0")  # Quote buys nothing.
                    page.get_by_role("button", name="Retrieve these dockets").click()
                    expect(page.locator("#stat-dockets")).to_have_text("1", timeout=15000)
                    page.get_by_role("button", name="1:24-cr-00001", exact=True).click()
                    expect(page.locator("#detail-nature")).to_contain_text("FRAUD BY WIRE")
                    expect(page.locator("#detail-nature")).not_to_contain_text("ASSAULT")
                    page.get_by_role("button", name="Close case").click()
                    page.get_by_role("searchbox", name="Filter cases").fill("not found")
                    expect(page.locator("#case-rows tr")).to_have_count(0)
                    page.get_by_role("searchbox", name="Filter cases").fill("")
                    with page.expect_download() as result:
                        page.get_by_role("button", name="CSV ↓", exact=True).click()
                    download = result.value
                    exported = Path(folder) / "download.csv"
                    download.save_as(exported)
                    self.assertIn("FRAUD BY WIRE", exported.read_text(encoding="utf-8-sig"))
                    page.reload()
                    page.get_by_role("button", name="Jordan Lawyer").click()
                    expect(page.locator("#stat-dockets")).to_have_text("1")
                    expect(page.locator("#stat-spent")).to_have_text("$0.00")
                    # Source text cannot execute HTML/scripts, even in case details.
                    identifier = ws.list()["jobs"][0]["id"]
                    evidence = ws.folder(identifier) / "output/evidence.json"
                    from roc.common import read_json
                    data = read_json(evidence)
                    data["cases"][0]["caseTitle"] = '<img src="https://foreign.example/x" onerror="alert(1)">'
                    write_json(evidence, data)
                    expect(page.locator(".case-title")).to_contain_text("<img", timeout=10000)
                    self.assertEqual(page.locator("#case-rows img").count(), 0)
                    self.assertEqual(blocked, [])
                    self.assertEqual(errors, [])
                    context.close()
                    browser.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(10)
                ws.close()
