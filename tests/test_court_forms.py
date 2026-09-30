"""Browser regression tests against fictional court forms. No PACER requests."""
import os
from contextlib import nullcontext
import tempfile
import unittest
from unittest.mock import patch

from roc.common import RocError
from roc.retrieve import CourtRetriever, choose_case, configure_report, lookup_main_case, select_case_line
from roc.courts import court_profile
from roc.docket import enrich, parse_report
from roc.pacer import Session
from roc.store import RunStore
from tests.test_roc import report, party, count


FORM = """<html><body>
<input id="case_number_text_area_0" onkeyup="document.getElementById('case_number_find_button_0').style.display='inline'">
<input type="button" id="case_number_find_button_0" value="Find This Case" style="display:none"
 onclick="document.getElementById('cases').style.display='block'">
<div id="cases" style="display:none">
<div id="case_line_1">1:24-cr-00001-ABC-1 Client <input type="checkbox"></div>
<div id="case_line_0">1:24-cr-00001-ABC USA v. Client <input type="checkbox"></div>
</div>
<input name="date_from" value="1/1/2024"><input name="date_to" value="1/1/2025">
<input name="documents_numbered_from_" value="1"><input name="documents_numbered_to_" value="2">
<input id="list_of_parties_and_counsel" type="checkbox">
<input id="terminated_parties" type="checkbox">
<input id="view_multi_docs" type="checkbox" checked>
<input id="view_all_attachments" type="checkbox" checked>
<input id="view_comb_doc_checkbox" type="checkbox" checked>
<input id="create_appendix" type="checkbox" checked>
<input id="list_of_member_cases" type="checkbox" checked>
<input name="output_format" type="radio" value="html" checked>
<input name="output_format" type="radio" value="pdf">
<input type="button" value="Run Report" onclick="window.submissions=(window.submissions||0)+1">
</body></html>"""


