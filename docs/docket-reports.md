# Docket client reports

Each explicitly selected docket is retrieved by the existing bounded court-web adapter using the official API session. The parser reads the docket header, party/counsel blocks and structured criminal count tables. It does not follow document links or infer representation from mentions in docket-entry prose. API search, authentication, receipt handling, spending caps, pause/resume and purchased-report reuse are unchanged.

## Running reports from Clients

**Run docket reports** opens a cost-warning dialog, defaulting to every supported case in the search without a parsed docket, newest filed first. Current case filters do not narrow this default; the dialog states the scope and offers **Choose specific cases**. Unsupported cases remain in the index and are counted separately. Previewing does not contact PACER.

New purchases require an explicitly entered additional cap of at least $3. The cap may cover only part of the list. ROC checks that $3 remains before opening the next court form and reserves that amount before submitting the report; confirmed receipts replace the reservation. ROC stops before the next reservation would exceed the limit, saves partial results, and displays a persistent spending-limit notice in the interface. The notice survives reload; no automatic continuation, cap increase, or replacement case occurs. Saved reports are reused, and a fresh selection previews only the remaining work.

## Reports and counting

The case index retains all indexed cases. **Role** replaces the visible Team label; `role` is the canonical presentation field, with `team` retained for existing integrations. Criminal Nature of Case copies the represented defendant's listed counts; prosecution cases retain separate count profiles for the defendants. Civil Nature of Case uses the docket's Nature of Suit.

**Clients** ranks counsel-matched names by distinct court/case pairs, descending. Ties sort by name. The Clients cases report has one row per grouped name and court/case, combining multiple explicit party roles where necessary. Repeated party blocks, multiple attorneys, multiple counts and duplicate roles never add to a case total. The same number in different courts counts separately. Transfers appearing in different courts remain separate court cases.

Clients require an exact configured attorney-alias match in that party's counsel cell. PRO SE entries, explicit court contacts and mediators are excluded from counsel matching. Counsel aliases use the existing attorney-name normalization. A client's presence in a caption, a similar name or an appearance elsewhere on the docket does not establish a match. Former attorneys and terminated parties can be included: these reports describe listed associations, not necessarily current representation.

**Client type** is the explicit party role on each client/case row and in the case drilldown: Plaintiff, Defendant, Petitioner, Respondent, Claimant, etc. A client can have different types in different cases or multiple listed types within one case. It is not a global summary label. **Role** describes the lawyer’s role for the case. Opponents and other parties do not enter Clients merely because they share a docket or a reporting group with a client. Their original counsel and party blocks remain in case details and evidence.

Downloads contain all saved client rows, independent of interface filters. The interface and tabular exports no longer have separate Defendants or Plaintiffs reports or relationship filters.

## Names and coverage

Automatic party-name grouping uses Unicode presentation normalization, whitespace and capitalization. Further punctuation, word order, initials, spelling and corporate suffix differences remain distinct unless covered by an [explicit saved name rule](name-rules.md). All original source spellings are retained. A name shared by different people is still one **name label**, not a verified identity. Distinct defendant numbers keep namesakes' counsel associations separate in the underlying party records and client selection. A shared name label does not merge the underlying client/counsel associations. Organization groups explicitly label related-entity reporting groups. No fuzzy identity or corporate-family inference runs automatically.

The coverage banner and export notes report indexed cases, parsed dockets, cases with matched clients, and party tables needing review. Zero results do not imply there were no parties in unexamined cases. Missing counts do not erase a supported client association. An unfamiliar table is visibly flagged and does not create inferred opposing-party associations.

## Files and reuse

- `case-index.xlsx`: Case index, Coverage, Clients, Clients cases, plus Name rules when configured. Counts are numeric; source strings are literal text. Long nature fields remain unwrapped.
- `case-index.csv`: the original ten-column case index with Role.
- `party-reports.zip`: clients-summary.csv and clients-cases.csv, coverage/methodology notes and the name-rule snapshot. Both CSVs also exist individually in the run's output folder.
- `case-index.html`: the case index and both client reports with navigation and clipped cells.
- `party-reports.json`: versioned coverage, policies, ranked summaries, case rows, all parsed parties, matched counsel, source file references and hashes. Version 2 adds the name-rule snapshot, group identifiers/kinds and original per-party associations inside grouped case rows. The full `parties` array retains ungrouped source identities. Legacy plaintiff/defendant groups remain in this evidence JSON for compatibility; they are not user-facing tabs.
- `evidence.json`: case-level enrichment, original parsed blocks, counts and counsel matches. `review.json` retains categorized findings.

**Update exports** refreshes old layouts and name rules from saved evidence without purchases. Earlier downloads are marked stale until refreshed. **Rebuild exports from saved data** reparses purchased reports without PACER authentication, searches or charges. Opening earlier evidence can derive party reports in memory from the saved counsel blocks and that run's configured attorney aliases; it never guesses from the old represented-name list. The optional CLI Google Sheets publisher generates the case index and two client tabs with clipped cells and coverage notes. No live Sheets publication was performed for this change.

## Validation

Offline regression fixtures cover multi-party cases, co-defendants, prosecution, duplicate blocks, identical numbers in different districts, multiple client roles, partial layouts, older evidence, conservative name grouping and safe exports. Browser tests cover client-only navigation, case-specific client types, client-specific charge drilldown and downloads without external network access.

The new pipeline was also replayed against 176 previously purchased district-validation reports and 539 attorney trials. Prior role, client and charge results were unchanged. Replaying Agnifilo's 334-case index with its two saved dockets yielded coverage of 2/334, with no new PACER requests or charges. These are regression checks on saved samples, not universal-layout certification.
