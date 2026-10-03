"""Engine callers and small, revision-based result polling use no external APIs."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from roc.common import RocError, read_json, write_json
from roc.engine import run_workflow
from roc.exports import output_directory, publish_exports
from roc.cli import run
from roc.store import RunStore
from roc.workspace import Workspace
from tests.test_live_workflow import record


class EngineTests(unittest.TestCase):
    def test_engine_accepts_configuration_and_returns_result_without_console(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            write_json(root / 'index.json', [record()])
            config = {'lawyer': {'firstName': 'Jordan', 'lastName': 'Lawyer'},
                      'runDirectory': 'run', 'budgetCents': 0, 'indexFile': 'index.json'}
            progress = []
            output = io.StringIO()
            with contextlib.redirect_stdout(output), patch('builtins.input', side_effect=AssertionError('No prompt')):
                result = run_workflow(config, root, progress=lambda stage, message, **details: progress.append(stage))
            self.assertEqual(result.exit_code, 0)
            self.assertEqual(result.metadata['caseCount'], 1)
            self.assertEqual(result.cases[0]['caseNumber'], '1:24-cr-00001')
            self.assertTrue(result.workbook.is_file())
            self.assertEqual(progress[-1], 'complete')
            self.assertEqual(output.getvalue(), '')
            self.assertIn('Saved 1 cases', result.message)

    def test_live_engine_requires_explicit_session_provider(self):
        with tempfile.TemporaryDirectory() as folder, patch('roc.pacer.Session.prompt') as prompt:
            with self.assertRaisesRegex(RocError, 'No authentication was attempted'):
                run_workflow({'lawyer': {'firstName': 'Jordan', 'lastName': 'Lawyer'},
                              'runDirectory': '.', 'budgetCents': 100}, folder, live=True)
            prompt.assert_not_called()

    def test_cli_publication_keeps_run_lock_and_separate_metadata(self):
        with tempfile.TemporaryDirectory() as folder, contextlib.redirect_stdout(io.StringIO()):
            root = Path(folder)
            config = {'lawyer': {'firstName': 'Jordan', 'lastName': 'Lawyer'}, 'runDirectory': 'run',
                      'budgetCents': 0, 'indexFile': 'index.json', 'googleOAuthFile': 'fictional-oauth.json'}
            write_json(root / 'index.json', [record()])
            write_json(root / 'config.json', config)
            def publisher(*args):
                with self.assertRaises(RocError):
                    with RunStore(root / 'run', 0):
                        pass
                return 'https://example.invalid/fictional-sheet'
            with patch('roc.cli.publish_google', side_effect=publisher):
                self.assertEqual(run(root / 'config.json', publish=True), 0)
            metadata = read_json(root / 'run/result.json')
            self.assertEqual(metadata['spreadsheetUrl'], 'https://example.invalid/fictional-sheet')
            self.assertNotIn('spreadsheetUrl', read_json(output_directory(root / 'run') / 'result.json'))

    def test_cli_does_not_publish_a_replaced_generation(self):
        with tempfile.TemporaryDirectory() as folder, contextlib.redirect_stdout(io.StringIO()):
            root = Path(folder)
            config = {'lawyer': {'firstName': 'Jordan', 'lastName': 'Lawyer'}, 'runDirectory': 'run',
                      'budgetCents': 0, 'indexFile': 'index.json', 'googleOAuthFile': 'fictional-oauth.json'}
            write_json(root / 'index.json', [record()])
            write_json(root / 'config.json', config)
            result = run_workflow(config, root)
            publish_exports(result.cases, root / 'run', 'A later generation', dict(result.metadata))
            with patch('roc.cli.run_workflow', return_value=result), patch('roc.cli.publish_google') as publisher:
                with self.assertRaisesRegex(RocError, 'changed before publication'):
                    run(root / 'config.json', publish=True)
                publisher.assert_not_called()


class ProjectionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.ws = Workspace(temp.name)
        self.addCleanup(self.ws.close)
        self.ident = self.ws.new(demo=True)
        self.ws.future.result(10)

    def test_unchanged_poll_does_not_read_or_rebuild_case_projection(self):
        full = self.ws.summary(self.ident, detail=True, compact=True)
        revision = full['dataRevision']
        with patch.object(self.ws, 'cases', side_effect=AssertionError('No case read')), \
                patch.object(self.ws, 'party_reports', side_effect=AssertionError('No projection rebuild')):
            small = self.ws.summary(self.ident, detail=True, since=revision, compact=True)
        self.assertTrue(small['dataUnchanged'])
        for field in ('cases', 'partyReports', 'caseCount', 'enrichedCount'):
            self.assertNotIn(field, small)
        self.assertLess(len(json.dumps(small)), 6000)
        # Progress updates still arrive without invalidating the data projection.
        manifest = self.ws.manifest(self.ident)
        manifest['message'] = 'Paused fixture'
        self.ws.save(self.ident, manifest)
        latest = self.ws.summary(self.ident, detail=True, since=revision)
        self.assertTrue(latest['dataUnchanged'])
        self.assertEqual(latest['message'], 'Paused fixture')

    def test_changed_evidence_and_name_rules_invalidate_revision(self):
        original = self.ws.summary(self.ident, detail=True)
        path = output_directory(self.ws.folder(self.ident)) / 'evidence.json'
        evidence = read_json(path)
        evidence['cases'][0]['caseTitle'] = 'Changed saved caption'
        write_json(path, evidence)
        changed = self.ws.summary(self.ident, detail=True, since=original['dataRevision'])
        self.assertFalse(changed['dataUnchanged'])
        self.assertEqual(changed['cases'][0]['caseTitle'], 'Changed saved caption')
        write_json(self.ws.root / 'name-rules.json', {'version': 1, 'history': [], 'revision': 1, 'rules': []})
        rules = self.ws.summary(self.ident, detail=True, since=changed['dataRevision'])
        self.assertFalse(rules['dataUnchanged'])

    def test_compact_projection_keeps_all_filter_names_roles_and_explicit_groups(self):
        path = output_directory(self.ws.folder(self.ident)) / 'evidence.json'
        evidence = read_json(path)
        evidence['cases'][0]['enrichment'] = {'partyDetails': [
            {'name': 'Client', 'role': 'Plaintiff', 'matchedCounsel': ['Jordan Lawyer']},
            {'name': 'Related Entity', 'role': 'Defendant', 'matchedCounsel': []}]}
        write_json(path, evidence)
        write_json(self.ws.root / 'name-rules.json', {'version': 1, 'history': [], 'revision': 1, 'rules': [
            {'id': 'a' * 32, 'kind': 'organization-group', 'label': 'Company Group', 'names': ['Company Group', 'Related Entity']}]})
        full = self.ws.summary(self.ident, detail=True)
        compact = self.ws.summary(self.ident, detail=True, compact=True)
        reports = compact['partyReports']
        self.assertEqual(reports['clients'], full['partyReports']['clients'])
        self.assertNotIn('defendants', reports)
        self.assertNotIn('plaintiffs', reports)
        self.assertNotIn('parties', reports)
        self.assertEqual([(p['displayName'], p['partyRole']) for p in reports['partyDetails']],
                         [('Client', 'Plaintiff'), ('Company Group', 'Defendant')])
        self.assertEqual(reports['partyDetails'][1]['sourceNames'], ['Related Entity'])
        self.assertEqual(reports['partyDetails'][0]['matchedCounsel'], ['Jordan Lawyer'])
        self.assertEqual(reports['partyDetails'][0]['relationship'], 'Client')
