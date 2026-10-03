# ROC code review — October 2, 2026

Reviewed commit: `4a89d7fbf4534259d298e662008d99581a9d0d13`.

## Assessment

ROC has a sound core and does not need a rewrite. Search, court retrieval, parsing, reporting, and optional model analysis already have useful module boundaries. The spending safeguards, explicit approvals, cached responses, and account isolation are substantial work worth preserving.

The most valuable cleanup is at the boundaries between those modules: parser uncertainty, interrupted operations, publication of exports, and the browser's representation of run state. I confirmed six actionable issues with seven offline reproductions. Two deserve priority because they can produce incorrectly attributed data or release an uncertain spending reservation.

This was a review, not a repair pass. No application code, existing tests, running ROC process, PACER session, account workspace, or production credential was changed. No PACER, Google, or model requests were submitted. This section describes the review snapshot before the authorized repair pass; implementation follow-up is recorded in [review fixes](review-fixes-2026-10-02.md).

## Findings, ordered by priority

### 1. P1 — An unrecognized party boundary can silently mix defendants' counts

**Location:** [docket.py:153](C:/Users/jackq/Documents/Codex/2026-09-26/a-x20/record-of-counsel/roc/docket.py:153).

The parser starts a party only when the first cell matches its fixed role list and contains an underline element. If the next defendant heading has the same words but lacks that formatting, it does not reset the current party. Later count rows then attach to the preceding defendant. The parser can report the resulting nature field as resolved with no layout warning.

**Reproduction:** a two-defendant synthetic docket gives Jordan Lawyer only Client One. Removing `<u>` from the second defendant's heading causes the second defendant to disappear and `UNRELATED CHARGE` to appear in Client One's nature field. A separate reproduction using an underlined `ThirdParty Defendant` heading drops that client without a warning because the role is outside the fixed list.

**Recommended correction:** recognize a party boundary independently of whether its role or presentation is fully supported. Stop carrying forward the preceding party/count section at an unfamiliar boundary; retain the raw role and flag the association for review. Add regression fixtures for formatting changes and additional civil roles. Do not solve this with fuzzy attorney-name matching or guessed roles.

**Scope:** this is a demonstrated failure mode in modified fixtures, not a claim that the previous real validation reports contain this error. Reprocessing the 176 saved expansion reports reproduced all saved parsed assessments; no parser-output differences appeared in that sample.

### 2. P1 — An unknown document landing response can incorrectly release its reservation

**Location:** [document_download.py:136](C:/Users/jackq/Documents/Codex/2026-09-26/a-x20/record-of-counsel/roc/document_download.py:136).

After the document landing GET, every `purchase_form()` rejection becomes `not-submitted` with zero charged cents. That conclusion is stronger than the available evidence: an unrecognized HTML response is not proof that the GET was unbilled. Direct PDFs already remain pending, but an HTML viewer/receipt does not receive the same treatment.

**Reproduction:** a synthetic landing response containing `Transaction Receipt`, `Cost: $3.00`, and a viewer frame is rejected as an unsupported price form. Two explicit attempts make two GETs, while the document ledger reports zero spending, zero pending requests, and two `not-submitted` transactions. Nothing retries automatically; the bug is that subsequent attempts are permitted after an ambiguous response.

**Recommended correction:** mark a request unsubmitted/unbilled only after positively recognizing an unbilled response. Preserve the reservation for unfamiliar responses, including HTML viewers; use a supported authentic receipt to reconcile when possible. Preserve the original response rather than allowing another attempt to overwrite it.

**Scope:** this tests the accounting branch, not an observed live PACER charge. Document Grabber still has no live PDF acceptance test. Fix this before that paid pilot.

### 3. P2 — Interrupted exports can expose different generations of results

**Locations:** [output.py:46](C:/Users/jackq/Documents/Codex/2026-09-26/a-x20/record-of-counsel/roc/output.py:46), [workspace.py:294](C:/Users/jackq/Documents/Codex/2026-09-26/a-x20/record-of-counsel/roc/workspace.py:294), [workspace.py:483](C:/Users/jackq/Documents/Codex/2026-09-26/a-x20/record-of-counsel/roc/workspace.py:483).

