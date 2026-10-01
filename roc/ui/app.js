"use strict";
const $ = id => document.getElementById(id);
const token = location.hash.slice(1) || sessionStorage.getItem("roc-token") || "";
if (token) sessionStorage.setItem("roc-token", token);
history.replaceState(null, "", location.pathname);
let currentId = null, current = null, selected = new Set(), page = 0, pageRows = [], quote = null;
let busyAction = false, refreshing = false, tableStamp = "", sidebarStamp = "", activeRun = null;
let connection = {connected:false, connecting:false}, afterLogin = null, authSubmitted = false, authSending = false;
let shuttingDown = false, stopped = false, pollTimer = null;
let reportView = 'cases', partyPage = 0;
const pageSize = 50;
const money = cents => new Intl.NumberFormat("en-US", {style:"currency", currency:"USD"}).format(cents / 100);
const labels = {"missing-source":"Missing from source", "needs-review":"Needs review", "not-tested":"Not tested by sample"};
function node(tag, text, className) {const n = document.createElement(tag); if (text !== undefined) n.textContent = text; if (className) n.className = className; return n;}
function error(e) {if (stopped) return; $('notice').textContent = shuttingDown ? 'ROC is no longer reachable. Your stop request was sent; reopen the launcher to check saved run status.' : e.message || String(e); $('notice').hidden = false;}
function bind(id, event, fn) {$(id).addEventListener(event, e => {Promise.resolve().then(() => fn(e)).catch(error);});}
async function api(path, value) {
  const response = await fetch(path, {method:value === undefined ? "GET" : "POST", headers:{"X-ROC-Token":token, ...(value === undefined ? {} : {"Content-Type":"application/json"})}, ...(value === undefined ? {} : {body:JSON.stringify(value)})});
  const result = await response.json(); if (!response.ok) throw new Error(result.error || "Request failed."); return result;
}
function cents(value) {if (!/^\d+(\.\d{1,2})?$/.test(value)) throw new Error("Enter a dollar amount with at most two decimal places."); return Math.round(Number(value) * 100);}
async function action(fn) {
  if (busyAction) return; busyAction = true; $('notice').hidden = true;
  try {await fn(); await refresh();} finally {busyAction = false; setButtons();}
}
function showSearch() {currentId = null; current = null; selected.clear(); $('search-view').hidden = false; $('run-view').hidden = true; $('breadcrumb').textContent = "Workspace / New search"; sidebarStamp = ""; refresh().catch(error);}
async function openRun(id) {
  currentId = id; current = null; selected.clear(); page = 0; tableStamp = ""; sidebarStamp = "";
  $('filter').value = ""; $('type-filter').value = ""; $('review-filter').value = ""; $('sort').value = "newest";
  $('party-filter').value = ''; $('party-relation').value = ''; setReportView('cases');
  $('search-view').hidden = true; $('run-view').hidden = false; await refresh();
}
function setButtons() {
  const busy = Boolean(activeRun) || busyAction || connection.connecting || shuttingDown;
  $('connect-pacer').disabled = busy; $('disconnect-pacer').disabled = busy;
  $('connect-pacer').hidden = connection.connected; $('disconnect-pacer').hidden = !connection.connected;
  $('connection-status').textContent = stopped ? 'ROC stopped' : shuttingDown ? 'Stopping ROC…' : connection.connecting ? 'Connecting…' : connection.connected ? 'PACER connected' : 'Sign-in required';
  $('stop-roc').disabled = shuttingDown;
  for (const id of ['auth-username','auth-password','auth-otp','auth-client','auth-redact','auth-submit']) $(id).disabled = connection.connecting || authSending || authSubmitted || shuttingDown;
  $('search-submit').disabled = busy; $('demo').disabled = busy;
  $('preview').disabled = busy || !current || !current.indexReady || selected.size === 0;
  for (const id of ['resume','signin','reconcile','edit-cap','rebuild','select-visible']) $(id).disabled = busy;
  $('confirm-retrieve').disabled = busy || !quote?.fitsBudget;
  if (current) {
    for (const view of ['clients','defendants','plaintiffs']) $('view-'+view).disabled = !current.partyReports;
    $('pause').hidden = !current.busy; $('pause').disabled = busyAction || current.pauseRequested;
    $('pause').textContent = current.pauseRequested ? "Pause requested…" : "Pause after current request";
    $('resume').hidden = !['stopped','interrupted'].includes(current.state);
    $('signin').hidden = current.demo || !['stopped','interrupted'].includes(current.state);
    $('reconcile').hidden = !current.pendingCount && !current.stoppedReason;
    $('edit-cap').hidden = current.demo; $('rebuild').disabled = busy || !current.indexReady;
    $('exports').querySelectorAll('button').forEach(b => b.disabled = current.busy);
  }
}
async function refresh() {
  if (refreshing || stopped) return; refreshing = true;
  let continuation = null;
  try {
    const listing = await api('/api/runs'); activeRun = listing.active; connection = listing.connection;
    shuttingDown = shuttingDown || listing.stopping;
    if (listing.closed) {
      stopped = true; clearInterval(pollTimer); $('notice').textContent = 'ROC has stopped. You can close this tab. Reopen Start ROC to return to your saved runs.'; $('notice').hidden = false;
    }
    $('redaction-text').textContent = connection.redactionNotice || '';
    if (authSubmitted && !connection.connecting) {
      authSubmitted = false;
      if (connection.connected) {
        continuation = afterLogin; afterLogin = null; clearCredentials(); $('auth-dialog').close();
      } else {
        $('auth-message').textContent = connection.message; $('auth-message').hidden = false;
      }
    }
    const stamp = JSON.stringify([currentId, listing.jobs.map(j => [j.id,j.state,j.caseCount,j.spentCents])]);
    if (stamp !== sidebarStamp) {
      sidebarStamp = stamp; $('runs').replaceChildren();
      if (!listing.jobs.length) $('runs').append(node('p', 'Your searches will be saved here.', 'help'));
      for (const job of listing.jobs) {
        const b = node('button', `${job.lawyer.firstName} ${job.lawyer.lastName}`, `run-link${job.id === currentId ? ' active' : ''}`);
        b.append(node('small', `${job.demo ? 'Demo · ' : ''}${job.caseCount} cases · ${job.state === 'running' ? 'In progress' : job.createdUtc.slice(0,10)}`));
        b.addEventListener('click', () => openRun(job.id).catch(error)); $('runs').append(b);
      }
    }
    const requested = currentId;
    if (requested) {const data = await api(`/api/runs/${requested}`); if (requested === currentId) {current = data; renderRun();}}
    setButtons();
  } finally {refreshing = false;}
  if (continuation && !shuttingDown) await continuation();
}
function renderRun() {
  const r = current;
  $('breadcrumb').textContent = `Workspace / ${r.lawyer.firstName} ${r.lawyer.lastName}`;
  $('run-kind').textContent = r.demo ? 'FREE DEMO · FICTIONAL RECORD' : 'CASE RESEARCH';
  $('run-name').textContent = `${r.lawyer.firstName} ${r.lawyer.lastName}`;
  const dates = r.search.dateFiledFrom || r.search.dateFiledTo ? `${r.search.dateFiledFrom || 'Any date'} → ${r.search.dateFiledTo || 'Present'}` : 'All filing dates';
  $('run-scope').textContent = `${r.search.courtId?.length ? r.search.courtId.join(', ').toUpperCase() : 'All federal courts'} · ${dates} · Saved ${r.createdUtc.slice(0,10)}`;
  $('stat-cases').textContent = r.caseCount.toLocaleString(); $('stat-dockets').textContent = r.enrichedCount.toLocaleString();
  $('stat-spent').textContent = money(r.spentCents); $('stat-cap').textContent = money(r.budgetCents);
  const states = {running:'IN PROGRESS',ready:'READY',stopped:'STOPPED',interrupted:'INTERRUPTED',new:'NEW'};
  $('run-status').textContent = states[r.state] || r.state;
  $('progress-message').textContent = r.message;
  $('pending-message').textContent = r.pauseRequested ? 'Waiting for the current request to finish. No further purchases will begin after the pause checkpoint.' : r.pendingCount ? `${r.pendingCount} unresolved receipt(s): ${money(r.pendingCents)} reserved. Further purchases are blocked.` : r.stoppedReason || '';
  const newStamp = JSON.stringify([r.cases,r.partyReports,r.busy]); if (tableStamp !== newStamp) {tableStamp = newStamp; renderTable(); renderParties();}
  const coverage = r.partyReports?.coverage;
  $('report-coverage').textContent = coverage ? `${coverage.parsedDockets} of ${coverage.indexedCases} indexed cases have parsed dockets · ${coverage.indexOnlyCases} index only · Counsel matched in ${coverage.casesWithMatchedClients} cases${coverage.partyTablesNeedingReview ? ` · ${coverage.partyTablesNeedingReview} party tables need review` : ''}. Party summaries cover saved dockets only.` : 'An update is ready. Use Stop ROC, then reopen Start ROC to load party reports. Saved work is retained; reconnect PACER when you next need access.';
  $('exports').replaceChildren();
  const formats = {'case-index.xlsx':'Excel ↓','case-index.csv':'CSV ↓','party-reports.zip':'Party CSVs ↓','case-index.html':'HTML ↓','evidence.json':'Evidence ↓','review.json':'Findings ↓'};
  for (const [file, title] of Object.entries(formats)) if (r.downloads.includes(file)) {
    const b = node('button', title, 'secondary'); b.addEventListener('click', () => download(file).catch(error)); $('exports').append(b);
  }
  $('receipt-list').replaceChildren();
  if (!r.transactions.length) $('receipt-list').append(node('p', 'No PACER transactions in this run.', 'help'));
  r.transactions.forEach(t => $('receipt-list').append(node('div', `${t.startedUtc.slice(0,19).replace('T',' ')} UTC · ${t.kind === 'docket' ? 'Court docket report' : 'PCL API page'} · ${t.state === 'complete' ? money(t.chargedCents) + ' confirmed' : money(t.reservedCents) + ' reserved; receipt unresolved'}`, 'receipt')));
}
function filteredRows() {
  const text = $('filter').value.trim().toLocaleLowerCase(), kind = $('type-filter').value, review = $('review-filter').value;
  const names = new Map();
  for (const p of current?.partyReports?.parties || []) names.set(p.caseKey, `${names.get(p.caseKey) || ''} ${p.name}`);
  let rows = (current?.cases || []).filter(c => (!kind || c.caseType === kind) && (!review || (review === 'enriched' ? c.enriched : c.issues.some(i => i.category === review))) && (!text || [c.caseNumber,c.caseTitle,c.district,c.role || c.team,c.nature,names.get(c.key)].join(' ').toLocaleLowerCase().includes(text)));
  const sort = $('sort').value;
  rows.sort((a,b) => sort === 'court' ? a.district.localeCompare(b.district) || a.caseNumber.localeCompare(b.caseNumber) : (sort === 'oldest' ? 1 : -1) * a.dateFiled.localeCompare(b.dateFiled) || a.key.localeCompare(b.key));
  return rows;
}
function selectionLabel(rows) {
  const shown = new Set(rows.map(r => r.key)), hidden = [...selected].filter(k => !shown.has(k)).length;
  $('selection-count').textContent = selected.size; $('selection-hidden').textContent = hidden ? `(${hidden} outside this filter)` : ''; setButtons();
}
function renderTable() {
  const rows = filteredRows(); page = Math.min(page, Math.max(0, Math.ceil(rows.length / pageSize)-1)); pageRows = rows.slice(page*pageSize, (page+1)*pageSize);
  $('case-rows').replaceChildren(); $('empty').hidden = Boolean(rows.length);
  $('empty').textContent = current?.indexReady ? 'No cases match these filters.' : 'The case index will appear here after the API search finishes.';
  for (const c of pageRows) {
    const tr = node('tr'), cb = node('input'); cb.type = 'checkbox'; cb.checked = selected.has(c.key); cb.disabled = !c.eligible || Boolean(activeRun);
    cb.setAttribute('aria-label', `Select ${c.caseNumber}`); cb.title = c.ineligibleReason || 'Select docket';
    cb.addEventListener('change', () => {cb.checked ? selected.add(c.key) : selected.delete(c.key); selectionLabel(rows);});
    const checkCell = node('td'); checkCell.append(cb); tr.append(checkCell);
    const titleCell = node('td'), b = node('button',c.caseNumber); b.addEventListener('click', () => showCase(c));
    titleCell.append(b, node('span',c.caseTitle,'case-title')); titleCell.title = c.caseTitle; tr.append(titleCell);
    for (const value of [c.caseType,c.district,c.dateFiled,c.role || c.team || 'Unresolved',c.nature,c.status]) {const td = node('td',value); td.title = value; tr.append(td);}
    const findings = node('td'), review = c.issues.some(i => i.category === 'needs-review'), missing = c.issues.some(i => i.category === 'missing-source');
    findings.append(node('span',review ? 'Review' : missing ? 'Source gap' : c.enriched ? 'Enriched' : 'Index only',`pill${review || missing ? ' warn' : c.enriched ? ' good' : ''}`)); tr.append(findings); $('case-rows').append(tr);
  }
  if (reportView === 'cases') $('result-count').textContent = `${rows.length.toLocaleString()} shown`;
  $('page-summary').textContent = rows.length ? `${page*pageSize+1}–${Math.min((page+1)*pageSize,rows.length)} of ${rows.length.toLocaleString()} cases` : '0 cases';
  $('previous').disabled = page === 0; $('next').disabled = (page+1)*pageSize >= rows.length; selectionLabel(rows);
}
function showCase(c) {
  $('detail-number').textContent = c.caseNumber; $('detail-title').textContent = c.caseTitle; $('detail-nature').textContent = c.nature;
  $('detail-fields').replaceChildren();
  for (const [key,value] of [['District',c.district],['Filed',c.dateFiled],['Role',c.role || c.team || 'Unresolved — select this docket to investigate'],['Status',c.status],['Represented parties',c.representedParties.join('; ') || 'Not established from the case index']]) $('detail-fields').append(node('p',`${key}: ${value}`));
  $('detail-parties').replaceChildren();
  for (const p of (current.partyReports?.parties || []).filter(p => p.caseKey === c.key)) {
    const block = node('div',undefined,'party-appearance');
    block.append(node('strong',p.name),node('p',`${p.partyRole} · ${p.relationship}${p.defendantNumbers.length ? ' · Defendant '+p.defendantNumbers.join(', ') : ''}`));
    if (p.matchedCounsel.length) block.append(node('p','Matched counsel: '+p.matchedCounsel.join('; '),'help'));
    $('detail-parties').append(block);
  }
  $('detail-issues').replaceChildren();
  if (!c.enriched) $('detail-issues').append(node('p','This is index information. Counsel roles and client-specific criminal counts require the selected docket.', 'help'));
  if (!c.issues.length) $('detail-issues').append(node('p','No flagged findings in the available data.', 'help'));
  for (const i of c.issues) {const block = node('div',undefined,'issue'); block.append(node('strong',labels[i.category] || i.category),node('span',i.message)); $('detail-issues').append(block);}
  $('case-dialog').showModal();
}
function setReportView(view) {
  reportView = view; partyPage = 0;
  $('cases-panel').hidden = view !== 'cases'; $('parties-panel').hidden = view === 'cases';
  for (const name of ['cases','clients','defendants','plaintiffs']) {
    $('view-'+name).classList.toggle('active',view === name); $('view-'+name).setAttribute('aria-pressed',String(view === name));
  }
  renderTable(); renderParties();
}
function partyMatches(row) {
  const relation = $('party-relation').value;
  if (relation === 'civilPlaintiffCaseCount') return row.role === 'Civil Plaintiff' && row.relationships.includes('Opposing party');
  const target = {clientCaseCount:'Client',opposingCaseCount:'Opposing party',sameSideCaseCount:'Other party on same side',unresolvedCaseCount:'Unresolved'}[relation];
  return !target || row.relationships.includes(target);
}
function renderParties() {
  if (reportView === 'cases' || !current?.partyReports) return;
  const text = $('party-filter').value.trim().toLocaleLowerCase(), relation = $('party-relation').value;
  const rows = current.partyReports[reportView].summary.filter(s => (!text || s.sourceNames.join(' ').toLocaleLowerCase().includes(text)) && (!relation || s[relation] > 0));
  rows.sort((a,b) => (b[relation || 'caseCount']-a[relation || 'caseCount']) || a.name.localeCompare(b.name));
  partyPage = Math.min(partyPage,Math.max(0,Math.ceil(rows.length/pageSize)-1));
  $('party-rows').replaceChildren();
  for (const s of rows.slice(partyPage*pageSize,(partyPage+1)*pageSize)) {
    const tr = node('tr'), cell = node('td'), b = node('button',s.name);
    b.addEventListener('click',() => showParty(s)); cell.append(b); cell.title = s.sourceNames.join('; '); tr.append(cell);
    for (const value of [s[relation || 'caseCount'],s.clientCaseCount,s.opposingCaseCount,s.civilPlaintiffCaseCount,s.sameSideCaseCount,s.unresolvedCaseCount,s.roles.join('; ')]) {const td = node('td',String(value)); td.title = String(value); tr.append(td);}
    $('party-rows').append(tr);
  }
  $('party-empty').hidden = Boolean(rows.length);
  $('party-empty').textContent = current.partyReports.coverage.parsedDockets ? 'No names match this view. Check the docket coverage and counsel matches.' : 'Retrieve selected dockets from the Case index to build these reports.';
  $('result-count').textContent = `${rows.length.toLocaleString()} names`;
  $('party-page-summary').textContent = rows.length ? `${partyPage*pageSize+1}–${Math.min((partyPage+1)*pageSize,rows.length)} of ${rows.length} names${relation ? ' · Filtered distinct-case count; relationship columns show full totals' : ''}` : '0 names';
  $('party-previous').disabled = partyPage === 0; $('party-next').disabled = (partyPage+1)*pageSize >= rows.length;
}
function showParty(summary) {
  const appearances = current.partyReports[reportView].cases.filter(p => p.nameKey === summary.nameKey && partyMatches(p));
  const byCase = new Map(); appearances.forEach(p => {if (!byCase.has(p.caseKey)) byCase.set(p.caseKey,[]); byCase.get(p.caseKey).push(p);});
  $('party-name').textContent = summary.name; $('party-source-names').textContent = 'Source names: '+summary.sourceNames.join('; ');
  $('party-case-count').textContent = `${byCase.size} distinct cases in this view. Counts reflect saved docket coverage.`;
  $('party-case-list').replaceChildren();
  const caseMap = new Map(current.cases.map(c => [c.key,c]));
  const rows = [...byCase.entries()].sort((a,b) => b[1][0].dateFiled.localeCompare(a[1][0].dateFiled) || a[0].localeCompare(b[0]));
  for (const [key, parties] of rows) {
    const c = caseMap.get(key), block = node('div',undefined,'party-appearance'), b = node('button',c.caseNumber,'text-button');
    b.addEventListener('click',() => showCase(c)); block.append(b,node('strong',c.caseTitle),node('p',`${c.district} · ${c.dateFiled} · ${c.status} · ${c.role || 'Unresolved'}`));
    block.append(node('p',parties.map(p => `${p.partyRole}: ${p.relationship}`).join('; '),'help'));
    $('party-case-list').append(block);
  }
  $('party-dialog').showModal();
}
async function download(file) {
  const response = await fetch(`/api/runs/${currentId}/download/${file}`,{headers:{'X-ROC-Token':token}});
  if (!response.ok) throw new Error((await response.json()).error);
  const url = URL.createObjectURL(await response.blob()), a = node('a'); a.href = url; a.download = file; document.body.append(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(url),1000);
}
bind('home','click',e => {e.preventDefault(); showSearch();}); bind('new-search','click',showSearch); bind('new-from-run','click',showSearch);
bind('search-form','submit',async e => {
  e.preventDefault(); const data = new FormData(e.target);
  const values = {firstName:data.get('firstName'),lastName:data.get('lastName'),aliases:data.get('aliases').split('\n').map(s=>s.trim()).filter(Boolean),courts:data.getAll('courts'),dateFiledFrom:data.get('dateFiledFrom'),dateFiledTo:data.get('dateFiledTo'),budgetCents:cents(data.get('budget'))};
  await connectedAction('Connect to run the case search you just requested.', () => action(async () => {const result = await api('/api/runs', values); await openRun(result.id);}));
});
bind('demo','click',() => action(async () => {const result = await api('/api/demo',{}); await openRun(result.id);}));
for (const view of ['cases','clients','defendants','plaintiffs']) bind('view-'+view,'click',() => setReportView(view));
bind('party-filter','input',() => {partyPage = 0; renderParties();}); bind('party-relation','change',() => {partyPage = 0; renderParties();});
bind('party-previous','click',() => {partyPage--; renderParties();}); bind('party-next','click',() => {partyPage++; renderParties();});
for (const id of ['filter','type-filter','review-filter','sort']) bind(id,id === 'filter' ? 'input' : 'change',() => {page = 0; renderTable();});
bind('previous','click',() => {page--; renderTable();}); bind('next','click',() => {page++; renderTable();});
bind('select-visible','click',() => {pageRows.filter(c=>c.eligible).forEach(c=>selected.add(c.key)); renderTable();});
bind('clear-selection','click',() => {selected.clear(); renderTable();});
bind('preview','click',() => action(async () => {
  quote = await api(`/api/runs/${currentId}/quote`,{keys:[...selected]});
  $('quote-text').textContent = `${quote.selectedCount} selected · ${quote.newReports} new reports · ${quote.cachedReports} saved or demo reports`;
  $('quote-cost').textContent = money(quote.maximumAdditionalCents);
  $('quote-budget').textContent = `${money(quote.spentCents)} already spent of the ${money(quote.budgetCents)} cap. ${quote.fitsBudget ? 'This selection fits within the cap.' : 'This exceeds the cap. Close this preview and select fewer cases or change the cap.'}`;
  $('quote-cases').replaceChildren(...quote.cases.map(c=>node('p',`${c.caseNumber} · ${c.district}`))); $('quote-dialog').showModal();
}));
bind('confirm-retrieve','click',async () => {
  const id = currentId, keys = [...quote.keys], needsConnection = quote.newReports > 0; $('quote-dialog').close();
  const proceed = () => action(async () => {await api(`/api/runs/${id}/retrieve`,{keys}); selected.clear(); tableStamp = '';});
  if (needsConnection) await connectedAction('Connect to retrieve the dockets you just confirmed.', proceed); else await proceed();
});
for (const [id, command] of [['pause','pause'],['reconcile','reconcile'],['rebuild','export']]) bind(id,'click',() => action(() => api(`/api/runs/${currentId}/${command}`,{})));
bind('resume','click',async () => {const id = currentId; const proceed = () => action(() => api(`/api/runs/${id}/resume`,{})); if (current.demo) await proceed(); else await connectedAction('Connect to resume this saved operation. Existing receipts and selections are retained.', proceed);});
bind('signin','click',() => openAuth('Reconnect PACER. After connecting, use Resume saved operation to continue.', null));
bind('edit-cap','click',() => {$('cap-input').value = (current.budgetCents/100).toFixed(2); $('cap-dialog').showModal();});
bind('close-cap','click',() => $('cap-dialog').close());
bind('cap-form','submit',async e => {e.preventDefault(); await action(async () => {await api(`/api/runs/${currentId}/budget`,{budgetCents:cents($('cap-input').value)}); $('cap-dialog').close();});});
async function start() {
  const courts = await api('/api/courts'); courts.forEach(c=>{const option=node('option',c.name);option.value=c.id;$('court-select').append(option);});
  await refresh(); pollTimer = setInterval(() => refresh().catch(error),1800);
}

