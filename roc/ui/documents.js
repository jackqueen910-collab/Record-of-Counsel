"use strict";
let docRun = null, docKeys = [], docState = null, analysisQuote = null, purchaseQuote = null;
let docSelected = new Set(), docBusy = false, docStamp = '', docAvailable = false;
let docStatusStamp = '';
const docMessage = text => $('documents-message').textContent = text;
function docControls() {
  const busy = docBusy || !!activeRun || busyAction || shuttingDown;
  $('open-documents').disabled = !current || litigantRun() || !docAvailable || shuttingDown;
  $('open-documents').title = docAvailable ? 'Analyze saved dockets and choose PDFs' : 'Stop ROC and reopen Start ROC to load Document Grabber';
  for (const id of ['preview-analysis','document-model','document-bundle']) $(id).disabled = busy;
  $('preview-analysis').disabled = busy || !docKeys.length;
  $('preview-documents').disabled = busy || !docSelected.size;
  const valid = (quote,id) => {try {return quote && cents($(id).value) >= quote.maximumCents;} catch {return false;}};
  $('confirm-analysis').disabled = busy || current?.demo || !valid(analysisQuote,'ai-cap') || (analysisQuote?.maximumCents > 0 && !docState?.configured);
  $('confirm-documents').disabled = busy || current?.demo || !valid(purchaseQuote,'document-cap');
}
async function docTask(fn) {
  if (docBusy) return;
  docBusy = true; docControls();
  try {await fn();} catch (e) {if (!e.staleAccount) docMessage(e.message || String(e));}
  finally {docBusy = false; docControls();}
}
async function loadDocuments() {
  if (!docRun) return;
  const run = docRun, state = await api(`/api/runs/${run}/documents`);
  if (run !== docRun) return;
  docState = state;
  $('documents-method').textContent = state.disclaimer;
  $('claude-status').textContent = state.connectionMessage || 'AI setup needed. Contact the ROC owner.';
  $('document-spending').textContent = `AI usage estimate: ${money(state.ai.spentCents)} · Documents: ${money(state.documents.spentCents)} · Pending: ${state.ai.pendingCount + state.documents.pendingCount}. Separate from case-search and docket spending.`;
  const stamp = JSON.stringify([state.results,state.documents]);
  if (stamp !== docStamp) {
    docStamp = stamp; docSelected.clear(); purchaseQuote = null; $('purchase-preview').hidden = true;
    const list = $('document-candidates'); list.replaceChildren();
    for (const result of Object.values(state.results.cases)) {
      const source = result.source;
      const entryLabel = id => {const entry=source.entries.find(e=>e.id===id);return entry ? `entry ${entry.number || 'unnumbered'} (${entry.date})` : 'unresolved entry';};
      const section = node('section',undefined,'candidate-case');
      section.append(node('h4',`${source.caseNumber} · ${source.district}`));
      section.append(node('p',`${result.model} · ${source.entries.length} docket entries examined · ${result.candidates.length} candidates`,'help'));
      if ((source.policyVersion || 1) < 2) section.append(node('p','Saved under the earlier “as to” attribution policy. A new analysis requires a new preview and cap.','help'));
      for (const warning of source.warnings) section.append(node('p',warning,'cost-warning'));
      for (const c of result.candidates) {
        const row = node('div',undefined,'document-candidate'), label = node('label',undefined,'checkbox-label');
        const box = node('input'); box.type = 'checkbox'; box.dataset.candidate = c.candidateId;
        box.disabled = c.status !== 'matched' || !c.url;
        box.addEventListener('change',()=>{box.checked ? docSelected.add(c.candidateId) : docSelected.delete(c.candidateId); purchaseQuote=null; $('purchase-preview').hidden=true;docControls();});
        label.append(box,node('strong',`Entry ${c.number || 'unnumbered'} · ${c.kind} · ${c.status === 'matched' ? c.url ? 'Candidate' : 'Text-only / unsupported link' : 'Needs review'}`)); row.append(label);
        const detail = node('details'), summary = node('summary',`${c.date} · ${c.client || 'Client unresolved'} · Show evidence`);
        detail.append(summary,node('p',c.reason),node('p',`Classification evidence: ${c.evidenceQuote}`));
        for (const q of c.attributionEvidence || []) detail.append(node('p',`Client attribution · ${entryLabel(q.entryId)}: ${q.quote}`));
        if (c.asToQuote) detail.append(node('p',`Earlier “as to” evidence: ${c.asToQuote}`));
        for (const q of c.linkEvidence || []) detail.append(node('p',`Order link · ${entryLabel(q.motionId)}: ${q.quote}`));
        detail.append(node('p',`Linked motions: ${c.motionIds.map(entryLabel).join('; ') || 'None'}`),node('pre',c.text));
        row.append(detail); section.append(row);
      }
      if (!result.candidates.length) section.append(node('p','No candidates returned. This is not a completeness guarantee.','help'));
      list.append(section);
    }
    if (!list.children.length) list.append(node('p','Analyze selected saved dockets to see candidates here.','help'));
  }
  docControls();
}
bind('open-documents','click',()=>docTask(async()=>{
  docRun=currentId;docKeys=[...selected];docSelected.clear();docStamp='';docStatusStamp='';analysisQuote=null;purchaseQuote=null;
  $('analysis-preview').hidden=true;$('purchase-preview').hidden=true;
  $('documents-selection').textContent=`${docKeys.length} cases selected in the case index, including any hidden by filters. Close this panel to change the selection. Existing candidates from this run appear below.`;
  docMessage(current.demo ? 'The demo never sends AI requests or buys PDFs. Use a real saved run for a live pilot.' : 'Previewing analysis costs uses saved data only.');
  $('documents-dialog').showModal(); await loadDocuments();
}));
bind('document-model','change',()=>{analysisQuote=null;$('analysis-preview').hidden=true;docControls();});
bind('preview-analysis','click',()=>docTask(async()=>{
  analysisQuote=await api(`/api/runs/${docRun}/documents-analysis-quote`,{keys:docKeys,model:$('document-model').value});
  $('analysis-cases').replaceChildren(...analysisQuote.cases.map(c=>node('p',`${c.caseNumber} · ${c.district} · ${c.entryCount} entries · Clients: ${c.clients.join('; ')} · ${c.cached ? 'Saved analysis' : money(c.maximumCents)+' reserved'}`)));
  $('analysis-price').textContent=`Maximum new AI reservation: ${money(analysisQuote.maximumCents)}. No PACER requests.`;
  $('ai-cap').value=analysisQuote.maximumCents ? '' : '0.00';$('analysis-preview').hidden=false;
  docMessage('Review the exact cases and enter a fresh AI cap to proceed.');
}));
bind('ai-cap','input',docControls);bind('document-cap','input',docControls);
bind('confirm-analysis','click',()=>docTask(async()=>{
  await api(`/api/runs/${docRun}/documents-analyze`,{...analysisQuote,budgetCents:cents($('ai-cap').value)});
  analysisQuote=null;$('analysis-preview').hidden=true;docMessage('Analysis started. You can close this panel; saved results will appear here.');await refresh();
}));
bind('preview-documents','click',()=>docTask(async()=>{
  purchaseQuote=await api(`/api/runs/${docRun}/documents-purchase-quote`,{candidateIds:[...docSelected]});
  $('purchase-items').replaceChildren(...purchaseQuote.items.map(i=>node('p',`${i.courtId} · ${i.caseNumber} · Entry ${i.entryNumber} · ${i.kind} · ${i.cached ? 'Saved PDF' : 'Up to $3.00'}`)));
  $('purchase-price').textContent=`Maximum new PACER document spending: ${money(purchaseQuote.maximumCents)}.`;
  $('document-cap').value=purchaseQuote.maximumCents ? '' : '0.00';$('purchase-preview').hidden=false;
}));
bind('confirm-documents','click',()=>docTask(async()=>{
  const run=docRun,request={...purchaseQuote,budgetCents:cents($('document-cap').value)};
  const go=async()=>{await api(`/api/runs/${run}/documents-download`,request);docMessage('Document retrieval started. The cap applies only to the PDFs just listed.');await refresh();};
  if (purchaseQuote.maximumCents) { $('documents-dialog').close();await connectedAction('Connect PACER to download the selected PDFs under the confirmed document cap.',()=>action(go)); }
  else await go();
  purchaseQuote=null;$('purchase-preview').hidden=true;
}));
bind('document-refresh','click',()=>docTask(loadDocuments));
bind('document-bundle','click',()=>docTask(async()=>{
  const blob=await downloadBlob(`/api/runs/${docRun}/document-bundle`);
  const url=URL.createObjectURL(blob),a=node('a');a.href=url;a.download='roc-document-bundle.zip';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}));
window.rocDocumentsReset=()=>{
  docRun=null;docKeys=[];docState=analysisQuote=purchaseQuote=null;docSelected.clear();docStamp=docStatusStamp='';docAvailable=false;
  for (const id of ['document-candidates','analysis-cases','purchase-items','documents-selection','document-spending','analysis-price','purchase-price','documents-message']) $(id).replaceChildren();
  $('ai-cap').value='';$('document-cap').value='';$('analysis-preview').hidden=true;$('purchase-preview').hidden=true;
  docControls();
};
window.rocDocumentsUpdate=async available=>{
  docAvailable=available;docControls();
  if ($('documents-dialog').open && !docBusy) {
    const stamp=JSON.stringify([current?.state,current?.message]);
    if (current?.id === docRun && stamp !== docStatusStamp) {docStatusStamp=stamp;docMessage(current.message);}
    await loadDocuments();
  }
};
