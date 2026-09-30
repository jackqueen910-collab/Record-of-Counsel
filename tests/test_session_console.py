import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from roc.common import RocError, read_json, write_json
from roc.console import run_live_session
from roc.pacer import Session
from roc.store import RunStore


class SessionConsoleTests(unittest.TestCase):
    def test_resume_reuses_session_only_after_operator_input(self):
        session = Session("fictional-token")
        used, resumed = [], []
        def attempt(provider):
            used.append(provider())
            return 2 if len(used) == 1 else 0
        def reader(prompt):
            self.assertEqual(len(used), 1)
            resumed.append(True)
            return "r"
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / "config.json"
            write_json(config, {"runDirectory": "run"})
            with patch("roc.console.Session.prompt", return_value=session) as login, contextlib.redirect_stdout(io.StringIO()):
                code = run_live_session(config, reader=reader, attempt=attempt, reloader=lambda: None)
            self.assertEqual(code, 0)
            self.assertEqual(used, [session, session])
            self.assertEqual(login.call_count, 1)
            self.assertEqual(resumed, [True])
            self.assertNotIn("fictional-token", (Path(folder) / "run/status.json").read_text())

    def test_failed_authentication_exits_without_retry(self):
        reader = Mock()
        def attempt(provider):
            try:
                provider()
            except RocError:
                return 2
        with tempfile.TemporaryDirectory() as folder, patch("roc.console.Session.prompt", side_effect=RocError("Auth failed")) as login:
            config = Path(folder) / "config.json"
            write_json(config, {"runDirectory": "run"})
            self.assertEqual(run_live_session(config, reader=reader, attempt=attempt), 2)
            reader.assert_not_called()
            self.assertEqual(login.call_count, 1)

    def test_pending_charge_still_blocks_resume(self):
        attempts = []
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / "config.json"
            write_json(config, {"runDirectory": "run"})
            def attempt(provider):
                provider()
                attempts.append(True)
                with RunStore(Path(folder) / "run", 1000) as store:
                    if len(attempts) == 1:
                        store.reserve("docket", {"case": "fictional"}, 300)
                    else:
                        with self.assertRaises(RocError):
                            store.reserve("docket", {"case": "fictional"}, 300)
                return 2
            choices = iter(["r", "q"])
            with patch("roc.console.Session.prompt", return_value=Session("fictional")), contextlib.redirect_stdout(io.StringIO()):
                code = run_live_session(config, reader=lambda _: next(choices), attempt=attempt, reloader=lambda: None)
            self.assertEqual(code, 2)
            self.assertEqual(len(read_json(Path(folder) / "run/ledger.json")["transactions"]), 1)
