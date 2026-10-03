"""Landing tabs through real account routing; only synthetic PACER replies."""
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
from tests.test_litigant_search import litigant_record
from tests.test_workspace import record, response


@unittest.skipUnless(os.environ.get('ROC_BROWSER_TESTS') == '1', 'Set ROC_BROWSER_TESTS=1')
class SearchTabsBrowserTests(unittest.TestCase):
    def test_default_name_order_tabs_entity_search_person_search_and_attorney_regression(self):
        from playwright.sync_api import sync_playwright, expect
        queries, errors, external = [], [], []
        def request(url, payload, headers):
            queries.append(payload)
            rows = [litigant_record(payload['lastName'], payload.get('firstName',''))] if payload['partyType']=='pty' else [record()]
            return response(rows)
        session = Session('fake-session', requester=request)
        with tempfile.TemporaryDirectory() as folder, contextlib.redirect_stdout(io.StringIO()), \
                patch.object(Session, 'login', return_value=session) as login, \
                patch('roc.engine.CourtRetriever', side_effect=AssertionError('No docket requests')):
            hub = Accounts(folder)
            server, url = make_server(hub)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with sync_playwright() as pw:
                    browser = pw.chromium.launch(headless=True)
                    page = browser.new_page(viewport={'width':1440, 'height':1100}, accept_downloads=True)
                    def route(r):
                        if urlsplit(r.request.url).netloc == urlsplit(url).netloc:
                            r.continue_()
                        else:
                            external.append(r.request.url)
                            r.abort()
                    page.route('**/*', route)
                    page.on('pageerror', lambda error:errors.append(str(error)))
                    page.goto(url)
                    # Normal launches do not offer or create demo searches.
                    expect(page.locator('#demo-callout')).not_to_be_visible()
                    self.assertTrue(page.evaluate('api("/api/demo",{}).then(()=>false,e=>e.message.includes("only in the development"))'))
                    self.assertEqual(hub.guests, [])
                    self.assertEqual(queries, [])
                    expect(page.get_by_role('tab', name='Search Attorney')).to_have_attribute('aria-selected','true')
                    self.assertEqual(page.locator('.search-names input').evaluate_all('(xs)=>xs.map(x=>x.name)'), ['lastName','firstName'])
                    expect(page.get_by_label('Last name', exact=True)).to_be_visible()
                    expect(page.get_by_label('First name', exact=True)).to_have_attribute('required','')
                    page.locator('#search-last').fill('Lawyer')
                    page.locator('#search-first').fill('Jordan')
                    page.locator('#search-aliases summary').click()
                    page.locator('textarea[name=aliases]').fill('Jordan A. Lawyer')
                    page.locator('#add-search-name').click()
                    page.locator('[name=additionalLastName]').fill('Lawyer')
                    page.locator('[name=additionalFirstName]').fill('Jordy')
                    expect(page.locator('#search-name-count')).to_contain_text('2 name searches')
                    # Keyboard tab navigation changes semantics and preserves separate name drafts.
                    page.locator('#search-attorney').focus()
                    page.keyboard.press('ArrowRight')
                    expect(page.get_by_role('tab', name='Search Litigant')).to_be_focused()
                    expect(page.locator('#search-last')).to_have_value('')
                    expect(page.get_by_label('Last name / Entity name', exact=True)).to_be_visible()
                    self.assertFalse(page.locator('#search-first').evaluate('(x)=>x.required'))
                    expect(page.locator('#search-aliases')).not_to_be_visible()
                    expect(page.locator('.additional-name-row')).to_have_count(0)
                    page.locator('#search-last').fill('Acme Corporation')
                    page.locator('#add-search-name').click()
                    page.locator('[name=additionalLastName]').fill('The Acme Company')
                    self.assertFalse(page.locator('[name=additionalFirstName]').evaluate('(x)=>x.required'))
                    # Duplicates are visibly collapsed to one query; rows can be removed.
                    page.locator('#add-search-name').click()
                    page.locator('[name=additionalLastName]').nth(1).fill('Acme Corporation')
                    expect(page.locator('#search-name-count')).to_contain_text('Identical names searched once')
                    page.get_by_role('button',name='Remove additional name 2').click()
                    page.locator('input[name=budget]').fill('1.00')
                    page.locator('#search-attorney').click()
                    expect(page.locator('#search-last')).to_have_value('Lawyer')
                    expect(page.locator('textarea[name=aliases]')).to_have_value('Jordan A. Lawyer')
                    expect(page.locator('[name=additionalFirstName]')).to_have_value('Jordy')
                    page.locator('#search-litigant').click()
                    expect(page.locator('#search-last')).to_have_value('Acme Corporation')
                    expect(page.locator('[name=additionalLastName]')).to_have_value('The Acme Company')
                    self.assertEqual(queries, [])
                    screenshot_dir = os.environ.get('ROC_SEARCH_SCREENSHOTS')
                    if screenshot_dir:
                        page.screenshot(path=str(Path(screenshot_dir)/'litigant.png'), full_page=True)
                        page.locator('#search-attorney').click()
                        page.screenshot(path=str(Path(screenshot_dir)/'attorney.png'), full_page=True)
                        page.locator('#search-litigant').click()
                        page.set_viewport_size({'width':390,'height':844})
                        page.screenshot(path=str(Path(screenshot_dir)/'litigant-mobile.png'), full_page=True)
                        self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), 391)
                        page.set_viewport_size({'width':1440,'height':1100})
                    page.locator('#search-submit').click()
                    expect(page.locator('#auth-dialog')).to_be_visible()
                    self.assertEqual(queries, [])
                    page.locator('#auth-username').fill('test-user')
                    page.locator('#auth-password').fill('test-password')
                    page.locator('#auth-redact').check()
                    page.locator('#auth-submit').click()
                    expect(page.locator('#run-status')).to_have_text('READY', timeout=15000)
                    expect(page.locator('#run-name')).to_have_text('Acme Corporation')
                    expect(page.locator('#run-kind')).to_have_text('LITIGANT SEARCH')
                    expect(page.locator('#runs')).to_contain_text('Litigant ·')
                    self.assertEqual(queries, [{'lastName':'Acme Corporation','partyType':'pty'}, {'lastName':'The Acme Company','partyType':'pty'}])
                    self.assertEqual(login.call_count, 1)
                    expect(page.locator('#view-clients')).not_to_be_visible()
                    expect(page.locator('#document-launch')).not_to_be_visible()
                    expect(page.locator('#docket-selection')).not_to_be_visible()
                    expect(page.locator('#case-rows')).to_contain_text('Acme Corporation')
                    page.locator('#case-rows button').first.click()
                    expect(page.locator('#detail-parties')).to_contain_text('PCL party role: dft')
                    expect(page.locator('#detail-fields')).to_contain_text('Found by search: Acme Corporation; The Acme Company')
                    page.get_by_role('button', name='Close case', exact=True).click()
                    page.locator('#result-filters>summary').click()
                    page.locator('[data-facet="party"]>summary').click()
                    expect(page.locator('[data-facet="role"]')).to_have_count(0)
                    expect(page.locator('[data-facet="party"]')).to_contain_text('Uses matched litigants returned by PCL')
                    page.get_by_role('searchbox',name='Find Party name options').fill('Acme')
                    page.get_by_role('checkbox',name='Party name: Acme Corporation',exact=True).check()
                    page.get_by_role('combobox',name='Party role',exact=True).select_option(label='Defendant')
                    expect(page.locator('#case-rows tr')).to_have_count(1)
                    self.assertEqual(len(queries),2)  # Filtering saved API results submits no searches.
                    with page.expect_download() as download:
                        page.get_by_role('button',name='Excel ↓',exact=True).click()
                    self.assertEqual(download.value.suggested_filename,'case-index.xlsx')
                    # Attorney searches still use the existing pipeline and disclose their mode.
                    page.locator('#new-from-run').click()
                    page.locator('#search-attorney').click()
                    expect(page.locator('#search-last')).to_have_value('Lawyer')
                    page.locator('#search-submit').click()
                    expect(page.locator('#run-kind')).to_have_text('ATTORNEY SEARCH')
                    expect(page.locator('#run-status')).to_have_text('READY', timeout=15000)
                    self.assertEqual(queries[-2:], [{'lastName':'Lawyer','firstName':'Jordan','partyType':'aty'}, {'lastName':'Lawyer','firstName':'Jordy','partyType':'aty'}])
                    expect(page.locator('#view-clients')).to_be_visible()
                    expect(page.locator('#document-launch')).to_be_visible()
                    # Personal-name litigant lookup sends the optional first name.
                    page.locator('#new-from-run').click()
                    page.locator('#search-litigant').click()
                    page.get_by_role('button',name='Remove additional name 1').click()
                    page.locator('#search-last').fill('Smith')
                    page.locator('#search-first').fill('Jane')
                    page.locator('#search-submit').click()
                    expect(page.locator('#run-name')).to_have_text('Jane Smith')
                    expect(page.locator('#run-status')).to_have_text('READY', timeout=15000)
                    self.assertEqual(queries[-1], {'lastName':'Smith','firstName':'Jane','partyType':'pty'})
                    self.assertEqual(len(queries),5)
                    # An account sign-out clears both hidden tab drafts, too.
                    page.locator('#disconnect-pacer').click()
                    expect(page.locator('#search-attorney')).to_have_attribute('aria-selected','true')
                    expect(page.locator('#search-last')).to_have_value('')
                    expect(page.locator('.additional-name-row')).to_have_count(0)
                    page.locator('#search-litigant').click()
                    expect(page.locator('#search-first')).to_have_value('')
                    expect(page.locator('#search-last')).to_have_value('')
                    expect(page.locator('.additional-name-row')).to_have_count(0)
                    self.assertEqual(errors, [])
                    self.assertEqual(external, [])
                    browser.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(10)
                hub.close()
