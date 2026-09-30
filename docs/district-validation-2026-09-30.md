# District validation — September 30, 2026

This report records the first expansion batch. The later [completed expansion report](district-validation-remaining-2026-09-30.md) brings sampled coverage to all 90 state and D.C. districts.

ROC retrieved one civil and one criminal docket in each of 14 additional districts: **28 of 28 reports, $16.10 in confirmed receipts against a $90 cap**, with no pending charges. Official PACER API authentication and PCL case discovery cost $2.80; the court-web docket reports cost $13.30. No underlying filings or replacement samples were purchased.

The standalone program selected cases from one official API results page per court/type, using an explicit September 2025 filing window. This was a bounded compatibility sample, not a comprehensive case search. Court websites supplied full reports, including parties and counsel, using the API-authenticated session. This is not a document-retrieval API.

## Source review and coverage

Saved source headings, party/counsel blocks, civil Nature of Suit and criminal count rows were inspected separately from the automated assessment. After offline parser corrections, 22 reports passed automated checks; six retained the limits listed below. Source review was performed by the development assistant, not an independent human legal reviewer.

All 14 courts are now `sample-verified` and eligible for the normal bounded retrieval workflow. This means the saved civil/criminal samples worked; it does not establish universal layout coverage or exercise every role in every court. Together with the earlier SDNY and New Jersey criminal samples, the registry now has 16 sampled courts and 78 unverified courts.

| District | Civil attorney roles exercised | Criminal result | Docket cost for both samples |
|---|---|---|---:|
| Eastern New York | Defense only; plaintiff self-represented | Prosecution and defense matched; unresolved mixed indictment versions for two defendants remain flagged | $2.30 |
| District of Columbia | Plaintiff and defense | Prosecution, defense and terminated count checked | $1.30 |
| Northern California | Plaintiff and defense | Prosecution, defense and count checked | $0.70 |
| Southern Florida | Neither; no listed attorney | Prosecution, defense and counts checked | $0.60 |
| Central California | Plaintiff and defense | Prosecution, defense and count checked | $0.90 |
| Northern Illinois | Plaintiff only; no defense counsel in MDL member report | Prosecution, defense and counts checked; court staff excluded | $0.80 |
| Southern Texas | Plaintiff and defense | Prosecution, defense and count checked; interpreter excluded | $0.90 |
| Western Texas | Plaintiff and defense | Prosecution, defense and superseding count versions checked | $1.00 |
| Middle Florida | Plaintiff and defense | Prosecution, defense and count checked | $1.20 |
| Eastern Pennsylvania | Plaintiff and defense | Prosecution, defense and three counts checked | $0.60 |
| Arizona | Plaintiff and defense | Prosecution, defense and count checked | $0.60 |
| Southern California | Neither; plaintiff self-represented, no defense counsel listed | Prosecution, defense and superseding count checked after dismissal-wording fix | $1.00 |
| Eastern California | Government plaintiff only; no defense counsel listed | Prosecution, defense and superseding counts checked | $0.80 |
| Massachusetts | Plaintiff and defense | Prosecution, defense and count checked | $0.60 |

The five incomplete civil-role tests are limitations of the selected samples. ROC does not infer missing counsel from another case or purchase a replacement. The remaining criminal ambiguity is retained for review; raw count versions remain in evidence. Historical charges can include terminated counts and do not assert that every charge is pending.

## Fixes exercised

- A court's large-report confirmation now preserves the full request and completes its original reservation once. The saved EDNY continuation completed without repeating the initial purchase.
- State-first district headers such as “California Northern District” are matched to the same exact district, retaining cross-court identity checks.
- Self-represented parties and explicitly labeled probation, pretrial and interpreter contacts are retained separately from counsel.
- An explicit “motion to dismiss granted” disposition resolves an unmatched older indictment count. Denied, pending and not-granted motions do not.
- The Windows validation launcher now distinguishes successful completion from an early stop.

Counsel names still require explicitly configured aliases. Courts may include status suffixes, government labels and institutional law-office accounts in their representation cells. The parser preserves those source labels; listing a representation entry is not independent verification that it names an individual lawyer. Charge descriptions likewise retain unfamiliar source wording rather than inventing a shorter legal characterization.

## Evidence and reproducibility

Private run artifacts are in `runs/district-validation-2026-09-30/`: original responses, the ledger, original live assessments, refreshed assessments, the HTML report and `source-review.json`. The ledger's amounts were checked against all 56 saved receipts: 28 API pages and 28 docket reports. Offline reprocessing left the ledger unchanged and made no network requests.

The public registry records each court's date, sample count, exercised roles, limits and the two report hashes. Real reports, names, case data, receipts and credentials remain outside version control. Synthetic regression tests cover the parser and retrieval changes; live records are replayed only from the private run folder. No court is promoted automatically by the batch command.