@unittest.skipUnless(os.getenv("ROC_BROWSER_TESTS") == "1", "Set ROC_BROWSER_TESTS=1 with Playwright installed.")
class CourtFormTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def setUp(self):
        self.page = self.browser.new_page()
        self.page.route("**/*", lambda route: route.abort())
        self.page.set_default_timeout(2000)
        self.page.set_content(FORM)

    def tearDown(self):
        self.page.close()

    def test_keyboard_activated_finder_and_full_report_configuration(self):
        # fill() alone leaves this legacy keyboard-driven finder hidden.
        self.page.locator("#case_number_text_area_0").fill("1:24-cr-00001")
        self.assertFalse(self.page.locator("#case_number_find_button_0").is_visible())
        choose_case(self.page, "1:24-cr-00001")
        self.assertTrue(self.page.locator("#case_line_0 input").is_checked())
        self.assertFalse(self.page.locator("#case_line_1 input").is_checked())
        run_button = configure_report(self.page)
        for name in ("date_from", "date_to", "documents_numbered_from_", "documents_numbered_to_"):
            self.assertEqual(self.page.locator(f'[name="{name}"]').input_value(), "")
        for ident in ("view_multi_docs", "view_all_attachments", "view_comb_doc_checkbox", "create_appendix", "list_of_member_cases"):
            self.assertFalse(self.page.locator("#" + ident).is_checked())
        self.assertTrue(self.page.locator("#list_of_parties_and_counsel").is_checked())
        self.assertTrue(self.page.locator("#terminated_parties").is_checked())
        self.assertIsNone(self.page.evaluate("window.submissions"))
        run_button.click()
        self.assertEqual(self.page.evaluate("window.submissions"), 1)

    def test_blur_validation_and_collapsed_case_list(self):
        html = FORM.replace('onkeyup=', 'onchange=')
        html = html.replace("document.getElementById('cases').style.display='block'", "document.getElementById('case_number_show_button_0').style.display='inline'")
        html = html.replace('<div id="cases"', '<input id="case_number_show_button_0" type="button" value="Show Case List" style="display:none" onclick="document.getElementById(\'cases\').style.display=\'block\'"> <div id="cases"')
        self.page.set_content(html)
        choose_case(self.page, "1:24-cr-00001")
        self.assertTrue(self.page.locator("#case_line_0 input").is_checked())
        self.assertFalse(self.page.locator("#case_line_1 input").is_checked())

    def automatic_case_page(self, xml):
        form = '''<html><body><input id="all_case_ids" type="hidden" value="0">
        <input id="case_number_text_area_0" onblur="findCase()">
        <script>function findCase(){fetch('/cgi-bin/possible_case_numbers.pl?fictional').then(r=>r.text()).then(t=>{
          const c=new DOMParser().parseFromString(t,'text/xml').querySelector('case');
          document.getElementById('case_number_text_area_0').value=c.getAttribute('number');
          document.getElementById('all_case_ids').value=c.getAttribute('id');
        })}</script>''' + FORM[FORM.index('<input name="date_from"'):]
        def serve(route):
            if '/cgi-bin/DktRpt.pl' in route.request.url:
                route.fulfill(content_type='text/html', body=form)
            elif '/cgi-bin/possible_case_numbers.pl' in route.request.url:
                route.fulfill(content_type='application/xml', body=xml)
            else:
                route.abort()
        self.page.route('https://ecf.njd.uscourts.gov/**', serve)
        self.page.goto('https://ecf.njd.uscourts.gov/cgi-bin/DktRpt.pl')

    def test_single_main_case_is_automatically_selected_without_a_list(self):
        self.automatic_case_page('<request><case id="123" number="1:24-cr-1" defendant="0" title="Fictional case"/></request>')
        choose_case(self.page, '1:24-cr-00001')
        self.assertEqual(self.page.locator('#all_case_ids').input_value(), '123')
        self.assertEqual(self.page.locator('[id^="case_line_"]').count(), 0)
        self.assertIsNone(self.page.evaluate('window.submissions'))

    def test_automatically_selected_defendant_subcase_is_rejected(self):
        self.automatic_case_page('<request><case id="123" number="1:24-cr-1-2" defendant="2" title="Fictional defendant"/></request>')
        with self.assertRaises(RocError):
            choose_case(self.page, '1:24-cr-00001')
        self.assertIsNone(self.page.evaluate('window.submissions'))

    def test_unfamiliar_date_control_and_pdf_stop_before_submission(self):
        self.page.locator('[name="date_from"]').evaluate("n=>n.remove()")
        with self.assertRaises(RocError):
            configure_report(self.page)
        self.page.set_content(FORM)
        self.page.locator('[name="output_format"][value="pdf"]').check()
        with self.assertRaises(RocError):
            configure_report(self.page)
        self.assertIsNone(self.page.evaluate("window.submissions"))

    def test_shared_retriever_on_unverified_courts_uses_only_intercepted_fixtures(self):
        from playwright.sync_api import Browser
        original = Browser.new_context
        received = []
        contexts = []
        def intercepted_context(browser, *args, **kwargs):
            context = original(browser, *args, **kwargs)
            contexts.append(context)
            def respond(route):
                url = route.request.url
                received.append(url)
                for code in ("nyedc", "gudc"):
                    profile = court_profile(code)
                    if url == profile.origin + "/cgi-bin/DktRpt.pl":
                        form = FORM.replace("window.submissions=(window.submissions||0)+1", "location.href='/fictional-receipt'")
                        route.fulfill(content_type="text/html", body=form)
                        return
                    if url == profile.origin + "/fictional-receipt":
                        body = report(party("Defendant", "Client", "Jordan Lawyer", count("18:1343.F FRAUD BY WIRE", "1")) +
                                      party("Defendant", "Unrelated Defendant", "Different Counsel", count("18:111.F ASSAULT", "2"), "2"))
                        route.fulfill(content_type="text/html", body=body.replace("Example District", profile.district))
                        return
                route.abort()  # No request from this test may reach the network.
            context.route("**/*", respond)
            return context
        with tempfile.TemporaryDirectory() as folder, RunStore(folder, 600) as store, \
             patch.object(Browser, "new_context", intercepted_context), \
             patch("playwright.sync_api.sync_playwright", return_value=nullcontext(self.pw)):
            retriever = CourtRetriever(Session("fictional-token"), store, allow_unverified=True)
            for code in ("nyedc", "gudc"):
                profile = court_profile(code)
                case = {"courtId": code, "caseNumber": "1:24-cr-00001", "key": code + "|1:24-cr-00001",
                        "pacerLink": profile.origin + "/cgi-bin/iqquerymenu.pl?fictional"}
                path = retriever.retrieve(case)
                parsed = enrich(parse_report(path.read_text(encoding="utf-8")), ["Jordan Lawyer"])
                self.assertEqual(parsed["nature"], "Wire fraud (count 1).")
                self.assertEqual(parsed["representedParties"], ["Client"])
                calls = len(received)
                self.assertEqual(retriever.retrieve(case), path)
                self.assertEqual(len(received), calls)
                self.assertFalse(court_profile(code).sample_verified)
            self.assertEqual(store.spent, 600)  # Fictional receipts only.
            self.assertEqual(len(store.ledger["transactions"]), 2)
        self.assertEqual(len(contexts), 2)
        self.assertEqual(len(received), 4)


class CaseLookupTests(unittest.TestCase):
    def test_serial_number_is_not_mistaken_for_defendant_suffix(self):
        self.assertEqual(select_case_line(['1:24-cr-1-2', '1:24-cr-1'], '1:24-cr-00001'), 1)

    def test_lookup_requires_exact_main_case(self):
        xml = '<request><case id="123" number="1:24-cr-1" defendant="0"/><case id="124" number="1:24-cr-1-2" defendant="2"/></request>'
        self.assertEqual(lookup_main_case(xml, '1:24-cr-00001')['id'], '123')
        with self.assertRaises(RocError):
            lookup_main_case(xml, '1:24-cr-99999')
