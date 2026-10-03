"""Offline regressions for the October 2026 correctness/recovery review."""
import contextlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from roc.cli import run
from roc.exports import output_directory
from roc.common import RocError, read_json, write_json
from roc.docket import enrich, parse_report
from roc.document_download import download_document
from roc.document_ledger import ExpenseLedger
from roc.documents import classify_result
from roc.accounts import Accounts
from roc.exports import export_metadata, incomplete_export, publish_exports
from roc.locking import ProcessLock
from roc.pacer import Session
from roc.store import RunStore
from roc.workspace import Workspace
from tests.test_document_grabber import FakeTransport, source, candidates
from tests.test_live_workflow import record
from tests.test_report_confirmation import CONFIRMATION
from tests.test_roc import report, party, count
from tests.test_workspace import FakeCourt, form, response, retrieval_values


class ReviewRegressions(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))
        self.enterContext(patch('urllib.request.OpenerDirector.open', side_effect=AssertionError('No network in regression tests')))

    def test_party_boundary_does_not_depend_on_underlining(self):
        first = party('Defendant', 'Client One', 'Jordan Lawyer', count('CLIENT CHARGE', '1'))
        second = party('Defendant', 'Other Defendant', 'Other Lawyer', count('UNRELATED CHARGE', '2'), number='2')
        second = second.replace('<u>', '').replace('</u>', '')
        parsed = parse_report(report(first + second))
        result = enrich(parsed, ['Jordan Lawyer'])
        self.assertEqual(parsed['warnings'], [])
        self.assertEqual([p['name'] for p in parsed['parties']], ['Client One', 'Other Defendant'])
        self.assertEqual(result['nature'], 'CLIENT CHARGE (1)')
        self.assertEqual(result['fieldStatus']['nature'], 'resolved')

    def test_unrecognized_role_preserved_with_review_and_separate_counts(self):
        for role in ('ThirdParty Defendant', 'Trustee'):
            parsed = parse_report(report(
                party('Plaintiff', 'First Party', 'Jordan Lawyer', count('FIRST', '1')) +
                party(role, 'Additional Client', 'Jordan Lawyer', count('SECOND', '2')), kind='CIVIL'))
            self.assertTrue(parsed['warnings'])
            self.assertEqual(parsed['parties'][1]['role'], role)
            self.assertEqual(len(parsed['parties'][0]['counts']), 1)
            result = enrich(parsed, ['Jordan Lawyer'])
            self.assertEqual(result['representedParties'], ['First Party', 'Additional Client'])
            self.assertEqual(result['fieldStatus']['nature'], 'needs-review')

    def test_civil_missing_nos_preserves_known_pcl_value(self):
        row = record(kind='Civil')
        row.update(caseNumberFull='1:2024cv00001', natureOfSuit='360')
        write_json(self.root / 'index.json', [row])
        html = report(party('Plaintiff', 'Civil Client', 'Jordan Lawyer'), kind='CIVIL')
        html = html.replace('Example District', 'Southern District of New York').replace('1:24-cr-00001', '1:24-cv-00001')
        (self.root / 'docket.html').write_text(html, encoding='utf-8')
        config = {'lawyer': {'firstName': 'Jordan', 'lastName': 'Lawyer'}, 'indexFile': 'index.json',
                  'runDirectory': 'run', 'budgetCents': 0,
                  'savedDockets': [{'courtId': 'nysdc', 'caseNumber': '1:24-cv-00001', 'path': 'docket.html'}]}
        write_json(self.root / 'config.json', config)
        self.assertEqual(run(self.root / 'config.json'), 0)
        case = read_json(output_directory(self.root / 'run') / 'evidence.json')['cases'][0]
        self.assertEqual(case['nature'], '360 - Personal Injury: Other')
        self.assertEqual(case['enrichment']['fieldStatus']['nature'], 'missing-source')
        self.assertEqual(case['enrichment']['nature'], '')

    def test_unknown_document_landing_keeps_reservation_and_cannot_repeat(self):
        s = source()
        c = classify_result(candidates(), s)[0]
        receipt = b'<html><body>Transaction Receipt<br>Cost: $3.00<iframe src="document.pdf"></iframe></body></html>'
        for raw in (receipt, b'<html>Unfamiliar court response</html>', b'%PDF-1.4 unexpected'):
            root = self.root / str(len(raw))
            transport = FakeTransport(landing=raw)
            ExpenseLedger(root, 'documents').approve(300)
            for _ in range(2):
                with self.assertRaises(RocError):
                    download_document(root, s, c, None, transport=transport)
            state = ExpenseLedger(root, 'documents').public()
            self.assertEqual(len(transport.requests), 1)
            self.assertEqual((state['spentCents'], state['pendingCount']), (0, 1))
            self.assertEqual((root / state['transactions'][0]['priceFile']).read_bytes(), raw)

    def workspace(self):
        session = Session('fictional', requester=lambda *a: response([record()]))
        ws = Workspace(self.root, lambda: session)
        self.addCleanup(ws.close)
        ident = ws.new(form())
        ws.future.result(20)
        return ws, ident

    def test_export_failure_keeps_old_generation_and_resume_reuses_purchased_docket(self):
        ws, ident = self.workspace()
        folder = ws.folder(ident)
        previous = output_directory(folder)
        old_xlsx = (previous / 'case-index.xlsx').read_bytes()
        FakeCourt.fail_key = None
        with patch('roc.cli.CourtRetriever', FakeCourt), patch('openpyxl.workbook.workbook.Workbook.save', side_effect=OSError('Synthetic interruption')):
            ws.act(ident, 'retrieve', retrieval_values(ws, ident, ['nysdc|1:24-cr-00001']))
            ws.future.result(20)
        state = ws.summary(ident, detail=True)
        self.assertEqual(state['state'], 'stopped')
        self.assertEqual(state['cases'][0]['role'], '')
        self.assertEqual(state['enrichedCount'], 0)
        self.assertTrue(state['exportsIncomplete'])
        self.assertEqual(output_directory(folder), previous)
        self.assertEqual((previous / 'case-index.xlsx').read_bytes(), old_xlsx)
        with self.assertRaisesRegex(RocError, 'Resume'):
            ws.download(ident, 'case-index.xlsx')
        with patch('roc.cli.CourtRetriever', side_effect=AssertionError('Must reuse saved docket')):
            ws.act(ident, 'resume')
            ws.future.result(20)
        self.assertFalse(incomplete_export(folder))
        self.assertNotEqual(output_directory(folder), previous)
        self.assertEqual(ws.summary(ident, True)['cases'][0]['role'], 'Criminal Defense')
        self.assertEqual(export_metadata(folder)['enrichedCases'], 1)
        self.assertEqual(ws.receipts(ident)['spentCents'], 310)
        self.assertNotEqual(ws.download(ident, 'case-index.xlsx').read_bytes(), old_xlsx)

    def test_pointer_failure_does_not_publish_any_new_export(self):
        ws, ident = self.workspace()
        folder = ws.folder(ident)
        previous = output_directory(folder)
        def fail_pointer(path, value):
            if Path(path).name == 'current.json':
                raise OSError('Synthetic pointer interruption')
            write_json(path, value)
        with patch('roc.exports.write_json', side_effect=fail_pointer):
            with self.assertRaises(OSError):
                publish_exports(ws.cases(ident), folder, 'Unpublished', export_metadata(folder))
        self.assertEqual(output_directory(folder), previous)
        self.assertTrue(incomplete_export(folder))

    def test_legacy_flat_exports_remain_readable(self):
        folder = self.root / 'legacy'
        write_json(folder / 'output/evidence.json', {'cases': []})
        write_json(folder / 'result.json', {'caseCount': 0})
        self.assertEqual(output_directory(folder), folder / 'output')
        self.assertEqual(export_metadata(folder), {'caseCount': 0})
        self.assertFalse(incomplete_export(folder))

    def stopped_confirmation(self, ws, ident, submitted=False):
        folder = ws.folder(ident)
        with RunStore(folder, 1000) as store:
            tx = store.reserve('docket', {'court': 'nysdc', 'caseNumber': '1:24-cr-00001',
                               'scope': 'all-defendants', 'partiesAndCounsel': True}, 300)
            store.save_response(tx, CONFIRMATION)
            if submitted:
                tx['confirmationSubmittedUtc'] = 'Already submitted'
                store.save()
        manifest = ws.manifest(ident)
        manifest.update(state='stopped', lastAction='retrieve', needsResume=True, selected=['nysdc|1:24-cr-00001'])
        ws.save(ident, manifest)

    def test_resume_finishes_only_saved_unsubmitted_confirmation(self):
        ws, ident = self.workspace()
        self.stopped_confirmation(ws, ident)
        resumed = []
        class ResumingCourt(FakeCourt):
            def retrieve(self, case):
                raise AssertionError('Must not open a new docket report')
            def resume_confirmation(self, tx, case):
                resumed.append(case['key'])
                tx['confirmationSubmittedUtc'] = 'Submitted once'
                self.store.save()
                raw = report(party('Defendant', 'Client', 'Jordan Lawyer', count('CHARGE', '1')))
                return self.store.finish(tx, raw.replace('Example District', 'Southern District of New York'))
        with patch('roc.cli.CourtRetriever', ResumingCourt):
            ws.act(ident, 'resume')
            ws.future.result(20)
        self.assertEqual(resumed, ['nysdc|1:24-cr-00001'])
        self.assertEqual(ws.summary(ident)['state'], 'ready')
        self.assertEqual(ws.receipts(ident)['spentCents'], 310)
        self.assertEqual(len(ws.ledger(ident)['transactions']), 2)

    def test_submitted_confirmation_cannot_resume(self):
        ws, ident = self.workspace()
        self.stopped_confirmation(ws, ident, submitted=True)
        with patch('roc.cli.CourtRetriever') as retriever:
            ws.act(ident, 'resume')
            ws.future.result(20)
            retriever.assert_not_called()
        self.assertEqual(ws.receipts(ident)['pendingCount'], 1)
        self.assertIn('unresolved receipt', ws.summary(ident)['message'])

    def test_confirmation_rejects_changed_selection_or_reduced_cap_before_retrieval(self):
        ws, ident = self.workspace()
        self.stopped_confirmation(ws, ident)
        for selected, budget in (([], 1000), (['nysdc|1:24-cr-00001'], 100)):
            manifest = ws.manifest(ident)
            manifest['selected'] = selected
            manifest['config']['budgetCents'] = budget
            ws.save(ident, manifest)
            with patch('roc.cli.CourtRetriever') as retriever:
                ws.act(ident, 'resume')
                ws.future.result(20)
                retriever.assert_not_called()
            self.assertEqual(ws.receipts(ident)['pendingCount'], 1)

    def test_crash_releases_root_nested_and_run_locks_without_clearing_receipts(self):
        code = '''from roc.accounts import Accounts
from roc.workspace import Workspace
from roc.store import RunStore
import os,sys
from pathlib import Path
root=Path(sys.argv[1]); a=Accounts(root); w=Workspace(root/'nested')
s=RunStore(root/'nested'/'run',300); s.__enter__(); s.reserve('docket',{},300)
os._exit(0)
'''
        child = subprocess.run([sys.executable, '-c', code, str(self.root)], capture_output=True,
                               timeout=15, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.assertEqual(child.returncode, 0, child.stderr.decode(errors='replace'))
        accounts = Accounts(self.root)
        self.addCleanup(accounts.close)
        ws = Workspace(self.root / 'nested')
        self.addCleanup(ws.close)
        self.assertIsNone(ws.active)
        with RunStore(self.root / 'nested/run', 300) as store:
            self.assertEqual(len(store.ledger['transactions']), 1)
            with self.assertRaisesRegex(RocError, 'unresolved receipt'):
                store.check_pending()
        with self.assertRaises(RocError):
            Accounts(self.root)

    def test_legacy_live_pid_is_not_stolen_and_dead_pid_is_recoverable(self):
        path = self.root / '.workspace.lock'
        path.write_text(str(os.getpid()))
        with self.assertRaisesRegex(RocError, 'Live owner'):
            ProcessLock(path, 'Live owner').acquire()
        child = subprocess.run([sys.executable, '-c', 'import os; print(os.getpid())'], capture_output=True, timeout=15,
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.assertEqual(child.returncode, 0)
        path.write_bytes(child.stdout.strip())
        lock = ProcessLock(path, 'Busy').acquire()
        self.addCleanup(lock.release)
        with self.assertRaises(RocError):
            ProcessLock(path, 'Busy').acquire()
