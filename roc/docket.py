"""Conservative parsing of CM/ECF party/counsel and count tables.

The source remains authoritative. Copy listed counts without resolving indictment
versions, and keep each represented defendant's counts separate.
"""
from __future__ import annotations

from decimal import Decimal
from html.parser import HTMLParser
import hashlib
import re

from .common import RocError, clean, name_key, normalize_case_number
from .review import issue, review_warnings


CIVIL_TEAMS = {"Plaintiff": "Civil Plaintiff", "Defendant": "Civil Defense", **{
    role: role for role in ("Petitioner", "Respondent", "Claimant", "Amicus", "Intervenor",
                           "Movant", "Interested Party", "Notice Party", "Debtor", "Creditor")}}
TEAM_VALUES = ["Prosecution", "Criminal Defense", *CIVIL_TEAMS.values(), "Multiple roles"]
ROLE_VALUES = TEAM_VALUES  # Keep the old import for saved integrations.
NOT_LISTED = "Not listed in source"


class Node:
    def __init__(self, tag, attrs=()):
        self.tag, self.attrs, self.children = tag, dict(attrs), []

    def walk(self, tag):
        if self.tag == tag:
            yield self
        for child in self.children:
            if isinstance(child, Node):
                yield from child.walk(tag)

    def text(self):
        return "".join(c.text() if isinstance(c, Node) else c for c in self.children)


class Tree(HTMLParser):
    VOID = set("area base br col embed hr img input link meta param source track wbr".split())

    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.root = Node("root")
        self.stack = [self.root]
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs)
        self.stack[-1].children.append(node)
        if tag in ("br", "hr"):
            node.children.append("\n")
        if tag not in self.VOID:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                if tag in ("td", "th", "tr", "p", "h3"):
                    self.stack[i].children.append("\n")
                del self.stack[i:]
                break

    def handle_data(self, value):
        if self.stack[-1].tag not in ("script", "style"):
            self.stack[-1].children.append(value)


def receipt_cents(html):
    text = clean(Tree(html).root.text())
    if "Transaction Receipt" not in text:
        raise RocError("Docket receipt missing; keep the request pending and do not retry.")
    receipt = text[text.rfind("Transaction Receipt"):]
    match = re.search(r"\bCost:\s*\$?\s*(\d+\.\d{2})\b", receipt)
    if not match:
        raise RocError("Docket cost is unreadable; do not retry.")
    return int(Decimal(match[1]) * 100)


def expand_count_ids(label):
    result = []
    for part in label.split(","):
        m = re.fullmatch(r"\s*(\d+)(s*)(?:\s*[-–]\s*(\d+)(s*))?\s*", part, re.I)
        if not m:
            raise RocError(f"Unrecognized count label: {label}")
        a, suffix, b, suffix2 = m.groups()
        if b and (suffix.lower() != suffix2.lower() or int(b) < int(a)):
            raise RocError(f"Ambiguous count range: {label}")
        if int(b or a) - int(a) > 10000:
            raise RocError("Count range is too large.")
        result.extend({"number": n, "revision": len(suffix), "id": f"{n}{suffix.lower()}"}
                      for n in range(int(a), int(b or a) + 1))
    return result


def counsel_blocks(cell):
    """CM/ECF also puts self-represented parties in its counsel column.

    Read each bold name with its following block so PRO SE on one entry does
    not remove other, actual attorneys from the same cell.
    """
    blocks = []
    def visit(node):
        if node.tag == "b":
            blocks.append({"name": clean(node.text()), "text": node.text()})
            return
        for child in node.children:
            if isinstance(child, Node):
                visit(child)
            elif blocks:
                blocks[-1]["text"] += child
    visit(cell)
    attorneys, self_represented, court_contacts = [], [], []
    for block in blocks:
        if not block["name"]:
            continue
        if re.search(r"\bPRO[\s-]*SE\b", block["text"], re.I):
            target = self_represented
        elif (re.search(r"\bDesignation:\s*(?:Pretrial Services|Probation Department|Interpreter)\b", block["text"], re.I)
              or re.fullmatch(r"[A-Za-z .-]*\bInterpreter", block["name"], re.I)
              or (name_key(block["name"]) == "us probation" and
                  re.search(r"\bUNITED STATES PROBATION OFFICE\b", block["text"], re.I))
              or (name_key(block["name"]) == "usm" and
                  re.search(r"\bUNITED STATES MARSHAL\b", block["text"], re.I))):
            # Court notification entries can appear in the representation cell.
            # Use explicit staff labels, never an attorney's office/address text.
            target = court_contacts
        else:
            target = attorneys
        target.append(block["name"])
    return attorneys, self_represented, court_contacts


