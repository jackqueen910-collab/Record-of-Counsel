"""Bounded court-web document purchase, separate from the official PCL API.

Only the single-document price-confirmation form is supported. Attachment menus,
JavaScript-only forms and unfamiliar viewers stop without a purchase POST.
"""
from decimal import Decimal
import http.cookiejar
import re
import urllib.error
import urllib.request
from urllib.parse import urlencode, urljoin, urlsplit

from .common import RocError, clean, fingerprint, normalize_case_number, now
from .courts import profile_for_url
from .docket import Tree
from .documents import document_url
from .document_ledger import ExpenseLedger
from .pacer import NoRedirect


def purchase_form(raw, url, number, document_number):
    root = Tree(raw).root
    text = clean(root.text())
    if re.search(r'\btranscript\b', text, re.I):
        raise RocError('Transcript purchase is outside Document Grabber’s scope.')
    case = re.search(r'Case\s+Number\s*:\s*(\S+)', text, re.I)
    doc = re.search(r'Document\s+Number\s*:\s*(\d+)(?![\d-])', text, re.I)
    costs = re.findall(r'\bCost\s*:\s*\$?\s*(\d+\.\d{2})\b', text, re.I)
    if (not case or normalize_case_number(case[1]) != number or not doc or doc[1] != document_number or
            len(costs) != 1 or not re.search(r'Billable\s+Pages\s*:\s*\d+', text, re.I)):
        raise RocError('Unfamiliar document price screen or wrong case/document. No purchase submitted.')
    price = int(Decimal(costs[0]) * 100)
    if price > 300:
        raise RocError('Document price exceeds the $3 reservation. No purchase submitted.')
    forms = list(root.walk('form'))
    if len(forms) != 1 or forms[0].attrs.get('method', '').lower() != 'post':
        raise RocError('Unsupported document purchase form. No purchase submitted.')
    form = forms[0]
    origin = profile_for_url(url).origin
    action = urljoin(url, form.attrs.get('action', ''))
    p = urlsplit(action)
    if (profile_for_url(action).origin != origin or p.fragment or
            p.path not in ('/cgi-bin/show_doc.pl', urlsplit(url).path) or
            not re.fullmatch(r'[\w=&;.,%-]*', p.query) or 'onsubmit' in form.attrs):
        raise RocError('Unsupported document purchase target. No purchase submitted.')
    fields, submit = {}, []
    if list(form.walk('select')) or list(form.walk('textarea')):
        raise RocError('Document selection/attachment form needs review. No purchase submitted.')
    controls = [*form.walk('input'), *form.walk('button')]
    for c in controls:
        typ = c.attrs.get('type', 'submit' if c.tag == 'button' else 'text').lower()
        if typ == 'hidden':
            name = c.attrs.get('name', '')
            if not name or name in fields:
                raise RocError('Ambiguous purchase form fields. No purchase submitted.')
            fields[name] = c.attrs.get('value', '')
        elif typ == 'submit' and clean(c.attrs.get('value', '') or c.text()).lower() == 'view document' and 'onclick' not in c.attrs:
            submit.append(c)
        elif typ == 'reset':
            continue
        else:
            raise RocError('Unsupported purchase controls. No purchase submitted.')
    if len(submit) != 1:
        raise RocError('Expected one View Document confirmation. No purchase submitted.')
    if submit[0].attrs.get('name'):
        name = submit[0].attrs['name']
        if name in fields:
            raise RocError('Duplicate purchase field. No purchase submitted.')
        fields[name] = submit[0].attrs.get('value', '')
    return {'url': action, 'fields': fields, 'priceCents': price}


