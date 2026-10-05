"use strict";
let surfRun=null,surfKey=null,surfData=null,surfQuote=null,surfSelected=new Set(),surfLoading=false,surfRevision='',surfLimit=100;
let libraryFiles=[],fileSelected=new Set(),pdfUrl=null,pdfName='';
function saveBlob(blob,name) {const url=URL.createObjectURL(blob),a=node('a');a.href=url;a.download=name;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);}
async function openSurfer(key=null) {
  if(!current?.indexReady)throw new Error('Finish the case search before opening Docket Surfer.');
  surfRun=currentId;
  const cases=selected.size&&!key?current.cases.filter(c=>selected.has(c.key)):current.cases;
  $('surfer-case').replaceChildren();
  for(const c of cases){const o=node('option',`${c.caseNumber} · ${c.district} · ${c.caseTitle}`);o.value=c.key;$('surfer-case').append(o);}
  surfKey=key||cases[0]?.key;if(!surfKey)throw new Error('No cases in this view.');
  $('surfer-case').value=surfKey;$('surfer-filter').value='';
  if(!$('surfer-dialog').open)$('surfer-dialog').showModal();
  await loadSurfer();
}
async function loadSurfer() {
  if(!surfRun||!surfKey||surfLoading)return;
  const run=surfRun,key=surfKey;surfLoading=true;surfSelected.clear();surfQuote=null;surfLimit=100;
  $('surfer-error').hidden=true;$('surfer-status').textContent='Loading saved docket…';$('surfer-history').hidden=true;
  try{
    const result=await api(`/api/runs/${run}/docket?caseKey=${encodeURIComponent(key)}`);
    if(run!==surfRun||key!==surfKey)return;
    surfData=result;const c=result.case;
    $('surfer-heading').textContent=c.caseNumber;$('surfer-title').textContent=`${c.caseTitle} · ${c.district} · Filed ${c.dateFiled} · ${c.caseType} · ${c.status}`;
    $('surfer-status').textContent=result.saved?`Saved docket · retrieved ${new Date(result.savedUtc).toLocaleString()} · ${result.source.entries.length} entries. Reading this copy costs nothing.`:'No saved docket yet. Get the report to see its entries, parties and document links. You will review the cost first.';
    $('surfer-get').hidden=result.saved;$('surfer-reload').hidden=!result.saved;$('surfer-history').hidden=!result.saved;$('surfer-parties').hidden=!result.saved;
    $('surfer-get').disabled=!!activeRun||!current?.cases.find(c=>c.key===key)?.eligible;
    $('surfer-party-list').replaceChildren();
    for(const p of result.source?.parties||[]){const box=node('div',undefined,'party-appearance');box.append(node('strong',`${p.name} · ${p.role}${p.defendantNumber?' ('+p.defendantNumber+')':''}`),node('p','Counsel: '+(p.counsel||[]).join('; ')));for(const count of p.counts||[])box.append(node('p',`${count.section}: ${count.rawText||count.rawCharge}${count.disposition?' · '+count.disposition:''}`));$('surfer-party-list').append(box);}
    if(result.source?.warnings.length){$('surfer-error').textContent=result.source.warnings.join(' ');$('surfer-error').hidden=false;}
    renderSurfer();
  }catch(e){if(!e.staleAccount){$('surfer-error').textContent=e.message;$('surfer-error').hidden=false;}}
  finally{surfLoading=false;}
}
function renderSurfer(){
  const text=$('surfer-filter').value.toLocaleLowerCase(),all=(surfData?.source?.entries||[]).filter(e=>`${e.number} ${e.date} ${e.text}`.toLocaleLowerCase().includes(text));
  if($('surfer-sort').value==='newest')all.reverse();
  $('surfer-entries').replaceChildren();
  for(const e of all.slice(0,surfLimit)){
    const tr=node('tr'),select=node('td'),cb=node('input');cb.type='checkbox';cb.checked=surfSelected.has(e.id);cb.disabled=!e.url||e.missingPurchase||!!activeRun;cb.setAttribute('aria-label',`Select docket entry ${e.number||e.id}`);
    cb.addEventListener('change',()=>{cb.checked?surfSelected.add(e.id):surfSelected.delete(e.id);updateSurfSelection();});select.append(cb);
    tr.append(select,node('td',e.number||'—'),node('td',e.date),node('td',e.text));
    const actions=node('td');
    if(e.savedFileId){const b=node('button','Open saved PDF','secondary');b.onclick=()=>openSavedPDF(e.savedFileId,`Entry ${e.number}`).catch(error);actions.append(b);}
    else if(e.missingPurchase)actions.append(node('span','Saved purchase missing — restore required'));
    else if(e.url){const b=node('button','Get document','secondary');b.disabled=!!activeRun;b.onclick=()=>reviewSurf([e.id]).catch(surfError);actions.append(b);}
    else actions.append(node('span',e.linkStatus==='text-only or unsupported link'?'Text only / link unavailable':e.linkStatus,'help'));
    tr.append(actions);$('surfer-entries').append(tr);
  }
  if(all.length>surfLimit){const tr=node('tr'),td=node('td'),b=node('button',`Show more (${all.length-surfLimit} remaining)`,'secondary');td.colSpan=5;b.onclick=()=>{surfLimit+=100;renderSurfer();};td.append(b);tr.append(td);$('surfer-entries').append(tr);}
  if(!all.length){const tr=node('tr'),td=node('td','No docket entries match this view.');td.colSpan=5;tr.append(td);$('surfer-entries').append(tr);}
  updateSurfSelection();
}
function surfError(e){if(e.staleAccount)return;$('surfer-error').textContent=e.message;$('surfer-error').hidden=false;}
function updateSurfSelection(){ $('surfer-selected').textContent=`${surfSelected.size} documents selected`;$('surfer-buy').disabled=!surfSelected.size||!!activeRun;}
async function reviewSurf(ids){
  surfQuote=await api(`/api/runs/${surfRun}/surfer-quote`,{caseKey:surfKey,entryIds:ids});
  $('surfer-purchase-summary').textContent=`${surfQuote.items.length} selected · maximum new charges ${money(surfQuote.maximumCents)}. Saved files are reused free.`;
  $('surfer-purchase-items').replaceChildren();for(const i of surfQuote.items)$('surfer-purchase-items').append(node('p',`Entry ${i.number}${i.cached?' · Already saved':''} — ${i.text}`));
  $('surfer-cap').value=surfQuote.maximumCents?'':'0.00';$('surfer-purchase-message').textContent='';surfCap();$('surfer-purchase').showModal();
}
function surfCap(){let valid=false;try{valid=!!surfQuote&&cents($('surfer-cap').value)>=surfQuote.maximumCents;}catch{}$('surfer-confirm').disabled=!valid||!!activeRun;}
bind('open-surfer','click',()=>openSurfer());
bind('surfer-case','change',async()=>{surfKey=$('surfer-case').value;await loadSurfer();});
bind('surfer-filter','input',()=>{surfLimit=100;renderSurfer();});bind('surfer-sort','change',renderSurfer);
bind('surfer-reload','click',loadSurfer);
bind('surfer-get','click',()=>action(()=>previewDockets([surfKey])));
bind('surfer-buy','click',()=>reviewSurf([...surfSelected]));bind('surfer-cap','input',surfCap);
bind('surfer-confirm','click',async()=>{
  const run=surfRun,request={caseKey:surfQuote.caseKey,entryIds:surfQuote.entryIds,quoteId:surfQuote.quoteId,budgetCents:cents($('surfer-cap').value)};
  const proceed=()=>action(async()=>{await api(`/api/runs/${run}/surfer-download`,request);$('surfer-purchase').close();$('surfer-dialog').close();});
  if(surfQuote.maximumCents)await connectedAction('Connect PACER to purchase the documents you selected.',proceed);else await proceed();
});
async function loadFiles(){const data=await api('/api/files');libraryFiles=data.files;fileSelected.clear();$('files-storage').textContent=`${libraryFiles.length} files · ${(data.totalBytes/1024/1024).toFixed(1)} MB saved`;renderFiles();}
function renderFiles(){
  const text=$('files-filter').value.toLocaleLowerCase();$('files-list').replaceChildren();
  for(const f of libraryFiles.filter(f=>`${f.courtId} ${f.caseNumber} ${f.description}`.toLocaleLowerCase().includes(text))){
    const row=node('div',undefined,'file-item'),cb=node('input');cb.type='checkbox';cb.disabled=!f.available;cb.checked=fileSelected.has(f.id);cb.setAttribute('aria-label',`Select saved ${f.caseNumber} ${f.entryNumber||'docket'}`);cb.onchange=()=>{cb.checked?fileSelected.add(f.id):fileSelected.delete(f.id);$('files-download').disabled=!fileSelected.size;};
    const description=node('div',undefined,'file-description');description.append(node('strong',`${f.courtId.toUpperCase()} · ${f.caseNumber} · ${f.kind==='docket'?'Docket':'Entry '+f.entryNumber}`),node('p',f.description),node('small',`${new Date(f.savedUtc).toLocaleString()} · ${money(f.costCents)} · ${(f.size/1024).toFixed(0)} KB${f.available?'':' · File missing'}`));
    const b=node('button',f.kind==='pdf'?'Open PDF':'Download docket','secondary');b.disabled=!f.available;b.onclick=()=> (f.kind==='pdf'?openSavedPDF(f.id,`${f.caseNumber} · Entry ${f.entryNumber}`):downloadBlob('/api/files/'+f.id).then(blob=>saveBlob(blob,`${f.courtId}_${f.caseNumber.replace(':','-')}_docket.html`))).catch(error);row.append(cb,description,b);$('files-list').append(row);
  }
  if(!libraryFiles.length)$('files-list').append(node('p','No purchases yet. Dockets and documents you retrieve will appear here automatically.','help'));
  $('files-download').disabled=!fileSelected.size;
}
async function openSavedPDF(id,name){const generation=accountGeneration,blob=await downloadBlob('/api/files/'+id);checkGeneration(generation);if(pdfUrl)URL.revokeObjectURL(pdfUrl);pdfUrl=URL.createObjectURL(blob);pdfName=name.replace(/[^\w. -]/g,'_')+'.pdf';$('pdf-heading').textContent=name;$('pdf-frame').src=pdfUrl;$('pdf-dialog').showModal();}
bind('pdf-download','click',()=>{const a=node('a');a.href=pdfUrl;a.download=pdfName;a.click();});
$('pdf-dialog').addEventListener('close',()=>{$('pdf-frame').removeAttribute('src');if(pdfUrl)URL.revokeObjectURL(pdfUrl);pdfUrl=null;});
bind('my-files','click',async()=>{await loadFiles();$('files-dialog').showModal();});bind('files-refresh','click',loadFiles);bind('files-filter','input',renderFiles);
bind('files-download','click',async()=>{
  const generation=accountGeneration,response=await fetch('/api/files/bundle',{method:'POST',headers:{...requestHeaders(),'Content-Type':'application/json'},body:JSON.stringify({ids:[...fileSelected]})});
  if(!response.ok){const result=await response.json();checkGeneration(generation);throw new Error(result.error);}
  const blob=await response.blob();checkGeneration(generation);saveBlob(blob,'ROC-My-Files.zip');
});
window.rocSurferReset=()=>{surfRun=surfKey=surfData=surfQuote=null;surfSelected.clear();libraryFiles=[];fileSelected.clear();$('surfer-entries').replaceChildren();$('files-list').replaceChildren();};
