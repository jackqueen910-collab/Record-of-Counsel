"""Source-copy policy, client boundaries, and categorized findings; no network."""
import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from roc.cli import run
from roc.common import read_json, write_json
from roc.docket import enrich, parse_report, NOT_LISTED, TEAM_VALUES
from roc.index import build_index
from roc.output import export_local, publish_google
from roc.validation import assess_report, save_results, validation_plan
from roc.store import RunStore
from tests.test_roc import party, count, report
from tests.test_validation_batch import config, record, html


class SourceCountsTests(unittest.TestCase):
    def test_raw_labels_ranges_and_repeated_numbers_survive_in_order(self):
        rows = count("CHARGE (WITH PARENTHESES)", "01r-03r") + count("OTHER CHARGE", "1s")
        rows += '<tr><td>Terminated Counts</td><td></td><td>Disposition</td></tr>'
        rows += count("OLDER CHARGE", "1", "Dismissed")
        result = enrich(parse_report(report(party("Defendant", "Client", "Lawyer", rows))), ["Lawyer"])
        self.assertEqual(result["nature"], "CHARGE (WITH PARENTHESES) (01r-03r); OTHER CHARGE (1s); OLDER CHARGE (1) [Terminated Counts]")
        self.assertFalse(result["issues"])
        self.assertEqual([c["rawCountLabel"] for c in result["selectedCounts"][0]["counts"]], ["01r-03r", "1s", "1"])

    def test_count_section_row_without_a_numeric_label_is_copied(self):
        raw = '<tr><td>Source-only charge description</td><td></td><td>Dismissed</td></tr>'
        result = enrich(parse_report(report(party("Defendant", "Client", "Lawyer", raw))), ["Lawyer"])
        self.assertEqual(result["nature"], "Source-only charge description")
        self.assertFalse(result["warnings"])

    def test_prosecution_preserves_different_and_missing_defendant_profiles(self):
        raw = report(party("Defendant", "A", "Defense A", count("ALPHA", "1")) +
                     party("Defendant", "B", "Defense B", number="2") +
                     party("Plaintiff", "USA", "Government Lawyer"))
        parsed = parse_report(raw)
        government = enrich(parsed, ["Government Lawyer"])
        self.assertEqual(government["nature"], "A (defendant 1): ALPHA (1)\nB (defendant 2): Not listed in source")
        self.assertEqual(government["fieldStatus"]["nature"], "partial")
        self.assertEqual(government["issues"][0]["category"], "missing-source")
        self.assertEqual(government["issues"][0]["party"], "B")
        self.assertFalse(government["warnings"])
        defense = enrich(parsed, ["Defense A"])
        self.assertEqual(defense["nature"], "ALPHA (1)")
        self.assertEqual(defense["representedParties"], ["A"])
        self.assertFalse(defense["issues"])

    def test_same_lawyer_multiple_civil_roles_are_explicit(self):
        raw = report(party("Petitioner", "A", "Lawyer") + party("Claimant", "B", "Lawyer"), "CIVIL")
        raw = raw.replace('</h3>', '</h3><p>Nature of Suit: 190 Contract Jurisdiction: Diversity</p>')
        result = enrich(parse_report(raw), ["Lawyer"])
        self.assertEqual(result["team"], "Multiple roles")
        self.assertEqual(result["teams"], ["Claimant", "Petitioner"])
        self.assertEqual(result["nature"], "190 Contract")
        self.assertFalse(result["issues"])

    def test_conflicting_criminal_roles_remain_review_items(self):
        raw = report(party("Defendant", "A", "Lawyer", count("ALPHA", "1")) + party("Plaintiff", "USA", "Lawyer"))
        result = enrich(parse_report(raw), ["Lawyer"])
        self.assertEqual(result["team"], "")
        self.assertEqual(result["nature"], "")
        self.assertEqual(result["issues"][0]["category"], "needs-review")
        self.assertTrue(result["warnings"])

    def test_missing_counsel_trial_is_not_a_parser_error(self):
        case = build_index([record("nyedc", "Civil")])[0]
        raw = html(case).replace('<b>Defense Lawyer</b>', '')
        assessment = assess_report(raw, case)
        self.assertEqual(assessment["automatedAssessment"], "source-limited")
        self.assertEqual({i["category"] for i in assessment["issues"]}, {"not-tested"})
        self.assertFalse(assessment["warnings"])
        with tempfile.TemporaryDirectory() as folder, RunStore(folder, 0) as store:
            s = {"courtId": "nyedc", "caseType": "Civil", "case": case, "sourceFile": "fictional.html", "assessment": assessment}
            result = save_results(Path(folder), validation_plan(config()), [s], store, "complete")
            self.assertEqual(result["sourceLimitedReports"], 1)
            self.assertEqual(result["reviewNeededReports"], 0)
            self.assertIn("Not tested by this sample", (Path(folder)/'validation-report.html').read_text())

    def test_missing_counts_and_unfamiliar_layout_have_different_categories(self):
        case = build_index([record("nyedc", "Criminal")])[0]
        raw = html(case).replace(count("18:1343.F FRAUD BY WIRE", "2"), '').replace(count("18:111.F ASSAULT", "1"), '')
        a = assess_report(raw, case)
        self.assertEqual({i["category"] for i in a["issues"]}, {"missing-source", "not-tested"})
        broken = raw.replace('<b>Client</b>', '')
        a = assess_report(broken, case)
        self.assertEqual(a["automatedAssessment"], "review-needed")
        self.assertTrue(any(i["category"] == "needs-review" for i in a["issues"]))

    def test_offline_export_propagates_source_omissions_and_new_team_values(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            case = build_index([record("nyedc", "Criminal")])[0]
            raw = html(case).replace(count("18:1343.F FRAUD BY WIRE", "2"), '')
            write_json(root/'index.json', [record("nyedc", "Criminal")])
            (root/'docket.html').write_text(raw, encoding='utf-8')
            write_json(root/'config.json', {"lawyer": {"firstName": "Defense", "lastName": "Lawyer"},
                "runDirectory": "run", "indexFile": "index.json", "budgetCents": 0,
                "savedDockets": [{"courtId": "nyedc", "caseNumber": "1:24-cr-1", "path": "docket.html"}]})
            with patch('socket.socket.connect', side_effect=AssertionError('No network')), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(run(root/'config.json'), 0)
            result = read_json(root/'run/output/evidence.json')['cases'][0]
            self.assertEqual(result['nature'], NOT_LISTED)
            self.assertFalse(result['warnings'])
            findings = read_json(root/'run/output/review.json')[0]
            self.assertEqual(findings['issues'][0]['category'], 'missing-source')
            self.assertTrue(findings['items'][0].startswith('Missing from source:'))
            from openpyxl import load_workbook
            book = load_workbook(root/'run/output/case-index.xlsx')
            self.assertEqual(book.active['H7'].value, NOT_LISTED)
            self.assertFalse(book.active['H7'].alignment.wrap_text)
            self.assertIn('all listed source rows', book.active['H7'].comment.text)
            self.assertNotIn('latest supported', book.active['H7'].comment.text)
            self.assertIn('Amicus', book.active.data_validations.dataValidation[0].formula1)
            self.assertLessEqual(len(','.join(TEAM_VALUES)), 253)

    def test_google_payload_keeps_full_counts_clipped_and_notes_current(self):
        case = build_index([record("nyedc", "Criminal")])[0]
        e = enrich(parse_report(html(case)), ['Government Lawyer'])
        e['sourceFile'] = 'fixture.html'
        case.update(team=e['team'], nature=e['nature'], enrichment=e, issues=e['issues'])
        with patch('roc.output.google_token', return_value='fictional'), patch('roc.output.google_request') as request:
            request.side_effect = [{'spreadsheetId': 'fictional', 'spreadsheetUrl': 'https://example.invalid'}, {}]
            publish_google([case], 'Fixture', 'unused.json')
        batch = request.call_args_list[1].args[1]['requests']
        cell = batch[0]['updateCells']['rows'][1]['values'][7]
        self.assertEqual(cell['userEnteredValue']['stringValue'], e['nature'])
        self.assertIn('all listed source rows', cell['note'])
        self.assertEqual(batch[1]['repeatCell']['cell']['userEnteredFormat']['wrapStrategy'], 'CLIP')
