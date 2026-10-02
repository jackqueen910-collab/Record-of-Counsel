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
let capStoppedRun = null;
let selectedNames = new Map(), rulesAvailable = false, ruleState = null, editingRule = null, pendingRule = null, rulesBusy = false;
let docketBudgetsAvailable = false, clientReportsAvailable = false;
let caseRows = [], facetChoices = {};
let accountView = 'signed-out', accountGeneration = 0, authTarget = '';
const alphabet = new Intl.Collator('en', {sensitivity:'base', numeric:true});
const facetDefinitions = [
  ['court','Court'],['type','Case type'],['status','Case status'],['role','Role'],['year','Filing year'],
  ['docket','Docket coverage'],['review','Source findings'],['party','Party name'],['title','Case title'],['nature','Nature of Case']
];
const unavailable = value => !value || /^(charges )?not supplied by PCL$|^N\/A$|^unresolved\b/i.test(value);
const pageSize = 50;
const money = cents => new Intl.NumberFormat("en-US", {style:"currency", currency:"USD"}).format(cents / 100);
const labels = {"missing-source":"Missing from source", "needs-review":"Needs review", "not-tested":"Not tested by sample"};
function node(tag, text, className) {const n = document.createElement(tag); if (text !== undefined) n.textContent = text; if (className) n.className = className; return n;}
function error(e) {if (stopped || e.staleAccount) return; $('notice').textContent = shuttingDown ? 'ROC is no longer reachable. Your stop request was sent; reopen the launcher to check saved run status.' : e.message || String(e); $('notice').hidden = false;}
function bind(id, event, fn) {$(id).addEventListener(event, e => {Promise.resolve().then(() => fn(e)).catch(error);});}
function checkGeneration(generation) {
  if (generation !== accountGeneration) {const e = new Error('Account changed.'); e.staleAccount = true; throw e;}
}
function requestHeaders() {return {'X-ROC-Token':token, 'X-ROC-View':accountView};}
function adoptAccountView(view, preserveAuth=false) {
  if (view === undefined || view === accountView) return;
  accountView = view; accountGeneration++;
  currentId = null; current = null; activeRun = null; capStoppedRun = null; quote = null;
  selected.clear(); selectedNames.clear(); caseRows = []; pageRows = []; facetChoices = {};
  ruleState = editingRule = pendingRule = null; rulesAvailable = false;
  tableStamp = sidebarStamp = ''; page = partyPage = 0; reportView = 'cases';
  for (const dialog of document.querySelectorAll('dialog[open]')) if (!preserveAuth || dialog.id !== 'auth-dialog') dialog.close();
  if (!preserveAuth) {afterLogin = null; authSubmitted = false; authTarget = ''; clearCredentials(); $('auth-username').value = ''; $('auth-client').value = '';}
  if (!preserveAuth || connection.signedIn && connection.username !== authTarget) $('search-form').reset();
  for (const id of ['runs','case-rows','party-rows','exports','receipt-list','facet-groups','active-filters',
    'party-name','party-source-names','party-case-count','party-case-list','detail-number','detail-title','detail-fields','detail-nature','detail-parties','detail-issues',
    'rules-list','rule-impact','rule-comparisons','rules-message','quote-cases','quote-text','quote-cost','quote-budget','docket-cap-message',
    'run-name','run-scope','run-kind','run-status','stat-cases','stat-dockets','stat-spent','stat-cap','result-count','page-summary','party-page-summary',
    'selection-count','selection-hidden','name-selection-count','progress-message','pending-message','report-coverage','clients-docket-scope','other-cap-stop-text']) $(id).replaceChildren();
  for (const id of ['filter','party-filter','rule-label','rule-names','cap-input','docket-cap']) $(id).value = '';
  for (const id of ['notice','other-cap-stop','run-view','rule-preview']) $(id).hidden = true;
  $('search-view').hidden = false; $('breadcrumb').textContent = 'Workspace / New search'; document.title = 'Record of Counsel';
  if (window.rocDocumentsReset) window.rocDocumentsReset();
}
async function api(path, value) {
  const generation = accountGeneration;
  const response = await fetch(path, {method:value === undefined ? "GET" : "POST", headers:{...requestHeaders(), ...(value === undefined ? {} : {"Content-Type":"application/json"})}, ...(value === undefined ? {} : {body:JSON.stringify(value)})});
  const result = await response.json(); checkGeneration(generation);
  if (!response.ok) throw new Error(result.error || "Request failed.");
  if (value !== undefined && result.viewId) adoptAccountView(result.viewId,path === '/api/connection/sign-in');
  return result;
}
async function downloadBlob(path) {
  const generation = accountGeneration, response = await fetch(path,{headers:requestHeaders()});
  if (!response.ok) {const result = await response.json(); checkGeneration(generation); throw new Error(result.error);}
  const blob = await response.blob(); checkGeneration(generation); return blob;
}
function cents(value) {if (!/^\d+(\.\d{1,2})?$/.test(value)) throw new Error("Enter a dollar amount with at most two decimal places."); const result = Math.round(Number(value) * 100); if (!Number.isSafeInteger(result)) throw new Error('Enter a smaller dollar amount.'); return result;}
async function action(fn) {
  if (busyAction) return; busyAction = true; $('notice').hidden = true;
  try {await fn(); await refresh();} finally {busyAction = false; setButtons();}
}
function showSearch() {currentId = null; current = null; selected.clear(); $('search-view').hidden = false; $('run-view').hidden = true; $('breadcrumb').textContent = "Workspace / New search"; sidebarStamp = ""; refresh().catch(error);}
async function openRun(id) {
  currentId = id; current = null; selected.clear(); page = 0; tableStamp = ""; sidebarStamp = "";
  $('filter').value = ''; $('sort').value = 'newest'; facetChoices = {}; caseRows = []; $('result-filters').open = false; renderFacetGroups();
  $('party-filter').value = ''; $('party-sort').value = 'count-desc'; setReportView('cases');
  $('search-view').hidden = true; $('run-view').hidden = false; await refresh();
}
function setButtons() {
  const busy = Boolean(activeRun) || busyAction || connection.connecting || shuttingDown;
  $('name-rules').disabled = busy || !rulesAvailable;
  $('name-rules').title = rulesAvailable ? 'Saved name corrections and organization groups' : 'Restart ROC to load name rules';
  $('group-names').disabled = busy || !rulesAvailable || selectedNames.size === 0;
  $('refresh-reports').disabled = busy || !clientReportsAvailable;
  for (const id of ['rule-label','rule-kind','rule-names','preview-rule','new-rule','delete-rule']) $(id).disabled = busy || rulesBusy;
  $('undo-rule').disabled = busy || rulesBusy || !ruleState?.canUndo;
  $('apply-rule').disabled = busy || rulesBusy || !pendingRule;
  $('connect-pacer').disabled = busy; $('disconnect-pacer').disabled = busy; $('switch-account').disabled = busy;
  $('connect-pacer').hidden = connection.connected; $('disconnect-pacer').hidden = !(connection.signedIn || connection.connected);
  $('switch-account').hidden = !connection.signedIn;
  $('connect-pacer').textContent = connection.signedIn ? 'Reconnect PACER' : 'Connect PACER';
  $('disconnect-pacer').textContent = connection.accountMode ? 'Sign out' : 'Disconnect';
  $('connection-status').textContent = stopped ? 'ROC stopped' : shuttingDown ? 'Stopping ROC…' : connection.connecting ? 'Connecting…' : connection.signedIn ? `${connection.username} · ${connection.connected ? 'PACER connected' : 'Saved searches available'}` : connection.connected ? 'PACER connected' : 'Sign-in required';
  $('stop-roc').disabled = shuttingDown;
  for (const id of ['auth-username','auth-password','auth-otp','auth-client','auth-redact','auth-submit']) $(id).disabled = connection.connecting || authSending || authSubmitted || shuttingDown;
  $('search-submit').disabled = busy; $('demo').disabled = busy;
  $('preview').disabled = busy || !docketBudgetsAvailable || !current || !current.indexReady || selected.size === 0;
  $('preview').title = docketBudgetsAvailable ? 'Review only selected cases and set a docket cap' : 'Stop ROC and reopen Start ROC to load separate docket caps';
  $('choose-dockets').disabled = busy || !docketBudgetsAvailable;
  $('choose-dockets').textContent = docketBudgetsAvailable ? 'Choose cases for docket reports' : 'Restart ROC to enable docket caps';
  $('clients-dockets').disabled = busy || !docketBudgetsAvailable || !current?.indexReady || missingDockets().length === 0;
  $('clients-dockets').textContent = docketBudgetsAvailable ? 'Run docket reports →' : 'Restart ROC to enable docket caps';
  document.querySelectorAll('button.docket-field').forEach(b => b.disabled = busy || !docketBudgetsAvailable);
  for (const id of ['resume','signin','reconcile','edit-cap','rebuild','select-visible']) $(id).disabled = busy;
  $('confirm-retrieve').disabled = busy || !docketCapValid();
  $('docket-cap').disabled = busy || !quote?.newReports;
  if (current) {
    $('view-clients').disabled = !current.partyReports;
    $('pause').hidden = !current.busy; $('pause').disabled = busyAction || current.pauseRequested;
    $('pause').textContent = current.pauseRequested ? "Pause requested…" : "Pause after current request";
    $('resume').hidden = !['stopped','interrupted'].includes(current.state);
    $('signin').hidden = current.demo || !['stopped','interrupted'].includes(current.state);
    $('reconcile').hidden = !current.pendingCount && !current.stoppedReason;
    $('edit-cap').hidden = current.demo; $('rebuild').disabled = busy || !current.indexReady;
    $('exports').querySelectorAll('button').forEach(b => b.disabled = current.busy || current.exportsNeedRefresh || !clientReportsAvailable);
  }
}
async function refresh() {
  if (refreshing || stopped || authSending) return; refreshing = true;
  let continuation = null;
  try {
    const listing = await api('/api/runs');
    if (authSending) return;
    adoptAccountView(listing.connection.viewId,authSubmitted && listing.connection.username === authTarget);
    activeRun = listing.active; connection = listing.connection;
    capStoppedRun = listing.jobs.find(j=>j.docketCapStopped && j.id !== currentId) || null;
    $('other-cap-stop').hidden = !capStoppedRun;
    if (capStoppedRun) $('other-cap-stop-text').textContent = `Docket retrieval for ${capStoppedRun.lawyer.firstName} ${capStoppedRun.lawyer.lastName} stopped at its spending limit. Purchased reports and results are saved.`;
    rulesAvailable = Number.isInteger(listing.nameRulesRevision);
    docketBudgetsAvailable = listing.docketBudgetVersion === 2;
    clientReportsAvailable = listing.clientReportVersion === 1;
    if ($('rules-dialog').open && ruleState && rulesAvailable && listing.nameRulesRevision !== ruleState.revision && !rulesBusy) {
      invalidateRulePreview(); $('rules-message').textContent = 'Name rules changed in another tab. Close and reopen Name rules to load the latest version.'; $('rules-message').hidden = false;
    }
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
    const stamp = JSON.stringify([accountView,connection.signedIn,currentId, listing.jobs.map(j => [j.id,j.state,j.caseCount,j.spentCents])]);
    if (stamp !== sidebarStamp) {
      sidebarStamp = stamp; $('runs').replaceChildren();
      if (connection.accountMode && !connection.signedIn) $('runs').append(node('p', 'Saved searches will appear here after you sign in with PACER.', 'help'));
      else if (!listing.jobs.length) $('runs').append(node('p', 'Your searches will be saved here.', 'help'));
      for (const job of listing.jobs) {
        const b = node('button', `${job.lawyer.firstName} ${job.lawyer.lastName}`, `run-link${job.id === currentId ? ' active' : ''}`);
        b.append(node('small', `${job.demo ? 'Demo · ' : ''}${job.caseCount} cases · ${job.state === 'running' ? 'In progress' : job.createdUtc.slice(0,10)}`));
        b.addEventListener('click', () => openRun(job.id).catch(error)); $('runs').append(b);
      }
    }
    const requested = currentId;
    if (requested) {const data = await api(`/api/runs/${requested}`); if (requested === currentId) {current = data; renderRun();}}
    setButtons();
    if (window.rocDocumentsUpdate) await window.rocDocumentsUpdate(listing.documentGrabberVersion === 3);
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
  $('docket-cap-stop').hidden = !r.docketCapStopped;
  document.title = r.docketCapStopped ? 'ROC — Docket spending limit reached' : 'Record of Counsel';
  const missing = missingDockets().length, unsupported = r.cases.filter(c=>!c.eligible && !c.enriched).length;
  $('clients-docket-scope').textContent = `${missing} supported cases still lack a parsed docket. Default: run all of them, newest filed first, within your cap. Saved dockets are reused.${unsupported ? ` ${unsupported} other cases have no supported retrieval; see the Case index.` : ''}`;
  const newStamp = JSON.stringify([r.cases,r.partyReports,r.busy,activeRun,rulesAvailable,docketBudgetsAvailable]); if (tableStamp !== newStamp) {tableStamp = newStamp; caseRows = buildCaseRows(r.cases,r.partyReports); renderFacetGroups(); renderTable(); renderParties();}
  $('docket-guidance').hidden = !r.indexReady || !r.cases.some(c=>!c.enriched);
  const coverage = r.partyReports?.coverage;
  $('report-coverage').textContent = coverage ? `${coverage.parsedDockets} of ${coverage.indexedCases} indexed cases have parsed dockets · ${coverage.indexOnlyCases} index only · Counsel matched in ${coverage.casesWithMatchedClients} cases${coverage.partyTablesNeedingReview ? ` · ${coverage.partyTablesNeedingReview} party tables need review` : ''}. Client summaries cover saved dockets only.` : 'An update is ready. Use Stop ROC, then reopen Start ROC to load party reports. Saved work is retained; reconnect PACER when you next need access.';
  $('exports-stale').hidden = clientReportsAvailable && !r.exportsNeedRefresh;
  $('exports-stale-message').textContent = clientReportsAvailable ? 'Updated reports are available. Refresh this run’s downloads from saved evidence, free.' : 'The Clients view is ready. To update downloads, use Stop ROC and reopen Start ROC, then choose Update exports. Restarting ends the current PACER sign-in; saved data stays available.';
  $('exports').replaceChildren();
  const formats = {'case-index.xlsx':'Excel ↓','case-index.csv':'CSV ↓','party-reports.zip':'Client CSVs ↓','case-index.html':'HTML ↓','evidence.json':'Evidence ↓','review.json':'Findings ↓'};
  for (const [file, title] of Object.entries(formats)) if (r.downloads.includes(file)) {
    const b = node('button', title, 'secondary'); b.addEventListener('click', () => download(file).catch(error)); $('exports').append(b);
  }
  $('receipt-list').replaceChildren();
  if (!r.transactions.length) $('receipt-list').append(node('p', 'No PACER transactions in this run.', 'help'));
  r.transactions.forEach(t => $('receipt-list').append(node('div', `${t.startedUtc.slice(0,19).replace('T',' ')} UTC · ${t.kind === 'docket' ? 'Court docket report' : 'PCL API page'} · ${t.state === 'complete' ? money(t.chargedCents) + ' confirmed' : money(t.reservedCents) + ' reserved; receipt unresolved'}`, 'receipt')));
}
function buildCaseRows(cases, reports) {
  const names = new Map(), sourceNames = new Map(), replacements = new Map();
  const add = (map,key,value) => {if (!value) return; if (!map.has(key)) map.set(key,new Set()); map.get(key).add(value);};
  for (const view of ['clients','defendants','plaintiffs']) for (const row of reports?.[view]?.cases || []) {
    add(names,row.caseKey,row.name);
    if (row.groupId) {
      if (!replacements.has(row.caseKey)) replacements.set(row.caseKey,new Map());
      for (const p of row.sourceParties || []) replacements.get(row.caseKey).set(p.name,row.name);
    }
  }
  for (const p of reports?.parties || []) {
    add(sourceNames,p.caseKey,p.name); add(names,p.caseKey,replacements.get(p.caseKey)?.get(p.name) || p.name);
  }
  return cases.map(c => {
    const savedPartyNames = [...(names.get(c.key) || [])].sort(alphabet.compare);
    const court = c.district ? c.district + (c.court && c.court !== 'U.S. District Court' ? ` · ${c.court}` : '') : c.court || null;
    const nature = unavailable(c.nature) ? null : c.nature, role = unavailable(c.role || c.team) ? null : c.role || c.team;
    const findings = [...new Set(c.issues.map(i=>labels[i.category] || i.category))];
    return {...c,savedPartyNames,facetValues:{court:[court],type:[c.caseType || null],status:[c.status || null],role:[role],
      year:[c.dateFiled?.slice(0,4) || null],docket:[c.enriched ? 'Report retrieved' : 'Without docket report'],
      review:findings.length ? findings : ['No flagged findings'],party:savedPartyNames.length ? savedPartyNames : [null],
      title:[c.caseTitle || null],nature:[nature]},
      sortValues:{date:c.dateFiled || null,party:savedPartyNames[0] || null,title:c.caseTitle || null,court,nature},
      searchText:[c.caseNumber,c.caseTitle,c.caseType,c.district,c.court,c.role || c.team,c.nature,c.status,...savedPartyNames,...(sourceNames.get(c.key) || [])].join(' ').toLocaleLowerCase()};
  });
}
function matchesCaseFilters(c, choices, text='') {
  return (!text || c.searchText.includes(text)) && Object.entries(choices).every(([key,values])=>!values.size || c.facetValues[key].some(v=>values.has(v)));
}
function compareCases(a,b,sort) {
  const field = sort === 'newest' || sort === 'oldest' ? 'date' : sort.split('-')[0];
  const direction = sort === 'newest' || sort.endsWith('-desc') ? -1 : 1;
  const left = a.sortValues[field], right = b.sortValues[field];
  // Missing values remain last in either direction; case key breaks ties.
  if (left === null && right !== null) return 1;
  if (right === null && left !== null) return -1;
  return (left !== null && right !== null ? direction * alphabet.compare(left,right) : 0) || alphabet.compare(a.key,b.key);
}
function filteredRows() {
  const text = $('filter').value.trim().toLocaleLowerCase(), sort = $('sort').value;
  return caseRows.filter(c=>matchesCaseFilters(c,facetChoices,text)).sort((a,b)=>compareCases(a,b,sort));
}
function facetLabel(key,value) {return value === null ? key === 'party' ? 'No saved party names' : 'Not available' : value;}
function facetCounts(rows,key) {
  const counts = new Map(); for (const row of rows) for (const value of new Set(row.facetValues[key])) counts.set(value,(counts.get(value) || 0)+1);
  return counts;
}
function renderFacetGroups() {
  const prior = new Map([...$('facet-groups').children].map(group=>[group.dataset.facet,{open:group.open,text:group.querySelector('input[type=search]').value}]));
  $('facet-groups').replaceChildren();
  for (const [key,label] of facetDefinitions) {
    const counts = facetCounts(caseRows,key);
    // Retain active values absent from updated evidence so a filter never
    // silently broadens. They remain removable with a visible zero count.
    for (const value of facetChoices[key] || []) if (!counts.has(value)) counts.set(value,0);
    if (!counts.size) continue;
    const group = node('details',undefined,'facet-group'); group.dataset.facet = key;
    const heading = node('summary',label), badge = node('span',undefined,'facet-badge'); heading.append(badge); group.append(heading);
    const search = node('input'); search.type = 'search'; search.placeholder = `Find ${label.toLowerCase()} options…`; search.setAttribute('aria-label',`Find ${label} options`); search.hidden = counts.size <= 8; search.value = search.hidden ? '' : prior.get(key)?.text || '';
    const options = node('div',undefined,'facet-options'), hint = node('p',undefined,'help'); let limit = 100;
    const draw = () => {
      const text = search.value.trim().toLocaleLowerCase();
      const values = [...counts.keys()].filter(v=>facetLabel(key,v).toLocaleLowerCase().includes(text)).sort((a,b)=>a === b ? 0 : a === null ? 1 : b === null ? -1 : alphabet.compare(a,b));
      options.replaceChildren();
      for (const value of values.slice(0,limit)) {
        const row = node('label',undefined,'facet-option'), cb = node('input'); cb.type = 'checkbox'; cb.checked = facetChoices[key]?.has(value) || false;
        const caption = facetLabel(key,value); cb.setAttribute('aria-label',`${label}: ${caption}`);
        cb.addEventListener('change',()=>{if (!facetChoices[key]) facetChoices[key] = new Set(); cb.checked ? facetChoices[key].add(value) : facetChoices[key].delete(value); page = 0; renderTable();});
        const text = node('span',caption); text.title = caption; row.append(cb,text,node('small',String(counts.get(value)))); options.append(row);
      }
      hint.textContent = values.length ? `${Math.min(limit,values.length)} of ${values.length} options` : 'No matching options.';
      if (values.length > limit) {const more = node('button','Show more options','text-button'); more.addEventListener('click',()=>{limit += 100; draw();}); options.append(more);}
    };
    search.addEventListener('input',()=>{limit = 100; draw();}); group.append(search,options,hint);
    group.addEventListener('toggle',()=>{if (group.open) draw();}); group.open = prior.get(key)?.open || false;
    $('facet-groups').append(group); if (group.open) draw();
  }
  renderFilterState();
}
function renderFilterState() {
  const active = facetDefinitions.flatMap(([key,label])=>[...(facetChoices[key] || [])].map(value=>({key,label,value})));
  $('facet-count').textContent = active.length ? `${active.length} checked` : 'All results';
  $('active-filters').replaceChildren();
  for (const {key,label,value} of active) {
    const caption = `${label}: ${facetLabel(key,value)}`, chip = node('button',caption+' ×','filter-chip'); chip.title = caption; chip.setAttribute('aria-label',`Remove ${caption}`);
    chip.addEventListener('click',()=>{facetChoices[key].delete(value); page = 0; renderFacetGroups(); renderTable();}); $('active-filters').append(chip);
  }
  for (const group of $('facet-groups').children) {const size = facetChoices[group.dataset.facet]?.size || 0; group.querySelector('.facet-badge').textContent = size ? `${size} checked` : '';}
  $('filter-state').hidden = !active.length && !$('filter').value;
  $('sort-help').hidden = !$('sort').value.startsWith('party-');
}
function selectionLabel(rows) {
  const shown = new Set(rows.map(r => r.key)), hidden = [...selected].filter(k => !shown.has(k)).length;
  $('selection-count').textContent = selected.size; $('selection-hidden').textContent = hidden ? `(${hidden} outside this filter)` : ''; setButtons();
}
function docketField(c, field, value) {
  const td = node('td');
  const missing = !c.enriched && (field === 'Role' ? !value || /^unresolved/i.test(value) : !value || /^(charges )?not supplied by PCL$|^N\/A$/i.test(value));
  if (!missing) {td.textContent = value || 'Unresolved'; td.title = value || 'Unresolved'; return td;}
  if (!c.eligible) {td.append(node('span','Docket unavailable','pill')); td.title = c.ineligibleReason; return td;}
  const b = node('button','Run docket report','docket-field');
  b.setAttribute('aria-label',`Run docket for ${field} in ${c.caseNumber}`);
  b.title = `Review this case and set a spending cap to investigate ${field}. No purchase until you confirm.`;
  b.addEventListener('click',()=>action(()=>previewDockets([c.key])).catch(error));
  td.append(b); return td;
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
    titleCell.append(b, node('span',c.caseTitle,'case-title')); titleCell.title = c.caseTitle;
    if (c.savedPartyNames.length || $('sort').value.startsWith('party-')) {const names = node('span',c.savedPartyNames.length ? 'Parties: '+c.savedPartyNames.join('; ') : 'No saved party names','case-parties'); names.title = names.textContent; titleCell.append(names);}
    tr.append(titleCell);
    for (const value of [c.caseType,c.district,c.dateFiled]) {const td = node('td',value); td.title = value; tr.append(td);}
    tr.append(docketField(c,'Role',c.role || c.team),docketField(c,'Nature of Case',c.nature),node('td',c.status));
    const findings = node('td'), review = c.issues.some(i => i.category === 'needs-review'), missing = c.issues.some(i => i.category === 'missing-source');
    findings.append(node('span',review ? 'Review' : missing ? 'Source gap' : c.enriched ? 'Enriched' : 'Index only',`pill${review || missing ? ' warn' : c.enriched ? ' good' : ''}`)); tr.append(findings); $('case-rows').append(tr);
  }
  if (reportView === 'cases') $('result-count').textContent = `${rows.length.toLocaleString()} shown`;
  $('page-summary').textContent = rows.length ? `${page*pageSize+1}–${Math.min((page+1)*pageSize,rows.length)} of ${rows.length.toLocaleString()} cases` : '0 cases';
  $('previous').disabled = page === 0; $('next').disabled = (page+1)*pageSize >= rows.length; selectionLabel(rows); renderFilterState();
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
  selectedNames.clear(); updateNameSelection();
  $('cases-panel').hidden = view !== 'cases'; $('parties-panel').hidden = view === 'cases';
  for (const name of ['cases','clients']) {
    $('view-'+name).classList.toggle('active',view === name); $('view-'+name).setAttribute('aria-pressed',String(view === name));
  }
  renderTable(); renderParties();
}
function renderParties() {
  if (reportView === 'cases' || !current?.partyReports) return;
  const available = new Set(current.partyReports[reportView].summary.filter(s=>!s.groupId).map(s=>s.nameKey));
  for (const key of selectedNames.keys()) if (!available.has(key)) selectedNames.delete(key);
  const text = $('party-filter').value.trim().toLocaleLowerCase();
  const rows = current.partyReports.clients.summary.filter(s => !text || [s.name,...s.sourceNames].join(' ').toLocaleLowerCase().includes(text));
  const order = $('party-sort').value;
  rows.sort((a,b) => (order.startsWith('name-') ? (order === 'name-desc' ? -1 : 1) * alphabet.compare(a.name,b.name) : (order === 'count-asc' ? 1 : -1) * (a.caseCount-b.caseCount)) || alphabet.compare(a.name,b.name) || alphabet.compare(a.nameKey,b.nameKey));
  partyPage = Math.min(partyPage,Math.max(0,Math.ceil(rows.length/pageSize)-1));
  $('party-rows').replaceChildren();
  for (const s of rows.slice(partyPage*pageSize,(partyPage+1)*pageSize)) {
    const tr = node('tr'), cell = node('td'), b = node('button',s.name);
    const cb = node('input'); cb.type = 'checkbox'; cb.className = 'name-checkbox'; cb.checked = selectedNames.has(s.nameKey); cb.disabled = !rulesAvailable || Boolean(s.groupId) || Boolean(activeRun);
    cb.setAttribute('aria-label','Group '+s.name); cb.title = s.groupId ? 'Edit this existing group in Name rules' : 'Select a name to group';
    cb.addEventListener('change',() => {cb.checked ? selectedNames.set(s.nameKey,s) : selectedNames.delete(s.nameKey); updateNameSelection();}); cell.append(cb);
    b.addEventListener('click',() => showParty(s)); cell.append(b); cell.title = s.sourceNames.join('; '); tr.append(cell);
    if (s.groupId) {
      const badge = node('button',s.groupKind === 'organization-group' ? 'Organization group' : 'Name correction','group-badge');
      badge.disabled = !rulesAvailable || Boolean(activeRun); badge.addEventListener('click',() => openRules(s.groupId).catch(error)); cell.append(badge);
    }
    const count = node('td',String(s.caseCount)); tr.append(count);
    $('party-rows').append(tr);
  }
  $('party-empty').hidden = Boolean(rows.length);
  $('party-empty').textContent = current.partyReports.coverage.parsedDockets ? 'No clients match this view. Check the docket coverage and counsel matches.' : 'Retrieve selected dockets from the Case index to identify clients.';
  $('result-count').textContent = `${rows.length.toLocaleString()} names`;
  $('party-page-summary').textContent = rows.length ? `${partyPage*pageSize+1}–${Math.min((partyPage+1)*pageSize,rows.length)} of ${rows.length} names` : '0 names';
  $('party-previous').disabled = partyPage === 0; $('party-next').disabled = (partyPage+1)*pageSize >= rows.length;
  updateNameSelection();
}
function updateNameSelection() {
  const visible = new Set((current?.partyReports?.[reportView]?.summary || []).filter(s => [s.name,...s.sourceNames].join(' ').toLocaleLowerCase().includes($('party-filter').value.trim().toLocaleLowerCase())).map(s=>s.nameKey));
  const hidden = [...selectedNames.keys()].filter(k=>!visible.has(k)).length;
  $('name-selection-count').textContent = `${selectedNames.size} names selected${hidden ? ` (${hidden} outside these filters)` : ''}`; setButtons();
}
function showParty(summary) {
  const appearances = current.partyReports.clients.cases.filter(p => p.nameKey === summary.nameKey);
  const byCase = new Map(); appearances.forEach(p => {if (!byCase.has(p.caseKey)) byCase.set(p.caseKey,[]); byCase.get(p.caseKey).push(p);});
  $('party-name').textContent = summary.name; $('party-source-names').textContent = (summary.groupKind === 'organization-group' ? 'Organization group — separate legal entities. ' : '')+'Source names: '+summary.sourceNames.join('; ');
  $('party-case-count').textContent = `${byCase.size} distinct cases in this view. Counts reflect saved docket coverage.`;
  $('party-case-list').replaceChildren();
  const caseMap = new Map(current.cases.map(c => [c.key,c]));
  const rows = [...byCase.entries()].sort((a,b) => b[1][0].dateFiled.localeCompare(a[1][0].dateFiled) || a[0].localeCompare(b[0]));
  for (const [key, parties] of rows) {
    const c = caseMap.get(key), block = node('div',undefined,'party-appearance'), b = node('button',c.caseNumber,'text-button');
    b.addEventListener('click',() => showCase(c)); block.append(b,node('strong',c.caseTitle),node('p',`${c.district} · ${c.dateFiled} · ${c.status} · ${c.role || 'Unresolved'}`));
    block.append(node('p','Client type: '+parties.map(p => p.partyRole).join('; '),'help'));
    for (const p of parties) for (const source of p.sourceParties || []) if (p.groupId) block.append(node('p',`${source.name} · ${source.partyRole} · ${source.relationship}${source.matchedCounsel.length ? ' · Matched counsel: '+source.matchedCounsel.join('; ') : ''}`,'help'));
    $('party-case-list').append(block);
  }
  $('party-dialog').showModal();
}
async function download(file) {
  const blob = await downloadBlob(`/api/runs/${currentId}/download/${file}`);
  const url = URL.createObjectURL(blob), a = node('a'); a.href = url; a.download = file; document.body.append(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(url),1000);
}
bind('home','click',e => {e.preventDefault(); showSearch();}); bind('new-search','click',showSearch); bind('new-from-run','click',showSearch);
bind('view-cap-stop','click',()=>capStoppedRun && openRun(capStoppedRun.id));
bind('search-form','submit',async e => {
  e.preventDefault(); const data = new FormData(e.target);
  const values = {firstName:data.get('firstName'),lastName:data.get('lastName'),aliases:data.get('aliases').split('\n').map(s=>s.trim()).filter(Boolean),courts:data.getAll('courts'),dateFiledFrom:data.get('dateFiledFrom'),dateFiledTo:data.get('dateFiledTo'),budgetCents:cents(data.get('budget'))};
  await connectedAction('Connect to run the case search you just requested.', () => action(async () => {const result = await api('/api/runs', values); await openRun(result.id);}));
});
bind('demo','click',() => action(async () => {const result = await api('/api/demo',{}); await openRun(result.id);}));
for (const view of ['cases','clients']) bind('view-'+view,'click',() => setReportView(view));
bind('party-filter','input',() => {partyPage = 0; renderParties();});
bind('party-sort','change',() => {partyPage = 0; renderParties();});
bind('party-previous','click',() => {partyPage--; renderParties();}); bind('party-next','click',() => {partyPage++; renderParties();});
bind('clear-names','click',() => {selectedNames.clear(); renderParties();});
bind('name-rules','click',() => openRules());
bind('group-names','click',() => openRules(null,[...selectedNames.values()].flatMap(s=>s.sourceNames)));
bind('refresh-reports','click',() => action(() => api(`/api/runs/${currentId}/refresh-reports`,{})));
for (const id of ['filter','sort']) bind(id,id === 'filter' ? 'input' : 'change',() => {page = 0; renderTable();});
bind('clear-filters','click',()=>{facetChoices = {}; $('filter').value = ''; page = 0; renderFacetGroups(); renderTable();});
bind('previous','click',() => {page--; renderTable();}); bind('next','click',() => {page++; renderTable();});
bind('select-visible','click',() => {pageRows.filter(c=>c.eligible).forEach(c=>selected.add(c.key)); renderTable();});
bind('clear-selection','click',() => {selected.clear(); renderTable();});
bind('choose-dockets','click',() => {
  facetChoices.docket = new Set(['Without docket report']); page = 0; renderFacetGroups(); renderTable();
  $('filter').scrollIntoView({block:'center'}); $('filter').focus();
});
function missingDockets() {
  return (current?.cases || []).filter(c=>c.eligible && !c.enriched).sort((a,b)=>(b.dateFiled || '').localeCompare(a.dateFiled || '') || alphabet.compare(a.key,b.key));
}
bind('clients-dockets','click',()=>action(()=>previewDockets(missingDockets().map(c=>c.key), true)));
function docketCapValid() {
  if (!quote?.quoteId) return false;
  try {return cents($('docket-cap').value) >= (quote.newReports ? 300 : 0);} catch {return false;}
}
function updateDocketCap() {
  if (!quote) return;
  let cap = null; try {cap = cents($('docket-cap').value);} catch {}
  $('quote-budget').textContent = `${money(quote.spentCents)} already charged for this run. This new cap is additional to those charges and replaces any unused allowance from the previous cap.`;
  $('docket-cap-message').textContent = !quote.newReports ? 'No new charges: this selection uses saved or demo reports.' : cap === null ? 'Enter a fresh cap to enable retrieval. Opening this preview buys nothing.' : cap < 300 ? 'At least $3 is needed to reserve the first new report.' : `At most ${money(cap)} in new charges authorized. ${cap < quote.maximumAdditionalCents ? 'Your cap may cover only part of this list. ' : ''}ROC stops and shows a notice before the next $3 reservation would exceed your cap; it may stop with less than $3 unspent. Total run limit: ${money(quote.spentCents + cap)}. Resuming keeps this limit.`;
  $('docket-cap-message').classList.toggle('cap-short',quote.newReports > 0 && cap !== null && cap < 300); setButtons();
}
async function previewDockets(keys, allMissing = false) {
  const id = currentId;
  const result = await api(`/api/runs/${id}/quote`,{keys});
  if (!result.quoteId) throw new Error('Stop ROC and reopen Start ROC to load separate docket spending caps. No retrieval submitted.');
  quote = {...result,runId:id,allMissing};
  $('quote-heading').textContent = allMissing ? 'Run dockets to identify clients' : 'Your docket selection';
  $('quote-scope').textContent = allMissing ? 'Default: all supported cases in this search without parsed dockets, newest filed first. This includes cases outside your current filters. Choose specific cases below to narrow the list.' : 'ROC retrieves cases in the order listed below until they are complete or the spending cap stops the run.';
  $('revise-dockets').textContent = allMissing ? 'Choose specific cases' : 'Back to case selection';
  $('quote-text').textContent = `${quote.selectedCount} selected · ${quote.newReports} new reports · ${quote.cachedReports} saved or demo reports`;
  $('quote-cost').textContent = money(quote.maximumAdditionalCents);
  $('docket-cap').value = quote.newReports ? '' : '0.00';
  $('docket-cap').min = quote.newReports ? '3' : '0';
  updateDocketCap();
  $('quote-cases').replaceChildren(...quote.cases.map(c=>node('p',`${c.caseNumber} · ${c.district}`))); $('quote-dialog').showModal();
}
bind('preview','click',() => action(()=>previewDockets([...selected])));
bind('docket-cap','input',updateDocketCap);
bind('revise-dockets','click',() => {
  const allMissing = quote?.allMissing; $('quote-dialog').close(); setReportView('cases');
  if (allMissing) {selected.clear(); facetChoices = {docket:new Set(['Without docket report'])}; $('filter').value = ''; $('sort').value = 'newest'; page = 0; renderFacetGroups(); renderTable();}
  $('filter').scrollIntoView({block:'center'}); $('filter').focus();
});
$('quote-dialog').addEventListener('close',()=>{quote = null; setButtons();});
bind('confirm-retrieve','click',async () => {
  if (!docketCapValid()) return;
  const id = quote.runId, request = {keys:[...quote.keys],quoteId:quote.quoteId,docketBudgetCents:cents($('docket-cap').value)}, needsConnection = quote.newReports > 0; $('quote-dialog').close();
  const proceed = () => action(async () => {await api(`/api/runs/${id}/retrieve`,request); if (currentId === id) {request.keys.forEach(k=>selected.delete(k)); tableStamp = '';}});
  if (needsConnection) await connectedAction('Connect to retrieve the dockets you just confirmed.', proceed); else await proceed();
});
for (const [id, command] of [['pause','pause'],['reconcile','reconcile'],['rebuild','export']]) bind(id,'click',() => action(() => api(`/api/runs/${currentId}/${command}`,{})));
bind('resume','click',async () => {const id = currentId; const proceed = () => action(() => api(`/api/runs/${id}/resume`,{})); if (current.demo || current.lastAction === 'documents-analyze') await proceed(); else await connectedAction('Connect to resume this saved operation. Existing receipts and selections are retained.', proceed);});
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
  if (connection.signedIn) $('auth-username').value = connection.username;
}
async function connectedAction(context, next) {if (connection.connected) await next(); else openAuth(context, next);}
bind('connect-pacer','click',() => openAuth('Connect your PACER account to this ROC session. Signing in alone does not start a search.', null));
bind('switch-account','click',() => {openAuth('Sign in with another PACER account to open its saved searches. People sharing a PACER account share its history.',null); $('auth-username').value = ''; $('auth-client').value = ''; $('auth-username').focus();});
bind('disconnect-pacer','click',() => action(() => api('/api/connection/disconnect',{})));
bind('close-auth','click',() => $('auth-dialog').close());
$('auth-dialog').addEventListener('close',() => {clearCredentials(); afterLogin = null;});
bind('toggle-password','click',() => {const hidden = $('auth-password').type === 'password'; $('auth-password').type = hidden ? 'text' : 'password'; $('toggle-password').textContent = hidden ? 'Hide' : 'Show'; $('toggle-password').setAttribute('aria-label', hidden ? 'Hide password' : 'Show password');});
bind('auth-form','submit',async e => {
  e.preventDefault(); if (authSending || authSubmitted || connection.connecting) return;
  authSending = true; $('auth-message').textContent = 'Connecting to the official PACER authentication API…'; $('auth-message').hidden = false; setButtons();
  const fields = {username:$('auth-username').value,password:$('auth-password').value,otp:$('auth-otp').value,clientCode:$('auth-client').value,redact:$('auth-redact').checked};
  authTarget = fields.username.trim();
  if (connection.signedIn && connection.username !== authTarget) afterLogin = null;
  try {await api('/api/connection/sign-in',fields); authSubmitted = true; authSending = false; await refresh();}
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

function invalidateRulePreview() {pendingRule = null; $('rule-preview').hidden = true; setButtons();}
function editRule(rule=null, names=[]) {
  editingRule = rule?.id || null; invalidateRulePreview(); $('rules-message').hidden = true;
  $('rule-editor-heading').textContent = rule ? 'Edit rule' : 'New rule';
  $('rule-label').value = rule?.label || names[0] || ''; $('rule-kind').value = rule?.kind || 'name-correction';
  $('rule-names').value = (rule?.names || [...new Set(names)]).join('\n'); $('delete-rule').hidden = !rule;
}
function renderRules() {
  $('rules-list').replaceChildren();
  if (!ruleState.rules.length) $('rules-list').append(node('p','No saved rules yet.','help'));
  for (const rule of ruleState.rules) {
    const row = node('div',undefined,'rule-item'), b = node('button','Edit '+rule.label,'text-button');
    b.addEventListener('click',() => editRule(rule)); row.append(b,node('small',`${rule.kind === 'organization-group' ? 'Organization group' : 'Name correction'} · ${rule.names.length} source spellings`)); $('rules-list').append(row);
  }
}
async function openRules(id=null,names=[]) {
  ruleState = await api('/api/name-rules'); renderRules();
  editRule(id ? ruleState.rules.find(r=>r.id === id) : null,names); $('rules-dialog').showModal(); setButtons();
}
async function ruleAction(fn) {
  if (rulesBusy) return; rulesBusy = true; $('rules-message').hidden = true; setButtons();
  try {await fn();}
  catch(e) {invalidateRulePreview(); $('rules-message').textContent = e.message; $('rules-message').hidden = false;}
  finally {rulesBusy = false; await refresh(); setButtons();}
}
async function previewRule(operation,rule) {
  invalidateRulePreview();
  const request = {revision:ruleState.revision,operation,runId:currentId,...(rule ? {rule} : {})};
  const result = await api('/api/name-rules/preview',request);
  pendingRule = {...request,previewId:result.previewId};
  $('rule-impact').textContent = `${result.affectedRuns} saved runs contain matching names (${result.affectedCasesAcrossRuns} case appearances across runs). This rule set also applies to future searches. ${currentId ? 'The open run’s exports will be rebuilt automatically.' : 'Existing runs can update their exports for free.'}`;
  $('rule-preview-heading').textContent = operation === 'undo' ? 'Undo the last saved change' : operation === 'delete' ? 'Remove this rule' : 'Preview name grouping';
  $('rule-comparisons').replaceChildren();
  for (const comparison of result.comparisons) {
    if (!comparison.before.length && !comparison.after.length) continue;
    const section = node('div',undefined,'rule-comparison'); section.append(node('h3',comparison.report[0].toUpperCase()+comparison.report.slice(1)));
    const columns = node('div',undefined,'two-columns');
    for (const side of ['before','after']) {
      const col = node('div'); col.append(node('strong',side === 'before' ? 'Before' : 'After'));
      for (const s of comparison[side].slice(0,30)) col.append(node('p',`${s.name} — ${s.caseCount} distinct case${s.caseCount === 1 ? '' : 's'}${s.groupKind === 'organization-group' ? ' (organization group)' : ''}`));
      if (comparison[side].length>30) col.append(node('p',`…and ${comparison[side].length-30} more names`,'help'));
      if (!comparison[side].length) col.append(node('p','None','help'));
      columns.append(col);
    }
    section.append(columns); $('rule-comparisons').append(section);
  }
  if (!$('rule-comparisons').children.length) $('rule-comparisons').append(node('p',currentId ? 'No report names in the open run change. The saved rule will still apply wherever its source names occur.' : 'Open a saved run to preview its before/after case totals.','help'));
  $('apply-rule').textContent = operation === 'undo' ? 'Confirm undo' : operation === 'delete' ? 'Confirm removal' : 'Save rule';
  $('rule-preview').hidden = false; setButtons(); $('rule-preview').scrollIntoView({block:'start'});
}
for (const id of ['rule-label','rule-kind','rule-names']) bind(id,'input',invalidateRulePreview);
bind('new-rule','click',() => editRule());
bind('rule-form','submit',e => {e.preventDefault(); return ruleAction(() => previewRule('save',{
  ...(editingRule ? {id:editingRule} : {}),label:$('rule-label').value,kind:$('rule-kind').value,
  names:$('rule-names').value.split('\n').map(s=>s.trim()).filter(Boolean)}));});
bind('delete-rule','click',() => ruleAction(() => previewRule('delete',{id:editingRule})));
bind('undo-rule','click',() => ruleAction(() => previewRule('undo')));
bind('apply-rule','click',() => ruleAction(async () => {
  const request = pendingRule; if (!request) return;
  ruleState = await api('/api/name-rules/apply',request); selectedNames.clear(); tableStamp = ''; renderRules(); editRule();
  $('rules-message').textContent = 'Saved. Original source names and counsel associations are preserved. No PACER requests.'; $('rules-message').hidden = false;
}));
