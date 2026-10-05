"""Manual docket browsing and explicit PDF purchases, with no model calls."""
from pathlib import Path

from .common import RocError, fingerprint, read_json
from .documents import read_entries
from .document_download import document_key, download_document, DocumentMenuFound
from .document_ledger import ExpenseLedger


class DocketSurfer:
    def __init__(self, workspace):
        self.ws = workspace
        self.root = workspace.root / 'purchased-documents'

    def case(self, run, key):
        case = next((c for c in self.ws.cases(run) if c['key']==key),None)
        if case is None:
            raise RocError('This case is not in the saved search.')
        report = self.ws.completed_reports(run).get(key)
        result = {'case':{k:case.get(k,'') for k in ('key','caseNumber','caseTitle','courtId','district','dateFiled','caseType','status','nature')}, 'saved':bool(report)}
        if not report:
            return result
        source = read_entries(Path(report['path']).read_text(encoding='utf-8'),case)
        expanded = []
        menus = []
        for entry in source['entries']:
            menu_path = self.root/'menus'/(document_key(entry['url'])+'.json') if entry['url'] else None
            if menu_path and menu_path.is_file():
                menu = read_json(menu_path)
                if menu['caseKey'] != key or menu['url'] != entry['url']:
                    raise RocError('Saved document menu does not match this docket entry.')
                expanded.append(entry | {'url':'','linkStatus':'Document menu — choose a file below'})
                menus.append(menu)
                for i, option in enumerate(menu['options']):
                    expanded.append(entry | option | {'id':entry['id']+'d'+str(i+1),'linkStatus':'available'})
            else:
                expanded.append(entry)
        source['entries'] = expanded
        if menus:
            source['sourceSha256'] = fingerprint({'docket':source['sourceSha256'],'menus':menus})
        saved = {e['documentKey']:e for e in self.ws.library.entries() if e['kind']=='pdf'}
        for entry in source['entries']:
            prior = saved.get(document_key(entry['url'])) if entry['url'] else None
            entry['savedFileId'] = prior['id'] if prior and prior['available'] else ''
            entry['missingPurchase'] = bool(prior and not prior['available'])
        result.update(source=source,savedUtc=now_from_path(report['path']))
        return result

    def quote(self, run, values):
        self.ws.idle()
        self.ws.library.check_document_receipts()
        case = self.case(run, values.get('caseKey'))
        if not case['saved']:
            raise RocError('Retrieve this docket report first.')
        ids=values.get('entryIds')
        if not isinstance(ids,list) or not ids or len(ids)>100 or any(not isinstance(i,str) for i in ids) or len(set(ids))!=len(ids):
            raise RocError('Select 1–100 distinct document entries.')
        by_id={e['id']:e for e in case['source']['entries']}
        items=[];seen=set()
        for identity in ids:
            entry=by_id.get(identity)
            if not entry or not entry['url'] or not entry['number']:
                raise RocError('This entry has no supported document link. Text-only entries cannot be purchased.')
            key=document_key(entry['url'])
            if key in seen:continue
            seen.add(key)
            old=self.ws.library.find_document(key)
            if old and not old['available']:
                raise RocError('A previously purchased file is missing. Restore it instead of buying it again.')
            items.append({'entryId':identity,'number':entry['number'],'text':entry['text'],'url':entry['url'],'cached':bool(old)})
        quote={'caseKey':values['caseKey'],'entryIds':ids,'sourceSha256':case['source']['sourceSha256'],
               'items':items,'maximumCents':sum(0 if e['cached'] else 300 for e in items)}
        quote['quoteId']=fingerprint({'run':run,**quote})
        return quote

    def approve(self, run, values):
        quote=self.quote(run,values)
        if values.get('quoteId')!=quote['quoteId']:
            raise RocError('Review this selection again. The purchase preview changed.')
        cap=values.get('budgetCents')
        if type(cap)is not int or cap<quote['maximumCents']:
            raise RocError('The fresh document cap must cover the displayed maximum. Select fewer documents or increase the cap.')
        if self.ws.manifest(run)['demo']:
            raise RocError('Demo cases never buy documents.')
        if quote['maximumCents']:self.ws.provider()
        ExpenseLedger(self.root,'documents').approve(cap)
        m=self.ws.manifest(run);m['surferOperation']=quote;self.ws.save(run,m)
        self.ws._start(run,'surfer-download')

    def work(self,run,provider):
        quote=self.ws.manifest(run)['surferOperation']
        case=self.case(run,quote['caseKey']);source=case['source']
        if source['sourceSha256']!=quote['sourceSha256']:
            raise RocError('The saved docket changed. Preview the documents again.')
        self.ws.library.check_document_receipts()
        count=0
        for item in quote['items']:
            self.ws.checkpoint()
            old=self.ws.library.find_document(document_key(item['url']))
            if old:
                if not old['available']:raise RocError('A saved purchase is missing; no repeat purchase submitted.')
                count+=1;continue
            try:
                download_document(self.root,source,item,provider(),self.ws.checkpoint,allow_menus=True)
            except DocumentMenuFound as exc:
                return str(exc) + f' {count} earlier selected files are saved in My Files.'
            ledger=ExpenseLedger(self.root,'documents')
            tx=ledger.find(document_key(item['url']));tx.update(runId=run,description=item['text']);ledger.save()
            count+=1
        return f'Saved {count} selected documents to My Files. No AI analysis was used.'


def now_from_path(path):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(Path(path).stat().st_mtime,timezone.utc).isoformat()
