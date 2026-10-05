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

from .common import RocError, clean, fingerprint, normalize_case_number, now, write_json
from .courts import profile_for_url
from .docket import Tree
from .documents import document_url
from .document_ledger import ExpenseLedger
from .pacer import NoRedirect


def price_form(raw, url, allow_attachments=False):
    """Accept only a recognized, unsubmitted price-confirmation screen."""
    root = Tree(raw).root
    text = clean(root.text())
    if re.search(r'Transaction\s+Receipt', text, re.I) or list(root.walk('iframe')) or list(root.walk('object')):
        raise RocError('Unexpected document receipt or viewer. Charge unresolved; no retry.')
    if re.search(r'\btranscript\b', text, re.I):
        raise RocError('Transcript purchase is outside Document Grabber’s scope.')
    case = re.search(r'Case\s+Number\s*:\s*(\S+)', text, re.I)
    doc = re.search(r'Document\s+Number\s*:\s*(' + (r'\d+(?:-\d+)?' if allow_attachments else r'\d+') + r')(?![\d-])', text, re.I)
    costs = re.findall(r'\bCost\s*:\s*\$?\s*(\d+\.\d{2})\b', text, re.I)
    if (not case or not doc or
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
    return {'url': action, 'fields': fields, 'priceCents': price,
            'caseNumber': normalize_case_number(case[1]), 'documentNumber': doc[1]}


def purchase_form(raw, url, number, document_number):
    form = price_form(raw, url)
    if form['caseNumber'] != number or form['documentNumber'] != document_number:
        raise RocError('Wrong case/document on price screen. No purchase submitted.')
    return form


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


class DocumentMenuFound(RocError):
    pass


def attachment_menu(raw, url, case_number, entry_number):
    """A narrow, source-bound unsubmitted menu. Unknown pages remain unresolved."""
    tree = Tree(raw).root
    text = clean(tree.text())
    if ('Document Selection Menu' not in text or re.search(r'Transaction\s+Receipt|Billable\s+Pages|Cost\s*:',text,re.I)
            or list(tree.walk('form')) or list(tree.walk('iframe'))):
        return None
    case = re.search(r'Case\s+Number\s*:\s*(\S+)',text,re.I)
    doc = re.search(r'Document\s+Number\s*:\s*(\d+)(?![\d-])',text,re.I)
    if not case or normalize_case_number(case[1]) != case_number or not doc or doc[1] != entry_number:
        return None
    origin = profile_for_url(url).origin
    options = []
    for row in tree.walk('tr'):
        cells = [c for c in row.children if hasattr(c,'tag') and c.tag in ('td','th')]
        if len(cells)<2:
            continue
        label = clean(cells[0].text())
        if label.lower() in ('main document','main','0') or label == entry_number:
            number = entry_number
        elif re.fullmatch(re.escape(entry_number)+r'-[1-9]\d*',label):
            number = label
        else:
            m = re.fullmatch(r'(?:Attachment\s*)?([1-9]\d*)',label,re.I)
            if not m:
                continue
            number = entry_number+'-'+m[1]
        links = list(row.walk('a'))
        if len(links)!=1 or any(k.startswith('on') for k in links[0].attrs):
            return None
        try:
            child = document_url(links[0].attrs.get('href',''),origin)
        except RocError:
            return None
        if child==url or any(o['url']==child or o['number']==number for o in options):
            return None
        options.append({'number':number,'url':child,'text':clean(row.text())})
    return options or None


def download_document(root, source, candidate, session, checkpoint=lambda: None, transport=None, analysis_id='', allow_menus=False):
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
    t['priceFile'] = str(landing.relative_to(root))
    ledger.save()
    if raw.startswith(b'%PDF-'):
        raise RocError('Unexpected direct PDF before a price confirmation. Saved response; charge unresolved. No retry.')
    # Unknown HTML may be a charged viewer/receipt. Keep its reservation and
    # evidence, just as for a direct PDF; never assume every GET is unbilled.
    decoded = raw.decode('utf-8', errors='replace')
    if allow_menus:
        options = attachment_menu(decoded,url,source['caseNumber'],candidate['number'])
        if options:
            write_json(root/'menus'/(key+'.json'),{'caseKey':source['caseKey'],'url':url,'options':options})
            ledger.not_submitted(t)
            raise DocumentMenuFound('Document menu saved. Reopen this docket to choose the main document or individual attachments. No PDF purchase was submitted for this entry.')
    form = price_form(decoded, url, allow_attachments=allow_menus)
    if form['caseNumber'] != source['caseNumber'] or form['documentNumber'] != candidate['number']:
        ledger.not_submitted(t)
        raise RocError('Wrong case/document on price screen. No purchase submitted.')
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
