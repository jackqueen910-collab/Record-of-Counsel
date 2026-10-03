"""End-to-end local UI with mocked Anthropic/court responses and blocked network."""
import contextlib
import io
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

from roc.common import read_json, write_json
from roc.interface import make_server
from roc.workspace import Workspace
from roc.pacer import Session
from roc.ai_credentials import ProjectAIKey
from tests.test_ai_credentials import MemoryCredentialStore
from tests.test_document_grabber import CASE, fixture, reply, FakeTransport


@unittest.skipUnless(os.environ.get('ROC_BROWSER_TESTS') == '1','Set ROC_BROWSER_TESTS=1')
class DocumentBrowserTests(unittest.TestCase):
    def test_analysis_candidate_review_purchase_and_download_without_external_network(self):
        from playwright.sync_api import sync_playwright, expect
        with tempfile.TemporaryDirectory() as folder, contextlib.redirect_stdout(io.StringIO()):
            owner_store=MemoryCredentialStore()
            ws=Workspace(folder,ai_credentials=ProjectAIKey(owner_store,{}));ident=ws.new(demo=True);ws.future.result(10)
            rawpath=ws.folder(ident)/'demo-docket.html';rawpath.write_text(fixture(),encoding='utf-8')
            evidence_path=ws.folder(ident)/'output/evidence.json';evidence=read_json(evidence_path)
            evidence['cases'][0].update(CASE);write_json(evidence_path,evidence)
            manifest=ws.manifest(ident);manifest['demo']=False;ws.save(ident,manifest)
            # A non-demo run needs the completed PCL index, just like a real
            # search. Evidence alone may belong to a paused partial search.
            write_json(ws.folder(ident)/'pcl-records.json',read_json(ws.folder(ident)/'demo-index.json'))
            self.assertTrue(ws.summary(ident)['indexReady'])
            ws.connection.session=Session('test-pacer-token')
            reports={CASE['key']:{'path':str(rawpath)}};transport=FakeTransport()
            server,url=make_server(ws);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                with patch.object(ws,'completed_reports',return_value=reports), \
                     patch('roc.claude.request_claude',return_value=reply()) as ai, \
                     patch('roc.document_download.CourtDocumentHTTP',return_value=transport), \
                     patch('roc.pacer.request_json',side_effect=AssertionError('No PACER network')), sync_playwright() as pw:
                    browser=pw.chromium.launch(headless=True);page=browser.new_page(viewport={'width':1440,'height':1100})
                    errors=[];blocked=[]
                    page.on('pageerror',lambda e:errors.append(str(e)))
                    def route(r):
                        if urlsplit(r.request.url).netloc != urlsplit(url).netloc:
                            blocked.append(r.request.url);r.abort()
                        else: r.continue_()
                    page.route('**/*',route);page.goto(url)
                    page.get_by_role('button',name='Jordan Lawyer').click()
                    expect(page.locator('#run-status')).to_have_text('READY')
                    expect(page.locator('#case-rows input[type=checkbox]')).to_be_enabled()
                    page.locator('#case-rows input[type=checkbox]').check()
                    page.locator('#open-documents').click()
                    expect(page.locator('#documents-method')).to_contain_text('signature block')
                    page.locator('#preview-analysis').click()
                    expect(page.locator('#analysis-price')).to_contain_text('Maximum new AI reservation')
                    self.assertEqual(ai.call_count,0);self.assertEqual(len(transport.requests),0)
                    page.locator('#ai-cap').fill('2.00')
                    expect(page.locator('#confirm-analysis')).to_be_disabled()
                    expect(page.locator('#claude-status')).to_contain_text('AI setup needed')
                    expect(page.locator('#claude-key, #claude-form, #claude-disconnect')).to_have_count(0)
                    # Owner provisioning happens outside the regular browser UI.
                    owner_store.key='test-private-key'
                    page.locator('#document-refresh').click()
                    expect(page.locator('#claude-status')).to_contain_text('ROC account configured')
                    expect(page.locator('#confirm-analysis')).to_be_enabled()
                    rejected=page.request.post(urlsplit(url)._replace(path='/api/claude',fragment='').geturl(),
                        headers={'X-ROC-Token':urlsplit(url).fragment},data={'apiKey':'unapproved-browser-key'})
                    self.assertEqual(rejected.status,404)
                    self.assertEqual(owner_store.key,'test-private-key')
                    page.locator('#confirm-analysis').click()
                    expect(page.locator('.document-candidate')).to_have_count(3,timeout=15000)
                    self.assertEqual(ai.call_count,1);self.assertEqual(len(transport.requests),0)
                    self.assertEqual(ai.call_args.args[0],'test-private-key')
                    self.assertNotIn('test-private-key',page.content())
                    motion=page.locator('.document-candidate').nth(0)
                    motion.locator('summary').click()
                    expect(motion).to_contain_text('Client attribution · entry 10 (01/02/2024): as to Example Client (1)')
                    motion.locator('summary').click()
                    order=page.locator('.document-candidate').nth(1)
                    order.locator('summary').click()
                    expect(order).to_contain_text('Order link · entry 10 (01/02/2024): ORDER denying motion 10')
                    order.locator('summary').click()
                    boxes=page.locator('.document-candidate input')
                    expect(boxes.nth(0)).not_to_be_checked();expect(boxes.nth(1)).to_be_disabled();expect(boxes.nth(2)).to_be_disabled()
                    boxes.nth(0).check();page.locator('#preview-documents').click()
                    expect(page.locator('#purchase-price')).to_contain_text('$3.00')
                    page.locator('#document-cap').fill('2.99');expect(page.locator('#confirm-documents')).to_be_disabled()
                    page.locator('#document-cap').fill('3.00')
                    # Save desktop/mobile evidence before the explicit purchase.
                    output=Path('runs/document-grabber-check');output.mkdir(parents=True,exist_ok=True)
                    page.locator('#documents-dialog').screenshot(path=str(output/'document-grabber-desktop.png'))
                    page.set_viewport_size({'width':390,'height':844})
                    page.locator('#documents-dialog').screenshot(path=str(output/'document-grabber-mobile.png'))
                    page.set_viewport_size({'width':1440,'height':1100})
                    page.locator('#confirm-documents').click()
                    expect(page.locator('#documents-dialog')).not_to_be_visible()
                    expect(page.locator('#progress-message')).to_contain_text('Selected documents saved',timeout=15000)
                    self.assertEqual(len(transport.requests),2)
                    page.locator('#open-documents').click()
                    expect(page.locator('#document-spending')).to_contain_text('Documents: $0.20')
                    with page.expect_download() as download:
                        page.locator('#document-bundle').click()
                    self.assertEqual(download.value.suggested_filename,'roc-document-bundle.zip')
                    self.assertFalse(errors);self.assertFalse(blocked)
                    self.assertNotIn('test-private-key',''.join(p.read_text(errors='ignore') for p in Path(folder).rglob('*.json')))
                    browser.close()
            finally:
                server.shutdown();server.server_close();thread.join(10);ws.close()
