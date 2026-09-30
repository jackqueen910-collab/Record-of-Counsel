"""Independent, optional Playwright court-web retriever.

This is web retrieval, NOT a PACER document API. It uses the official API token.
The supported form contract is deliberately narrow; unknown forms stop before Run Report.
"""
import re
from html import escape
from urllib.parse import urlparse, urljoin
from xml.etree import ElementTree

from .common import RocError, normalize_case_number, fingerprint, write_json, now, clean
from .docket import parse_report, Tree
from .courts import profile_for_url, profile_for_case, require_enabled

COURT_TOKEN_COOKIE = "NextGenCSO"  # Court cookie is case-sensitive; API JSON uses nextGenCSO.
LARGE_REPORT_NOTICE = "The report may take a long time to run because this case has many docket entries."


def large_report_confirmation(raw, origin):
    """Recognize only the observed full-report continuation, not a new purchase."""
    tree = Tree(raw).root
    text = clean(tree.text())
    if LARGE_REPORT_NOTICE not in text or "as initially requested" not in text:
        return None
    if "Transaction Receipt" in text or "DOCKET FOR CASE" in text:
        return None
    forms = list(tree.walk("form"))
    if len(forms) != 1 or forms[0].attrs.get("method", "").lower() != "post":
        raise RocError("Unfamiliar large-report confirmation form; do not resubmit.")
    target = urljoin(origin + "/cgi-bin/DktRpt.pl", forms[0].attrs.get("action", ""))
    parsed = urlparse(target)
    if (profile_for_url(target).origin != origin or parsed.path != "/cgi-bin/DktRpt.pl"
            or not re.fullmatch(r"\d+-L_\d+_\d+-\d+", parsed.query) or parsed.fragment):
        raise RocError("Large-report confirmation target is unfamiliar; do not resubmit.")
    controls = list(forms[0].walk("input"))
    radios = [n for n in controls if n.attrs.get("name") == "date_from"]
    if (len(radios) != 4 or any(n.attrs.get("type", "").lower() != "radio" for n in radios)
            or sum(n.attrs.get("value") == "" for n in radios) != 1
            or any(n.attrs.get("name") not in ("date_from", "button1", "reset") for n in controls)):
        raise RocError("Unfamiliar large-report confirmation controls; do not resubmit.")
    return target


def pending_confirmation(store):
    """Unknown charges still block everything; one saved continuation can resume."""
    pending = [t for t in store.ledger["transactions"] if t["state"] != "complete"]
    if not pending:
        store.check_pending()
        return None
    if len(pending) == 1 and not store.ledger.get("stoppedReason"):
        t = pending[0]
        path = store.root / t["responseFile"]
        if t["kind"] == "docket" and path.exists() and not t.get("confirmationSubmittedUtc"):
            from .courts import court_profile
            profile = court_profile(t["parameters"]["court"])
            if large_report_confirmation(path.read_text(encoding="utf-8"), profile.origin):
                return t
    store.check_pending()
    return None


def court_cookies(session, origin):
    court_origin(origin)
    cookies = [{"name": COURT_TOKEN_COOKIE, "value": session.token, "url": origin,
                "secure": True, "httpOnly": True, "sameSite": "Lax"}]
    if session.client_code:
        cookies.append({"name": "PacerClientCode", "value": session.client_code, "url": origin,
                        "secure": True, "httpOnly": True, "sameSite": "Lax"})
    return cookies


def court_origin(url):
    return profile_for_url(url).origin


def has_defendant_suffix(value):
    if "|" in value:
        return True  # The court widget's defendant checkbox embeds extra metadata.
    match = re.fullmatch(r"\d+:\d{2,4}-?[a-z]+-?\d+(?P<suffix>(?:-.*)?)", value, re.I)
    return bool(match and re.search(r"-\d+$", match["suffix"]))


def select_case_line(lines, number):
    matches = []
    for i, text in enumerate(lines):
        token = text.strip().split(" ", 1)[0]
        try:
            normalized = normalize_case_number(token)
        except RocError:
            continue
        # Main case only; a trailing numeric suffix denotes a defendant subcase.
        if normalized == number and not has_defendant_suffix(token):
            matches.append(i)
    if len(matches) != 1:
        raise RocError("The court did not return exactly one main-case selection.")
    return matches[0]


def lookup_main_case(raw, number):
    """Verify the court widget's already-fetched XML, not a separate API request."""
    try:
        root = ElementTree.fromstring(raw)
    except ElementTree.ParseError:
        raise RocError("Court case lookup returned unreadable XML; no report submitted.") from None
    matches = []
    for node in root.iter("case"):
        raw_number = node.get("number", "")
        try:
            match = normalize_case_number(raw_number) == number
        except RocError:
            continue
        if match and node.get("defendant") in (None, "0") and not has_defendant_suffix(raw_number):
            ident = node.get("id", "")
            if ident.isdigit():
                matches.append({"id": ident, "number": raw_number, "title": node.get("title", "")})
    if len(matches) != 1:
        raise RocError("Court lookup did not identify exactly one requested main case; no report submitted.")
    return matches[0]


