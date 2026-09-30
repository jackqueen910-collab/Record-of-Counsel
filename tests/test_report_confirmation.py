"""Full-report continuation fixtures. Every browser request is intercepted."""
from contextlib import nullcontext
import os
import tempfile
import unittest
from unittest.mock import patch

from roc.common import RocError
from roc.courts import court_profile
from roc.pacer import Session
from roc.retrieve import CourtRetriever, large_report_confirmation, pending_confirmation
from roc.store import RunStore
from tests.test_court_forms import FORM
from tests.test_roc import report, party, count


CONFIRMATION = '''<html><body><form method="POST" enctype="multipart/form-data" action="../cgi-bin/DktRpt.pl?123-L_1_1-1">
<p>The report may take a long time to run because this case has many docket entries.</p>
<input type="radio" name="date_from" value="01/01/2024" checked> for the past week
<input type="radio" name="date_from" value="10/01/2023"> for the past 90 days
<input type="radio" name="date_from" value="01/01/2023"> for the past year
<input type="radio" name="date_from" value=""> as initially requested
<input type="submit" name="button1" value="Run Report">
</form></body></html>'''


def case():
    p = court_profile("nyedc")
    return {"courtId": "nyedc", "caseNumber": "1:24-cr-00001", "key": "nyedc|1:24-cr-00001",
            "pacerLink": p.origin + "/cgi-bin/iqquerymenu.pl?fictional"}


def parameters():
    return {"court": "nyedc", "caseNumber": "1:24-cr-00001", "scope": "all-defendants", "partiesAndCounsel": True}


class ConfirmationGuardTests(unittest.TestCase):
    def test_only_exact_observed_form_contract_is_recognized(self):
        origin = court_profile("nyedc").origin
        self.assertEqual(large_report_confirmation(CONFIRMATION, origin), origin + "/cgi-bin/DktRpt.pl?123-L_1_1-1")
        self.assertIsNone(large_report_confirmation("Please log in", origin))
        for bad in (CONFIRMATION.replace("../cgi-bin/DktRpt.pl?123-L_1_1-1", "https://example.com/"),
                    CONFIRMATION.replace("DktRpt.pl", "show_doc.pl"),
                    CONFIRMATION.replace('method="POST"', 'method="GET"'),
                    CONFIRMATION.replace('value=""', 'value="unexpected"'),
                    CONFIRMATION.replace('</form>', '<input name="unknown" value="1"></form>')):
            with self.assertRaises(RocError):
                large_report_confirmation(bad, origin)

    def test_saved_confirmation_keeps_reservation_and_blocks_other_requests(self):
        with tempfile.TemporaryDirectory() as folder, RunStore(folder, 600) as store:
            t = store.reserve("docket", parameters(), 300)
            store.save_response(t, CONFIRMATION)
            self.assertIs(pending_confirmation(store), t)
            self.assertEqual(t["state"], "pending")
            self.assertEqual(store.spent, 0)
            with self.assertRaises(RocError):
                store.reserve("pcl-case", {}, 10)
            t["confirmationSubmittedUtc"] = "fictional-submission"
            with self.assertRaises(RocError):
                pending_confirmation(store)

    def test_unknown_page_or_multiple_pending_transactions_cannot_resume(self):
        with tempfile.TemporaryDirectory() as folder, RunStore(folder, 600) as store:
            t = store.reserve("docket", parameters(), 300)
            store.save_response(t, "Unknown response without receipt")
            with self.assertRaises(RocError):
                pending_confirmation(store)
            store.save_response(t, CONFIRMATION)
            store.ledger["transactions"].append(dict(t))
            with self.assertRaises(RocError):
                pending_confirmation(store)


@unittest.skipUnless(os.getenv("ROC_BROWSER_TESTS") == "1", "Set ROC_BROWSER_TESTS=1 with Playwright installed.")
class ConfirmationBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.pw = sync_playwright().start()

    @classmethod
    def tearDownClass(cls):
        cls.pw.stop()

    def exercise(self, resume):
        from playwright.sync_api import Browser
        original = Browser.new_context
        calls = []
        origin = court_profile("nyedc").origin
        body = report(party("Defendant", "Client", "Defense Attorney", count("18:1343.F FRAUD BY WIRE", "1")))
        body = body.replace("Example District", court_profile("nyedc").district)
        def intercepted_context(browser, *args, **kwargs):
            context = original(browser, *args, **kwargs)
            def respond(route):
                request = route.request
                calls.append((request.method, request.url, request.post_data))
                if request.url == origin + "/cgi-bin/DktRpt.pl" and not resume:
                    route.fulfill(content_type="text/html", body=FORM.replace("window.submissions=(window.submissions||0)+1", "location.href='/confirmation'"))
                elif request.url == origin + "/confirmation" and not resume:
                    route.fulfill(content_type="text/html", body=CONFIRMATION)
                elif request.url == origin + "/cgi-bin/DktRpt.pl?123-L_1_1-1" and request.method == "POST":
                    self.assertIn('name="date_from"\r\n\r\n\r\n', request.post_data)
                    self.assertNotIn("01/01/2024", request.post_data)
                    route.fulfill(content_type="text/html", body=body)
                else:
                    route.abort()
            context.route("**/*", respond)
            return context
        with tempfile.TemporaryDirectory() as folder, RunStore(folder, 300) as store, \
                patch.object(Browser, "new_context", intercepted_context), \
                patch("playwright.sync_api.sync_playwright", return_value=nullcontext(self.pw)):
            retriever = CourtRetriever(Session("fictional"), store, allow_unverified=True)
            if resume:
                t = store.reserve("docket", parameters(), 300)
                store.save_response(t, CONFIRMATION)
                path = retriever.resume_confirmation(t, case())
            else:
                path = retriever.retrieve(case())
                t = store.ledger["transactions"][0]
            self.assertEqual(t["state"], "complete")
            self.assertTrue(t["confirmationSubmittedUtc"])
            self.assertEqual(store.spent, 300)
            self.assertEqual(len(store.ledger["transactions"]), 1)
            before = len(calls)
            self.assertEqual(retriever.retrieve(case()), path)
            self.assertEqual(before, len(calls))
        self.assertEqual(sum(method == "POST" for method, _, _ in calls), 1)
        if resume:
            self.assertEqual(len(calls), 1)  # No new report-form GET or initial submission.

    def test_initial_large_report_keeps_full_scope_and_one_reservation(self):
        self.exercise(False)

    def test_resume_posts_saved_action_once_without_reopening_report_form(self):
        self.exercise(True)