class CourtDocumentHTTP:
    """No scripts, redirects or cross-court requests; cookies stay in memory."""
    def __init__(self, session, origin):
        self.origin, self.session = origin, session
        self.jar = http.cookiejar.CookieJar()
        for name, value in [('NextGenCSO', session.token), ('PacerClientCode', session.client_code)]:
            if value:
                self.jar.set_cookie(http.cookiejar.Cookie(0, name, value, None, False, urlsplit(origin).hostname,
                    False, False, '/', True, True, None, True, None, None, {}, False))
        self.opener = urllib.request.build_opener(NoRedirect(), urllib.request.HTTPCookieProcessor(self.jar))

    def request(self, url, fields=None):
        if profile_for_url(url).origin != self.origin:
            raise RocError('Cross-court document request refused.')
        req = urllib.request.Request(url, data=urlencode(fields).encode() if fields is not None else None,
            headers={'Content-Type': 'application/x-www-form-urlencoded'} if fields is not None else {})
        try:
            with self.opener.open(req, timeout=90) as response:
                raw = response.read(50_000_001)
                if len(raw) > 50_000_000:
                    raise RocError('Document exceeds the supported download size. No retry.')
                for cookie in self.jar:
                    if cookie.name == 'NextGenCSO':
                        self.session.token = cookie.value
                return raw
        except urllib.error.HTTPError as exc:
            if exc.code in (301, 302, 303, 307, 308, 401, 403):
                self.session.usable = False
            raise RocError(f'Court returned HTTP {exc.code}. No redirect, login or purchase retry; check saved status.') from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise RocError('Court document connection failed. No automatic retry; check saved status.') from None


def document_key(url):
    return fingerprint({'url': url})


def download_document(root, source, candidate, session, checkpoint=lambda: None, transport=None, analysis_id=''):
    origin = profile_for_url(candidate['url']).origin
    url = document_url(candidate['url'], origin)
    key = document_key(url)
    ledger = ExpenseLedger(root, 'documents')
    path = root / 'pdfs' / (key + '.pdf')
    old = ledger.find(key)
    if old:
        if old['state'] != 'complete' or not path.is_file():
            raise RocError('Purchased document is missing or unresolved. Restore/reconcile it instead of repurchasing.')
        return path
    checkpoint()
    ledger.check()
    if ledger.spent + 300 > ledger.data['limitCents']:
        raise RocError('Document spending cap reached. No court request submitted.')
    transport = transport or CourtDocumentHTTP(session, origin)
    # Landing links normally show an unbilled price screen. Reserve even this GET
    # so an unexpected direct PDF cannot disappear from the spending record.
    t = ledger.reserve(key, 300, {'caseKey': source['caseKey'], 'entryNumber': candidate['number'],
                                 'url': url, 'responseFile': str(path.relative_to(root)), 'analysisId': analysis_id})
    raw = transport.request(url)
    landing = root / 'prices' / (key + '.html')
    landing.parent.mkdir(parents=True, exist_ok=True)
    landing.write_bytes(raw)
    if raw.startswith(b'%PDF-'):
        raise RocError('Unexpected direct PDF before a price confirmation. Saved response; charge unresolved. No retry.')
    try:
        form = purchase_form(raw.decode('utf-8', errors='replace'), url, source['caseNumber'], candidate['number'])
    except RocError:
        # A received HTML landing page was not submitted; a future explicit
        # attempt after an adapter fix can reread this nonbillable price screen.
        ledger.not_submitted(t)
        raise
    t.update(priceCents=form['priceCents'], priceFile=str(landing.relative_to(root)))
    ledger.save()
    try:
        checkpoint()
    except RocError:
        ledger.not_submitted(t)
        raise
    t['purchaseSubmittedUtc'] = now()
    ledger.save()  # Must reach disk before the only purchase POST.
    body = transport.request(form['url'], form['fields'])
    path.parent.mkdir(parents=True, exist_ok=True)
    if not body.startswith(b'%PDF-') or b'%%EOF' not in body[-2048:]:
        # Preserve wrappers/errors, but never blindly follow their links or repeat POST.
        (path.parent / (key + '-unexpected.bin')).write_bytes(body)
        raise RocError('Purchase did not return a complete direct PDF. Response saved; charge unresolved. No retry.')
    temp = path.with_suffix('.tmp'); temp.write_bytes(body); temp.replace(path)
    ledger.finish(t, form['priceCents'], costBasis='Accepted court price screen and completed PDF download')
    return path
