import contextlib
import io
import unittest
from unittest.mock import patch

from roc.common import RocError
from roc.pacer import Session, SignInError


class SignInPromptTests(unittest.TestCase):
    def prompt(self, answers, login_results):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), \
             patch("roc.pacer.sys.stdin.isatty", return_value=True), \
             patch("roc.pacer.sys.stdout.isatty", return_value=True), \
             patch("builtins.input", side_effect=answers) as reader, \
             patch.object(Session, "login", side_effect=login_results) as login:
            result = Session.prompt()
        return result, reader, login, output.getvalue()

    def test_visible_entry_can_be_corrected_in_same_prompt_after_explicit_yes(self):
        expected = Session("fictional-token")
        result, reader, login, output = self.prompt(
            ["y", "test-user", "wrong-test-password", "", "123456", "y",
             "test-user", "correct-test-password", "", "654321"],
            [SignInError("13"), expected])
        self.assertIs(result, expected)
        self.assertEqual(login.call_count, 2)
        self.assertEqual(login.call_args.args, ("test-user", "correct-test-password", "654321", "", True))
        prompts = [call.args[0] for call in reader.call_args_list]
        self.assertEqual(sum("password (visible)" in p for p in prompts), 2)
        self.assertEqual(sum("MFA code (visible" in p for p in prompts), 2)
        self.assertEqual(sum("Re-enter credentials" in p for p in prompts), 1)
        self.assertNotIn("correct-test-password", output)
        self.assertNotIn("fictional-token", output)

    def test_declining_retry_does_not_send_another_login(self):
        with contextlib.redirect_stdout(io.StringIO()), \
             patch("roc.pacer.sys.stdin.isatty", return_value=True), \
             patch("roc.pacer.sys.stdout.isatty", return_value=True), \
             patch("builtins.input", side_effect=["y", "user", "password", "", "123456", "n"]), \
             patch.object(Session, "login", side_effect=SignInError("13")) as login:
            with self.assertRaises(RocError):
                Session.prompt()
            self.assertEqual(login.call_count, 1)

    def test_connection_failure_stops_without_credential_retry(self):
        with contextlib.redirect_stdout(io.StringIO()), \
             patch("roc.pacer.sys.stdin.isatty", return_value=True), \
             patch("roc.pacer.sys.stdout.isatty", return_value=True), \
             patch("builtins.input", side_effect=["y", "user", "password", "", "123456"]) as reader, \
             patch.object(Session, "login", side_effect=RocError("Connection failed")) as login:
            with self.assertRaises(RocError):
                Session.prompt()
            self.assertEqual(login.call_count, 1)
            self.assertEqual(reader.call_count, 5)
