# Record of Counsel (ROC)

An on-demand, deterministic PACER workflow. No AI model, Codex session, chat connector, or web host is required to run the program.

**Current release: 0.1 prototype.** The complete saved-data workflow runs independently. Official API transport and the court-web retrieval adapter are implemented; the new standalone live retrieval path still needs its own authorized, paid acceptance test. Do not mistake offline tests for nationwide live coverage.

## What it does

1. Reads saved official PCL responses, or authenticates and searches the official PCL API.
2. Groups records by court and normalized case number, retaining prosecution and defense records.
3. Optionally retrieves explicitly selected docket reports using court websites. This is **web retrieval**, not a document-retrieval API. Current adapters target SDNY and District of New Jersey forms.
4. Matches explicit attorney-name aliases to the docket's party/counsel table.
5. Extracts the matched client's counts, preserves indictment versions, and summarizes the latest supported version without duplicate historical counts.
6. Writes a ten-column case index as XLSX, CSV and readable HTML, plus detailed evidence and review JSON.
7. Optionally creates a fresh Google Sheet using the operator's own Google OAuth grant.

The main output columns are Case number, Case title, Case Type, Team, Court, District, Date filed, Nature of Case, Status (PACER), and PACER link. Nature of Case contains charges and count numbers only. Client names, source details and dispositions stay in notes/evidence. Long charge cells stay on one line; HTML and Google Sheets clip them.

## Run the free offline demo

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
- Add explicit `retrieveDockets` selections with `courtId` and `caseNumber` to retrieve reports. No full-index docket sweep occurs by default.
- Sign-in prompts for PACER username, password, optional client code and MFA. Passwords/MFA/tokens are not written to disk. Login failures stop; there is no browser-login fallback.
- The program purchases no underlying filings. It selects full-case docket reports with parties/counsel and terminated parties included.

Install the optional court-browser dependency only on a machine where that installation is permitted:

```console
python -m pip install -e ".[live]"
python -m playwright install chromium
python -m roc run local-live.json --live
```

This is the development/runtime setup, not a decision about installation or deployment on colleagues' managed computers. Hosting and colleague distribution remain a separate decision.

Unexpected forms, court redirects to login, ambiguous case choices and missing receipts stop the process. The narrow court adapter intentionally does not guess through unfamiliar screens. New courts require adapter checks and tests.

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
- Team comes from the represented party's role, never the case filing date or case title.
- Prosecution is recognized when the matched attorney represents the United States. Prosecution charge summaries describe defendants' charges, not charges against the government.
- Different charge profiles for multiple clients/defendants are flagged for review rather than flattened into misleading count numbers.
- Earlier indictment versions remain in evidence. Unresolved mixed versions, missing counts and unfamiliar layouts produce review items.
- No AI fallback exists. Unsupported cases remain unresolved.
- Court labels and civil Nature of Suit descriptions have a small initial reference map. Unknown codes are retained and flagged; `courtLabels` can extend the map without changing code.
- The first release covers case indexing and docket enrichment. It does not yet reproduce the earlier Hochman plaintiff roster, cross-case person harmonization or defendant-frequency analysis.

## Validation and project layout

`tests/` contains synthetic cases for defense, prosecution, civil sides, unrelated co-defendants, conflicting aliases, superseded counts, incomplete tables, safe export, request caching, budgets, failed login, and receipt recovery. The saved real two-docket pilot is tested locally and is intentionally excluded from Git.

| Module | Purpose |
|---|---|
| `roc/pacer.py` | Official authentication and PCL pagination |
| `roc/retrieve.py` | Independent court-web report retrieval |
| `roc/store.py` | Durable reservations, cache, receipts, run lock |
| `roc/index.py` | Case normalization and deduplication |
| `roc/docket.py` | Parties, counsel, roles, counts and charge summaries |
| `roc/output.py` | XLSX/CSV/HTML and optional Google Sheets |
| `roc/cli.py` | Single runnable workflow |

Public interface references: [PACER authentication API](https://pacer.uscourts.gov/sites/default/files/files/PACER%20Authentication%20API-2025_v2_0.pdf), [PCL API](https://pacer.uscourts.gov/sites/default/files/files/PCL-API-08-2026-1.pdf), [Playwright](https://playwright.dev/python/docs/intro), [Google Sheets API](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/batchUpdate).