function clearCredentials() {$('auth-password').value = ''; $('auth-otp').value = '';}
function openAuth(context, next) {
  afterLogin = next; $('auth-context').textContent = context; $('auth-message').hidden = true;
  $('auth-submit').textContent = next ? 'Connect and continue' : 'Connect PACER';
  clearCredentials(); $('auth-password').type = 'text'; $('toggle-password').textContent = 'Hide'; $('toggle-password').setAttribute('aria-label','Hide password');
  $('auth-redact').checked = false; $('auth-dialog').showModal(); setButtons();
}
async function connectedAction(context, next) {if (connection.connected) await next(); else openAuth(context, next);}
bind('connect-pacer','click',() => openAuth('Connect your PACER account to this ROC session. Signing in alone does not start a search.', null));
bind('disconnect-pacer','click',() => action(() => api('/api/connection/disconnect',{})));
bind('close-auth','click',() => $('auth-dialog').close());
$('auth-dialog').addEventListener('close',() => {clearCredentials(); afterLogin = null;});
bind('toggle-password','click',() => {const hidden = $('auth-password').type === 'password'; $('auth-password').type = hidden ? 'text' : 'password'; $('toggle-password').textContent = hidden ? 'Hide' : 'Show'; $('toggle-password').setAttribute('aria-label', hidden ? 'Hide password' : 'Show password');});
bind('auth-form','submit',async e => {
  e.preventDefault(); if (authSending || authSubmitted || connection.connecting) return;
  authSending = true; $('auth-message').textContent = 'Connecting to the official PACER authentication API…'; $('auth-message').hidden = false; setButtons();
  const fields = {username:$('auth-username').value,password:$('auth-password').value,otp:$('auth-otp').value,clientCode:$('auth-client').value,redact:$('auth-redact').checked};
  try {await api('/api/connection/sign-in',fields); authSubmitted = true; await refresh();}
  catch(e) {$('auth-message').textContent = e.message;}
  finally {fields.password = ''; fields.otp = ''; authSending = false; setButtons();}
});
bind('stop-roc','click',() => $('stop-dialog').showModal());
bind('cancel-stop','click',() => $('stop-dialog').close());
bind('confirm-stop','click',async () => {
  await api('/api/stop',{}); shuttingDown = true; afterLogin = null; clearCredentials(); $('stop-dialog').close();
  $('notice').textContent = 'Stopping ROC after the current request settles. No new requests will start.'; $('notice').hidden = false; setButtons(); await refresh();
});
start().catch(error);
