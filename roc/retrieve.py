"""Independent, optional Playwright court-web retriever.

This is web retrieval, NOT a PACER document API. It uses the official API token.
The supported form contract is deliberately narrow; unknown forms stop before Run Report.
"""
import re
from urllib.parse import urlparse

from .common import RocError, normalize_case_number, fingerprint, write_json
from .docket import parse_report

SUPPORTED_HOSTS = {"ecf.nysd.uscourts.gov", "ecf.njd.uscourts.gov"}
COURT_TOKEN_COOKIE = "NextGenCSO"  # Court cookie is case-sensitive; API JSON uses nextGenCSO.


def court_cookies(session, origin):
    court_origin(origin)
    cookies = [{"name": COURT_TOKEN_COOKIE, "value": session.token, "url": origin,
                "secure": True, "httpOnly": True, "sameSite": "Lax"}]
    if session.client_code:
        cookies.append({"name": "PacerClientCode", "value": session.client_code, "url": origin,
                        "secure": True, "httpOnly": True, "sameSite": "Lax"})
    return cookies


def court_origin(url):
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in SUPPORTED_HOSTS or parsed.username or parsed.password or parsed.port:
        raise RocError("Court retrieval adapter is not enabled for this host.")
    return f"https://{parsed.hostname}"


def select_case_line(lines, number):
    matches = []
    for i, text in enumerate(lines):
        token = text.strip().split(" ", 1)[0]
        try:
            normalized = normalize_case_number(token)
        except RocError:
            continue
        # Main case only; a trailing numeric suffix denotes a defendant subcase.
        if normalized == number and not re.search(r"-\d+$", token):
            matches.append(i)
    if len(matches) != 1:
        raise RocError("The court did not return exactly one main-case selection.")
    return matches[0]


def choose_case(page, number):
    field = page.locator("#case_number_text_area_0")
    if field.count() != 1:
        raise RocError("Unsupported court case selector; no report submitted.")
    field.fill("")
    # CM/ECF updates the finder on keyboard events; fill() alone does not fire keyup.
    field.press_sequentially(number, delay=30)
    field.press("Tab")  # Some versions validate on change/blur rather than keyup.
    pick = page.locator("[id^=case_line_]")
    available = '#case_number_find_button_0:visible, #case_number_show_button_0:visible, [id^="case_line_"]:visible'
    page.locator(available).first.wait_for(state="visible")
    if not pick.first.is_visible():
        show = page.get_by_role("button", name="Show Case List", exact=True)
        if not show.is_visible():
            page.get_by_role("button", name="Find This Case", exact=True).click()
            page.locator('#case_number_show_button_0:visible, [id^="case_line_"]:visible').first.wait_for(state="visible")
        if not pick.first.is_visible() and show.is_visible():
            show.click()
    pick.first.wait_for(state="visible")
    index = select_case_line(pick.all_text_contents(), number)
    pick.nth(index).get_by_role("checkbox").check()


def configure_report(page):
    # These names come from the court form, not its incidental table nesting.
    for name in ("date_from", "date_to"):
        control = page.locator(f'input[name="{name}"]')
        if control.count() != 1:
            raise RocError("Date range controls are unfamiliar; no report submitted.")
        control.fill("")
    for name in ("documents_numbered_from_", "documents_numbered_to_"):
        control = page.locator(f'input[name="{name}"]')
        if control.count():
            control.fill("")
    page.locator("#list_of_parties_and_counsel").check()
    page.locator("#terminated_parties").check()
    for ident in ("view_multi_docs", "view_all_attachments", "view_comb_doc_checkbox", "create_appendix",
                  "list_of_member_cases", "links_to_notices_of_electronic_filing"):
        control = page.locator("#" + ident)
        if control.count() and control.is_checked():
            control.uncheck()
    radios = page.locator('input[name="output_format"]:checked')
    if radios.count() != 1 or radios.get_attribute("value") != "html":
        raise RocError("HTML output could not be confirmed; no report submitted.")
    run = page.get_by_role("button", name="Run Report", exact=True)
    if not run.is_enabled():
        raise RocError("Court has not enabled Run Report.")
    return run