CSV, evidence JSON, party reports, workbook, HTML, and final run metadata are published separately. The download freshness check compares name-rule and client-report versions, but has no completed-export generation marker. A failure partway through publication can therefore leave old and new files downloadable together.

**Reproduction:** start with an index-only run, retrieve a fictional docket, then force workbook saving to fail. The UI/evidence now contains `Criminal Defense`, while the downloadable Excel file is byte-for-byte the earlier index-only workbook. `exportsNeedRefresh` is false and both downloads are allowed. The operation is marked stopped, but that does not disclose which files are stale.

**Recommended correction:** produce exports in a new generation directory, then publish one manifest/pointer only after all required files succeed. Bind UI/download metadata to that generation. Keep the previous complete generation available with a clear label while a rebuild is incomplete. Cover failures before and during workbook writing, not just successful exports.

### 4. P2 — A crash or forced shutdown prevents a normal restart

**Locations:** [accounts.py:53](C:/Users/jackq/Documents/Codex/2026-09-26/a-x20/record-of-counsel/roc/accounts.py:53), [workspace.py:60](C:/Users/jackq/Documents/Codex/2026-09-26/a-x20/record-of-counsel/roc/workspace.py:60), [store.py:28](C:/Users/jackq/Documents/Codex/2026-09-26/a-x20/record-of-counsel/roc/store.py:28), [desktop.py:16](C:/Users/jackq/Documents/Codex/2026-09-26/a-x20/record-of-counsel/roc/desktop.py:16).

Workspace/run exclusivity is implemented through files created with `O_EXCL`. Cleanup occurs only on an orderly exit. The launcher can detect that the old HTTP server is gone, but opening the workspace then fails because the stale lock file remains. Account workspaces and individual run folders can also retain their own locks.

**Reproduction:** a separate test process constructs an account workspace and exits without cleanup. After that process has definitely ended, a new `Accounts` instance still reports that the workspace is already open.

**Recommended correction:** use a process-held OS file lock that releases on process death, while retaining a separate owner record for diagnostics. Alternatively, implement carefully verified stale-owner recovery; checking a PID alone is insufficient because PIDs can be reused. Restoring access must leave uncertain receipts pending and must not resume paid work automatically.

**Scope:** orderly shutdown/reopening works and is tested. This finding concerns abnormal exits and forced reboot. The README acknowledges manual lock removal, but that is a poor recovery path for the intended nontechnical user.

### 5. P2 — Docket enrichment can hide a civil Nature of Suit already known from PCL

**Locations:** [docket.py:265](C:/Users/jackq/Documents/Codex/2026-09-26/a-x20/record-of-counsel/roc/docket.py:265), [cli.py:126](C:/Users/jackq/Documents/Codex/2026-09-26/a-x20/record-of-counsel/roc/cli.py:126).

For a civil docket without a parsed Nature of Suit, `enrich()` returns the truthy string `Not listed in source`. The workflow then replaces the PCL nature field with that string, even when PCL supplied a usable value. The original remains in raw evidence, but disappears from the displayed case field and exports.

**Reproduction:** an index record with NOS `360` displays `Not listed in source` after enriching it with a civil docket lacking the NOS label. This also conflicts with the documented behavior that an existing civil Nature of Suit stays visible.

**Recommended correction:** retain structured value/source/status separately from display labels. Prefer a supported docket value when present; otherwise retain the PCL value and note the docket omission. Missing-source text should not be used as a substantive replacement value.

### 6. P2 — Saved full-report confirmation recovery is absent from ordinary runs

**Locations:** [cli.py:52](C:/Users/jackq/Documents/Codex/2026-09-26/a-x20/record-of-counsel/roc/cli.py:52), [retrieve.py:51](C:/Users/jackq/Documents/Codex/2026-09-26/a-x20/record-of-counsel/roc/retrieve.py:51), [validation.py:246](C:/Users/jackq/Documents/Codex/2026-09-26/a-x20/record-of-counsel/roc/validation.py:246).

