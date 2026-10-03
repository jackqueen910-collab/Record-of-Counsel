# Record of Counsel (ROC)

An on-demand PACER workflow. Case searching, docket enrichment and party reports remain deterministic and require no AI model, Codex session, chat connector or web host. The optional Document Grabber uses a shared, owner-configured ROC Anthropic account to classify saved docket entries.

The landing page offers **Search Attorney** (default) and **Search Litigant**, with last name before first name in both. Litigant searches accept **Last name / Entity name** and an optional first name. Attorney workflows retain their docket/client features; the first litigant release provides API case searching, saved results, filtering/sorting and downloads. See [litigant search scope](docs/litigant-search.md).

Both modes offer **Additional names → Add another name**. Each distinct supplied first/last-name pair runs its own official PCL search under one shared cap, with identical court/date filters. Overlapping cases are counted once. Case details and the **Search matches** export preserve which names found them. Added attorney names also participate in docket counsel matching. See [additional names, costs and recovery](docs/additional-names.md).

**Current release: 0.1 prototype.** The standalone workflow has completed live API search, court-web docket retrieval, client/counsel matching and local export. All **90 district courts in the states and D.C. have reviewed live samples** and are eligible for bounded retrieval. The latest batch completed 148 civil/criminal reports across the remaining 74 districts for $81.00, including earlier territory charges, under a $150 cap. The [coverage report](docs/district-validation-remaining-2026-09-30.md) records the sample limits. The four territorial districts remain registered but excluded from ordinary retrieval. Sampled tests do not establish universal layout or field coverage. Google Sheets publication still needs its own live acceptance test.

## What it does

1. Reads saved official PCL responses, or authenticates and searches the official PCL API.
2. Groups records by court and normalized case number, retaining prosecution and defense records.
3. Selects a bounded docket sample from those results (latest/oldest, court, dates, case type, total limit and optional per-court limit), or uses explicit case selections. Retrieves those reports using court websites. This is **web retrieval**, not a document-retrieval API. The shared adapter has reviewed samples from all state and D.C. districts; unverified districts require explicit configuration for controlled testing.
4. Matches explicit attorney-name aliases to the docket's party/counsel table.
5. Copies all listed counts for the matched client, preserving source labels, original/superseding entries and terminated counts. Multiple defendants receive separate labeled lists.
6. Writes a ten-column case index, client rankings and one-client-per-case reports with case-specific Client type as XLSX, CSV and readable HTML, plus detailed evidence and review JSON.
7. Optionally creates a fresh Google Sheet using the operator's own Google OAuth grant.

The main output columns are Case number, Case title, Case Type, Role, Court, District, Date filed, Nature of Case, Status (PACER), and PACER link. For one defendant, Nature of Case contains the source's listed charges/count labels with no name prefix. For multiple defendants, names and defendant numbers label the separate lists. Terminated rows are marked; sentencing/disposition text stays in evidence. Long charge cells stay on one line; HTML and Google Sheets clip them. Role also supports explicit source roles such as Petitioner, Respondent, Claimant and Amicus. See [counts and findings](docs/counts-and-findings.md) and [docket party reports](docs/docket-reports.md).

## Run ROC

### Document Grabber (pilot)

Select cases with saved dockets in the case index, then open **Document Grabber**. Preview the exact cases and AI reservation and set a fresh AI cap before sending any text. **Claude Sonnet 5.5** is the default; **Opus 5.5** is an explicit alternative. ROC never switches models or retries automatically. Regular users do not enter an API key; model usage is billed to the shared ROC account.

