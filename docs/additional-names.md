# Additional names in attorney and litigant searches

Both tabs offer **Additional names → Add another name**, with up to 30 optional rows. Last name comes first; litigants use **Last name / Entity name** with an optional first name. Attorney rows require both first and last names. Each tab keeps its own drafts; account sign-out/switch clears them. Remove deletes only an unsent form row, not earlier saved results.

Each supplied name creates an official PCL API query (`aty` for attorneys, `pty` for litigants). All names use the same court/date filters, PACER connection and run spending cap. Identical first/last pairs after trimming are searched once; other variants are kept as separate queries. There is no fuzzy expansion, automatic subsidiary discovery, changed search method or extra authentication per name. PCL prefix matching is unchanged.

Additional queries can add search charges even when their cases overlap. The form shows the number of distinct name queries and explains the shared cap. ROC executes them sequentially using the existing request reservations, authentic receipts and cache. Progress identifies the current name and shows cumulative spending for the launch and run. No docket or document is purchased by submitting this form.

Attorney names also become counsel-match aliases for docket parsing, Clients reports and Document Grabber source extraction. **Docket-only name spellings** retains the old `aliases` field and its original behavior: no additional searches. A middle-name spelling can go there because the current PCL queries use first and last names only. Old saved configurations are never silently expanded into new paid queries.

## Results and recovery

Cases are deduplicated by normalized court/case number. Identical API rows across queries are stored once in the combined index, with all matching query names. Distinct returned names/roles and case associations remain separate. Grouping several related entities does not establish that they are one legal entity.

Original API responses remain unmodified in the run's response cache. The combined `pcl-records.json` adds `_rocSearchNames` to source rows; `matchedSearchNames` is retained in case evidence and shown in case details. Multi-query local XLSX/HTML/CSV downloads include a **Search matches** table with searched names, returned names and case information. The run configuration/evidence also records the full query list, including zero-result queries.

If interrupted or capped, completed-name results remain available as explicitly partial results. Exports/docket selection remain disabled until all names finish; ROC does not present the partial index as complete. The partial file and `search-progress.json` record completed queries. Resume rebuilds from paid cached pages and requests only missing pages/names. Raising the cap does not reset receipts or automatically resume. Uncertain charges or missing purchased responses block further paid requests, using the existing recovery controls. There is no automatic retry.

## Configuration

Place `additionalNames` inside `lawyer` or `litigant`:

```json
{
  "searchType": "attorney",
  "lawyer": {
    "lastName": "Lawyer",
    "firstName": "Robert",
    "additionalNames": [{"lastName": "Lawyer", "firstName": "Bob"}],
    "aliases": ["Robert A. Lawyer"]
  },
  "runDirectory": "runs/example",
  "budgetCents": 1000
}
```

The HTTP form uses the same `additionalNames` list alongside primary `firstName` and `lastName`. This change was tested with synthetic PCL-shaped replies, saved fixtures and browser tests, without live PACER purchases.