def parse_report(html):
    root = Tree(html).root
    headings = [clean(n.text()) for n in root.walk("h3")]
    heading = next((h for h in headings if "DOCKET FOR CASE" in h), "")
    match = re.search(r"(CRIMINAL|CIVIL) DOCKET FOR CASE #:\s*(\S+)", heading)
    if not match:
        raise RocError("Not a supported district-court docket report; no case data inferred.")
    result = {"caseNumber": normalize_case_number(match[2]), "caseType": match[1].title(),
              "heading": heading, "sha256": hashlib.sha256(html.encode()).hexdigest(),
              "parties": [], "warnings": []}
    party, section = None, None
    for row in root.walk("tr"):
        cells = [c for c in row.children if isinstance(c, Node) and c.tag in ("td", "th")]
        if not cells:
            continue
        values = [clean(c.text()) for c in cells]
        if len(values) >= 3 and values[0] == "Date Filed" and "Docket Text" in values[-1]:
            break
        role = re.fullmatch(r"(Defendant|Plaintiff|Interested Party|Petitioner|Respondent|Movant|Debtor|Creditor|Claimant|Intervenor|Amicus|Mediator|Notice Party)(?:\s*\((\d+)\))?", values[0], re.I)
        if role and any(cells[0].walk("u")):
            party = {"role": role[1].title(), "defendantNumber": role[2], "name": "", "counsel": [], "counts": [], "warnings": []}
            result["parties"].append(party)
            section = None
            continue
        if party is None:
            continue
        if not party["name"]:
            names = [clean(b.text()) for b in cells[0].walk("b")]
            if names:
                party["name"] = names[0]
                if len(cells) >= 3 and "represented" in values[1].lower():
                    party["counsel"], party["selfRepresentedNames"], party["courtContacts"] = counsel_blocks(cells[-1])
                    if party["role"] == "Mediator":
                        # Some courts put the mediator in a "represented by" cell.
                        # The explicit row role is not an attorney-client relation.
                        party["courtContacts"].extend(party["counsel"])
                        party["counsel"] = []
                continue
        if values[0] in ("Pending Counts", "Terminated Counts", "Complaints"):
            section = values[0]
            continue
        if values[0].startswith("Highest Offense Level"):
            section = None
        if section in ("Pending Counts", "Terminated Counts") and len(cells) >= 3:
            if values[0] and values[0] != "None":
                # The display copies this complete source row, including unfamiliar
                # suffixes and ranges. Numeric IDs are optional evidence only.
                m = re.fullmatch(r"(.+?)\s*\((\d[^()]*)\)", values[0])
                charge, label = (m[1].strip(), m[2]) if m else (values[0], "")
                try:
                    ids = expand_count_ids(label)
                except RocError:
                    ids = []
                party["counts"].append({"section": section, "rawCharge": charge,
                    "rawCountLabel": label, "rawText": values[0], "countIds": ids, "disposition": values[-1]})
    if not result["parties"] or any(not p["name"] for p in result["parties"]):
        result["warnings"].append("Party table incomplete or unfamiliar.")
    text = clean(root.text())
    nos = re.search(r"Nature of Suit:\s*(.+?)(?=\s+(?:Jurisdiction:|Cause:|Plaintiff|Defendant)|$)", text)
    result["natureOfSuit"] = nos[1] if nos else ""
    return result


def listed_counts(party):
    """Source order and labels are authoritative; do not filter versions."""
    return [dict(c) for c in party["counts"]]


def summarize_counts(counts):
    lines = []
    for count in counts:
        text = count.get("rawText") or (count["rawCharge"] +
            (" (" + count["rawCountLabel"] + ")" if count["rawCountLabel"] else ""))
        # Preserve the source's pending/terminated distinction without deciding
        # which indictment supersedes another or appending sentencing prose.
        if count["section"] == "Terminated Counts":
            text += " [Terminated Counts]"
        lines.append(text)
    return "; ".join(lines)