def choose_case(page, number):
    lookups = []
    def capture(response):
        if urlparse(response.url).path == "/cgi-bin/possible_case_numbers.pl":
            try:
                lookups.append(response.text())
            except Exception:
                pass
    page.on("response", capture)
    try:
        return _choose_case(page, number, lookups)
    finally:
        page.remove_listener("response", capture)


def _choose_case(page, number, lookups):
    field = page.locator("#case_number_text_area_0")
    if field.count() != 1:
        raise RocError("Unsupported court case selector; no report submitted.")
    field.fill("")
    # CM/ECF updates the finder on keyboard events; fill() alone does not fire keyup.
    field.press_sequentially(number, delay=30)
    field.press("Tab")  # Some versions validate on change/blur rather than keyup.
    pick = page.locator("[id^=case_line_]")
    ready_script = """() => {
        const ids = document.getElementById('all_case_ids');
        if(ids && /^[1-9][0-9]*$/.test(ids.value)) return true;
        return [...document.querySelectorAll('#case_number_find_button_0, #case_number_show_button_0, [id^="case_line_"]')]
          .some(n => !!(n.offsetWidth || n.offsetHeight || n.getClientRects().length));
    }"""
    page.wait_for_function(ready_script)
    selected_ids = page.locator("#all_case_ids")
    if selected_ids.count() and re.fullmatch(r"[1-9][0-9]*", selected_ids.input_value()) and not pick.count():
        if not lookups:
            raise RocError("Automatic court selection lacked a verifiable lookup response; no report submitted.")
        chosen = lookup_main_case(lookups[-1], number)
        if selected_ids.input_value() != chosen["id"] or normalize_case_number(field.input_value()) != number:
            raise RocError("Automatic court selection differs from the requested main case; no report submitted.")
        return
    if not pick.first.is_visible():
        show = page.get_by_role("button", name="Show Case List", exact=True)
        if not show.is_visible():
            page.get_by_role("button", name="Find This Case", exact=True).click()
            page.wait_for_function(ready_script)
            if selected_ids.count() and re.fullmatch(r"[1-9][0-9]*", selected_ids.input_value()) and not pick.count():
                if not lookups:
                    raise RocError("Automatic court selection lacked a verifiable lookup response; no report submitted.")
                chosen = lookup_main_case(lookups[-1], number)
                if selected_ids.input_value() != chosen["id"] or normalize_case_number(field.input_value()) != number:
                    raise RocError("Automatic court selection differs from the requested main case; no report submitted.")
                return
        if not pick.first.is_visible() and show.is_visible():
            show.click()
    pick.first.wait_for(state="visible")
    numbers = [pick.nth(i).get_by_role("checkbox").get_attribute("value") or pick.nth(i).inner_text()
               for i in range(pick.count()) if pick.nth(i).get_by_role("checkbox").count()]
    rows = [pick.nth(i) for i in range(pick.count()) if pick.nth(i).get_by_role("checkbox").count()]
    index = select_case_line(numbers, number)
    rows[index].get_by_role("checkbox").check()


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


class DistrictCMECFAdapter:
    """Shared, tested form contract. New layouts require an explicit adapter change."""
    report_path = "/cgi-bin/DktRpt.pl"

    def select_case(self, page, number):
        choose_case(page, number)

    def prepare_report(self, page):
        return configure_report(page)

    def validate_report(self, raw, case, profile):
        report = parse_report(raw)
        if report["caseNumber"] != normalize_case_number(case["caseNumber"]):
            raise RocError("Court returned a different case; saved response requires review.")
        if not profile.matches_heading(report["heading"]):
            raise RocError("Court report heading differs from the requested district; saved response requires review.")
        return report


ADAPTERS = {"district-cmecf": DistrictCMECFAdapter()}


