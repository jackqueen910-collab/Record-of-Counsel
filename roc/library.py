"""Account-scoped catalogue of purchased files; no network or automatic deletion."""
import csv
import io
import re
import zipfile

from .common import RocError, fingerprint, read_json
from .document_ledger import ExpenseLedger


class Library:
    def __init__(self, workspace):
        self.ws = workspace

    def entries(self):
        result = []
        for manifest in self.ws.root.glob('*/workspace.json'):
            run = manifest.parent.name
            if not re.fullmatch('[a-f0-9]{32}', run):
                continue
            root = self.ws.folder(run)
            for tx in self.ws.ledger(run)['transactions']:
                if tx['kind'] != 'docket' or tx['state'] != 'complete':
                    continue
                p = tx['parameters']
                result.append(self._entry(root, tx, run, 'docket', p['court'], p['caseNumber'], 'Docket report'))
            ledger = root / 'documents' / 'documents-ledger.json'
            if ledger.exists():
                for tx in read_json(ledger)['transactions']:
                    if tx['state'] == 'complete':
                        court, number = tx['caseKey'].split('|', 1)
                        result.append(self._entry(root/'documents', tx, run, 'pdf', court, number, 'Entry '+tx['entryNumber']))
        root = self.ws.root / 'purchased-documents'
        ledger = root / 'documents-ledger.json'
        if ledger.exists():
            for tx in read_json(ledger)['transactions']:
                if tx['state'] == 'complete':
                    court, number = tx['caseKey'].split('|', 1)
                    result.append(self._entry(root, tx, tx.get('runId',''), 'pdf', court, number, tx.get('description') or 'Entry '+tx['entryNumber']))
        return sorted(result, key=lambda x: x['savedUtc'], reverse=True)

    def _entry(self, root, tx, run, kind, court, number, description):
        path = (root / tx['responseFile']).resolve()
        if not path.is_relative_to(self.ws.root):
            raise RocError('Saved file points outside this account. No file was opened.')
        return {'id': fingerprint({'path': str(path.relative_to(self.ws.root))}),
                'runId': run, 'kind': kind, 'courtId': court, 'caseNumber': number,
                'caseKey': court+'|'+number, 'entryNumber': tx.get('entryNumber',''),
                'description': description, 'savedUtc': tx.get('completedUtc',tx['startedUtc']),
                'costCents': tx.get('chargedCents',0), 'size': path.stat().st_size if path.is_file() else 0,
                'available': path.is_file(), 'documentKey': tx.get('key',''), '_path': path}

    def public(self):
        rows = [{k:v for k,v in e.items() if k != '_path'} for e in self.entries()]
        return {'files': rows, 'totalBytes': sum(e['size'] for e in rows), 'retention': 'Keep purchases; automatic deletion is off.'}

    def file(self, identity):
        entry = next((e for e in self.entries() if e['id'] == identity), None)
        if not entry or not entry['available']:
            raise RocError('This saved file is unavailable. It will not be bought again automatically.')
        label = f"{entry['courtId']}_{entry['caseNumber'].replace(':','-')}_" + ('docket.html' if entry['kind']=='docket' else f"entry-{entry['entryNumber']}.pdf")
        return entry['_path'], label

    def find_document(self, key):
        return next((e for e in self.entries() if e['kind']=='pdf' and e['documentKey']==key), None)

    def check_document_receipts(self):
        for manifest in self.ws.root.glob('*/workspace.json'):
            ExpenseLedger(manifest.parent/'documents','documents').check()
        ExpenseLedger(self.ws.root/'purchased-documents','documents').check()

    def bundle(self, ids):
        if not isinstance(ids,list) or not ids or len(ids)>500 or any(not isinstance(i,str) for i in ids):
            raise RocError('Select between 1 and 500 saved files.')
        entries = {e['id']:e for e in self.entries()}
        chosen = []
        for identity in dict.fromkeys(ids):
            if identity not in entries or not entries[identity]['available']:
                raise RocError('A selected saved file is unavailable. Refresh My Files.')
            chosen.append(entries[identity])
        root = self.ws.root/'library-exports'
        root.mkdir(exist_ok=True)
        path = root/'my-files.zip'
        index = io.StringIO(); writer=csv.writer(index)
        writer.writerow(['File','Court','Case number','Entry','Description','Saved','Cost'])
        with zipfile.ZipFile(path.with_suffix('.tmp'),'w',zipfile.ZIP_DEFLATED) as archive:
            for e in chosen:
                source, name=self.file(e['id'])
                name=e['id'][:10]+'/'+name
                archive.write(source,name)
                values=[name,e['courtId'],e['caseNumber'],e['entryNumber'],e['description'],e['savedUtc'],f"{e['costCents']/100:.2f}"]
                writer.writerow(["'"+v if isinstance(v,str) and re.match(r'^(?:\s*[=+@\-]|[\t\r\n])',v) else v for v in values])
            archive.writestr('index.csv','\ufeff'+index.getvalue())
        path.with_suffix('.tmp').replace(path)
        return path
