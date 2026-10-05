"""Document Grabber workflow, independent of any chat or agent session."""
import csv
import io
import json
from pathlib import Path
import re
import zipfile

from .common import RocError, fingerprint, now, read_json, write_json
from .ai_credentials import ProjectAIKey
from .claude import MODELS, analyze, analysis_key, estimate, payload
from .document_download import document_key, download_document
from .document_ledger import ExpenseLedger
from .documents import DISCLAIMER, read_entries
from .search import require_attorney, counsel_aliases


class DocumentGrabber:
    def __init__(self, workspace, credentials=None):
        self.ws = workspace
        self.credentials = credentials if credentials is not None else ProjectAIKey()

    def root(self, identifier):
        return self.ws.folder(identifier) / 'documents'

    def state(self, identifier):
        require_attorney(self.ws.manifest(identifier)['config'])
        root = self.root(identifier)
        path = root / 'results.json'
        return {**self.credentials.status(), 'models': MODELS, 'disclaimer': DISCLAIMER,
                'ai': ExpenseLedger(root, 'ai').public(), 'documents': ExpenseLedger(root, 'documents').public(),
                'results': read_json(path) if path.exists() else {'cases': {}}, 'liveValidated': False}

    def sources(self, identifier, keys):
        require_attorney(self.ws.manifest(identifier)['config'])
        if not isinstance(keys, list) or not keys or any(not isinstance(k, str) for k in keys) or len(set(keys)) != len(keys):
            raise RocError('Select one or more distinct cases.')
        cases = {c['key']: c for c in self.ws.cases(identifier)}
        reports = self.ws.completed_reports(identifier)
        aliases = counsel_aliases(self.ws.manifest(identifier)['config'])
        sources = []
        for key in keys:
            if key not in cases:
                raise RocError('A selected case is not in this run.')
            if key not in reports:
                raise RocError(f"{cases[key]['caseNumber']} needs a saved docket first. Run its docket report or deselect it; no reports are bought here.")
            sources.append(read_entries(Path(reports[key]['path']).read_text(encoding='utf-8'), cases[key], aliases))
        return sources

    def analysis_quote(self, identifier, values):
        self.ws.idle()
        model = values.get('model', 'claude-sonnet-5-5')
        ledger = ExpenseLedger(self.root(identifier), 'ai')
        ledger.check()
        sources = self.sources(identifier, values.get('keys'))
        cases, maximum = [], 0
        for s in sources:
            request = payload(s, model)
            previous = ledger.find(analysis_key(s, model))
            cost = 0 if previous else estimate(request)
            maximum += cost
            cases.append({'key': s['caseKey'], 'caseNumber': s['caseNumber'], 'district': s['district'],
                          'entryCount': len(s['entries']), 'clients': s['clients'], 'warnings': s['warnings'],
                          'cached': bool(previous), 'maximumCents': cost, 'sourceSha256': s['sourceSha256'],
                          'analysisId': analysis_key(s, model)})
        quote = {'model': model, 'keys': values['keys'], 'cases': cases, 'maximumCents': maximum, 'spentCents': ledger.spent}
        quote['quoteId'] = fingerprint({'run': identifier, **quote})
        return quote

    def purchase_quote(self, identifier, values):
        self.ws.idle()
        self.ws.library.check_document_receipts()
        state = self.state(identifier)
        ExpenseLedger(self.root(identifier), 'documents').check()
        ids = values.get('candidateIds')
        if not isinstance(ids, list) or not ids or any(not isinstance(i, str) for i in ids) or len(set(ids)) != len(ids):
            raise RocError('Choose distinct document candidates to download.')
        all_candidates = {c['candidateId']: (r['source'], c, r['analysisId']) for r in state['results']['cases'].values() for c in r['candidates']}
        items, seen = [], set()
        for ident in ids:
            if ident not in all_candidates:
                raise RocError('Candidate list changed. Review it again.')
            source, c, analysis_id = all_candidates[ident]
            if c['status'] != 'matched' or not c['url']:
                raise RocError('Only candidates with supporting source evidence and supported document links can be downloaded. Review-only/text-only rows remain in the export.')
            key = document_key(c['url'])
            if key in seen:
                continue
            seen.add(key)
            prior = ExpenseLedger(self.root(identifier), 'documents').find(key)
            if prior and prior['state'] != 'complete':
                raise RocError('This document has an unresolved purchase.')
            shared = self.ws.library.find_document(key)
            if shared and not shared['available']:
                raise RocError('A saved PDF is missing. Restore it instead of repurchasing.')
            items.append({'candidateId': ident, 'caseNumber': source['caseNumber'], 'courtId': source['courtId'],
                          'entryNumber': c['number'], 'kind': c['kind'], 'url': c['url'], 'cached': bool(prior or shared),
                          'analysisId': analysis_id, 'evidenceFingerprint': fingerprint(c)})
        quote = {'candidateIds': ids, 'items': items, 'maximumCents': sum(300 for i in items if not i['cached']),
                 'spentCents': state['documents']['spentCents']}
        quote['quoteId'] = fingerprint({'run': identifier, **quote})
        return quote

    def approve(self, identifier, action, values):
        self.ws.idle()
        quote = self.analysis_quote(identifier, values) if action == 'documents-analyze' else self.purchase_quote(identifier, values)
        if values.get('quoteId') != quote['quoteId']:
            raise RocError('Preview this selection again before confirming; the quote is missing or stale.')
        cap = values.get('budgetCents')
        if type(cap) is not int or cap < quote['maximumCents']:
            raise RocError('The fresh cap must cover this selection’s displayed reservation. Narrow the selection or raise the cap.')
        if self.ws.manifest(identifier)['demo']:
            raise RocError('The free demo never calls Claude or purchases documents. Use a real saved run for a live pilot.')
        if action == 'documents-analyze' and quote['maximumCents']:
            self.credentials.require()  # Owner setup, never browser-supplied keys.
        if action == 'documents-download' and quote['maximumCents']:
            self.ws.provider()  # Check the API session before saving this operation.
        ledger = ExpenseLedger(self.root(identifier), 'ai' if action == 'documents-analyze' else 'documents')
        ledger.approve(cap)
        m = self.ws.manifest(identifier)
        m['documentOperation'] = {'action': action, 'quote': quote}
        self.ws.save(identifier, m)
        self.ws._start(identifier, action)

    def work(self, identifier, action, session_provider=None):
        session_provider = session_provider or self.ws.provider
        m = self.ws.manifest(identifier)
        operation = m.get('documentOperation', {})
        if operation.get('action') != action:
            raise RocError('No approved Document Grabber operation to resume.')
        root = self.root(identifier)
        quote = operation['quote']
        path = root / 'results.json'
        results = read_json(path) if path.exists() else {'cases': {}}
        if action == 'documents-analyze':
            sources = self.sources(identifier, quote['keys'])
            expected = {c['key']: c['analysisId'] for c in quote['cases']}
            if any(expected[s['caseKey']] != analysis_key(s, quote['model']) for s in sources):
                raise RocError('Saved docket or analysis policy changed since approval; make a new analysis preview.')
            ledger = ExpenseLedger(root, 'ai')
            needs_key = any(not ledger.find(analysis_key(s, quote['model'])) for s in sources)
            api_key = self.credentials.require() if needs_key else ''
            for source in sources:
                self.ws.checkpoint()
                result = analyze(root, source, quote['model'], api_key, self.ws.checkpoint)
                result['analyzedUtc'] = now()
                write_json(root / 'analyses' / (result['analysisId'] + '.json'), result)
                results['cases'][source['caseKey']] = result
                write_json(path, results)
            return 'Document candidates ready. Review the evidence, then select PDFs and set a separate document cap.'
        candidates = {c['candidateId']: (r['source'], c, r['analysisId']) for r in results['cases'].values() for c in r['candidates']}
        self.ws.library.check_document_receipts()
        for item in quote['items']:
            self.ws.checkpoint()
            if (item['candidateId'] not in candidates or
                    fingerprint(candidates[item['candidateId']][1]) != item['evidenceFingerprint'] or
                    candidates[item['candidateId']][2] != item['analysisId']):
                raise RocError('Document candidate changed since approval. Make a new preview.')
            source, candidate, analysis_id = candidates[item['candidateId']]
            shared = self.ws.library.find_document(document_key(candidate['url']))
            if shared:
                if not shared['available']:
                    raise RocError('A saved PDF is missing. Restore it instead of repurchasing.')
                continue
            old = ExpenseLedger(root, 'documents').find(document_key(candidate['url']))
            session = None if old and old['state'] == 'complete' else session_provider()
            download_document(root, source, candidate, session, self.ws.checkpoint, analysis_id=analysis_id)
        return 'Selected documents saved. Download the bundle in Document Grabber.'

    def bundle(self, identifier):
        self.ws.idle()
        root = self.root(identifier)
        state = self.state(identifier)
        if not state['results']['cases'] and not any(t['state']=='complete' for t in state['documents']['transactions']):
            raise RocError('There are no analyzed filings or purchased PDFs to export. Use Docket Surfer to browse a case, or My Files for saved purchases.')
        out = root / 'document-bundle.zip'
        root.mkdir(parents=True, exist_ok=True)
        stream = io.StringIO(newline='')
        writer = csv.writer(stream)
        writer.writerow(['Court', 'Case number', 'Entry', 'Date', 'Kind', 'Client (docket attribution)', 'Status', 'Evidence', 'Docket text', 'PDF file'])
        pdfs, included, reused = {}, set(), []
        for r in state['results']['cases'].values():
            source = r['source']
            for c in r['candidates']:
                file = ''
                if c['url']:
                    key = document_key(c['url'])
                    saved = self.ws.library.find_document(key)
                    if saved:
                        path = saved['_path']
                        if not saved['available']:
                            raise RocError('A saved PDF is missing. Restore it instead of repurchasing.')
                        file = f"{source['courtId']}/{source['caseNumber'].replace(':','-')}/entry-{c['number']}-{key[:10]}.pdf"
                        pdfs[file] = path
                        included.add(key)
                        reused.append({k:saved[k] for k in ('id','caseKey','entryNumber','savedUtc','costCents')})
                values = [source['courtId'], source['caseNumber'], c['number'], c['date'], c['kind'], c['client'], c['status'], c['evidenceQuote'], c['text'], file]
                writer.writerow(["'" + v if isinstance(v, str) and re.match(r'^(?:\s*[=+@\-]|[\t\r\n])', v) else v for v in values])
        # A later model/source analysis may no longer include an earlier candidate.
        # Never drop an already purchased document from the downloadable bundle.
        for t in state['documents']['transactions']:
            if t['state'] != 'complete' or t['key'] in included:
                continue
            court, number = t['caseKey'].split('|', 1)
            path = root / 'pdfs' / (t['key'] + '.pdf')
            if not path.is_file():
                raise RocError('An earlier purchased PDF is missing. Restore it instead of repurchasing.')
            file = f"{court}/{number.replace(':','-')}/entry-{t['entryNumber']}-{t['key'][:10]}.pdf"
            pdfs[file] = path
            writer.writerow([court, number, t['entryNumber'], '', '', '', 'Purchased under an earlier analysis',
                             'See archived analyses and spending.json', '', file])
        temp = out.with_suffix('.tmp')
        with zipfile.ZipFile(temp, 'w', zipfile.ZIP_DEFLATED) as z:
            z.writestr('index.csv', '\ufeff' + stream.getvalue())
            z.writestr('methodology.txt', DISCLAIMER + '\n\nCandidates may be incomplete or misclassified. Review the saved docket. '
                       'Archived analyses with source.policyVersion 1 used the earlier literal as-to rule and explicit motion-number linkage. '
                       'Their evidence is retained without reinterpreting or re-running those analyses. '
                       'PDF adapter has offline tests only; no live acceptance yet.\n')
            z.writestr('evidence.json', json.dumps(state['results'], indent=2))
            z.writestr('spending.json', json.dumps({'ai': state['ai'], 'documents': state['documents'], 'includedSavedPurchases': reused}, indent=2))
            for archive in sorted((root / 'analyses').glob('*.json')):
                z.write(archive, 'analyses/' + archive.name)
            for name, path in pdfs.items():
                z.write(path, name)
        temp.replace(out)
        return out