The retriever supports resuming an already-saved, never-submitted large-report confirmation under its original reservation. Court validation wires this into its workflow. Ordinary CLI/UI runs instead stop immediately on `store.check_pending()`, before they can use that recognized checkpoint.

**Reproduction:** create a normal saved search and a pending docket transaction whose response is the recognized confirmation form, with no continuation-submitted marker. The helper correctly recognizes it as resumable. The normal Resume action still stops at “unresolved receipt”; Check saved receipts stops because the confirmation is not a receipt. No extra request is made, so spending remains protected, but the user cannot finish through the normal workflow.

**Recommended correction:** move this recovery decision into shared retrieval orchestration used by both validation and normal runs. Resume only the exact previously approved case/form under the existing reservation, and continue refusing already-submitted or ambiguous responses.

## Cleanup that would make the code easier to work on

### A. Put the workflow engine below both the CLI and browser controller

`Workspace` imports `run()` from `cli.py`, writes a temporary configuration file, invokes that CLI-oriented function, then reads status files to determine the outcome. `run()` also prints output intended for a terminal. This works, but couples application behavior to one presentation layer.

Extract a small engine/service entry point that accepts validated configuration, session provider, cancellation checkpoint, storage, and progress callback, then returns a structured result. CLI argument parsing and browser request handling should both call it. Keep the existing search/retrieval/parser modules. Share recovery logic rather than building a second pipeline.

### B. Make run state explicit and migrate compatibility fields at load boundaries

The UI/controller combines `state`, `active`, `lastAction`, `needsResume`, `docketCapStopped`, `indexReady`, old progress files, and multiple version flags. Each addition has a reason, but the combination is difficult to reason about. The late Document Grabber fixture failure came from an example whose files and declared state did not agree.

Use one explicit operation record with its kind, state, approved scope, progress, and resumable checkpoint. Use typed contracts for configuration, case records, party associations, and operation outcomes. Normalize old `team` fields to `role` when loading legacy data, keeping any needed compatibility serialization at that boundary. Do this incrementally; do not migrate every saved file in one pass.

### C. Split and format the browser code by responsibility

`app.js` is 766 lines but about 71 KB; 54 lines exceed 200 characters, and the longest exceeds 600. `documents.js` also relies on shared globals from `app.js`. The issue is how many concerns and mutations must be understood together, not the raw line count.

Separate API/account access, run state/progress, search forms, result filtering/sorting, docket approvals, client/name-rule views, and Document Grabber. Native JavaScript modules are sufficient. Give Document Grabber an explicit API/state interface instead of cross-file globals. Adopt consistent formatting in its own change so behavior changes remain reviewable. A new frontend framework is not necessary for this cleanup.

### D. Stop rebuilding and sending unchanged result data on every progress poll

The browser polls every 1.8 seconds. Detailed summaries re-read evidence, rebuild all client/plaintiff/defendant report projections, and return those structures even when only progress changed—or nothing changed. The browser then stringifies large parts of the response to decide whether to redraw.

A synthetic benchmark with one client and twelve opposing parties per case measured:

| Cases | Detailed JSON response | Minimum server build + serialization time, 3 runs |
|---:|---:|---:|
| 100 | 2.25 MiB | 0.020 s |
| 1,000 | 22.51 MiB | 0.245 s |
| 5,000 | 112.74 MiB | 1.602 s |

These are local synthetic measurements, not timings of the user's current searches. They exclude browser parsing/rendering and vary with hardware and party counts. They demonstrate avoidable growth: the 50-row UI page does not limit the response size.

Return small progress/status responses separately. Fetch case/client projections when their evidence or name-rule revision changes, cache those projections, and omit unused duplicated report structures from the browser payload. This is a more useful optimization than introducing a database or background service now.

### E. Share persistence primitives without combining the spending allowances

`RunStore` and `ExpenseLedger` independently implement reservations, completion, duplicate prevention, and persistence. They should continue to enforce separate search/docket, AI, and document allowances, with their different receipt semantics.

Extract only the common durable-write/locking/transaction-state primitives. Keep provider-specific evidence and reconciliation explicit. Similarly, the two different `fingerprint()` implementations can be named by purpose or centralized with an explicit serialization/version contract; blindly changing existing hashes would break saved caches and approvals.

