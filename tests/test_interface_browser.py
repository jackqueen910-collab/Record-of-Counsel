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
                    page.locator('input[name="budget"]').fill("0.10")
                    page.get_by_role("button", name="Search cases").click()
                    expect(page.locator('#run-status')).to_have_text("READY", timeout=15000)
                    self.assertEqual(requests, [{"firstName":"Jordan", "lastName":"Lawyer", "partyType":"aty",
                        "courtCase":{"courtId":["nysdc"],"dateFiledFrom":"2020-01-01"}}])
                    self.assertEqual(FakeCourt.bought, [])
                    expect(page.locator('#docket-guidance')).to_contain_text('Many reports can get expensive fast')
                    expect(page.locator('#case-rows .docket-field')).to_have_count(4)
                    page.locator('#choose-dockets').click()
                    expect(page.locator('#active-filters')).to_contain_text('Without docket report')
                    expect(page.locator('#selection-count')).to_have_text('0')
                    capture = os.environ.get('ROC_UI_SCREENSHOTS')
                    if capture:
                        Path(capture).mkdir(parents=True,exist_ok=True)
                        page.screenshot(path=str(Path(capture)/'docket-guidance.png'),full_page=True)
                    page.get_by_role("button", name="Select this page").click()
                    page.locator('#preview').click()
                    expect(page.locator('#quote-cost')).to_have_text("$6.00")
                    expect(page.locator('#docket-cap')).to_have_value('')
                    expect(page.locator('#confirm-retrieve')).to_be_disabled()
                    page.locator('#docket-cap').fill('3.00')
                    expect(page.locator('#docket-cap-message')).to_contain_text('Choose fewer cases')
                    expect(page.locator('#confirm-retrieve')).to_be_disabled()
                    page.locator('#revise-dockets').click()
                    page.locator('#filter').fill('00002')
                    expect(page.locator('#selection-hidden')).to_contain_text('1 outside this filter')
                    page.get_by_role("button", name="Clear selection").click()
                    page.get_by_role("checkbox", name="Select 1:24-cr-00002").check()
                    page.locator('#preview').click()
                    expect(page.locator('#quote-cost')).to_have_text("$3.00")
                    expect(page.locator('#docket-cap')).to_have_value('')  # Never carry over an allowance.
                    page.locator('#docket-cap').fill('2.99')
                    expect(page.locator('#confirm-retrieve')).to_be_disabled()
                    page.locator('#docket-cap').fill('3.00')
                    expect(page.locator('#confirm-retrieve')).to_be_enabled()
                    expect(page.locator('#docket-cap-message')).to_contain_text('$3.10')
                    if capture:
                        page.screenshot(path=str(Path(capture)/'docket-cap.png'),full_page=True)
                    page.locator('#revise-dockets').click()
                    page.locator('#filter').fill('')
                    page.get_by_role('button',name='Select this page').click()
                    # A cell action previews that one case, not the existing checkbox selection.
                    page.get_by_role('button',name='Run docket for Role in 1:24-cr-00002',exact=True).click()
                    expect(page.locator('#quote-text')).to_contain_text('1 selected')
                    expect(page.locator('#quote-cases p')).to_have_count(1)
                    expect(page.locator('#docket-cap')).to_have_value('')
                    page.locator('#docket-cap').fill('3.00')
                    self.assertEqual(FakeCourt.bought, [])
                    page.locator('#confirm-retrieve').click()
                    expect(page.locator('#stat-dockets')).to_have_text("1", timeout=15000)
                    expect(page.locator('#stat-spent')).to_have_text("$3.10")
                    self.assertEqual(FakeCourt.bought, ["nysdc|1:24-cr-00002"])
                    self.assertEqual(len(requests), 1)
                    expect(page.locator('#selection-count')).to_have_text('1')
                    page.get_by_role('button',name='Remove Docket coverage: Without docket report',exact=True).click()
                    expect(page.locator('#case-rows')).to_contain_text('Criminal Defense')
                    expect(page.locator('#case-rows .docket-field')).to_have_count(2)
                    # Re-selecting the saved report needs no new allowance.
                    page.get_by_role('button',name='Clear selection').click()
                    page.get_by_role('checkbox',name='Select 1:24-cr-00002').check()
                    page.locator('#preview').click()
                    expect(page.locator('#quote-cost')).to_have_text('$0.00')
                    expect(page.locator('#docket-cap')).to_have_value('0.00')
                    expect(page.locator('#docket-cap')).to_be_disabled()
                    expect(page.locator('#confirm-retrieve')).to_be_enabled()
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
                    # The listing and detail are separate reads: an operation can
                    # finish between them. A later idle listing must re-enable
                    # selection even when the case/detail payload is unchanged.
                    stale_listing = {'active':True}
                    identifier = ws.list()['jobs'][0]['id']
                    def race_reply(route):
                        reply = route.fetch(); data = reply.json()
                        if stale_listing['active']: data['active'] = identifier
                        route.fulfill(response=reply,json=data)
                    page.route('**/api/runs',race_reply)
                    page.reload(); page.get_by_role('button',name='Jordan Lawyer').click()
                    expect(page.get_by_role('checkbox',name='Select 1:24-cr-00001')).to_be_disabled()
                    stale_listing['active'] = False
                    expect(page.get_by_role('checkbox',name='Select 1:24-cr-00001')).to_be_enabled(timeout=10000)
                    page.unroute('**/api/runs',race_reply)
                    page.locator('#view-clients').click()
                    expect(page.locator('#party-empty')).to_contain_text('Retrieve selected dockets')
                    expect(page.locator('#report-coverage')).to_contain_text('0 of 1')
                    page.locator('#view-cases').click()
                    page.get_by_role("checkbox", name="Select 1:24-cr-00001").check()
                    page.locator('#preview').click()
                    expect(page.locator("#quote-cost")).to_have_text("$0.00")
                    expect(page.get_by_role("button", name="Retrieve these dockets")).to_be_enabled()
                    expect(page.locator("#stat-dockets")).to_have_text("0")  # Quote buys nothing.
                    page.get_by_role("button", name="Retrieve these dockets").click()
                    expect(page.locator("#stat-dockets")).to_have_text("1", timeout=15000)
                    expect(page.locator('#report-coverage')).to_contain_text('1 of 1')
                    page.locator('#view-clients').click()
                    expect(page.locator('#party-rows tr')).to_have_count(1)
                    page.get_by_role('button',name='Example Client',exact=True).click()
                    expect(page.locator('#party-case-count')).to_contain_text('1 distinct cases')
                    expect(page.locator('#party-case-list')).to_contain_text('Client type: Defendant')
                    page.locator('#party-case-list').get_by_role('button',name='1:24-cr-00001').click()
                    expect(page.locator('#detail-nature')).not_to_contain_text('ASSAULT')
                    expect(page.locator('#detail-parties')).to_contain_text('Matched counsel: Jordan Lawyer')
                    page.get_by_role('button',name='Close case').click()
                    page.get_by_role('button',name='Close party').click()
                    expect(page.locator('#view-defendants, #view-plaintiffs, #party-relation')).to_have_count(0)
                    expect(page.locator('#party-rows')).not_to_contain_text('Another Defendant')
                    expect(page.locator('#party-rows')).not_to_contain_text('USA')
                    with page.expect_download() as result:
                        page.get_by_role('button',name='Client CSVs ↓',exact=True).click()
                    self.assertEqual(result.value.suggested_filename,'party-reports.zip')
                    page.locator('#view-cases').click()
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
                    # A refreshed tab may load new assets before an old, signed-in
                    # server is restarted. Keep the case view usable in that gap.
                    def legacy_reply(route):
                        reply = route.fetch()
                        data = reply.json()
                        data.pop('partyReports', None)
                        for row in data['cases']:
                            row.pop('role', None)
                        route.fulfill(response=reply, json=data)
                    page.route(f'**/api/runs/{identifier}', legacy_reply)
                    page.reload()
                    page.get_by_role('button',name='Jordan Lawyer').click()
                    expect(page.locator('#report-coverage')).to_contain_text('An update is ready')
                    expect(page.locator('#view-clients')).to_be_disabled()
                    expect(page.locator('#case-rows')).to_contain_text('Criminal Defense')
                    page.unroute(f'**/api/runs/{identifier}', legacy_reply)
                    # Keep source-supplied civil descriptions, distinguish an
                    # unresolved parsed docket, and do not offer unsupported courts.
                    def mixed_reply(route):
                        reply = route.fetch(); data = reply.json()
                        parsed = data['cases'][0]
                        parsed.update(role='',team='',nature='Not supplied by PCL')
                        civil = dict(parsed, key='nysdc|1:24-cv-00002',caseNumber='1:24-cv-00002',
                            enriched=False,caseType='Civil',nature='850 - Securities/Commodities')
                        unavailable = dict(civil, key='gudc|1:24-cv-00003',caseNumber='1:24-cv-00003',
                            nature='Not supplied by PCL',eligible=False,ineligibleReason='Court not enabled')
                        data['cases'].extend([civil,unavailable]);route.fulfill(response=reply,json=data)
                    page.route(f'**/api/runs/{identifier}',mixed_reply)
                    page.reload();page.get_by_role('button',name='Jordan Lawyer').click()
                    expect(page.locator('#case-rows tr')).to_have_count(3)
                    civil_row = page.locator('#case-rows tr').filter(has=page.get_by_role('button',name='1:24-cv-00002',exact=True))
                    expect(civil_row).to_contain_text('850 - Securities/Commodities')
                    expect(civil_row.locator('.docket-field')).to_have_count(1)
                    parsed_row = page.locator('#case-rows tr').filter(has=page.get_by_role('button',name='1:24-cr-00001',exact=True))
                    expect(parsed_row).to_contain_text('Unresolved')
                    expect(parsed_row.locator('.docket-field')).to_have_count(0)
                    unsupported_row = page.locator('#case-rows tr').filter(has=page.get_by_role('button',name='1:24-cv-00003',exact=True))
                    expect(unsupported_row.locator('.pill',has_text='Docket unavailable')).to_have_count(2)
                    expect(unsupported_row.locator('input')).to_be_disabled()
                    expect(unsupported_row.locator('.docket-field')).to_have_count(0)
                    def old_listing(route):
                        reply=route.fetch();data=reply.json();data.pop('docketBudgetVersion',None);route.fulfill(response=reply,json=data)
                    page.route('**/api/runs',old_listing)
                    page.reload();page.get_by_role('button',name='Jordan Lawyer').click()
                    expect(page.locator('#choose-dockets')).to_have_text('Restart ROC to enable docket caps')
                    expect(page.locator('#case-rows .docket-field')).to_be_disabled()
                    expect(page.locator('#preview')).to_be_disabled()
                    self.assertEqual(blocked, [])
                    self.assertEqual(errors, [])
                    context.close()
                    browser.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(10)
                ws.close()
