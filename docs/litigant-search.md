# Litigant search — first step

**Search Attorney** remains the default landing tab. Both forms put last name first. **Search Litigant** labels that field **Last name / Entity name**, makes first name optional, and hides attorney aliases. Organizations use the whole entity name in the last-name field with first name blank. Switching tabs preserves separate name drafts; signing out or switching PACER accounts clears both drafts. Courts, dates and cap are common controls. ROC branding is unchanged.

## Official API routing

Both modes use the existing official PCL `POST /pcl-public-api/rest/parties/find` API and existing PACER authentication, pagination, response cache, receipt reservations and stop/resume logic. Attorney requests specify `partyType: "aty"`; litigant requests specify `partyType: "pty"`. `lastName` is required by this interface. `firstName` is omitted when blank. Court/date filters remain inside `courtCase`. No new PACER browser search, alternate data source, model call or authentication workflow is introduced.

Names use PCL's default prefix matching. There is no automatic variant expansion or entity consolidation, and no party-role filter, so either side and other litigant roles can be returned. These are cases involving matched indexed names, not a verified list of claims filed by a particular unique person/entity. Source party-role codes are preserved without guessing that every court uses them consistently. Attorney records unexpectedly returned in a litigant search cause a review stop; ROC does not fall back to another search.

The [official August 2026 PCL API guide](https://pacer.uscourts.gov/sites/default/files/files/PCL-API-08-2026-1.pdf) documents party requests/results and entity-name use in `lastName` (pp. 13–20 and 50–55). Searches run only after explicit submission and sign-in, under the entered search cap. All development tests use synthetic responses; a live party-search acceptance check has not been run.

## Saved results and exports

New configs explicitly record `searchType` as `attorney` or `litigant`; older configs without it remain attorney searches. Attorney names stay under `lawyer` for compatibility; litigant names use `litigant`, never a fictitious lawyer. The saved-search sidebar and run header identify the mode.

The case index retains one row per normalized court/case pair. Every returned source row remains in evidence. Matched litigant names appear below the caption and participate in existing party-name sorting/filtering. Case details show each returned name and court-supplied role code. Excel, HTML and the CSV bundle include a **Matched litigants** table with those names, role codes and case information. Different names in one case do not inflate the case count; the same name in different cases keeps each case. Separate appearances with different roles are retained. Original API records remain in evidence JSON.

This release does **not** apply attorney-specific Clients, counsel matching, docket charge extraction or Document Grabber to litigants. The interface hides those actions for litigant runs; the backend also rejects docket previews/retrieval and document analysis. The CLI rejects litigant docket inputs/selections and Google publication before any sign-in or purchase. Existing attorney capabilities continue as before. Role/count enrichment, litigant-specific documents and other party features can be added as a subsequent step with appropriate matching semantics.

Litigant searches can pause/resume, reopen after sign-in, rebuild exports from saved data, and use the same account isolation as attorney searches. Search scope is fixed per run. Starting the other search mode creates a separate run and ledger; it never reinterprets an existing attorney run.

## CLI configuration

```json
{
  "searchType": "litigant",
  "litigant": {"lastName": "Example Corporation", "firstName": ""},
  "search": {"dateFiledFrom": "2020-01-01"},
  "runDirectory": "runs/example-litigant",
  "budgetCents": 1000
}
```

Run explicitly with `python -m roc run config.json --live`. For free offline replay, add an `indexFile` containing saved PCL party responses and omit `--live`. No docket selection belongs in this configuration yet.
