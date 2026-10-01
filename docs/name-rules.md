# Saved name rules

Name rules group report labels using saved docket evidence. Creating, previewing, editing, removing and undoing a rule never authenticates with PACER, submits a search, retrieves a report, or purchases a document.

## Use in the workspace

1. In Clients, check the names to combine and choose **Group selected names**. One name is enough for a spelling correction. Alternatively, use **Name rules → New rule** and type the source spellings.
2. Enter the preferred report name and select **Name correction — same party** or **Organization group — related entities**. The latter keeps the distinction between a reporting group and a single legal entity visible in reports.
3. Check the source names, one per line. Matching uses only capitalization, Unicode presentation and whitespace normalization; no fuzzy matching, suffix stripping, substring matching or inferred corporate relationships.
4. **Preview change** shows before/after names and distinct-case totals for the open run, plus the number of saved runs with matching names. Preview is read-only. Changing the form invalidates it. **Save rule** commits the reviewed change.
5. The open run's downloads rebuild automatically from saved evidence. Other runs display the current grouping immediately and show **Update exports** until their downloads have been rebuilt. Downloads from an outdated rule revision are blocked, so exported totals cannot silently disagree with the current view.

Rules apply to all saved and future searches **in this workspace**, including searches for other lawyers. They survive closing and reopening ROC. They do not alter original PACER names, case captions, attorney aliases, counsel matches, criminal counts, docket files or receipt ledgers.

Already-grouped rows have a clickable grouping label. Edit them there or through **Name rules**. Add source spellings to an existing rule rather than creating an overlapping group. Source names, including the preferred label, may belong to only one rule. Overlapping groups, chains and cycles are rejected. Unlisted variants remain separate.

## Counting and source boundaries

A group counts the union of its members' court/case pairs. If two names each appear in the same case, their group has one case, not two. All their distinct cases remain available in the case breakdown. Client selection occurs **before** report grouping. Grouping a client and an opposing party never adds the opponent to the client list or copies the opponent's charges onto the client.

Grouped rows retain their source spellings and per-source party roles, defendant numbers, matched counsel and relationships. The case detail continues to show the original parties. Only counsel-matched members enter the Clients reports. Opposing and other parties remain in the underlying evidence. Client type is recorded per case rather than assigned to a group globally. A name correction is the operator's explicit grouping decision, not an independent identity verification.

## Editing, removing and undoing

**Edit** changes a rule's label, type or members, with another preview before saving. **Remove rule** previews the ungrouped result and requires confirmation. **Undo last change** previews restoring the previous whole rule set; up to 20 changes are retained. Undo advances the revision, so older exports still require a refresh. All operations preserve the original records; removing rules falls back to ordinary source-name grouping.

A stale tab or a change in relevant saved cases invalidates the preview. Rule edits and report refreshes wait until the workspace is idle. Refreshes take the existing run lock without changing receipts, clearing a pending charge, or resuming an interrupted search/retrieval. They also work while signed out. Interrupted operations retain their Resume action.

## Files and standalone CLI

The private workspace file is `runs/workspace/name-rules.json` (or the equivalent beneath a custom `--directory`). It contains version 1, a monotonically increasing revision, normalized rules and the bounded undo history. Keep it with the workspace when moving or backing up saved runs. An unreadable file is reported rather than silently replaced with empty rules.

Each export records the applied revision and rules in `evidence.json` and `party-reports.json`. The CSV ZIP includes `name-rules.json`; Excel includes a Name rules sheet when rules exist. Summary and case sheets identify the grouping type and preserve source names/associations. Original parsed party/counsel records and court report files are preserved.

The CLI can use a workspace rule file or an exported rule snapshot by adding a relative or absolute path to its configuration:

```json
{
  "nameRulesFile": "runs/workspace/name-rules.json"
}
```

Alternatively supply a `nameRules` object with `revision` and `rules`. Use one mechanism, not both. A rule contains a stable 32-character lowercase hexadecimal `id`, a `label`, a `kind` (`name-correction` or `organization-group`) and a `names` array. Rule validation happens before any live authentication or request. The UI writes a fixed snapshot into each operation's config so all outputs for that operation use the same rule set. The optional Sheets publisher uses the same grouping engine; no live publication is needed for rule editing.

## Verification

Offline tests exercise same-case deduplication, separate-case retention, client/opponent boundaries, unchanged source counts, persistence, edits, removals, undo, overlapping rules, stale previews, literal spreadsheet text, CLI configuration, interrupted work and unresolved receipts. Browser acceptance covers selection, before/after preview, invalidation after editing, saving, downloads, grouping labels, edit/remove/undo, conflict errors and reopening. External browser traffic is blocked in those tests.