**One-time owner setup:** create a Claude Console API key scoped to one workspace (a service account is appropriate for shared ROC usage), then open `Setup-ROC-AI.cmd`, paste the key, and choose **Save ROC account**. See [Anthropic's key setup](https://platform.claude.com/docs/en/manage-claude/authentication). Unscoped multi-workspace keys need an additional workspace header and are not supported by this version. The key is stored in Windows Credential Manager for this Windows user on this machine, survives restarts, and is loaded by the backend automatically. The setup window can replace or remove it. Saving makes no API request; the first approved analysis validates access. The key is never sent to ROC's browser UI, written into run files/exports, or included in the repository.

For other backend deployments, explicitly configure `ROC_ANTHROPIC_API_KEY` in the host's secret environment. It overrides the Windows store; generic `ANTHROPIC_API_KEY` is intentionally ignored to avoid billing another project's account. This local version does not distribute credentials with copied code: another computer requires owner provisioning. A centrally shared hosted backend is outside this change.

Review the candidate motions and orders, Claude's interpretation of who filed each motion, and the quoted evidence supporting client attribution and motion/order links. Claude interprets filings made by or on behalf of a represented client from the full docket context; no specific “as to” or “filed by” wording is required. ROC checks source references and quotes, not the correctness of the model's reasoning. Confirm attorney involvement from the filing's signature block. Nothing is preselected. Choose supported PDF candidates, preview the exact documents and set a **separate PACER document cap** before purchasing. Prior case-search/docket allowances do not authorize document spending. Saved analyses/PDFs are reused. Download a ZIP with the index, evidence, spending records and any purchased PDFs. Text-only orders remain in the index. No dockets are bought automatically by this feature.

**This feature has offline integration tests, not live acceptance.** Sonnet accuracy and court document-price/viewer layouts still need a small, separately approved live pilot. Docket validation in 90 districts does not validate PDF downloads. The first adapter accepts only recognized single-document price forms returning a direct PDF; attachment menus, JavaScript-only forms and unknown viewers stop for review. See [Document Grabber scope, recovery and test plan](docs/document-grabber.md).

### Browser workspace

After the Python/runtime setup below, double-click **`Start-ROC.cmd`** on Windows. It starts `pythonw` in the background and exits; no terminal remains open. For a shortcut that avoids even the command launcher's brief window, run `Create-ROC-Shortcut.ps1` once and then double-click **Start ROC** in the project folder. The shortcut uses this machine's `.venv` and is not committed to Git. A developer can also run:

```console
python -m roc ui
```

ROC opens a local browser interface. Nothing is hosted, no chat agent is required, and opening it submits no PACER requests. **Connect PACER** opens a sign-in dialog in ROC with username, a visible password field and Show/Hide toggle, optional MFA/client code and the redaction acknowledgment. The local application sends those values to the same official PACER authentication API used by the CLI; it does not automate PACER's login website. A rejected login leaves the form editable and never retries automatically. Password/MFA inputs clear on success or dismissal; passwords, MFA codes and PACER session tokens are never written to ROC files or logs. No credential remembering is enabled.

**Your PACER login is your ROC login.** Before successful sign-in, the sidebar says “Saved searches will appear here after you sign in with PACER.” Each verified PACER username has its own saved searches, name rules, receipts, reports and Document Grabber files. People sharing a PACER username share that history. There is no separate registration. Use the same username spelling/case each time: PACER's authentication response does not provide a canonical account ID, so ROC does not guess that differently spelled usernames are one account.

Connecting from the header only connects the account. If an explicitly requested search, docket retrieval or resume needs authentication, **Connect and continue** carries out that one requested action after successful authentication. Canceling the dialog cancels that continuation. Reconnecting a stopped run retains its case selection and receipts and waits for **Resume saved operation**. Switching usernames cancels any continuation belonging to the old account. **Sign out** clears that browser's account view and in-memory PACER connection; **Switch account** signs in to a different history. Neither revokes sessions in other browsers or PACER clients. An expired PACER session still permits reading saved work during the current ROC login; only new PACER requests require reconnection. Restarting ROC requires sign-in to unlock saved searches again.

The normal interface has no demo prompt, and its demo endpoint is disabled. Fictional fixtures remain available for development through the offline CLI below. Automated browser tests explicitly opt in with `make_server(..., enable_demo=True)`; normal desktop and CLI interface launches never enable that option. Development demo files remain isolated from other accounts.

1. Choose **Search Attorney** or **Search Litigant**. Enter last name (or entity name for a litigant), then first name, optional filing dates/districts and a case search spending cap. First name is optional for litigants; leave it blank for an organization. **Additional names** expands the API search; all names share the cap. Each tab preserves its own name drafts during the session. Attorney **Docket-only name spellings** retains the former alias field for full-name counsel matching without extra searches. Leaving courts unselected searches all federal courts; no party-role restriction is applied.
2. **Search cases** performs the selected attorney or litigant search through the official PCL API and saves the index. Saved runs are labeled by search type. It never automatically buys dockets. Search charges count against the cap. An incomplete search can be resumed explicitly using purchased pages. Litigant results include matched source names and original party-role codes; their Excel/HTML/CSV bundle includes a Matched litigants table. The remaining docket/client steps below currently apply to attorney searches only.
3. Missing Role/Nature of Case cells offer **Run docket report** for that case. The highlighted guidance explains which information needs a docket and warns that many reports can get expensive. **Choose cases for docket reports** takes you to the case filters and selection controls without changing filters or selecting or buying anything. Filter, sort and select individual cases, then **Run docket reports for selected cases** to preview the exact selection and maximum additional cost. Selecting a page affects only that visible page; selections outside a filter remain visibly counted. Enter a **fresh spending cap for this selection**, then **Retrieve these dockets** to start court-web retrieval. The cap covers new charges, in addition to confirmed prior charges, and replaces any unused allowance. A cap of at least $3 can be below the full selection’s ceiling. ROC retrieves in the displayed order and stops before the next $3 reservation would exceed the allowance; actual charges can be lower, so a stop can leave less than $3 unspent. A persistent in-app notice reports the cap stop; partial client results and purchased reports remain available. Saved reports need no new spending. Resume retains the approved limit instead of granting a fresh allowance. Stale previews are rejected. It never purchases filings or substitutes unselected cases. Retrieval can leave fields unresolved where the source does not support them; existing civil Nature of Suit values stay visible.
From **Clients**, **Run docket reports** defaults to all supported cases without parsed dockets across the search, newest filed first, regardless of case filters. The warning dialog shows the exact list and maximum total cost, requires a fresh cap, and offers **Choose specific cases** to return to the case index without starting retrieval. Unsupported courts are counted separately. Older ROC processes require a restart to enable this cap behavior.

4. Inspect full charge text, matched clients, listed parties and categorized data notes in case details. Use **Clients** for rankings by distinct case count and case drilldowns. Client type (plaintiff, defendant, etc.) appears on each case entry, since a client can have different types across cases. A coverage banner distinguishes parsed dockets from index-only cases. Download the multi-sheet Excel workbook, case CSV, client CSV bundle, HTML, **Source data** or **Data notes** directly. Source data (`evidence.json`) contains structured case data, source records and audit details, not filings. Data notes (`review.json`) lists missing fields, conflicts and items needing review, not legal conclusions. Later retrievals retain earlier enrichment. **Rebuild exports from saved data** uses the existing index and purchased reports without network access, including generating new party reports for earlier runs.
5. Select names in a party view and choose **Group selected names**, or open **Name rules** in the sidebar. Choose a preferred name and either **Name correction** or **Organization group**, preview the distinct-case totals, then save. Rules persist across searches and launches in this workspace. Original source names, counsel matches and counts are preserved. Edit/remove rules or **Undo last change** through the same dialog. Saving refreshes the open run's exports free; other runs show **Update exports** when their downloads need refreshing. See [saved name rules](docs/name-rules.md).

Active operations show a moving bar in the highlighted progress box and a large spinner above the results. Search text explains that docket reports and downloads unlock after the case search finishes. These are activity indicators, not percentage estimates; they disappear when work pauses, stops or completes. Reduced-motion preferences disable animation while keeping the status text visible. Lowercase search names are capitalized in saved-search labels and headings only; supplied query text, source names and existing mixed-case/acronym capitalization are preserved.

The sidebar retains runs across launches under `runs/workspace/accounts/<opaque-account-key>`. Opening a saved run never resumes it automatically. New searches create separate snapshots and ledgers; use Resume for an interrupted operation instead of creating a duplicate search. Existing CLI run folders and older unassigned interface runs stay separate and are not automatically assigned to the first account that signs in. Google Sheets publishing remains a CLI-only option; its party-report payload is tested offline but still awaits live acceptance. See [account storage and session behavior](docs/accounts.md).

**Pause after current request** is cooperative: the current response and receipt are saved before the next purchase can begin. It does not cancel a request already sent or a full-report continuation under its existing reservation. **Stop ROC** similarly lets an in-flight request/authentication attempt settle, blocks further work, discards the in-memory connection and stops the local app. Closing the browser tab alone does not stop the worker. Reopen the launcher to reopen the same running app without making a PACER request. The API session is retained in this process across searches and retrievals; rejected/expired sessions require sign-in again. Reauthentication never clears an uncertain receipt or automatically retries the failed purchase. Restarting the process never restores PACER credentials from disk.

An uncertain receipt blocks further purchases. **Check saved receipts** attempts offline reconciliation only and never assumes an unknown charge is zero. If no receipt was saved, PACER billing/manual review is still needed. Changing the run cap never clears receipts or starts work. Process-held OS locks prevent two ROC processes from using the same workspace or run. They release automatically after a crash; reopening never clears receipts or resumes purchases automatically. Legacy PID markers are recovered only when the old process is confirmed absent. Search parameters are fixed within a run.

The interface listens only on `127.0.0.1`, checks the exact local Host/Origin and requires a per-process access token for its APIs, sign-in, shutdown and downloads. That launcher token does not unlock any account: account access also requires a PACER-verified browser session cookie. The backend resolves every run, download and name-rule request within that session's account folder. It serves only packaged UI assets and explicitly listed exports, not arbitrary local files. No remote fonts, scripts, analytics or UI services are used. `interface-connection.json` under the private run workspace contains the **local interface URL/access key only**, so the launcher can reopen a closed tab; this is not a PACER token and is removed on normal shutdown. It is never served as a download. The launcher probes only a validated loopback URL and does not follow redirects. `--no-open` prints the local link instead of opening a browser; `--directory` chooses another workspace. This is the development/local interface, not a decision about hosting or deployment on colleagues' managed computers.

### Sorting and filtering saved results

The case index sorts by filing date (oldest/newest) and by party name, case title, court or Nature of Case (A–Z/Z–A). Sorts use the full filtered result set before pagination. Missing values stay last in either direction; tied values use a stable case-key order. Party sorting uses the alphabetically first saved party name per case, including preferred labels from saved name rules. All saved party names appear below the title, with full text on hover. ROC does not infer parties from captions. Clients also offers name A–Z/Z–A and case-count sorting.

**Filter results** offers a searchable **Party name** picker with a **Party role** selector, plus checkboxes for court, case type, case status, attorney role, filing year, case title and Nature of Case. Docket buttons and case-detail data notes show retrieval coverage and source issues. Select any values within one category (OR); categories combine to narrow the results (AND). The attorney-role filter describes the searched lawyer, while party role describes the named party.

Type a party name to see up to 20 matching options, then select the names to filter cases. Typing alone does not filter the case list; selected names remain visible while finding others. Search also finds original spellings behind saved name groups. **Party role** defaults to **Any party role** and can be used alone. A name plus role must match the **same party appearance**: selecting 3M and Defendant excludes a case where 3M is the plaintiff and somebody else is a defendant. Name-option counts are distinct cases across the saved run, within the chosen party role. Repeated party blocks and multiple matching names do not inflate counts. Missing names and unrecorded roles have explicit options.

Attorney searches use saved docket parties; litigant searches use only the matched litigants returned by PCL. Cases without saved party data cannot match a specific name or known role. Standard PCL `dft`/`pla` codes display as Defendant/Plaintiff, other codes remain visibly labeled, and original evidence and downloads stay unchanged. Case captions and the lawyer's role never supply a missing party role. Other long filter lists retain their search and **Show more options** controls. These filters use saved data and do not submit searches or buy dockets.

Active filters have individual remove buttons and **Clear all filters**. Filters and sort order reset when opening a run. New evidence refreshes available options; an active option absent from the latest evidence remains selected with count zero rather than silently broadening the results. Filtering never selects or purchases dockets. Existing selections survive sorting, paging and filtering, and selections outside the current filter remain counted. The preview still lists every selected case before retrieval. Filters affect only the case-index view; party summary totals and whole-run downloads stay unchanged. All these controls work on saved data without PACER access or charges.

### Command-line demo

Use Python 3.11 or newer. From the repository directory:

```console
python -m pip install -e .
python -m roc run examples/demo.json
python -m unittest discover -v
```

The demo is fictional and makes **no network requests**. Open `runs/demo/output/case-index.html` or `case-index.xlsx`. The defendant represented by another lawyer is deliberately excluded from the charge summary.

To process real saved records, copy the example configuration to a local file, set `indexFile`, add `savedDockets`, and supply the lawyer's name and explicitly accepted aliases. Paths are resolved relative to the configuration file. The original ROC consolidated index format and raw PCL `content` records are both accepted. Keep local configs and records outside version control.

```json
{
  "lawyer": {"firstName": "Jordan", "lastName": "Lawyer", "aliases": ["Jordan A. Lawyer"]},
  "indexFile": "data/pcl-records.json",
  "runDirectory": "runs/my-search",
  "budgetCents": 0,
  "savedDockets": [
    {"courtId": "nysdc", "caseNumber": "1:24-cr-00001", "path": "data/docket.html"}
  ]
}
```

## Live operation

Live access is opt-in. A normal `run` cannot submit a PACER search or report.

- Omit `indexFile` to collect a new attorney index. Optional `search` criteria are passed under PCL's `courtCase` object, for example `dateFiledFrom`, `dateFiledTo`, or `courtId`.
- Set `budgetCents` to the maximum total PACER spending for that run folder. The ledger persists across reruns. Each PCL page reserves 10 cents; each court docket report reserves 300 cents.
- Set `dockets` to select reports automatically from the search results; an explicit positive `limit` is required. Alternatively, use `retrieveDockets` with `courtId` and `caseNumber` for specific cases. Do not combine these two options. No reports are fetched if neither is configured.
- Sign-in prompts for PACER username, password, optional client code and MFA. Password and MFA entry are visible in the operator's terminal. ROC does not write them or tokens to its files. An invalid-credentials/MFA response offers an explicit `y` to re-enter the fields in the same terminal; it never retries automatically. Connection failures and account notices stop. There is no browser-login fallback.
- The command-line docket workflow purchases no underlying filings. It selects full-case docket reports with parties/counsel and terminated parties included. Document Grabber’s separate browser-interface workflow can purchase explicitly selected PDFs under its own approval and cap.

Install the optional court-browser dependency only on a machine where that installation is permitted:

```console
python -m pip install -e ".[live]"
python -m playwright install chromium
python -m roc run local-live.json --live
```

This is the development/runtime setup, not a decision about installation or deployment on colleagues' managed computers. Hosting and colleague distribution remain a separate decision.

### A fresh search followed by new docket retrieval

Copy `examples/live.json` to `local-live.json`, replace the fictional lawyer with the lawyer you want, and set your spending cap. It contains neither `indexFile` nor `savedDockets`: every case comes from the new API search. It is not tied to any previous ROC dataset.

```json
{
  "lawyer": {"firstName": "Jordan", "lastName": "Lawyer", "aliases": ["Jordan A. Lawyer"]},
  "runDirectory": "runs/my-fresh-search",
  "budgetCents": 1000,
  "search": {"dateFiledFrom": "2020-01-01"},
  "dockets": {
    "order": "latest",
    "limit": 2,
    "courts": ["nysdc", "njdc"],
    "caseTypes": ["Criminal"]
  }
}
```

Use `python -m roc run local-live.json --live`, or double-click `Run-ROC.cmd` on Windows after installing into `.venv`. Sign in once in that terminal. The program then collects the index, records its selection in `docket-plan.json`, retrieves the reports, parses them and writes the output without a chat agent. `status.json` tracks progress without credentials. It stops on API failure, uncertain charges or unsupported report forms.

The Windows launcher adds `--keep-session`: after a retrieval error it pauses with the API token retained **only in process memory**. No requests happen while paused. After a code/configuration fix, type `R` to explicitly resume the job using that session and the purchased-response cache; `L` explicitly requests a new API sign-in, and `Q` quits and discards the token. Resume reloads the retrieval/parsing workflow code. It never automatically retries login or purchases, and an unresolved receipt still blocks every paid request. Closing the terminal or an expired PACER session requires another sign-in. Without `--keep-session`, the CLI remains a single attempt that exits when done or stopped.

`search` limits the PCL search itself. The separate `dockets` filters only affect which index cases receive docket enrichment: `order` can be `latest` or `oldest`; `dateFiledFrom`/`dateFiledTo` are inclusive; `exclude` accepts court/case pairs. Optional `maxPerCourt` caps reports from any one court within the overall `limit`. Cases outside the district registry or the configured validation policy remain in the index and are listed in the selection plan as skipped. Docket selection does not infer Role from a case's age.

A run folder is a durable job: restarting it reuses already purchased replies. Choose a **new runDirectory** when you want a new search of current PACER data; reusing a folder intentionally resumes the existing snapshot. Saved records are optional replay inputs and test fixtures, not part of the live architecture.

Unexpected forms, court redirects to login, ambiguous case choices and missing receipts stop the process. The narrow court adapter intentionally does not guess through unfamiliar screens. New courts require adapter checks and tests.

PCL sometimes returns a legacy `http://` case link, as observed for Western Virginia. ROC upgrades it to HTTPS only when its exact hostname matches that case's registered court, with no credentials or explicit port in the URL. The raw link remains in source records and `allCaseLinks`. Court retrieval opens the registry's HTTPS origin; it never follows the insecure link or accepts an unfamiliar host as a fallback.

## District-court coverage and offline planning

The checked-in `roc/district_courts.json` registry records official PACER court IDs, HTTPS origins, district labels, the shared adapter and validation evidence. It was sourced from the [official Court CM/ECF Lookup](https://pacer.uscourts.gov/file-case/court-cmecf-lookup) on September 30, 2026. It includes all 94 primary district-court systems, including D.C., Puerto Rico, Guam, the Northern Mariana Islands and the Virgin Islands. No bankruptcy, appellate, JPML or national specialty court is enabled for retrieval.

The directory also contains the Case Locator under the District category and a separate Northern Ohio asbestos system. Neither is counted as a primary district court. Northern Ohio's main system is registered; its auxiliary asbestos endpoint remains excluded pending separate validation.

| Status | Meaning | Default live behavior |
|---|---|---|
| `sample-verified` | Reviewed live samples establish the listed case types and roles; 90 courts, with sample limits exposed in registry JSON and the validation reports. | Eligible, subject to case filters and budget. |
| `unverified` | Registered without a completed validation scope. The four territorial districts were excluded; one purchased Guam report remains in the private record. | Skipped automatically; explicit selection requires opt-in. |

List coverage and preview a selection without a PACER login or any network requests:

```console
python -m roc courts
python -m roc courts --json
python -m roc plan examples/district-validation.json
python -m roc plan local-live.json
```

`plan` reads `indexFile`, or `pcl-records.json` already saved in the configured run folder. It prints the selected cases, court validation status and reasons for skipped cases. It does not search, buy reports, change the ledger or write files. The example uses fictional records and a zero budget.

To deliberately test additional districts, set the **top-level** `allowUnverifiedCourts` to `true`, retain a small positive docket `limit`, and optionally set `maxPerCourt` to `1`. Omitting `dockets.courts` considers all registered districts; providing a list limits the test to those districts. The flag also applies to explicit `retrieveDockets` selections. It defaults to `false`; reviewed courts are eligible without it. As registry coverage grows, an unrestricted selection can include newly verified districts within its existing count and spending caps. Use `dockets.courts` to pin the desired scope.

```json
{
  "allowUnverifiedCourts": true,
  "dockets": {
    "courts": ["nyedc", "dcdc"],
    "limit": 2,
    "maxPerCourt": 1,
    "caseTypes": ["Criminal", "Civil"]
  }
}
```

This fragment belongs in an otherwise complete run configuration. Actual retrieval still requires `--live`, API sign-in and a sufficient configured budget. Every case's court ID must agree with its registered website before the browser starts; a recognizable report must also match the requested district and case number. Court-specific exceptions belong in a tested adapter, not ad hoc redirects or host suffix guesses. Unknown forms stop before report submission when detected; an unexpected purchased report is saved and flagged without repurchase. A successful run does not silently promote an unverified court: promotion requires reviewing the report, receipt and parsed fields and updating the registry's evidence explicitly.

Coverage is recorded in `docket-plan.json`, per-docket evidence and output cell notes, without adding columns to the ten-column index. PCL indexing remains separate: records from other court types can still be retained in the index, but this expansion never retrieves their dockets.

### Run a bounded court-validation batch

`validate-courts` exercises the court adapter without depending on a particular lawyer or earlier dataset. Configure explicit district IDs, civil/criminal types, a filing-date window and a run-folder spending cap. Preview it without sign-in or network access:

```console
python -m roc validate-courts examples/court-batch.json
```

Copy that example to a local configuration and set the authorized budget before a live run:

```console
python -m roc validate-courts local-court-validation.json --live --keep-session
```

On Windows, `Validate-Courts.cmd` opens the same workflow using `local-court-validation.json` by default, or a configuration path supplied as its first argument. The ordinary `Run-ROC.cmd` attorney workflow remains separate.

The batch buys **one official PCL case-search results page (up to 54 records) per court/type**, then selects **at most one full docket from that page**. At the current page/report rates, each slot reserves at most $0.10 for discovery and $3 for the docket. A 14-court civil/criminal batch therefore has 28 slots and a planned ceiling of $86.80. Its configured cap is enforced independently by the persistent ledger. Discovery pages are samples, not comprehensive indexes. No further result pages or replacement dockets are purchased automatically. An empty or unusable page is recorded as `no-candidate`.

All discovery finishes through the official API before reports are requested from court websites. Authentication, API, form, receipt and report-identity failures stop the batch; no browser search or login fallback occurs. Resume reuses saved search pages and reports. Removing courts or case types from the configuration is allowed on resume: the previous plan and results are preserved in `scope-history`, and all prior receipts still count against the same run cap. A pending report confirmation must be resolved before reducing scope. Adding or replacing courts/types, changing filing dates or changing retrieval methods requires a new run folder, preventing an unnoticed expansion of an existing batch.

Some courts insert a large-report confirmation after the initial report submission. ROC recognizes the observed four-option date-range form and chooses **as initially requested**, preserving the full report. It saves that checkpoint and submits the continuation once under the original $3 reservation. If a saved confirmation was never submitted, an explicit batch resume can post that original form action without reopening or resubmitting the initial report. A durable marker prevents repeating the continuation after a timeout or crash. Unknown responses, already-submitted confirmations without receipts and multiple pending requests still block further purchases; the program never assumes their charges are zero.

Each saved report is parsed once and tested against the attorneys found in its party/counsel blocks. This exercises standard and additional source roles and client-specific counts without additional report purchases. `validation-results.json` preserves parsed parties, individual attorney trials, categorized issues and source file paths; `validation-report.html` gives a readable progress/results table. These are automated structural and consistency checks, **not independent verification of every source field**. Findings distinguish **Missing from source**, **Not tested by this sample**, and **Needs review**. A source/sample limit is separate from an actionable parsing or association problem. The command never promotes a court's registry status automatically; source review is a separate step.

## Receipts, interrupted runs and duplicate charges

Every paid operation is reserved in `ledger.json` before submission. Its raw reply is saved before receipt interpretation. Successful identical requests reuse the saved response. An unresolved request blocks further paid operations, including retries of the same request.

```console
python -m roc reconcile runs/my-search
```

Reconciliation reads saved responses only. If no usable receipt was saved, check PACER billing before manually resolving the ledger. A stale `.run.lock` must only be removed after confirming that the previous process has ended. Never run two processes against the same run folder.

## Google Sheets without this chat

The program does not use or extract Codex's Google Drive credentials. To enable `--publish`, supply `googleOAuthFile` containing your own Google OAuth `client_id`, `client_secret`, and `refresh_token`, authorized for the Sheets API. OAuth setup is not yet packaged into the UI. Do not commit that file.

```console
python -m roc run local-config.json --publish
```

This creates a new workbook and returns its URL. It does not overwrite the existing Reuters workbook or synchronize user edits. The standalone Sheets publisher has not yet been tested against a live Google account.

## Rules and limits

- Attorney matching is exact after punctuation/case normalization, using configured aliases. Similar surnames or incompatible middle initials do not automatically match.
- A party marked PRO SE in the court's representation column is retained as self-represented, not classified as an attorney. Mixed representation cells retain actual lawyers separately.
- Explicitly labeled probation, pretrial services and interpreter contacts are retained separately from counsel, even if the court marks them as notification recipients.
- Role comes from the represented party's role, never the case filing date or case title. Additional civil roles retain their source names. Multiple supported civil roles are labeled `Multiple roles`, with each party/role preserved in evidence. The old `team` JSON key remains a compatibility alias.
- Prosecution is recognized when the matched attorney represents the United States. Prosecution charge summaries describe defendants' charges, not charges against the government.
- Different charge profiles for multiple clients/defendants are displayed separately with labels. A missing profile never erases another defendant's available counts.
- All source count rows are copied in source order, including original, superseding and terminated entries. Labels such as `1`, `1s` and `1r` are preserved; ROC does not infer which indictment is operative. Missing counts display `Not listed in source`; unfamiliar layouts or conflicting attorney roles remain items needing review.
- No AI fallback exists. Unsupported cases remain unresolved.
- Labels are included for all 94 registered district courts. Civil Nature of Suit descriptions still have a small initial reference map. Unknown codes are retained and flagged; `courtLabels` can extend display labels without enabling retrieval or changing court-identity checks.
- The first release covers case indexing and docket enrichment. It does not yet reproduce the earlier Hochman plaintiff roster, cross-case person harmonization or defendant-frequency analysis.

## Validation and project layout

`tests/` contains synthetic cases for defense, prosecution, civil sides, unrelated co-defendants, conflicting aliases, superseded counts, incomplete tables, safe export, request caching, budgets, failed login, and receipt recovery. The saved real two-docket pilot is tested locally and is intentionally excluded from Git.

The September 29, 2026 live acceptance run began with a fresh API search, without saved index or docket inputs. It returned 433 attorney records grouped into 334 cases. After form-handling fixes, an explicit resume reused the paid search responses and the in-memory API session, retrieved exactly the two automatically selected reports, and exported the index. Receipts totaled $3.30: $0.90 for the search and $0.30/$2.10 for the reports. Both reports established the lawyer's defense role. One supplied four structured counts, including two dismissed counts; the other supplied no counts and was correctly flagged for review. Counts in the index describe the case's charges, including terminated counts, and do not assert that every charge remains pending. Dispositions remain in the evidence.

The first September 30 expansion batch completed 28 reports across 14 additional courts for $16.10 under a $90 cap. Under the source-copy count policy, offline reprocessing leaves 23 reports passing all field checks and five with untested capabilities, with no items needing review. The [first validation report](docs/district-validation-2026-09-30.md) records the original results; [counts and findings](docs/counts-and-findings.md) records the policy update.

The second expansion batch completed 148 reports across the remaining 74 state districts for $81.00 under a $150 cap. This includes 156 API discovery pages ($15.60) and 149 docket receipts ($65.40): eight discovery pages and one Guam report preceded the user's territory exclusion. Removing those courts preserved all charges and original scope history. Under the source-copy count policy, offline reprocessing leaves **100 reports passing all field checks, 48 with source/sample limits and zero needing review**. Nine of those 48 have missing structured counts; all 48 also leave a capability untested. All 90 state/D.C. districts retain reviewed samples; 88 have civil and criminal samples, while SDNY and New Jersey retain their original criminal samples. See the [completed coverage report](docs/district-validation-remaining-2026-09-30.md) and [policy update](docs/counts-and-findings.md). The two expansion rounds together cost $97.10. Real reports, receipts, credentials and generated case data remain outside version control; unfamiliar layouts and conflicting associations still produce review flags.

The browser regression tests use fictional local forms and block network requests. They check the keyboard-driven case finder, main-case versus defendant-subcase selection, removal of default date/document limits, inclusion of parties/counsel, and exclusion of document purchases. A full retriever test routes registered court origins to local fictional forms and receipts, checks client-specific parsing and cache reuse, and never connects to those courts. Run them with `ROC_BROWSER_TESTS=1` after installing the optional Playwright runtime; GitHub Actions includes them. They validate form handling, not live court coverage.

| Module | Purpose |
|---|---|
| `roc/pacer.py` | Official authentication and PCL pagination |
| `roc/courts.py`, `roc/district_courts.json` | District registry, origin checks and explicit validation policy |
| `roc/retrieve.py` | Independent court-web report retrieval |
| `roc/select.py` | Bounded automatic docket selection from any index |
| `roc/store.py` | Durable reservations, cache, receipts, run lock |
| `roc/index.py` | Case normalization and deduplication |
| `roc/docket.py` | Parties, counsel, roles, counts and charge summaries |
| `roc/review.py` | Categorized source omissions, untested capabilities and review items |
| `roc/output.py` | XLSX/CSV/HTML and optional Google Sheets |
| `roc/cli.py` | Single runnable workflow |
| `roc/workspace.py` | Separate search, preview, explicit selection, recovery and cumulative export controller |
| `roc/interface.py`, `roc/ui/` | Local browser interface, sign-in dialog and graceful Stop ROC control |
| `roc/connection.py`, `roc/desktop.py` | In-memory API connection, windowless launcher and reopening a running app |

Public interface references: [PACER authentication API](https://pacer.uscourts.gov/sites/default/files/files/PACER%20Authentication%20API-2025_v2_0.pdf), [PCL API](https://pacer.uscourts.gov/sites/default/files/files/PCL-API-08-2026-1.pdf), [Playwright](https://playwright.dev/python/docs/intro), [Google Sheets API](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/batchUpdate).

Court cookie naming is case-sensitive: the authentication JSON property is `nextGenCSO`, while the court cookie is `NextGenCSO` (also used by [Juriscraper's PACER session implementation](https://github.com/freelawproject/juriscraper/blob/main/juriscraper/pacer/http.py)). ROC scopes that cookie to the selected court host, preserves it only in memory, and stops if the court redirects to login.