class CourtRetriever:
    def __init__(self, session, store, headless=True, progress=None, allow_unverified=False):
        self.session, self.store, self.headless = session, store, headless
        self.progress = progress or (lambda *args, **kwargs: None)
        self.allow_unverified = allow_unverified

    def retrieve(self, case):
        number = normalize_case_number(case["caseNumber"])
        profile = profile_for_case(case)
        require_enabled(profile, self.allow_unverified)
        adapter = ADAPTERS.get(profile.adapter)
        if adapter is None:
            raise RocError("Court adapter is not implemented; no report submitted.")
        origin = profile.origin
        parameters = {"court": case["courtId"], "caseNumber": number, "scope": "all-defendants", "partiesAndCounsel": True}
        cached = self.store.cached("docket", parameters)
        if cached:
            self.progress("reading_cached_docket", f"Using purchased report for {case['key']}; no new charge.")
            return cached
        if not profile.sample_verified:
            self.progress("unverified_court", f"Controlled test of {profile.district}: retrieval has not been live-verified.",
                          courtId=profile.court_id, validationStatus=profile.validation_status)
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
                page.goto(origin + adapter.report_path, wait_until="load")
                if urlparse(page.url).hostname != urlparse(origin).hostname:
                    raise RocError("Court redirected to sign-in. Stop; no browser-login fallback.")
                phase = "finding the case"
                adapter.select_case(page, number)
                phase = "configuring the docket report"
                run = adapter.prepare_report(page)
                transaction = self.store.reserve("docket", parameters, 300)
                phase = "waiting for the purchased report and receipt"
                self.progress("report_submitted", f"Submitting one docket report for {case['key']}; reserved up to $3.00.",
                              chargedCents=self.store.spent)
                try:
                    run.click()
                except Exception:
                    # A timeout can occur after submission. Observe; NEVER click again.
                    pass
                saved = self.finish_report(page, transaction, case, profile, adapter)
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

    def finish_report(self, page, transaction, case, profile, adapter):
        try:
            page.wait_for_function("""() => document.body &&
                (document.body.innerText.includes('Transaction Receipt') ||
                 document.body.innerText.includes('The report may take a long time to run because this case has many docket entries.'))""",
                timeout=120000)
        except Exception:
            pass  # Save what arrived; never click the initial report button again.
        raw = page.content()
        if large_report_confirmation(raw, profile.origin):
            self.store.save_response(transaction, raw)
            return self.finish_confirmation(page, transaction, case, profile, adapter)
        saved = self.store.finish(transaction, raw)
        adapter.validate_report(raw, case, profile)
        return saved

    def finish_confirmation(self, page, transaction, case, profile, adapter):
        if transaction.get("confirmationSubmittedUtc"):
            raise RocError("This report continuation was already submitted; receipt review is required before another request.")
        original = page.locator('input[type="radio"][name="date_from"][value=""]')
        if original.count() != 1:
            raise RocError("Full-report confirmation option is missing; no continuation submitted.")
        original.check()
        # Mark before the click. A timeout/crash must never replay this POST.
        transaction["confirmationSubmittedUtc"] = now()
        self.store.save()
        self.progress("confirming_full_report", f"Confirming the full report already requested for {case['key']}; same $3 reservation.",
                      chargedCents=self.store.spent)
        try:
            page.get_by_role("button", name="Run Report", exact=True).click()
        except Exception:
            pass
        try:
            page.get_by_text("Transaction Receipt", exact=True).wait_for(timeout=120000)
        except Exception:
            pass  # Receipt parsing below decides whether the reservation resolves.
        finally:
            raw = page.content()
            saved = self.store.finish(transaction, raw)
        adapter.validate_report(raw, case, profile)
        return saved

    def resume_confirmation(self, transaction, case):
        """Finish a saved, never-submitted confirmation using its original action.

        The report form is NOT reopened. A local browser form posts the observed
        original action and date_from='' once, under the same reservation.
        """
        profile = profile_for_case(case)
        require_enabled(profile, self.allow_unverified)
        if (transaction is not pending_confirmation(self.store)
                or transaction["parameters"]["court"] != case["courtId"]
                or transaction["parameters"]["caseNumber"] != case["caseNumber"]):
            raise RocError("Saved continuation does not match this selected case.")
        if self.store.spent + transaction["reservedCents"] > self.store.limit:
            raise RocError("Remaining budget does not cover the existing report reservation.")
        raw = (self.store.root / transaction["responseFile"]).read_text(encoding="utf-8")
        target = large_report_confirmation(raw, profile.origin)
        adapter = ADAPTERS[profile.adapter]
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=self.headless)
            context = browser.new_context()
            context.add_cookies(court_cookies(self.session, profile.origin))
            page = context.new_page()
            local_url = profile.origin + "/__roc_saved_report_confirmation__"
            # Intercept this synthetic navigation completely. Only the final
            # observed form action is allowed to reach the court.
            form = ('<form method="post" enctype="multipart/form-data" action="' + escape(target, quote=True) + '">'
                    '<input type="radio" name="date_from" value=""><button type="submit">Run Report</button></form>')
            page.route(local_url, lambda route: route.fulfill(content_type="text/html", body=form))
            try:
                page.goto(local_url)
                saved = self.finish_confirmation(page, transaction, case, profile, adapter)
                for cookie in context.cookies(profile.origin):
                    if cookie["name"] == COURT_TOKEN_COOKIE:
                        self.session.token = cookie["value"]
                return saved
            finally:
                context.close()
                browser.close()

    def save_form_diagnostic(self, page, case, phase):
        """Record control structure only, never cookies, hidden values or login fields."""
        try:
            if profile_for_url(page.url).court_id != str(case["courtId"]).lower():
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
