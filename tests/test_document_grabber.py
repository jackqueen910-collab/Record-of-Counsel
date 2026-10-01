"""No external calls: source attribution, budgets, recovery and saved PDF workflow."""
import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from roc.common import RocError, read_json, write_json
from roc.claude import analyze, estimate, payload
from roc.ai_credentials import ProjectAIKey
from tests.test_ai_credentials import MemoryCredentialStore
from roc.document_download import download_document, purchase_form
from roc.document_ledger import ExpenseLedger
from roc.documents import read_entries, classify_result, document_url
from roc.workspace import Workspace
from roc.pacer import Session

ORIGIN = 'https://ecf.nysd.uscourts.gov'
CASE = {'key': 'nysdc|1:24-cr-00001', 'caseNumber': '1:24-cr-00001', 'caseType': 'Criminal',
        'courtId': 'nysdc', 'district': 'Southern District of New York', 'caseTitle': 'USA v Example Client',
        'pacerLink': ORIGIN + '/cgi-bin/iqquerymenu.pl?1'}
HISTORY = '''<table><tr><th>Date Filed</th><th>#</th><th>Docket Text</th></tr>
<tr><td>01/02/2024</td><td><a href="/doc1/123456">10</a></td><td>MOTION to dismiss indictment as to Example Client (1). (Lawyer, Jordan)</td></tr>
<tr><td>01/03/2024</td><td><a href="/doc1/123457">11</a></td><td>MOTION to dismiss as to Another Defendant (2). (Other, Counsel)</td></tr>
<tr><td>01/04/2024</td><td>12</td><td>ORDER denying motion 10 as to Example Client (1).</td></tr>
<tr><td>01/05/2024</td><td><a href="https://attacker.invalid/doc1/99">13</a></td><td>MOTION for summary judgment filed by Example Client.</td></tr>
<tr><td>01/06/2024</td><td><a href="/doc1/123459">14</a></td><td>ORDER denying motion 11 as to Another Defendant (2).</td></tr>
<tr><td>01/07/2024</td><td><a href="/doc1/123460">15</a></td><td>ORDER concerning motions. Hearing on January 10.</td></tr></table>'''


def fixture():
    return (Path(__file__).parents[1] / 'roc/ui/demo-docket.html').read_text(encoding='utf-8').replace('</body>', HISTORY + '</body>')


def source():
    return read_entries(fixture(), CASE, ['Jordan Lawyer'])


def candidate(ident='e1', kind='motion', client='Example Client', quote='as to Example Client (1)', motions=None, evidence='MOTION to dismiss indictment', attribution=None, links=None):
    return {'entryId': ident, 'kind': kind, 'client': client,
            'attributionEvidence': attribution if attribution is not None else ([{'entryId':ident,'quote':quote}] if quote else []),
            'motionIds': motions or [],
            'linkEvidence': links if links is not None else [{'motionId':m,'quote':evidence} for m in (motions or [])],
            'evidenceQuote': evidence, 'reason': 'Fictional manually reviewed example.'}


def candidates():
    return {'candidates': [candidate(), candidate('e3','order',quote='',motions=['e1'],evidence='ORDER denying motion 10'),
                           candidate('e4','uncertain',quote='',evidence='MOTION for summary judgment')]}


def reply(result=None, stop='end_turn'):
    return json.dumps({'model':'claude-sonnet-5-5','stop_reason':stop,'usage':{'input_tokens':1200,'output_tokens':300},
                       'content':[{'type':'text','text':json.dumps(result or candidates())}]})


def price(number='10', cost='0.20'):
    return f'''<html><body>PACER Service Center<br>Case Number: 1:24-cr-00001<br>Document Number: {number}<br>
    Billable Pages: 2<br>Cost: ${cost}<form method="POST" action="/cgi-bin/show_doc.pl">
    <input type="hidden" name="document_id" value="123456"><input type="submit" name="commit" value="View Document"></form></body></html>'''


