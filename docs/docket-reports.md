# Docket party reports

Each explicitly selected docket is retrieved by the existing bounded court-web adapter using the official API session. The parser reads the docket header, party/counsel blocks and structured criminal count tables. It does not follow document links or infer representation from mentions in docket-entry prose. API search, authentication, receipt handling, spending caps, pause/resume and purchased-report reuse are unchanged.

## Reports and counting

The case index retains all indexed cases. **Role** replaces the visible Team label; `role` is the canonical presentation field, with `team` retained for existing integrations. Criminal Nature of Case copies the represented defendant's listed counts; prosecution cases retain separate count profiles for the defendants. Civil Nature of Case uses the docket's Nature of Suit.

**Clients**, **Defendants** and **Plaintiffs** rank source names by distinct court/case pairs, descending. Ties sort by name. Each corresponding cases report has one row per grouped name and court/case, combining multiple explicit party roles where necessary. Repeated party blocks, multiple attorneys, multiple counts and duplicate roles never add to a case total. The same number in different courts counts separately. Transfers appearing in different courts remain separate court cases.

Clients require an exact configured attorney-alias match in that party's counsel cell. PRO SE entries, explicit court contacts and mediators are excluded from counsel matching. Counsel aliases use the existing attorney-name normalization. A client's presence in a caption, a similar name or an appearance elsewhere on the docket does not establish a match. Former attorneys and terminated parties can be included: these reports describe listed associations, not necessarily current representation.

Plaintiff and defendant reports use those **explicit source roles**. Petitioners, respondents, claimants and other supported roles remain in the full party evidence and client reports when counsel matches; they are not silently converted into plaintiffs or defendants.

Relationship columns distinguish:

- **As client:** this party has a counsel match.
- **Opposing party:** plaintiff versus defendant based on a resolved Civil Plaintiff, Civil Defense, Prosecution or Criminal Defense role.
- **Other on same side:** an unmatched co-plaintiff or co-defendant on the lawyer's side.
- **Unresolved:** unsupported/multiple lawyer roles, no counsel match, or a flagged party-table layout prevents assigning a side.

The defendant summary also reports **Opposed as civil plaintiff counsel**. It counts opposing defendants in cases where the lawyer's Role is Civil Plaintiff. It is a case count, not a count of claims, proof of filing counsel, or a resolution of counterclaims. Unusual party relationships remain visible for review. The interface's relationship filter changes the displayed distinct-case count and case drilldown to that subset; the other relationship columns show full totals. Downloads always contain the whole saved report, independent of interface filters.

## Names and coverage

Party names are grouped only by Unicode presentation normalization, whitespace and capitalization. Punctuation, word order, initials, spelling and corporate suffix differences remain distinct. All original source spellings are retained. A name shared by different people is still one **name label**, not a verified identity. Distinct defendant numbers keep namesakes' counsel associations separate in the underlying party records and client selection. A name label can therefore have more than one relationship in a case; relationship counts need not sum to its distinct-case total. An explicit alias/merge editor is a possible future feature; no fuzzy identity or corporate-family inference runs automatically.

The coverage banner and export notes report indexed cases, parsed dockets, cases with matched clients, and party tables needing review. Zero results do not imply there were no parties in unexamined cases. Missing counts do not erase a supported client association. An unfamiliar table is visibly flagged and does not create inferred opposing-party associations.

## Files and reuse

- `case-index.xlsx`: Case index, Coverage, Clients, Clients cases, Defendants, Defendants cases, Plaintiffs, Plaintiffs cases. Counts are numeric; source strings are literal text. Long nature fields remain unwrapped.
- `case-index.csv`: the original ten-column case index with Role.
- `party-reports.zip`: six summary/case CSVs plus coverage and methodology notes. The six CSVs also exist individually in the run's output folder.
- `case-index.html`: the case index and all six party reports with navigation and clipped cells.
- `party-reports.json`: versioned coverage, policies, ranked summaries, case rows, all parsed parties, matched counsel, source file references and hashes.
- `evidence.json`: case-level enrichment, original parsed blocks, counts and counsel matches. `review.json` retains categorized findings.

**Rebuild exports from saved data** reparses purchased reports without PACER authentication, searches or charges. Opening earlier evidence can derive party reports in memory from the saved counsel blocks and that run's configured attorney aliases; it never guesses from the old represented-name list. The optional CLI Google Sheets publisher generates the case index and six party tabs with clipped cells and coverage notes. No live Sheets publication was performed for this change.

## Validation

Offline regression fixtures cover multi-party cases, co-defendants, prosecution, duplicate blocks, identical numbers in different districts, multiple client roles, partial layouts, older evidence, conservative name grouping and safe exports. Browser tests cover report navigation, relationship filters, client-specific charge drilldown and downloads without external network access.

The new pipeline was also replayed against 176 previously purchased district-validation reports and 539 attorney trials. Prior role, client and charge results were unchanged. Replaying Agnifilo's 334-case index with its two saved dockets yielded coverage of 2/334, with no new PACER requests or charges. These are regression checks on saved samples, not universal-layout certification.
