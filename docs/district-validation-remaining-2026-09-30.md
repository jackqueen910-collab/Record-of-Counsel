# Completed district expansion — September 30, 2026

**Policy update:** The later [source-counts change](counts-and-findings.md) copies all listed counts and separates source/sample limits from review items. Reprocessing yields **100 field-check passes, 48 source/sample limits and no items needing review**, with the same $81.00 receipts. The findings and table below record the original validation policy.

ROC completed **148 of 148 selected full docket reports across 74 additional districts**, with **$81.00 in confirmed receipts under the $150 cap** and no unresolved charges. All **90 primary district courts in the states and D.C. now have reviewed live samples** and are eligible for the normal bounded retrieval workflow. The four territorial districts remain unverified and skipped by default.

The two expansion rounds cost **$97.10**: $16.10 for the first 14 districts and $81.00 for this batch. This total excludes the original SDNY/New Jersey pilot and earlier attorney-index work.

## What ran

Authentication used the official PACER API. Case discovery used the official PCL case API: one first page per court and case type, using an explicit September 2025 filing window. ROC selected one civil and one criminal case per retained court, then retrieved full reports from the court websites with the API-authenticated session. This court-web step is not a document-retrieval API. No underlying documents or replacement sample reports were purchased.

| Receipt category | Requests | Cost |
|---|---:|---:|
| Official PCL discovery pages | 156 | $15.60 |
| Court-web docket reports | 149 | $65.40 |
| Total | 305 | **$81.00** |

Eight discovery pages and one Guam civil report were purchased before the user excluded Guam, the Northern Mariana Islands, Puerto Rico and the Virgin Islands. Those purchases cost $1.00 and remain in the same ledger. The final 74-court scope accounts for the other $80.00. The original plan/results were preserved in `scope-history`; scope reduction did not reset the spending cap or discard purchased responses.

## What validation establishes

The saved reports exercised actual court navigation, case selection, report forms, receipts and report identity checks. Source review compared party/counsel blocks, civil Nature of Suit and per-defendant raw count rows with the parser. A separate regular-expression inventory compared complete party/counsel membership and raw counts/dispositions in all 148 reports; after corrections it found no differences. All 29 source PRO SE entries also matched the self-representation classification. The development assistant inspected source excerpts, with spot-checks for long rosters; this was not a second human review of every field.

After offline reprocessing, **94 reports pass the automated field checks and 54 retain review flags**. These flags are documented sample or feature limits, not failed report downloads. They include absent counsel, absent structured counts, nonstandard party roles, unfamiliar count suffixes, mixed indictment versions and different charge profiles across defendants. Source information remains in the evidence; ROC does not invent the missing values or merge incompatible count profiles.

All 74 newly reviewed courts have registry entries containing their two source hashes, exercised roles, review date and specific limits. Together with the earlier 16 courts, this provides 90 sampled courts: 88 with civil and criminal reports, plus the original criminal samples for SDNY and New Jersey. `sample-verified` describes these observations, not exhaustive coverage of every case, role or layout in a court.

## Fixes and retained boundaries

- Exact registered-court HTTP links returned by PCL are normalized to HTTPS before retrieval. Cross-court links, credential-bearing URLs, explicit ports and lookalike hosts remain rejected; raw API links remain saved.
- Guam's observed heading was recognized without weakening district identity checks. Its previously purchased report remains outside the final validation scope.
- Resume now permits removing courts or case types while preserving scope history and the original receipts. Additions, substitutions, changed dates or changed methods still require a new run folder. Unresolved charges still block purchases.
- Explicit probation and marshal notification accounts are kept separately from counsel. Claimant, intervenor, amicus and notice-party roles are retained without forcing a standard Team.
- Mediator rows are retained as court contacts, even when the court uses a `represented by` column. Underlined role headings are distinguished from a party whose name is itself “Mediator.”
- Unfamiliar count labels such as `1r` retain their raw charge and disposition and produce a review flag. ROC does not guess their version ordering or publish a partial charge summary.

The parser changes were replayed against the earlier 28 expansion reports without changing their parsed parties/counsel/counts. The original two pilot reports also parsed successfully. Saved PACER data is optional evidence and regression input, not a dependency of the live retrieval architecture.

## Coverage by district

“Both” means the two standard attorney roles for that case type were exercised. “Neither” means this sample does not establish those roles; it does not mean the court cannot supply them. The final column covers the two docket receipts only, excluding discovery pages. Every row has one civil and one criminal report.

