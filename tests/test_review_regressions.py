"""Offline regressions for the October 2026 correctness/recovery review."""
import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from roc.cli import run
from roc.common import RocError, read_json, write_json
from roc.docket import enrich, parse_report
from roc.document_download import download_document
from roc.document_ledger import ExpenseLedger
from roc.documents import classify_result
from tests.test_document_grabber import FakeTransport, source, candidates
from tests.test_live_workflow import record
from tests.test_roc import report, party, count


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
        case = read_json(self.root / 'run/output/evidence.json')['cases'][0]
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
