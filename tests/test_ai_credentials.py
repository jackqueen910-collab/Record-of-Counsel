"""Project account loading and isolated Windows credential persistence. No network."""
import json
import os
import unittest
import uuid

from roc.ai_credentials import ENV_NAME, ProjectAIKey, WindowsCredentialStore
from roc.common import RocError


class MemoryCredentialStore:
    def __init__(self, key=''):
        self.key = key
        self.reads = 0

    def read(self):
        self.reads += 1
        return self.key


class ProjectAIKeyTests(unittest.TestCase):
    def test_reopening_and_owner_updates_need_no_browser_entry(self):
        store = MemoryCredentialStore('fictional-owner-key')
        key = ProjectAIKey(store, {})
        self.assertEqual(key.require(), 'fictional-owner-key')
        self.assertTrue(ProjectAIKey(store, {}).status()['configured'])
        self.assertNotIn('fictional-owner-key', json.dumps(key.status()))
        store.key = 'fictional-rotated-key'
        self.assertEqual(key.require(), 'fictional-rotated-key')
        store.key = ''
        self.assertFalse(key.status()['configured'])
        with self.assertRaisesRegex(RocError, 'owner'):
            key.require()

    def test_only_explicit_roc_environment_overrides_and_invalid_values_fail_closed(self):
        store = MemoryCredentialStore('fictional-owner-key')
        self.assertEqual(ProjectAIKey(store, {'ANTHROPIC_API_KEY':'different-project-key'}).require(), 'fictional-owner-key')
        store.reads = 0
        self.assertEqual(ProjectAIKey(store, {ENV_NAME:'fictional-backend-key'}).require(), 'fictional-backend-key')
        self.assertEqual(store.reads, 0)
        for invalid in ('', 'private-key\n', 'private\x00key', 'private key', 'é', 'x'*2049):
            key = ProjectAIKey(store, {ENV_NAME:invalid})
            with self.assertRaises(RocError):
                key.require()
            self.assertFalse(key.status()['configured'])
            self.assertNotIn('private', json.dumps(key.status()))
        self.assertEqual(store.reads, 0)  # Never fall back to another billing account.

    def test_store_failures_are_reported_without_disabling_cached_results(self):
        class Unavailable:
            def read(self):
                raise RocError('ROC could not read its Windows credential. Ask the owner to check AI setup.')
        state = ProjectAIKey(Unavailable(), {}).status()
        self.assertFalse(state['configured'])
        self.assertIn('owner', state['connectionMessage'])

    @unittest.skipUnless(os.name == 'nt', 'Windows Credential Manager')
    def test_native_store_roundtrip_update_and_delete_only_its_test_entry(self):
        target = 'RecordOfCounsel/Tests/' + uuid.uuid4().hex
        store = WindowsCredentialStore(target)
        self.assertEqual(store.read(), '')
        try:
            store.save('fictional-test-key')
            self.assertEqual(WindowsCredentialStore(target).read(), 'fictional-test-key')
            store.save('fictional-replacement')
            self.assertEqual(WindowsCredentialStore(target).read(), 'fictional-replacement')
        finally:
            store.remove()
        self.assertEqual(store.read(), '')


if __name__ == '__main__':
    unittest.main()