| District | Civil attorney roles | Criminal attorney roles | Sample limits | Dockets |
|---|---|---|---|---:|
| District of Alaska | Plaintiff | Both | Civil sample does not exercise Civil Defense. Source roles Claimant are preserved without assigning a standard Team. | $0.90 |
| Middle District of Alabama | Both | Both | No field gap in these samples. | $3.30 |
| Northern District of Alabama | Both | Both | No field gap in these samples. | $0.70 |
| Southern District of Alabama | Plaintiff | Neither | Civil sample does not exercise Civil Defense. Criminal sample does not exercise Criminal Defense, Prosecution. This sample does not establish a resolved client-specific criminal count summary. | $0.30 |
| Eastern District of Arkansas | Both | Both | No field gap in these samples. | $0.90 |
| Western District of Arkansas | Both | Prosecution | Criminal sample does not exercise Criminal Defense. This sample does not establish a resolved client-specific criminal count summary. | $0.30 |
| District of Colorado | Both | Both | No field gap in these samples. | $0.80 |
| District of Connecticut | Defense | Both | Civil sample does not exercise Civil Plaintiff. | $1.00 |
| District of Delaware | Neither | Both | Civil sample does not exercise Civil Defense, Civil Plaintiff. | $1.00 |
| Northern District of Florida | Neither | Both | Civil sample does not exercise Civil Defense, Civil Plaintiff. | $1.10 |
| Middle District of Georgia | Neither | Both | Civil sample does not exercise Civil Defense, Civil Plaintiff. | $0.80 |
| Northern District of Georgia | Defense | Both | Civil sample does not exercise Civil Plaintiff. | $0.90 |
| Southern District of Georgia | Both | Both | No field gap in these samples. | $1.60 |
| District of Hawaii | Defense | Both | Civil sample does not exercise Civil Plaintiff. | $2.10 |
| Northern District of Iowa | Neither | Both | Civil sample does not exercise Civil Defense, Civil Plaintiff. Source roles Respondent are preserved without assigning a standard Team. | $0.80 |
| Southern District of Iowa | Both | Both | No field gap in these samples. | $0.80 |
| District of Idaho | Both | Both | No field gap in these samples. | $0.80 |
| Central District of Illinois | Both | Both | No field gap in these samples. | $1.30 |
| Southern District of Illinois | Both | Both | Source roles Amicus are preserved without assigning a standard Team. | $1.40 |
| Northern District of Indiana | Defense | Both | Civil sample does not exercise Civil Plaintiff. | $1.20 |
| Southern District of Indiana | Both | Prosecution | Criminal sample does not exercise Criminal Defense. Source supplies no structured counts for at least one defendant; no charge summary inferred. This sample does not establish a resolved client-specific criminal count summary. | $0.60 |
| District of Kansas | Both | Both | No field gap in these samples. | $1.20 |
| Eastern District of Kentucky | Both | Both | No field gap in these samples. | $0.90 |
| Western District of Kentucky | Both | Both | No field gap in these samples. | $0.70 |
| Eastern District of Louisiana | Defense | Both | Civil sample does not exercise Civil Plaintiff. | $0.80 |
| Middle District of Louisiana | Both | Both | No field gap in these samples. | $0.80 |
| Western District of Louisiana | Neither | Both | Civil sample does not exercise Civil Defense, Civil Plaintiff. | $0.50 |
| District of Maryland | Neither | Neither | Civil sample does not exercise Civil Defense, Civil Plaintiff. Criminal sample does not exercise Criminal Defense, Prosecution. This sample does not establish a resolved client-specific criminal count summary. | $0.20 |
| District of Maine | Defense | Both | Civil sample does not exercise Civil Plaintiff. | $1.10 |
| Eastern District of Michigan | Neither | Both | Civil sample does not exercise Civil Defense, Civil Plaintiff. | $0.50 |
| Western District of Michigan | Both | Both | No field gap in these samples. | $0.70 |
| District of Minnesota | Both | Prosecution | Criminal sample does not exercise Criminal Defense. This sample does not establish a resolved client-specific criminal count summary. | $0.50 |
| Eastern District of Missouri | Both | Both | Source count labels use an unsupported suffix (r); raw charges and dispositions retained, summary withheld. This sample does not establish a resolved client-specific criminal count summary. | $1.10 |
| Western District of Missouri | Plaintiff | Both | Civil sample does not exercise Civil Defense. | $0.60 |
| Northern District of Mississippi | Both | Both | No field gap in these samples. | $0.60 |
| Southern District of Mississippi | Both | Both | No field gap in these samples. | $1.00 |
| District of Montana | Both | Both | Unresolved count versions retained for review; no combined summary inferred. This sample does not establish a resolved client-specific criminal count summary. | $0.80 |
| Eastern District of North Carolina | Both | Both | No field gap in these samples. | $0.70 |
| Middle District of North Carolina | Neither | Both | Civil sample does not exercise Civil Defense, Civil Plaintiff. | $1.00 |
| Western District of North Carolina | Both | Both | No field gap in these samples. | $0.40 |
| District of North Dakota | Both | Both | No field gap in these samples. | $2.00 |
| District of Nebraska | Both | Prosecution | Criminal sample does not exercise Criminal Defense. Source supplies no structured counts for at least one defendant; no charge summary inferred. This sample does not establish a resolved client-specific criminal count summary. | $0.60 |
| District of New Hampshire | Plaintiff | Neither | Civil sample does not exercise Civil Defense. Source roles Respondent are preserved without assigning a standard Team. Criminal sample does not exercise Criminal Defense, Prosecution. This sample does not establish a resolved client-specific criminal count summary. | $0.50 |
| District of New Mexico | Neither | Both | Civil sample does not exercise Civil Defense, Civil Plaintiff. Source roles Petitioner, Respondent are preserved without assigning a standard Team. | $0.80 |
| District of Nevada | Defense | Both | Civil sample does not exercise Civil Plaintiff. Unresolved count versions retained for review; no combined summary inferred. | $1.50 |
| Northern District of New York | Both | Both | No field gap in these samples. | $1.80 |
| Western District of New York | Both | Both | No field gap in these samples. | $0.50 |
| Northern District of Ohio | Neither | Both | Civil sample does not exercise Civil Defense, Civil Plaintiff. | $0.60 |
| Southern District of Ohio | Plaintiff | Both | Civil sample does not exercise Civil Defense. | $0.60 |
| Eastern District of Oklahoma | Both | Both | No field gap in these samples. | $0.50 |
| Northern District of Oklahoma | Both | Both | No field gap in these samples. | $0.80 |
| Western District of Oklahoma | Both | Both | No field gap in these samples. | $0.40 |
| District of Oregon | Defense | Both | Civil sample does not exercise Civil Plaintiff. | $0.50 |
| Middle District of Pennsylvania | Both | Both | No field gap in these samples. | $0.50 |
| Western District of Pennsylvania | Defense | Both | Civil sample does not exercise Civil Plaintiff. | $1.10 |
| District of Rhode Island | Both | Both | No field gap in these samples. | $0.70 |
| District of South Carolina | Plaintiff | Both | Civil sample does not exercise Civil Defense. | $2.40 |
| District of South Dakota | Both | Both | No field gap in these samples. | $0.60 |
| Eastern District of Tennessee | Neither | Both | Civil sample does not exercise Civil Defense, Civil Plaintiff. Source supplies no structured counts for at least one defendant; no charge summary inferred. This sample does not establish a resolved client-specific criminal count summary. | $0.40 |
| Middle District of Tennessee | Defense | Prosecution | Civil sample does not exercise Civil Plaintiff. Criminal sample does not exercise Criminal Defense. Source supplies no structured counts for at least one defendant; no charge summary inferred. This sample does not establish a resolved client-specific criminal count summary. | $0.40 |
| Western District of Tennessee | Neither | Both | Civil sample does not exercise Civil Defense, Civil Plaintiff. | $0.70 |
| Eastern District of Texas | Both | Both | No field gap in these samples. | $1.20 |
| Northern District of Texas | Defense | Both | Civil sample does not exercise Civil Plaintiff. Source supplies no structured counts for at least one defendant; no charge summary inferred. This sample does not establish a resolved client-specific criminal count summary. | $0.60 |
| District of Utah | Both | Prosecution | Criminal sample does not exercise Criminal Defense. Source supplies no structured counts for at least one defendant; no charge summary inferred. This sample does not establish a resolved client-specific criminal count summary. | $0.40 |
| Eastern District of Virginia | Both | Both | No field gap in these samples. | $1.40 |
| Western District of Virginia | Both | Both | Different defendant charge profiles remain separate; no single prosecution summary inferred. | $2.40 |
| District of Vermont | Neither | Both | Civil sample does not exercise Civil Defense, Civil Plaintiff. | $0.60 |
| Eastern District of Washington | Both | Both | Different defendant charge profiles remain separate; no single prosecution summary inferred. | $1.10 |
| Western District of Washington | Plaintiff | Both | Civil sample does not exercise Civil Defense. | $0.60 |
| Eastern District of Wisconsin | Plaintiff | Both | Civil sample does not exercise Civil Defense. | $0.60 |
| Western District of Wisconsin | Both | Both | No field gap in these samples. | $0.90 |
| Northern District of West Virginia | Plaintiff | Neither | Civil sample does not exercise Civil Defense. Criminal sample does not exercise Criminal Defense, Prosecution. This sample does not establish a resolved client-specific criminal count summary. | $0.20 |
| Southern District of West Virginia | Neither | Prosecution | Civil sample does not exercise Civil Defense, Civil Plaintiff. Criminal sample does not exercise Criminal Defense. This sample does not establish a resolved client-specific criminal count summary. | $0.20 |
| District of Wyoming | Plaintiff | Both | Civil sample does not exercise Civil Defense. | $0.40 |

## Evidence and reproduction

Private artifacts are in `runs/remaining-district-validation-2026-09-30/`: the original responses, ledger, scope history, original live assessments, refreshed assessments, HTML report, source-review notes and comparison results. Every one of the 305 receipts was re-read and reconciled to the ledger. Offline review and reprocessing made no PACER requests and left the ledger unchanged.

Real case data, reports, receipt details and credentials remain outside version control. The public registry contains only court-level validation metadata and source hashes. Synthetic tests cover the retrieval boundaries and parser fixes; the batch command never promotes courts automatically.
