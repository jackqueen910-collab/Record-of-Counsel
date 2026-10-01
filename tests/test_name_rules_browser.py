"""Offline browser acceptance for reversible workspace name rules."""
import contextlib
import io
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

from openpyxl import load_workbook

from roc.interface import make_server
from roc.workspace import Workspace
from tests.test_workspace import retrieval_values


@unittest.skipUnless(os.environ.get('ROC_BROWSER_TESTS') == '1', 'Set ROC_BROWSER_TESTS=1')
class NameRulesBrowserTests(unittest.TestCase):
    def test_group_preview_edit_remove_undo_exports_and_client_boundaries(self):
        from playwright.sync_api import sync_playwright, expect
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()), \
                patch('roc.pacer.Session.prompt',side_effect=AssertionError('No sign-in')), \
                patch('roc.pacer.request_json',side_effect=AssertionError('No PACER requests')):
            ws=Workspace(tmp)
            identifier=ws.new(demo=True);ws.future.result(10)
            ws.act(identifier,'retrieve',retrieval_values(ws, identifier, ['nysdc|1:24-cr-00001']));ws.future.result(10)
            server,url=make_server(ws)
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                with sync_playwright() as pw:
                    browser=pw.chromium.launch(headless=True)
                    page=browser.new_page(viewport={'width':1440,'height':1100},accept_downloads=True)
                    blocked=[];errors=[]
                    def route(request):
                        if urlsplit(request.request.url).netloc==urlsplit(url).netloc:request.continue_()
                        else:blocked.append(request.request.url);request.abort()
                    page.route('**/*',route);page.on('pageerror',lambda e:errors.append(str(e)))
                    page.goto(url)
                    page.get_by_role('button',name='Jordan Lawyer').click()
                    page.locator('#view-clients').click()
                    page.get_by_role('checkbox',name='Group Example Client',exact=True).check()
                    page.get_by_role('button',name='Group selected names').click()
                    expect(page.locator('#rules-dialog')).to_be_visible()
                    page.get_by_label('Source names, one per line').fill('Example Client\nAnother Defendant')
                    page.get_by_label('Preferred report name').fill('Example group')
                    page.get_by_label('Grouping type').select_option('organization-group')
                    page.get_by_role('button',name='Preview change',exact=True).click()
                    expect(page.locator('#rule-comparisons')).to_contain_text('Example group — 1 distinct case')
                    self.assertEqual(ws.name_rules.public()['revision'],0)
                    self.assertEqual(ws.receipts(identifier)['spentCents'],0)
                    # Editing after a preview invalidates it; a new preview is required.
                    page.get_by_label('Preferred report name').fill('Example Holdings')
                    expect(page.locator('#rule-preview')).not_to_be_visible()
                    page.get_by_role('button',name='Preview change',exact=True).click()
                    expect(page.locator('#rule-preview')).to_be_visible()
                    capture=os.environ.get('ROC_UI_SCREENSHOTS')
                    if capture:
                        Path(capture).mkdir(parents=True,exist_ok=True)
                        page.screenshot(path=str(Path(capture)/'name-rule-preview.png'),full_page=True)
                    page.get_by_role('button',name='Save rule',exact=True).click()
                    expect(page.locator('#rules-message')).to_contain_text('Saved.')
                    page.get_by_role('button',name='Close name rules').click()
                    expect(page.locator('#party-rows tr')).to_have_count(1)
                    expect(page.locator('#party-rows')).to_contain_text('Example Holdings')
                    expect(page.locator('#exports-stale')).not_to_be_visible()
                    expect(page.locator('#run-status')).to_have_text('READY',timeout=10000)
                    if capture:
                        page.screenshot(path=str(Path(capture)/'name-rule-grouped.png'),full_page=True)
                    page.locator('#view-clients').click()
                    page.get_by_role('button',name='Example Holdings',exact=True).click()
                    expect(page.locator('#party-source-names')).to_contain_text('Example Client')
                    expect(page.locator('#party-source-names')).not_to_contain_text('Another Defendant')
                    page.locator('#party-case-list').get_by_role('button',name='1:24-cr-00001').click()
                    expect(page.locator('#detail-nature')).to_contain_text('FRAUD BY WIRE')
                    expect(page.locator('#detail-nature')).not_to_contain_text('ASSAULT')
                    page.get_by_role('button',name='Close case').click();page.get_by_role('button',name='Close party').click()
                    with page.expect_download() as download:
                        page.get_by_role('button',name='Excel ↓',exact=True).click()
                    target=Path(tmp)/'download.xlsx';download.value.save_as(target)
                    book=load_workbook(target)
                    self.assertEqual(book['Name rules']['A2'].value,'Example Holdings')
                    self.assertEqual(book['Clients']['B4'].value,1)
                    # Reopening the UI keeps the saved rule and allows editing/removal.
                    page.reload();page.get_by_role('button',name='Jordan Lawyer').click()
                    page.get_by_role('button',name='Name rules',exact=True).click()
                    page.get_by_role('button',name='Edit Example Holdings',exact=True).click()
                    page.get_by_label('Preferred report name').fill('Renamed Holdings')
                    page.get_by_role('button',name='Preview change',exact=True).click()
                    expect(page.locator('#rule-comparisons')).to_contain_text('Renamed Holdings')
                    page.get_by_role('button',name='Save rule',exact=True).click()
                    expect(page.locator('#rules-message')).to_contain_text('Saved.')
                    expect(page.locator('#new-rule')).to_be_enabled(timeout=10000)
                    page.get_by_role('button',name='Edit Renamed Holdings',exact=True).click()
                    page.get_by_role('button',name='Remove rule…').click()
                    expect(page.locator('#rule-preview-heading')).to_have_text('Remove this rule')
                    page.get_by_role('button',name='Confirm removal').click()
                    expect(page.locator('#rules-message')).to_contain_text('Saved.')
                    expect(page.locator('#undo-rule')).to_be_enabled(timeout=10000)
                    page.get_by_role('button',name='Undo last change').click()
                    expect(page.locator('#rule-preview-heading')).to_have_text('Undo the last saved change')
                    page.get_by_role('button',name='Confirm undo').click()
                    expect(page.locator('#rules-message')).to_contain_text('Saved.')
                    expect(page.locator('#new-rule')).to_be_enabled(timeout=10000)
                    # A conflicting second rule cannot silently steal an existing member.
                    page.get_by_role('button',name='New rule',exact=True).click()
                    page.get_by_label('Preferred report name').fill('Conflicting group')
                    page.get_by_label('Source names, one per line').fill('Example Client')
                    page.get_by_role('button',name='Preview change',exact=True).click()
                    expect(page.locator('#rules-message')).to_contain_text('already in another rule')
                    self.assertEqual(len(ws.name_rules.public()['rules']),1)
                    self.assertEqual(ws.name_rules.public()['rules'][0]['label'],'Renamed Holdings')
                    self.assertEqual(ws.receipts(identifier)['spentCents'],0)
                    self.assertEqual(blocked,[]);self.assertEqual(errors,[])
                    browser.close()
            finally:
                server.shutdown();server.server_close();thread.join(10);ws.close()
