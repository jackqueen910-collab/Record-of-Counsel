# Source counts and categorized findings

On September 30, 2026, the user authorized copying all counts as listed rather than resolving which indictment version controls. ROC now applies that policy to newly retrieved reports and offline replays. No PACER access or extra spending was needed for this change.

## Count output

Nature of Case copies each source count row in source order, preserving the charge wording, statute prefix, count label, range and suffix. Original and superseding rows may both appear. ROC does not deduplicate repeated count numbers or infer which charges are current. Rows from the source's Terminated Counts section receive that marker. Dispositions remain in the evidence, outside the Nature of Case text.

For example, source rows `WIRE FRAUD (1)`, `MAIL FRAUD (1s)` and `OTHER CHARGE (1r)` all remain visible. No meaning or version ranking is assigned to an unfamiliar suffix. A recognizable count-table row without a separable numeric label is also copied intact.

Only the represented defendant's rows are used for a defense lawyer. One defendant's list has no name prefix. When an attorney represents several defendants, each receives a separate labeled list; prosecution output likewise lists the case defendants separately. A missing count table for one defendant displays `Not listed in source` without suppressing another defendant's available rows. Defendant associations, source sections and dispositions are retained in JSON evidence.

The evidence's legacy `selectedCounts` key now contains **all listed source rows** per subject. `countPolicy: all-listed-source-rows` records this meaning. `countSummaries` preserves each defendant's display and field status. Optional numeric `countIds` are legacy annotations, not a version-selection rule; an empty list does not discard the source row.

## Roles

The standard Team values remain Prosecution, Criminal Defense, Civil Plaintiff and Civil Defense. Civil Petitioner, Respondent, Claimant, Amicus, Intervenor, Movant, Interested Party, Notice Party, Debtor and Creditor roles use their source role name as the Team value. `Multiple roles` covers a lawyer appearing in more than one supported civil role; evidence and cell notes identify each party and role. Conflicting prosecution/defense associations or an unsupported criminal role remain items needing review.

Self-represented parties, mediators and identified court-notification staff remain separate from counsel. An amicus is not relabeled as a plaintiff or defendant. Civil Nature of Suit can still be copied when counsel has an additional role.

## Findings

| Display category | JSON category | Meaning |
|---|---|---|
| Missing from source | `missing-source` | The report does not list a needed value, such as structured counts. Display the absence explicitly. |
| Not tested by this sample | `not-tested` | A validation sample does not exercise a capability, such as matching defense counsel. Another sample would be required to test it. |
| Needs review | `needs-review` | An unfamiliar layout, unmatched configured attorney alias or conflicting association needs investigation. |

`issues` contains category, code, message and relevant field/party context. Legacy `warnings` is reserved for items needing review. Case exports retain the existing ten columns; categorized findings appear in `review.json`, evidence and cell notes. Validation JSON/HTML separates `checks-passed`, `source-limited` and `review-needed`. This classification does not fill missing source information or claim an untested capability passed.

## Offline validation

Both expansion batches were reprocessed with networking blocked. Party/counsel associations and every raw charge, label, source section and disposition were compared with the prior saved assessments and remained unchanged. The ledgers also remained unchanged.

| Batch | Reports | All field checks passed | Source/sample limits | Needs review | Existing receipts |
|---|---:|---:|---:|---:|---:|
| First 14 districts | 28 | 23 | 5 | 0 | $16.10 |
| Remaining 74 districts | 148 | 100 | 48 | 0 | $81.00 |

Nine reports in the second batch have missing structured counts; they are included in its 48 samples with untested capabilities. The other 39 have only sample-coverage limitations. No replacement dockets were purchased. The initial two pilot reports are additional historical samples, outside this 176-report table.

Prior assessments, HTML reports and source-review records remain in each private run's `policy-history/before-all-listed-counts/`. Current results and court metadata describe the new policy; earlier validation documents retain their original findings as historical records. The live workflow still operates on arbitrary newly searched cases without depending on these fixtures.

Synthetic regression tests cover unfamiliar labels, original/superseding/terminated rows, separate defendants, partially missing profiles, additional civil roles, conflicting criminal roles, categorized output, clipped spreadsheet cells and Google Sheets request payloads. This change did not publish or edit a live Google Sheet.
