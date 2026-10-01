# Document Grabber

The local ROC interface can analyze selected, already-saved docket reports and purchase selected candidate PDFs. No chat agent or hosting is required. PACER authentication and case searches stay on the official APIs. PDF downloads use **court-web HTTP requests**, not a PACER document API. No browser login/search fallback is used.

## Scope and attribution

The first classification policy includes civil dismissal, pleadings judgment, summary judgment, judgment as a matter of law and default judgment motions; criminal dismissal, acquittal, arrest of judgment and new trial motions; and orders deciding those motions. Partial dispositive motions count. Suppression, limine, discovery, bail, sentencing, scheduling, supporting memoranda and attachments are excluded. This is a configurable-in-code first policy, not a comprehensive legal taxonomy.

The source parser reads docket history separately from party/counsel/count tables. Client names come from existing exact counsel-alias matching, not captions or entity/name-grouping rules. Each entry retains its date, docket number, full text and an allowed source document link. The model sees docket text, case type and client names; it never supplies URLs, receives PACER credentials or gets purchasing tools.

The user’s chosen attribution rule appears in the interface and export:

> Documents are selected because the docket’s “as to” field names a client represented by this attorney. This does not establish that the attorney personally authored or filed the motion. Check the filing’s signature block to confirm the attorney’s involvement. Orders are linked to those motions.

Claude returns strict JSON with entry IDs, exact evidence quotes, an as-to quote/client, related motion IDs and explanations. ROC rejects invented/duplicate entry IDs or absent evidence quotes. Unsupported attribution becomes a review row. Orders need a qualified motion and an explicit reference to its docket number to be downloadable; ambiguous links remain for review. These checks do **not** prove the model correctly interpreted every entry. Candidate discovery can miss documents and a client being mentioned under “as to” is only the agreed proxy. There is no claim of personally verified authorship or completeness.

Review rows and text-only/unsupported-link entries are exported but cannot be purchased automatically. Main document links only: `/doc1/<digits>` on the case’s registered HTTPS court origin, without query/fragment. No attachment selection, combined PDFs, browser scripts, transcripts, documents linked from model output or arbitrary external URLs.

## Models and spending

Anthropic Messages API, model IDs `claude-sonnet-5-5` (default) and `claude-opus-5-5` (explicit choice). Standard rates checked October 1, 2026: Sonnet $2 input / $10 output per million tokens; Opus $4 / $20. See [official models](https://platform.claude.com/docs/en/models/overview) and [structured output contract](https://platform.claude.com/docs/en/build-with-claude/structured-outputs).

One model request per selected case, with the complete parsed entry list. Requests above 750,000 encoded bytes stop rather than truncating the docket. Maximum output is 16,384 tokens; a truncated/refused reply is not used, even if usage is billable. The local cost preview reserves one token per encoded request byte plus 8,192 protocol/schema tokens, plus maximum output, rounded up to cents per request. This is deliberately conservative, not an exact token quote or an external billing guarantee. Actual token usage at the configured rates is saved, rounded up per request; Anthropic’s invoice is authoritative. No prompt cache, batch requests, model tools, hidden escalation or automatic retries.

Analysis starts only after a matching preview ID and fresh cap. API keys stay in memory and are cleared on disconnect/stop. No paid key-validation call occurs on entry. Saved response fingerprints include source content, clients, prompt/schema/policy and model. Repeating an unchanged analysis reuses the response; changing models is a new explicit paid choice. Bad JSON or uncertain classification never triggers an automatic second model call.

PDF selections have their own preview/allowance, separate from both AI and original PACER search/docket spending. Each new document reserves $3. Before the only purchase POST, ROC verifies the court price screen’s case/document number, price and form contract; unfamiliar forms stop. It records the accepted price screen before submitting. A completed direct PDF plus the accepted price establishes the document ledger entry; this is labeled as **accepted court price**, not a fabricated independent PACER billing receipt. Actual PACER billing remains authoritative. Price above $3 and transcript/attachment forms are refused before purchase.

## Persistence and recovery

Everything is under the run’s `documents/` directory (ignored by Git):

- `ai-ledger.json`, `documents-ledger.json`: separate persistent allowances, reservations, usage/prices and completion states.
- `responses/`: authentic Claude replies saved before usage/schema validation.
- `results.json`: latest successfully validated candidate list per case, with full source entries.
- `analyses/`: archived validated analyses, retained when a new model/source replaces the current candidates. Preview approvals bind to the analysis policy and full candidate evidence, not just a document number.
- `prices/`: received court landing/price screens.
- `pdfs/`: completed PDFs and unexpected purchase response bytes retained for review.
- `document-bundle.zip`: evidence, CSV index, methodology, spending records and PDFs grouped by court/case.

Bundles retain earlier purchased PDFs even when a later analysis no longer selects them. Their original analysis and spending records remain available for review.

Pause/stop acts between requests. An in-flight request settles first; closing the tab does not stop it. Resume preserves the original selection and absolute limit, and reuses completed responses/PDFs. Restart does not restore API keys or PACER sessions. Reopening a run never resumes work.

A pending paid request blocks further requests in its ledger, including cap changes. Do not delete it or assume a timeout means no charge. Current recovery for uncertain AI/PDF charges is **manual inspection of saved responses and provider billing**; the existing “Check saved receipts” command only reconciles PCL/docket receipts. There is no automatic document/AI reconciliation UI in this first version. A recognized HTML landing page rejected before any purchase POST is marked not submitted; an explicit retry after an adapter fix may reopen that price screen. A purchase POST is never automatically repeated. Previously purchased but missing files must be restored, not repurchased.

## Validation status

Offline tests cover fictional civil/criminal entry parsing; multiple defendants; wrong-client and missing-as-to attribution; exact evidence/reference checks; text-only orders; dates not mistaken for motion numbers; stale quotes; budgets; cache reuse; failed/truncated replies; unknown price forms; no duplicate purchases; browser interaction, credential persistence and ZIP export. Tests mock Anthropic and court responses and block external browser traffic.

These tests validate orchestration, not Claude’s classification accuracy or real court PDF layouts. Before wider use, run an explicitly budgeted pilot against saved real dockets: manually label relevant entries, compare Sonnet’s precision/recall, review client matches and order links, and purchase one selected motion plus one order to verify the price/download paths and real billing. No such paid pilot was performed during implementation.

October 1, 2026 verification: the complete local suite passed all 156 tests, including browser tests; the distributable wheel includes the new modules/UI. A read-only parser check against the two previously purchased Agnifilo reports found 12 entries (NJ) and 167 entries (SDNY), with one matched client per report and no unfamiliar-row warnings. Those reports contained 6 and 118 supported document landing links. This check reused local HTML and made no PACER or Claude calls; it does not establish that those links download successfully.
