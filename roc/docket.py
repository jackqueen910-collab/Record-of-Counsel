"""Conservative parsing of CM/ECF party/counsel and count tables.

The source remains authoritative. Unknown layouts, ambiguous versions and conflicting
client charge profiles produce review items, not invented summaries.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from html.parser import HTMLParser
import hashlib
import re

from .common import RocError, clean, name_key, normalize_case_number


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
              or re.fullmatch(r"[A-Za-z .-]*\bInterpreter", block["name"], re.I)):
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
        role = re.fullmatch(r"(Defendant|Plaintiff|Interested Party|Petitioner|Respondent|Movant|Debtor|Creditor)(?:\s*\((\d+)\))?", values[0], re.I)
        if role:
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
                continue
        if values[0] in ("Pending Counts", "Terminated Counts", "Complaints"):
            section = values[0]
            continue
        if values[0].startswith("Highest Offense Level"):
            section = None
        if section in ("Pending Counts", "Terminated Counts") and len(cells) >= 3:
            m = re.fullmatch(r"(.+?)\s*\(([\ds,\-– ]+)\)", values[0], re.I)
            if m:
                try:
                    ids = expand_count_ids(m[2])
                except RocError as exc:
                    party["warnings"].append(str(exc))
                    continue
                party["counts"].append({"section": section, "rawCharge": m[1].strip(),
                    "rawCountLabel": m[2], "countIds": ids, "disposition": values[-1]})
            elif values[0] and values[0] != "None":
                party["warnings"].append("Unrecognized count row: " + values[0])
    if not result["parties"] or any(not p["name"] for p in result["parties"]):
        result["warnings"].append("Party table incomplete or unfamiliar.")
    text = clean(root.text())
    nos = re.search(r"Nature of Suit:\s*(.+?)(?=\s+(?:Jurisdiction:|Cause:|Plaintiff|Defendant)|$)", text)
    result["natureOfSuit"] = nos[1] if nos else ""
    return result


# Label normalization, not inference of the charge from a statute alone.
LABELS = {
    "CONSPIRACY TO DEFRAUD THE UNITED STATES": "Conspiracy to defraud the U.S.",
    "TRANSPORTING FOR PROSTITUTION": "Transportation to engage in prostitution",
    "TRANSPORTATION TO ENGAGE IN PROSTITUTION": "Transportation to engage in prostitution",
    "FRAUD BY WIRE": "Wire fraud",
    "FRAUD BY WIRE, RADIO, OR TELEVISION": "Wire fraud",
    "ATTEMPT TO EVADE OR DEFEAT TAX": "Tax evasion",
    "SEX TRAFFICKING OF CHILDREN OR BY FORCE, FRAUD OR COERCION (SEX TRAFFICKING BY FORCE, FRAUD AND COERCION)": "Sex trafficking by force, fraud or coercion",
}


def charge_label(raw):
    # Remove the source's statute/code prefix, but preserve unfamiliar descriptive text.
    m = re.match(r"^(?:\d+:[\dA-Za-z().-]+(?:\s*&\s*(?:\d+:)?[\dA-Za-z().-]+)?\s+)([A-Z].*)$", raw)
    text = m[1] if m else raw
    text = re.sub(r"\s*\(dof\b.*$", "", text, flags=re.I).strip()
    return LABELS.get(text.upper(), text.lower().capitalize())


def compress(numbers):
    numbers = sorted(set(numbers))
    groups = []
    for n in numbers:
        if groups and n == groups[-1][-1] + 1:
            groups[-1].append(n)
        else:
            groups.append([n])
    return ", ".join(f"{g[0]}–{g[-1]}" if len(g) > 1 else str(g[0]) for g in groups)


def selected_counts(party):
    if party["warnings"]:
        raise RocError("; ".join(party["warnings"]))
    if not party["counts"]:
        raise RocError("No structured counts supplied for this client.")
    latest = max(i["revision"] for c in party["counts"] for i in c["countIds"])
    chosen = {}
    for count in party["counts"]:
        for item in count["countIds"]:
            if item["revision"] != latest:
                continue
            n = item["number"]
            value = {**count, "countId": item["id"], "number": n, "revision": latest,
                     "label": charge_label(count["rawCharge"])}
            if n in chosen and chosen[n]["rawCharge"] != value["rawCharge"]:
                raise RocError(f"Conflicting charges for count {n}.")
            chosen[n] = value
    for count in party["counts"]:
        for item in count["countIds"]:
            if item["revision"] < latest and item["number"] not in chosen and not re.search(
                    r"\b(?:superseded|dismissed)\b|\bmotion to dismiss (?:is |was )?granted\b",
                    count["disposition"], re.I):
                raise RocError("Different indictment versions have unresolved unmatched counts.")
    return [chosen[n] for n in sorted(chosen)]


def summarize_counts(counts):
    groups = defaultdict(list)
    for count in counts:
        groups[count["label"]].append(count["number"])
    parts = []
    for label, numbers in sorted(groups.items(), key=lambda item: min(item[1])):
        word = "count" if len(set(numbers)) == 1 else "counts"
        parts.append(f"{label} ({word} {compress(numbers)})")
    return "; ".join(parts) + "." if parts else ""


def enrich(report, aliases):
    keys = {name_key(n) for n in aliases}
    matched = [p for p in report["parties"] if any(name_key(n) in keys for n in p["counsel"])]
    result = {"team": "", "nature": "", "representedParties": [p["name"] for p in matched],
              "selectedCounts": [], "warnings": list(report["warnings"]),
              "sourceSha256": report["sha256"], "caseNumber": report["caseNumber"]}
    if not matched:
        result["warnings"].append("No exact configured attorney alias found in the counsel table.")
        return result
    roles = set()
    for p in matched:
        if report["caseType"] == "Criminal":
            roles.add("Criminal Defense" if p["role"] == "Defendant" else
                      "Prosecution" if p["role"] == "Plaintiff" and name_key(p["name"]) in
                      {"usa", "united states", "united states of america"} else "")
        else:
            roles.add({"Defendant": "Civil Defense", "Plaintiff": "Civil Plaintiff"}.get(p["role"], ""))
    if len(roles) != 1 or "" in roles:
        result["warnings"].append("Counsel side is ambiguous or unsupported.")
        return result
    result["team"] = next(iter(roles))
    if result["warnings"]:
        return result
    if report["caseType"] == "Civil":
        result["nature"] = report["natureOfSuit"]
        return result
    subjects = matched if result["team"] == "Criminal Defense" else [p for p in report["parties"] if p["role"] == "Defendant"]
    result["countScope"] = "represented defendants" if result["team"] == "Criminal Defense" else "case defendants; attorney represents the government"
    profiles = []
    for p in subjects:
        try:
            counts = selected_counts(p)
            result["selectedCounts"].append({"party": p["name"], "counts": counts})
            profiles.append(summarize_counts(counts))
        except RocError as exc:
            result["warnings"].append(p["name"] + ": " + str(exc))
    if result["warnings"]:
        return result
    if len(set(profiles)) != 1:
        result["warnings"].append("Multiple defendants have different charge profiles; review per-party evidence instead of merging count numbers.")
        return result
    result["nature"] = profiles[0]
    return result
