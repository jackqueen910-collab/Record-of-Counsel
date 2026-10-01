"""Docket-only party reports. Counts mean distinct court/case pairs, not claims.

Names are grouped by Unicode-normalized spelling, case and whitespace only.
Never infer that similar people or related legal entities are the same party.
"""
from collections import defaultdict
from .common import clean, name_key
from .name_rules import party_key, snapshot


def case_role(case):
    return case.get("role") or case.get("team", "")


def details(case, aliases=()):
    evidence = case.get("enrichment", {})
    if "partyDetails" in evidence:
        return evidence["partyDetails"]
    # Upgrade old evidence in memory from counsel associations, never from a
    # caption or represented-name list (same names may occur on different sides).
    keys = {name_key(a) for a in aliases}
    return [dict(p, matchedCounsel=[n for n in p.get("counsel", []) if name_key(n) in keys])
            for p in evidence.get("parsedDocket", {}).get("parties", [])]


def relationship(party, lawyer_role):
    if party.get("matchedCounsel"):
        return "Client"
    side = party["role"]
    opposing = {"Civil Plaintiff": "Defendant", "Civil Defense": "Plaintiff",
                "Prosecution": "Defendant", "Criminal Defense": "Plaintiff"}
    same = {"Civil Plaintiff": "Plaintiff", "Civil Defense": "Defendant",
            "Prosecution": "Plaintiff", "Criminal Defense": "Defendant"}
    if opposing.get(lawyer_role) == side:
        return "Opposing party"
    if same.get(lawyer_role) == side:
        return "Other party on same side"
    return "Unresolved"


def build_party_reports(cases, aliases=(), name_rules=None):
    rules = snapshot(name_rules)
    lookup = {party_key(name): rule for rule in rules["rules"] for name in rule["names"]}
    entries = {}
    coverage = {"indexedCases": len(cases), "parsedDockets": 0, "partyTablesNeedingReview": 0,
                "casesWithMatchedClients": 0, "casesWithPlaintiffs": 0, "casesWithDefendants": 0}
    for case in cases:
        evidence = case.get("enrichment")
        if not evidence:
            continue
        coverage["parsedDockets"] += 1
        parties = details(case, aliases)
        layout_review = bool(evidence.get("parsedDocket", {}).get("warnings")) or not parties
        coverage["partyTablesNeedingReview"] += layout_review
        coverage["casesWithMatchedClients"] += any(p.get("name") and p.get("matchedCounsel") for p in parties)
        for side in ("Plaintiff", "Defendant"):
            coverage["casesWith" + side + "s"] += any(p.get("name") and p["role"] == side for p in parties)
        for p in parties:
            name = clean(p.get("name", ""))
            if not name or p["role"] == "Mediator":
                continue
            key = party_key(name)
            # Defendant numbers distinguish namesakes within one criminal case.
            # Group name labels later, after preserving counsel associations.
            identity = (case["key"], key, p["role"], p.get("defendantNumber"))
            if identity not in entries:
                entries[identity] = {"nameKey": key, "name": name, "sourceNames": [], "partyRole": p["role"],
                    "caseKey": case["key"], **{field: case.get(field, "") for field in
                    ("caseNumber", "caseTitle", "caseType", "court", "district", "dateFiled", "status", "pacerLink", "nature")},
                    "role": case_role(case), "relationships": [], "matchedCounsel": [], "defendantNumbers": [],
                    "sourceFile": evidence.get("sourceFile", ""), "sourceSha256": evidence.get("sourceSha256", ""),
                    "partyTableStatus": "Needs review" if layout_review else "Parsed"}
            row = entries[identity]
            additions = {"sourceNames": [name], "matchedCounsel": p.get("matchedCounsel", []),
                         "relationships": ["Unresolved" if layout_review and not p.get("matchedCounsel") else relationship(p, case_role(case))],
                         "defendantNumbers": [p["defendantNumber"]] if p.get("defendantNumber") else []}
            for field, values in additions.items():
                row[field] = sorted(set(row[field]) | set(values))
    rows = sorted(entries.values(), key=lambda p: (p["nameKey"], p["caseKey"], p["partyRole"]))
    # Repeated source blocks for a party are one row and one case. An explicit
    # counsel match wins over an unmatched repeat of that same party-role block.
    for row in rows:
        if row["matchedCounsel"]:
            row["relationships"] = ["Client"]
        row["relationship"] = "; ".join(row["relationships"])
    coverage["indexOnlyCases"] = coverage["indexedCases"] - coverage["parsedDockets"]
    coverage["casesWithoutMatchedClients"] = coverage["parsedDockets"] - coverage["casesWithMatchedClients"]
    groups = {}
    for group, predicate in (("clients", lambda r: bool(r["matchedCounsel"])),
                             ("plaintiffs", lambda r: r["partyRole"] == "Plaintiff"),
                             ("defendants", lambda r: r["partyRole"] == "Defendant")):
        selected_by_case = {}
        for r in rows:
            if not predicate(r):
                continue
            # Match counsel and select clients BEFORE grouping names. A family
            # group must never turn an opponent into a represented client.
            source = {k: r[k] for k in ("name", "sourceNames", "partyRole", "defendantNumbers", "matchedCounsel", "relationship")}
            rule = lookup.get(r["nameKey"])
            r = dict(r, sourceParties=[source], groupId=rule["id"] if rule else "", groupKind=rule["kind"] if rule else "")
            if rule:
                r.update(name=rule["label"], nameKey=party_key(rule["label"]))
            identity = (r["nameKey"], r["caseKey"])
            if identity not in selected_by_case:
                selected_by_case[identity] = dict(r, partyRoles=[r["partyRole"]])
                continue
            merged = selected_by_case[identity]
            for field in ("sourceNames", "matchedCounsel", "defendantNumbers", "relationships"):
                merged[field] = sorted(set(merged[field]) | set(r[field]))
            merged["partyRoles"] = sorted(set(merged["partyRoles"]) | {r["partyRole"]})
            merged["sourceParties"] = [*merged["sourceParties"], *r["sourceParties"]]
            merged["partyRole"] = "; ".join(merged["partyRoles"])
            merged["relationship"] = "; ".join(merged["relationships"])
        selected = list(selected_by_case.values())
        grouped = defaultdict(list)
        for row in selected:
            grouped[row["nameKey"]].append(row)
        summaries = []
        for key, appearances in grouped.items():
            keys = sorted({r["caseKey"] for r in appearances})
            names = sorted({n for r in appearances for n in r["sourceNames"]}, key=lambda n: (n.casefold(), n))
            summary = {"nameKey": key, "name": appearances[0]["name"] if appearances[0]["groupId"] else names[0],
                       "groupId": appearances[0]["groupId"], "groupKind": appearances[0]["groupKind"],
                       "sourceNames": names, "caseCount": len(keys), "caseKeys": keys,
                       "roles": sorted({r["role"] or "Unresolved" for r in appearances})}
            for label, relation in (("client", "Client"), ("opposing", "Opposing party"),
                                    ("sameSide", "Other party on same side"), ("unresolved", "Unresolved")):
                summary[label + "CaseCount"] = len({r["caseKey"] for r in appearances if relation in r["relationships"]})
            summary["civilPlaintiffCaseCount"] = len({r["caseKey"] for r in appearances
                if r["role"] == "Civil Plaintiff" and "Opposing party" in r["relationships"]})
            summaries.append(summary)
        groups[group] = {"summary": sorted(summaries, key=lambda s: (-s["caseCount"], s["nameKey"])), "cases": selected}
    return {"version": 2, "coverage": coverage, "nameRules": rules,
            "countPolicy": "Distinct court/case pairs in parsed dockets; repeated parties and counts do not inflate totals.",
            "namePolicy": "Same spelling ignoring case, Unicode presentation and whitespace, plus explicitly saved name rules (revision " + str(rules["revision"]) + "). Source names retained. Organization groups are reporting groups, not assertions that members are one legal entity.",
            "relationshipPolicy": "Clients require an exact configured counsel alias. Opposing parties use the listed plaintiff/defendant side and resolved lawyer role; this does not establish who filed a claim or current representation.",
            **groups, "parties": rows}


