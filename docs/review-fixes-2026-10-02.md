# Review implementation — October 2, 2026

This follows the [review of commit 4a89d7f](code-review-2026-10-02.md). The review is a historical snapshot; the behavior below supersedes its findings.

## Correctness and spending

- Party boundaries no longer depend on underlining. An unfamiliar role with a recognizable party row is retained and flagged, and ends the previous party's count section. Service-list contacts do not establish representation.
- A document landing response is treated as unbilled only when it matches the supported price-confirmation form. Unknown HTML, receipt/viewer wrappers and direct PDFs retain a pending reservation and their raw evidence; explicit retries remain blocked.
- Civil docket enrichment leaves an absent Nature of Suit value empty internally, retaining any known PCL value in the case index. The source omission remains in data notes.

## Publication and recovery

- Each export build lives in `output/generations/<id>/`. CSVs, workbook, HTML, source data and result metadata finish before one atomic `output/current.json` pointer publishes them. A failed build leaves the previous complete generation visible and blocks downloads with a Resume notice. Resume reuses saved searches and purchased dockets. Older flat output folders remain readable.
- Process-held Windows/POSIX locks release on process exit. The permanent `.guard` file must not be deleted while ROC is running. Legacy PID markers are recovered only when their process is confirmed absent. Reopening does not resume paid work or clear pending receipts.
- Normal workflow Resume recognizes the same narrowly supported, unsubmitted full-report confirmation used by court validation. It verifies the original case, scope and remaining allowance before continuing the existing reservation. Already-submitted forms, unknown responses and changed selections still stop.

Tests cover failure during workbook generation and pointer publication, abrupt process exit with nested locks and a pending receipt, legacy lock ownership, confirmation continuation and duplicate-purchase prevention. All fixtures are synthetic and offline.

No PACER or model charges were incurred by this repair pass. Live model evaluation, live PDF acceptance and Google Sheets publication remain separate acceptance work.