class CourtRetriever:
    def __init__(self, session, store, headless=True, progress=None):
        self.session, self.store, self.headless = session, store, headless
        self.progress = progress or (lambda *args, **kwargs: None)

    def retrieve(self, case):
        number = normalize_case_number(case["caseNumber"])
        origin = court_origin(case["pacerLink"])
        parameters = {"court": case["courtId"], "caseNumber": number, "scope": "all-defendants", "partiesAndCounsel": True}
        cached = self.store.cached("docket", parameters)
        if cached:
            self.progress("reading_cached_docket", f"Using purchased report for {case['key']}; no new charge.")
            return cached
        # Validate budget before opening the court form; reserve immediately before submission.
        self.store.check_pending()
        if self.store.spent + 300 > self.store.limit:
            raise RocError("Insufficient remaining budget for a $3 docket report reservation.")
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise RocError("Live court retrieval needs the optional playwright dependency and its Chromium browser. See README.") from None
        with sync_playwright() as pw:
            try:
                browser = pw.chromium.launch(headless=self.headless)
            except Exception:
                raise RocError("The standalone Chromium runtime could not start. Install it with python -m playwright install chromium; no court report was requested.") from None
            context = browser.new_context()
            # Token is sent only to this selected court, never persisted as browser state.
            context.add_cookies(court_cookies(self.session, origin))
            page = context.new_page()
            page.set_default_timeout(30000)
            phase = "opening the court report form"
            try:
                self.progress("retrieving_docket", f"Court-web report: {case['key']}. Opening the report form.",
                              chargedCents=self.store.spent)
                page.goto(origin + "/cgi-bin/DktRpt.pl", wait_until="load")
                if urlparse(page.url).hostname != urlparse(origin).hostname:
                    raise RocError("Court redirected to sign-in. Stop; no browser-login fallback.")
                phase = "finding the case"
                choose_case(page, number)
                phase = "configuring the docket report"
                run = configure_report(page)
                transaction = self.store.reserve("docket", parameters, 300)
                phase = "waiting for the purchased report and receipt"
                self.progress("report_submitted", f"Submitting one docket report for {case['key']}; reserved up to $3.00.",
                              chargedCents=self.store.spent)
                try:
                    run.click()
                except Exception:
                    # A timeout can occur after submission. Observe; NEVER click again.
                    pass
                try:
                    page.get_by_text("Transaction Receipt", exact=True).wait_for(timeout=120000)
                finally:
                    raw = page.content()
                    saved = self.store.finish(transaction, raw)
                report = parse_report(raw)
                if report["caseNumber"] != number:
                    raise RocError("Court returned a different case; saved response requires review.")
                for cookie in context.cookies(origin):
                    if cookie["name"] == COURT_TOKEN_COOKIE:
                        self.session.token = cookie["value"]
                self.progress("docket_saved", f"Saved docket and receipt for {case['key']}. Total receipts: ${self.store.spent / 100:.2f}.",
                              chargedCents=self.store.spent)
                return saved
            except RocError:
                self.save_form_diagnostic(page, case, phase)
                raise
            except Exception:
                self.save_form_diagnostic(page, case, phase)
                raise RocError(f"Court-web retrieval failed while {phase}. Inspect the saved ledger; no report was automatically resubmitted.") from None
            finally:
                context.close()
                browser.close()

    def save_form_diagnostic(self, page, case, phase):
        """Record control structure only, never cookies, hidden values or login fields."""
        try:
            if urlparse(page.url).hostname not in SUPPORTED_HOSTS:
                return
            controls = page.locator("input, button, select, textarea").evaluate_all("""nodes => nodes.map(n => ({
                tag:n.tagName, id:n.id, name:n.name, type:n.type,
                visible: !!(n.offsetWidth || n.offsetHeight || n.getClientRects().length),
                events: ['onchange','onblur','onkeyup','onkeydown','onclick'].reduce((r,k)=>{if(n.hasAttribute(k))r[k]=n.getAttribute(k);return r},{}),
                checked: n.type === 'checkbox' || n.type === 'radio' ? n.checked : undefined,
                value: n.type === 'radio' || n.type === 'submit' || n.type === 'button' ? n.value : undefined,
                label: n.labels ? Array.from(n.labels).map(l=>l.innerText).join(' ') : undefined
            }))""")
            write_json(self.store.root / "diagnostics" / (fingerprint(case["key"]) + ".json"),
                       {"case": case["key"], "phase": phase, "controls": controls,
                        "caseListText": page.locator('[id^="case_line_"]').all_text_contents(),
                        "caseNumberValue": page.locator('#case_number_text_area_0').input_value() if page.locator('#case_number_text_area_0').count() else None,
                        "scripts": page.locator('script[src]').evaluate_all("nodes=>nodes.map(n=>new URL(n.src,location.href).pathname)"),
                        "formText": page.locator('body').inner_text()[:12000]})
        except Exception:
            pass  # Diagnostics must not hide the original stop or trigger another request.