def coverage_text(reports):
    c = reports["coverage"]
    return (f"{c['parsedDockets']} of {c['indexedCases']} indexed cases have parsed dockets; "
            f"{c['indexOnlyCases']} index only. Counsel matched in {c['casesWithMatchedClients']} cases; "
            f"{c['partyTablesNeedingReview']} party tables need review. Summaries cover these saved dockets only.")


CLIENT_REPORT_VERSION = 1


def report_tables(reports):
    """Client-only presentation shared by CSV, Excel, HTML and Google Sheets.

    Party evidence and legacy groups stay available internally. Client type is
    case-specific, never a property of the cross-case name summary.
    """
    clients = reports["clients"]
    summary = {"name": "Clients", "file": "clients-summary.csv",
               "headers": ["Name", "Distinct cases", "Source names", "Grouping"],
               "rows": [[s["name"], s["caseCount"], "; ".join(s["sourceNames"]), s["groupKind"]]
                        for s in clients["summary"]]}
    fields = ["name", "caseNumber", "caseTitle", "caseType", "role", "partyRole", "court", "district", "dateFiled", "nature", "status", "pacerLink"]
    headers = ["Name", "Case number", "Case title", "Case Type", "Role", "Client type", "Court", "District", "Date filed", "Nature of Case", "Status (PACER)", "PACER link", "Matched counsel", "Source names", "Party table", "Grouping", "Source associations"]
    rows = [[r[f] for f in fields] + ["; ".join(r["matchedCounsel"]), "; ".join(r["sourceNames"]), r["partyTableStatus"], r["groupKind"],
            "; ".join(p["name"] + " [" + p["partyRole"] + "]: " + p["relationship"] +
                      (" (" + ", ".join(p["matchedCounsel"]) + ")" if p["matchedCounsel"] else "") for p in r["sourceParties"])] for r in clients["cases"]]
    return [summary, {"name": "Clients cases", "file": "clients-cases.csv", "headers": headers, "rows": rows}]
