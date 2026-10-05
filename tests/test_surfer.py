"""Manual purchases, library reuse, stale previews and receipt safety. No providers."""
import tempfile
import unittest
import zipfile
from unittest.mock import patch

from roc.common import RocError, read_json, write_json
from roc.exports import output_directory
from roc.workspace import Workspace
from roc.pacer import Session
from roc.document_ledger import ExpenseLedger
from roc.ai_credentials import ProjectAIKey
from roc.documents import classify_result
from tests.test_ai_credentials import MemoryCredentialStore
from tests.test_document_grabber import fixture, CASE, FakeTransport, price, source, candidates


def prepared_workspace(folder):
    ws=Workspace(folder,session_provider=lambda:Session('synthetic'),ai_credentials=ProjectAIKey(MemoryCredentialStore(),{}))
    return ws, prepare_run(ws)


def prepare_run(ws):
    run=ws.new(demo=True);ws.future.result(10)
    root=ws.folder(run);(root/'saved.html').write_text(fixture(),encoding='utf-8')
    m=ws.manifest(run);m['demo']=False;ws.save(run,m)
    write_json(root/'pcl-records.json',read_json(root/'demo-index.json'))
    path=output_directory(root)/'evidence.json';data=read_json(path);data['cases'][0].update(CASE);write_json(path,data)
    write_json(root/'ledger.json',{'transactions':[{'kind':'docket','key':'fixture','state':'complete','chargedCents':20,'reservedCents':300,
        'startedUtc':'2026-10-05T12:00:00Z','completedUtc':'2026-10-05T12:00:01Z','responseFile':'saved.html',
        'parameters':{'court':'nysdc','caseNumber':'1:24-cr-00001','scope':'all-defendants','partiesAndCounsel':True}}]})
    return run


class SurferTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.ws,self.run=prepared_workspace(self.temp.name);self.addCleanup(self.ws.close)

    def quote(self):return self.ws.surfer.quote(self.run,{'caseKey':CASE['key'],'entryIds':['e1']})

    def test_browsing_all_entries_needs_no_client_match_or_model(self):
        m=self.ws.manifest(self.run);m['config']['lawyer']={'firstName':'Unmatched','lastName':'Lawyer'};self.ws.save(self.run,m)
        with patch('roc.claude.request_claude',side_effect=AssertionError('No AI')):
            view=self.ws.surfer.case(self.run,CASE['key'])
            self.assertEqual(len(view['source']['entries']),6)
            self.assertEqual(view['source']['clients'],[])
            self.assertEqual(view['source']['entries'][2]['url'],'')
            self.assertEqual(view['source']['entries'][3]['url'],'')
            self.assertEqual(len(self.ws.library.public()['files']),1)
        with self.assertRaises(RocError):self.ws.surfer.quote(self.run,{'caseKey':CASE['key'],'entryIds':['e4']})
        with self.assertRaisesRegex(RocError,'no analyzed'):self.ws.grabber.bundle(self.run)

    def test_purchase_then_reopen_and_zip_reuses_saved_pdf(self):
        q=self.quote();self.assertEqual(q['maximumCents'],300)
        transport=FakeTransport()
        with patch('roc.document_download.CourtDocumentHTTP',return_value=transport),patch('roc.claude.request_claude',side_effect=AssertionError('No AI')):
            self.ws.act(self.run,'surfer-download',q|{'budgetCents':300});self.ws.future.result(10)
        self.assertEqual(self.ws.summary(self.run)['state'],'ready',self.ws.summary(self.run)['message'])
        self.assertEqual(len(transport.requests),2)
        self.assertEqual(self.quote()['maximumCents'],0)
        files=self.ws.library.public()['files'];self.assertEqual(len(files),2)
        pdf=next(e for e in files if e['kind']=='pdf');self.assertEqual(pdf['costCents'],20)
        self.assertEqual(self.ws.surfer.case(self.run,CASE['key'])['source']['entries'][0]['savedFileId'],pdf['id'])
        bundle=self.ws.library.bundle([e['id'] for e in files])
        with zipfile.ZipFile(bundle) as z:
            self.assertEqual(len(z.namelist()),3)
            self.assertEqual(sum(n.endswith('.pdf') for n in z.namelist()),1)
        q=self.quote()
        with patch('roc.document_download.CourtDocumentHTTP',side_effect=AssertionError('No repeat purchase')):
            self.ws.act(self.run,'surfer-download',q|{'budgetCents':0});self.ws.future.result(10)
        self.assertEqual(self.ws.summary(self.run)['state'],'ready')
        self.ws.library.file(pdf['id'])[0].unlink()
        with self.assertRaisesRegex(RocError,'missing'):self.quote()

    def test_source_change_budget_and_unknown_receipt_block_purchase(self):
        q=self.quote()
        with self.assertRaisesRegex(RocError,'cap'):self.ws.act(self.run,'surfer-download',q|{'budgetCents':299})
        p=self.ws.folder(self.run)/'saved.html';p.write_text(fixture().replace('MOTION to dismiss indictment','MOTION to dismiss superseding indictment'),encoding='utf-8')
        with self.assertRaisesRegex(RocError,'preview changed'):self.ws.act(self.run,'surfer-download',q|{'budgetCents':300})
        q=self.quote();transport=FakeTransport(body=b'unknown charged viewer')
        with patch('roc.document_download.CourtDocumentHTTP',return_value=transport):
            self.ws.act(self.run,'surfer-download',q|{'budgetCents':300});self.ws.future.result(10)
        self.assertEqual(self.ws.summary(self.run)['state'],'stopped')
        with self.assertRaisesRegex(RocError,'unresolved'):self.quote()

    def test_prior_grabber_unknown_charge_blocks_manual_purchases(self):
        ledger=ExpenseLedger(self.ws.folder(self.run)/'documents','documents');ledger.approve(300)
        ledger.reserve('previous',300,{'caseKey':CASE['key']})
        with self.assertRaisesRegex(RocError,'unresolved'):self.quote()

    def test_attachment_menu_requires_new_explicit_selection_and_cap(self):
        menu = '''<h2>Document Selection Menu</h2>Case Number: 1:24-cr-00001<br>Document Number: 10
        <table><tr><td>Main Document</td><td><a href="/doc1/8888">Motion</a></td></tr>
        <tr><td>Attachment 1</td><td><a href="/doc1/9999">Declaration</a></td></tr></table>'''
        transport=FakeTransport(landing=menu.encode())
        with patch('roc.document_download.CourtDocumentHTTP',return_value=transport):
            self.ws.act(self.run,'surfer-download',self.quote()|{'budgetCents':300});self.ws.future.result(10)
        self.assertEqual(len(transport.requests),1)
        ledger=ExpenseLedger(self.ws.surfer.root,'documents')
        self.assertEqual(ledger.spent,0);ledger.check()
        view=self.ws.surfer.case(self.run,CASE['key'])
        self.assertEqual([e['number'] for e in view['source']['entries'][:3]],['10','10','10-1'])
        self.assertEqual(view['source']['entries'][0]['url'],'')
        q=self.ws.surfer.quote(self.run,{'caseKey':CASE['key'],'entryIds':['e1d2']})
        self.assertEqual(q['maximumCents'],300)
        transport=FakeTransport(landing=price(number='10-1').encode())
        with patch('roc.document_download.CourtDocumentHTTP',return_value=transport):
            self.ws.act(self.run,'surfer-download',q|{'budgetCents':300});self.ws.future.result(10)
        self.assertEqual(self.ws.summary(self.run)['state'],'ready',self.ws.summary(self.run)['message'])
        pdf=next(e for e in self.ws.library.public()['files'] if e['kind']=='pdf')
        self.assertEqual(pdf['entryNumber'],'10-1')

    def test_unknown_menu_keeps_reservation_and_never_follows_external_link(self):
        menu='<h2>Document Selection Menu</h2>Case Number: 1:24-cr-00001<br>Document Number: 10<table><tr><td>1</td><td><a href="https://example.invalid/doc1/9999">Attachment</a></td></tr></table>'
        transport=FakeTransport(landing=menu.encode())
        with patch('roc.document_download.CourtDocumentHTTP',return_value=transport):
            self.ws.act(self.run,'surfer-download',self.quote()|{'budgetCents':300});self.ws.future.result(10)
        self.assertEqual(len(transport.requests),1)
        self.assertEqual(self.ws.summary(self.run)['state'],'stopped')
        with self.assertRaisesRegex(RocError,'unresolved'):self.quote()

    def test_optional_ai_flow_reuses_manual_purchase_and_includes_it_in_bundle(self):
        transport=FakeTransport()
        with patch('roc.document_download.CourtDocumentHTTP',return_value=transport):
            self.ws.act(self.run,'surfer-download',self.quote()|{'budgetCents':300});self.ws.future.result(10)
        s=source();rows=classify_result(candidates(),s)
        write_json(self.ws.grabber.root(self.run)/'results.json',{'cases':{CASE['key']:{'source':s,'candidates':rows,'analysisId':'fixture'}}})
        q=self.ws.grabber.purchase_quote(self.run,{'candidateIds':[rows[0]['candidateId']]})
        self.assertEqual(q['maximumCents'],0)
        with patch('roc.grabber.download_document',side_effect=AssertionError('No duplicate purchase')):
            self.ws.act(self.run,'documents-download',q|{'budgetCents':0});self.ws.future.result(10)
        self.assertEqual(self.ws.summary(self.run)['state'],'ready')
        with zipfile.ZipFile(self.ws.grabber.bundle(self.run)) as archive:
            self.assertEqual(sum(n.endswith('.pdf') for n in archive.namelist()),1)

    def test_library_and_docket_paths_cannot_escape_workspace(self):
        with self.assertRaises(RocError):self.ws.library.file('../../secret')
        ledger=self.ws.ledger(self.run);ledger['transactions'][0]['responseFile']='../../outside.html';write_json(self.ws.folder(self.run)/'ledger.json',ledger)
        with self.assertRaises(RocError):self.ws.library.public()