class FakeTransport:
    def __init__(self, landing=None, body=None):
        self.landing = price().encode() if landing is None else landing
        self.body = b'%PDF-1.4\nfictional fixture\n%%EOF' if body is None else body
        self.requests = []

    def request(self, url, fields=None):
        self.requests.append((url, fields))
        return self.landing if fields is None else self.body


class DocumentTests(unittest.TestCase):
    def test_source_clients_entries_links_and_court_identity(self):
        s = source()
        self.assertEqual(s['clients'], ['Example Client'])
        self.assertEqual(len(s['entries']), 6)
        self.assertEqual(s['entries'][0]['url'], ORIGIN+'/doc1/123456')
        self.assertFalse(s['entries'][2]['url'])
        self.assertFalse(s['entries'][3]['url'])
        with self.assertRaises(RocError):
            read_entries(fixture(), CASE | {'caseNumber':'1:24-cr-99999'}, ['Jordan Lawyer'])
        with self.assertRaises(RocError):
            read_entries(fixture(), CASE, ['Wrong Counsel'])
        for url in ['https://example.com/doc1/123', '/cgi-bin/show_doc.pl?1', '/doc1/1?download=1','javascript:alert(1)']:
            with self.assertRaises(RocError): document_url(url, ORIGIN)
        civil = fixture().replace('CRIMINAL DOCKET','CIVIL DOCKET').replace('1:24-cr-', '1:24-cv-')
        self.assertEqual(read_entries(civil, CASE | {'caseNumber':'1:24-cv-00001'}, ['Jordan Lawyer'])['clients'], ['Example Client'])

    def test_qualified_client_and_order_only_not_other_defendant(self):
        result = classify_result(candidates(), source())
        self.assertEqual([c['status'] for c in result], ['matched','matched','needs-review'])
        self.assertEqual(result[1]['client'], 'Example Client')
        bad = candidate('e2',client='Another Defendant',quote='as to Another Defendant (2)',evidence='MOTION to dismiss')
        self.assertEqual(classify_result({'candidates':[bad]}, source())[0]['status'],'needs-review')
        filed_by = candidate('e4',quote='filed by Example Client',evidence='MOTION for summary judgment')
        self.assertEqual(classify_result({'candidates':[filed_by]}, source())[0]['status'],'matched')
        bad = candidate('e5','order',quote='',motions=['e2'],evidence='ORDER denying motion 11')
        self.assertEqual(classify_result({'candidates':[candidate(),bad]},source())[1]['status'],'needs-review')
        bad = candidate('e6','order',quote='',motions=['e1'],evidence='ORDER concerning motions',links=[])
        self.assertEqual(classify_result({'candidates':[candidate(),bad]},source())[1]['status'],'needs-review')
        for c in [candidate('invented'), candidate(evidence='not on the docket')]:
            with self.assertRaises(RocError): classify_result({'candidates':[c]},source())
        with self.assertRaises(RocError): classify_result({'candidates':[candidate(),candidate()]},source())

    def test_ai_caps_cache_stop_reason_and_no_automatic_retry(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); model='claude-sonnet-5-5'; s=source(); calls=[]
            request=payload(s,model)
            self.assertNotIn('url', request['messages'][0]['content'])
            ledger=ExpenseLedger(root,'ai'); ledger.approve(estimate(request)-1)
            with self.assertRaises(RocError): analyze(root,s,model,'test-key',requester=lambda *a:calls.append(a))
            self.assertFalse(calls)
            ledger.approve(100)
            out=analyze(root,s,model,'test-key',requester=lambda *a: reply())
            self.assertEqual(len(out['candidates']),3)
            out2=analyze(root,s,model,'',requester=lambda *a:self.fail('cache must avoid network'))
            self.assertEqual(out,out2)
            self.assertNotIn('test-key', ''.join(p.read_text() for p in root.rglob('*.json')))
            self.assertEqual(ExpenseLedger(root,'ai').spent,1)
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); ExpenseLedger(root,'ai').approve(100)
            with self.assertRaises(RocError): analyze(root,s,model,'key',requester=lambda *a: reply(stop='max_tokens'))
            self.assertEqual(ExpenseLedger(root,'ai').spent,1)
            with self.assertRaises(RocError): analyze(root,s,model,'key',requester=lambda *a:self.fail('must not retry'))
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); ExpenseLedger(root,'ai').approve(100)
            def failed(*a): raise RocError('timeout')
            with self.assertRaises(RocError): analyze(root,s,model,'key',requester=failed)
            with self.assertRaises(RocError): ExpenseLedger(root,'ai').approve(10000)
            with self.assertRaises(RocError): analyze(root,s,model,'key',requester=lambda *a:self.fail('unknown request replay'))

    def test_semantic_attribution_accepts_varied_language_and_cited_context(self):
        # These are stipulated model judgments, not claims of live model accuracy.
        for text in ('MOTION to dismiss. Document filed by Example Client.',
                     'Through counsel, Example Client asks the Court to dismiss Counts Three and Four.',
                     'The joint request of Example Client and Another Defendant seeks dismissal.'):
            s=copy.deepcopy(source());s['entries'][0]['text']=text
            c=candidate(quote=text,evidence=text)
            self.assertEqual(classify_result({'candidates':[c]},s)[0]['status'],'matched')
        s=copy.deepcopy(source())
        s['entries'][0]['text']='Defendant 1 moves to dismiss the indictment.'
        s['entries'][1]['text']='Example Client is designated as defendant 1.'
        c=candidate(quote='',evidence=s['entries'][0]['text'],attribution=[
            {'entryId':'e1','quote':s['entries'][0]['text']},
            {'entryId':'e2','quote':s['entries'][1]['text']}])
        self.assertEqual(classify_result({'candidates':[c]},s)[0]['status'],'matched')
        c['attributionEvidence']=c['attributionEvidence'][1:]
        self.assertEqual(classify_result({'candidates':[c]},s)[0]['status'],'needs-review')

    def test_semantic_attribution_still_rejects_fabricated_evidence_and_unknown_clients(self):
        for item in ({'entryId':'invented','quote':'as to Example Client (1)'},
                     {'entryId':'e2','quote':'as to Example Client (1)'},
                     {'entryId':'e1','quote':'This quote is not in the docket.'}):
            with self.assertRaises(RocError):
                classify_result({'candidates':[candidate(attribution=[item])]},source())
        for c in (candidate(client='Example Clien'),candidate(attribution=[]),candidate()|{'reason':''}):
            self.assertEqual(classify_result({'candidates':[c]},source())[0]['status'],'needs-review')
        # A client name in an opposing filing is not automatically a match.
        s=copy.deepcopy(source());s['entries'][0]['text']='Government motion to dismiss as to Example Client.'
        c=candidate(kind='uncertain',quote=s['entries'][0]['text'],evidence=s['entries'][0]['text'])
        c['reason']='The named client is affected; the Government filed the motion.'
        self.assertEqual(classify_result({'candidates':[c]},s)[0]['status'],'needs-review')

    def test_semantic_order_link_without_docket_number_requires_grounded_link_evidence(self):
        s=copy.deepcopy(source())
        s['entries'][2]['text']='The defendant’s request to dismiss the indictment is GRANTED.'
        order=candidate('e3','order',quote='',motions=['e1'],evidence=s['entries'][2]['text'])
        order['reason']='This ruling grants the only supplied request to dismiss this indictment.'
        rows=classify_result({'candidates':[candidate(),order]},s)
        self.assertEqual(rows[1]['status'],'matched')
        self.assertEqual(rows[1]['client'],'Example Client')
        missing=copy.deepcopy(order);missing['linkEvidence']=[]
        self.assertEqual(classify_result({'candidates':[candidate(),missing]},s)[1]['status'],'needs-review')
        for link in ({'motionId':'invented','quote':order['evidenceQuote']},
                     {'motionId':'e1','quote':'MOTION to dismiss indictment'}):
            with self.assertRaises(RocError):
                classify_result({'candidates':[candidate(),order|{'linkEvidence':[link]}]},s)
        ambiguous=order|{'kind':'uncertain','reason':'Several requests could fit this ruling.'}
        self.assertEqual(classify_result({'candidates':[candidate(),ambiguous]},s)[1]['status'],'needs-review')

    def test_policy_change_does_not_silently_reuse_or_resubmit_old_analysis(self):
        from roc.claude import analysis_key
        s=source();current=analysis_key(s,'claude-sonnet-5-5')
        with patch('roc.claude.POLICY_VERSION',1):
            self.assertNotEqual(current,analysis_key(s,'claude-sonnet-5-5'))
        legacy=candidate();legacy.pop('attributionEvidence');legacy.pop('linkEvidence')
        legacy['asToQuote']='as to Example Client (1)'
        with self.assertRaises(RocError): classify_result({'candidates':[legacy]},s)

    def test_cost_form_rejects_wrong_case_attachments_transcripts_and_unsafe_targets(self):
        url=ORIGIN+'/doc1/123456'
        self.assertEqual(purchase_form(price(),url,CASE['caseNumber'],'10')['priceCents'],20)
        for html in [price('11'),price(cost='4.00'),price().replace('1:24-cr-00001','1:24-cr-00002'),
                     price().replace('/cgi-bin/show_doc.pl','https://evil.invalid/'),price()+'Transcript',
                     price().replace('type="hidden"','type="checkbox"')]:
            with self.assertRaises(RocError): purchase_form(html,url,CASE['caseNumber'],'10')

    def test_download_budget_single_post_cache_and_uncertain_pdf(self):
        s=source(); c=classify_result(candidates(),s)[0]
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); transport=FakeTransport(); ledger=ExpenseLedger(root,'documents');ledger.approve(299)
            with self.assertRaises(RocError): download_document(root,s,c,None,transport=transport)
            self.assertFalse(transport.requests)
            ledger.approve(300);path=download_document(root,s,c,None,transport=transport)
            self.assertTrue(path.read_bytes().startswith(b'%PDF'))
            self.assertEqual(len(transport.requests),2)
            self.assertEqual(ExpenseLedger(root,'documents').spent,20)
            self.assertEqual(download_document(root,s,c,None,transport=transport),path)
            self.assertEqual(len(transport.requests),2)
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);ExpenseLedger(root,'documents').approve(300)
            transport=FakeTransport(body=b'<html>unfamiliar viewer</html>')
            with self.assertRaises(RocError): download_document(root,s,c,None,transport=transport)
            with self.assertRaises(RocError): download_document(root,s,c,None,transport=transport)
            self.assertEqual(len(transport.requests),2)
            self.assertEqual(ExpenseLedger(root,'documents').public()['pendingCount'],1)
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);ExpenseLedger(root,'documents').approve(300)
            transport=FakeTransport(landing=price('99').encode())
            with self.assertRaises(RocError): download_document(root,s,c,None,transport=transport)
            self.assertEqual(len(transport.requests),1)
            self.assertEqual(ExpenseLedger(root,'documents').public()['pendingCount'],0)

    def test_workspace_analysis_download_bundle_exact_quotes_and_no_research_side_effects(self):
        with tempfile.TemporaryDirectory() as folder, contextlib.redirect_stdout(io.StringIO()):
            ws=Workspace(folder,lambda:Session('fictional-token'),ai_credentials=ProjectAIKey(MemoryCredentialStore('test-key'),{}))
            try:
                ident=ws.new(demo=True);ws.future.result(10)
                path=ws.folder(ident)/'demo-docket.html';path.write_text(fixture(),encoding='utf-8')
                reports={CASE['key']:{'path':str(path)}}
                m=ws.manifest(ident);m['demo']=False;ws.save(ident,m)
                with patch.object(ws,'completed_reports',return_value=reports), patch.object(ws,'cases',return_value=[CASE]), \
                     patch('roc.claude.request_claude',return_value=reply()) as ai, patch('roc.grabber.download_document') as dl:
                    q=ws.grabber.analysis_quote(ident,{'keys':[CASE['key']],'model':'claude-sonnet-5-5'})
                    self.assertEqual(ai.call_count,0)
                    with self.assertRaises(RocError): ws.act(ident,'documents-analyze',q|{'quoteId':'stale','budgetCents':100})
                    ws.act(ident,'documents-analyze',q|{'budgetCents':100});ws.future.result(10)
                    self.assertEqual(ws.manifest(ident)['state'],'ready')
                    self.assertEqual(ai.call_count,1);self.assertEqual(dl.call_count,0)
                    rows=ws.grabber.state(ident)['results']['cases'][CASE['key']]['candidates']
                    self.assertEqual(len(rows),3)
                    with self.assertRaises(RocError): ws.grabber.purchase_quote(ident,{'candidateIds':[rows[1]['candidateId']]})
                    pq=ws.grabber.purchase_quote(ident,{'candidateIds':[rows[0]['candidateId']]})
                    self.assertEqual(pq['maximumCents'],300)
                    result_path=ws.grabber.root(ident)/'results.json'
                    changed=read_json(result_path)
                    changed['cases'][CASE['key']]['candidates'][0]['client']='A different client'
                    write_json(result_path,changed)
                    with self.assertRaises(RocError): ws.act(ident,'documents-download',pq|{'budgetCents':300})
                    changed['cases'][CASE['key']]['candidates'][0]['client']='Example Client'
                    write_json(result_path,changed)
                    with self.assertRaises(RocError): ws.act(ident,'documents-download',pq|{'budgetCents':299})
                    ws.act(ident,'documents-download',pq|{'budgetCents':300});ws.future.result(10)
                    self.assertEqual(dl.call_count,1)
                    bundle=ws.grabber.bundle(ident)
                    with zipfile.ZipFile(bundle) as z:
                        self.assertIn('signature block',z.read('methodology.txt').decode())
                        self.assertIn('ORDER denying motion 10',z.read('index.csv').decode())
                    self.assertFalse(ws.ledger(ident)['transactions'])
            finally: ws.close()

    def test_owner_key_required_before_approval_and_cached_analysis_survives_its_removal(self):
        with tempfile.TemporaryDirectory() as folder, contextlib.redirect_stdout(io.StringIO()):
            store=MemoryCredentialStore()
            ws=Workspace(folder,ai_credentials=ProjectAIKey(store,{}))
            try:
                ident=ws.new(demo=True);ws.future.result(10)
                raw=ws.folder(ident)/'demo-docket.html';raw.write_text(fixture(),encoding='utf-8')
                m=ws.manifest(ident);m['demo']=False;ws.save(ident,m)
                with patch.object(ws,'completed_reports',return_value={CASE['key']:{'path':str(raw)}}), \
                     patch.object(ws,'cases',return_value=[CASE]),patch('roc.claude.request_claude',return_value=reply()) as ai:
                    q=ws.grabber.analysis_quote(ident,{'keys':[CASE['key']]})
                    with self.assertRaisesRegex(RocError,'owner'):
                        ws.act(ident,'documents-analyze',q|{'budgetCents':100})
                    self.assertFalse(ai.called)
                    self.assertNotIn('documentOperation',ws.manifest(ident))
                    self.assertFalse((ws.grabber.root(ident)/'ai-ledger.json').exists())
                    store.key='fictional-owner-key'
                    ws.act(ident,'documents-analyze',q|{'budgetCents':100});ws.future.result(10)
                    self.assertEqual(ai.call_count,1)
                    self.assertEqual(ai.call_args.args[0],'fictional-owner-key')
                    store.key=''
                    self.assertFalse(ws.grabber.state(ident)['configured'])
                    cached=ws.grabber.analysis_quote(ident,{'keys':[CASE['key']]})
                    self.assertEqual(cached['maximumCents'],0)
                    ws.act(ident,'documents-analyze',cached|{'budgetCents':0});ws.future.result(10)
                    self.assertEqual(ws.manifest(ident)['state'],'ready')
                    self.assertEqual(ai.call_count,1)
                    self.assertEqual(len(ws.grabber.state(ident)['results']['cases']),1)
            finally: ws.close()

    def test_purchased_pdf_remains_in_bundle_after_reanalysis_and_missing_files_block(self):
        with tempfile.TemporaryDirectory() as folder, contextlib.redirect_stdout(io.StringIO()):
            ws=Workspace(folder,ai_credentials=ProjectAIKey(MemoryCredentialStore('test-key'),{}))
            try:
                ident=ws.new(demo=True);ws.future.result(10)
                root=ws.grabber.root(ident);s=source();c=classify_result(candidates(),s)[0]
                ExpenseLedger(root,'documents').approve(300)
                pdf=download_document(root,s,c,None,transport=FakeTransport(),analysis_id='original-analysis')
                write_json(root/'analyses/original-analysis.json',{'source':s,'candidates':[c]})
                write_json(root/'results.json',{'cases':{CASE['key']:{'source':s,'candidates':[]}}})
                with zipfile.ZipFile(ws.grabber.bundle(ident)) as z:
                    self.assertEqual(len([n for n in z.namelist() if n.endswith('.pdf')]),1)
                    self.assertIn('Purchased under an earlier analysis',z.read('index.csv').decode())
                    self.assertIn('analyses/original-analysis.json',z.namelist())
                pdf.unlink()
                with self.assertRaises(RocError): ws.grabber.bundle(ident)
            finally: ws.close()

    def test_resume_does_not_reapprove_budget_or_accept_changed_analysis_policy(self):
        with tempfile.TemporaryDirectory() as folder, contextlib.redirect_stdout(io.StringIO()):
            ws=Workspace(folder,ai_credentials=ProjectAIKey(MemoryCredentialStore('test-key'),{}))
            try:
                ident=ws.new(demo=True);ws.future.result(10)
                raw=ws.folder(ident)/'demo-docket.html';raw.write_text(fixture(),encoding='utf-8')
                m=ws.manifest(ident);m['demo']=False;ws.save(ident,m)
                reports={CASE['key']:{'path':str(raw)}}
                with patch.object(ws,'completed_reports',return_value=reports),patch.object(ws,'cases',return_value=[CASE]),patch('roc.claude.request_claude',return_value=reply()) as ai:
                    q=ws.grabber.analysis_quote(ident,{'keys':[CASE['key']]})
                    with patch.object(ws,'checkpoint',side_effect=RocError('Paused')):
                        ws.act(ident,'documents-analyze',q|{'budgetCents':100});ws.future.result(10)
                    self.assertEqual(ai.call_count,0)
                    self.assertEqual(ws.manifest(ident)['state'],'stopped')
                    ws.act(ident,'resume');ws.future.result(10)
                    self.assertEqual(ai.call_count,1)
                    self.assertEqual(ExpenseLedger(ws.grabber.root(ident),'ai').data['limitCents'],100)
                    m=ws.manifest(ident);m['state']='stopped';ws.save(ident,m)
                    with patch('roc.claude.SYSTEM','A changed policy'):
                        ws.act(ident,'resume');ws.future.result(10)
                    self.assertEqual(ai.call_count,1)
                    self.assertIn('policy changed',ws.manifest(ident)['message'])
            finally: ws.close()


if __name__ == '__main__': unittest.main()