def enrich(report, aliases):
    keys = {name_key(n) for n in aliases}
    matched = [p for p in report["parties"] if any(name_key(n) in keys for n in p["counsel"])]
    items = [issue("needs-review", "report-layout", w) for w in report["warnings"]]
    result = {"team": "", "nature": "", "representedParties": [p["name"] for p in matched],
              "representedRoles": [{"party": p["name"], "role": p["role"]} for p in matched],
              "selectedCounts": [], "countSummaries": [], "countPolicy": "all-listed-source-rows",
              "issues": items, "warnings": [], "fieldStatus": {},
              "sourceSha256": report["sha256"], "caseNumber": report["caseNumber"]}

    def finish():
        # "team" remains a compatibility alias for older saved runs and clients.
        result["role"] = result["team"]
        result["roles"] = result.get("teams", [])
        result["fieldStatus"]["role"] = result["fieldStatus"].get("team", "needs-review")
        result["partyDetails"] = [{k: p.get(k) for k in ("name", "role", "defendantNumber", "counsel", "warnings")} |
                                  {"matchedCounsel": [n for n in p["counsel"] if name_key(n) in keys]}
                                  for p in report["parties"]]
        result["warnings"] = review_warnings(items)
        return result

    if not matched:
        items.append(issue("needs-review", "attorney-not-matched",
                           "No exact configured attorney alias found in the counsel table.", field="team"))
        result["fieldStatus"] = {"team": "needs-review", "nature": "needs-review"}
        return finish()
    roles = set()
    for p in matched:
        if report["caseType"] == "Criminal":
            role = ("Criminal Defense" if p["role"] == "Defendant" else
                    "Prosecution" if p["role"] == "Plaintiff" and name_key(p["name"]) in
                    {"usa", "united states", "united states of america"} else "")
        else:
            role = CIVIL_TEAMS.get(p["role"], "")
        roles.add(role)
    result["teams"] = sorted(roles - {""})
    if "" in roles or not roles or (report["caseType"] == "Criminal" and len(roles) != 1):
        items.append(issue("needs-review", "unsupported-or-conflicting-role",
                           "Counsel side is ambiguous or unsupported.", field="team"))
        result["fieldStatus"]["team"] = "needs-review"
    else:
        result["team"] = next(iter(roles)) if len(roles) == 1 else "Multiple roles"
        result["fieldStatus"]["team"] = "resolved"
    if report["warnings"]:
        result["fieldStatus"]["nature"] = "needs-review"
        return finish()
    if report["caseType"] == "Civil":
        result["nature"] = report["natureOfSuit"] or NOT_LISTED
        result["fieldStatus"]["nature"] = "resolved" if report["natureOfSuit"] else "missing-source"
        if not report["natureOfSuit"]:
            items.append(issue("missing-source", "nature-not-listed", "Civil Nature of Suit is not listed.", field="nature"))
        return finish()
    if not result["team"]:
        result["fieldStatus"]["nature"] = "needs-review"
        return finish()
    subjects = matched if result["team"] == "Criminal Defense" else [p for p in report["parties"] if p["role"] == "Defendant"]
    result["countScope"] = "represented defendants" if result["team"] == "Criminal Defense" else "case defendants; attorney represents the government"
    if not subjects:
        items.append(issue("missing-source", "defendants-not-listed", "No defendants are listed in the parsed party table.", field="nature"))
    for p in subjects:
        counts = listed_counts(p)
        result["selectedCounts"].append({"party": p["name"], "defendantNumber": p.get("defendantNumber"), "counts": counts})
        summary = summarize_counts(counts) if counts else NOT_LISTED
        state = "resolved" if counts else "missing-source"
        if not counts:
            items.append(issue("missing-source", "counts-not-listed", "No structured counts are listed for this defendant.",
                               party=p["name"], field="nature"))
        if p["warnings"]:
            items.extend(issue("needs-review", "count-layout", w, party=p["name"], field="nature") for w in p["warnings"])
            state = "needs-review"
        result["countSummaries"].append({"party": p["name"], "defendantNumber": p.get("defendantNumber"),
                                          "text": summary, "status": state})
    summaries = result["countSummaries"]
    if len(summaries) == 1:
        result["nature"] = summaries[0]["text"]
    elif summaries:
        # Names are needed only when distinguishing multiple defendants in one case.
        result["nature"] = "\n".join(s["party"] +
            (" (defendant " + s["defendantNumber"] + ")" if s.get("defendantNumber") else "") +
            ": " + s["text"] for s in summaries)
    else:
        result["nature"] = NOT_LISTED
    states = {s["status"] for s in summaries}
    result["fieldStatus"]["nature"] = ("needs-review" if "needs-review" in states else
        "partial" if len(states) > 1 else "resolved" if states == {"resolved"} else "missing-source")
    return finish()