## Tests and maintenance

- Keep the existing integration-heavy tests: budget stops, no automatic retry, exact docket selection, account isolation, and client-specific count attribution are valuable.
- Add the confirmed defect reproductions as normal regression tests when implementing their fixes. The review script currently asserts the faulty behavior deliberately; passing it confirms a finding, not a fix.
- Add Windows CI alongside Ubuntu. The product's desktop launcher, credential store, and owner setup are Windows features; Ubuntu alone skips those checks.
- Add export failure-injection tests and parser fixtures with independently stated expected party/count associations. Agreement between two functions reading the same parsed structure is not independent source validation.
- Prefer fixture builders representing valid states—completed index, partial search, pending receipt, completed export—over editing unrelated files/flags separately in each browser test.
- Add formatting checks after a dedicated formatting pass. Introduce types at the workflow/data boundaries first, rather than converting every module at once.
- Use sanitized local diagnostics with operation/stage and an error identifier. The UI should remain free of secrets and raw exceptions, but the current “Unexpected local error (OSError)” message is insufficient to locate many failures.
- Reconcile the README with the current feature set and split operational instructions from development/history. Preserve the honest distinction between sampled court support, offline tests, and live acceptance.

## Recommended sequence

1. Fix the parser boundary and uncertain document-response accounting, with regression tests. These protect correctness and spending.
2. Fix civil-source fallback and export generation consistency.
3. Improve crash recovery and put ordinary/validation confirmation recovery on the same path.
4. Extract the engine/controller boundary, then simplify operation state in small tested changes.
5. Split/format the UI and replace full-result polling with revision-based updates; add Windows CI.

Each stage should be independently reviewable. Keep the existing file-backed architecture for now. This review does not justify a framework migration, database migration, hosting change, fuzzy identity matching, or a change in PACER access method.

The original 1–3 hour cleanup estimate would cover a focused first tranche, not every item above with robust recovery tests. A reasonable next scope is the two P1 fixes plus the civil-value regression, followed by a separate pass on export/recovery and structural cleanup.

## Evidence and limitations

- Read the production Python modules, browser scripts, server/account boundaries, build configuration, documentation, and relevant tests. Scope includes the CLI, browser workflow, PACER transports, parsers, exports, budgets, account separation, and Document Grabber.
- Existing suite: 196 tests run. The restricted execution environment passed 194 and blocked two Windows integrations (Credential Manager and Tcl/Tk). Both passed when rerun with native Windows access, without code changes. All 196 existing tests therefore passed across those runs.
- Seven additional synthetic checks confirmed the six findings above. No application or checked-in test files were modified.
- Reparsed 176 previously purchased expansion reports containing 845 parsed party blocks; all matched their saved parsed assessments. This is a regression comparison, not a fresh independent audit of every party or count in those sources.
- Measured detailed-result serialization on synthetic 100/1,000/5,000-case datasets.
- No paid requests, live model evaluation, live PDF download, or Google Sheets publication was performed. Those previously documented acceptance gaps remain.
- This is a broad engineering review, not a claim of exhaustive security verification or support for every court layout.

Local evidence: [reproduction script](C:/Users/jackq/Documents/Codex/2026-09-26/a-x20/record-of-counsel/runs/code-review/reproduce_findings.py), [reproduction results](C:/Users/jackq/Documents/Codex/2026-09-26/a-x20/record-of-counsel/runs/code-review/reproductions.log), [suite log](C:/Users/jackq/Documents/Codex/2026-09-26/a-x20/record-of-counsel/runs/code-review/full-suite.log), [Windows recheck](C:/Users/jackq/Documents/Codex/2026-09-26/a-x20/record-of-counsel/runs/code-review/windows-recheck.log), [saved-source comparison](C:/Users/jackq/Documents/Codex/2026-09-26/a-x20/record-of-counsel/runs/code-review/saved-source-check.json), [projection benchmark](C:/Users/jackq/Documents/Codex/2026-09-26/a-x20/record-of-counsel/runs/code-review/projection-benchmark.json).
