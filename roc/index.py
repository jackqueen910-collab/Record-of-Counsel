"""Normalize official PCL records without dropping prosecution-side cases."""
from collections import defaultdict
from html import unescape

from .common import RocError, case_key, clean, normalize_case_number

COURTS = {"ctdc": "District of Connecticut", "dedc": "District of Delaware", "flsdc": "Southern District of Florida",
          "nhdc": "District of New Hampshire", "njdc": "District of New Jersey", "nysbk": "Southern District of New York",
          "pamdc": "Middle District of Pennsylvania", "cacdc": "Central District of California", "medc": "District of Maine",
          "nyedc": "Eastern District of New York", "utdc": "District of Utah", "nysdc": "Southern District of New York"}
NOS = {"530": "Habeas Corpus (General)", "850": "Securities/Commodities", "360": "Personal Injury: Other",
       "190": "Contract: Other", "160": "Stockholders Suits", "423": "Bankruptcy Withdrawal", "370": "Fraud or Truth-in-Lending"}


def records_from(source):
    if isinstance(source, list):
        return source
    if "cases" in source:
        return [r for case in source["cases"] for r in case["sourceRows"]]
    if "content" in source:
        return source["content"]
    raise RocError("Input must be saved PCL JSON or a saved ROC case index.")


def build_index(records, court_labels=None):
    courts = COURTS | (court_labels or {})
    groups = defaultdict(list)
    for row in records:
        case = row.get("courtCase", row)
        court = str(case.get("courtId") or row.get("courtId") or "").lower()
        if not court:
            raise RocError("PCL record has no court identifier.")
        raw = case.get("caseNumberFull") or row.get("caseNumberFull")
        if not raw:
            raw = f"{case['caseOffice']}:{case['caseYear']}{case['caseType']}{case['caseNumber']}"
        groups[case_key(court, raw)].append(row)
    result = []
    for key, entries in groups.items():
        court, number = key.split("|", 1)
        cases = [r.get("courtCase", r) for r in entries]
        warnings = []
        jurisdictions = {clean(c.get("jurisdictionType") or r.get("jurisdictionType")).lower() for r, c in zip(entries, cases)}
        kind = "Criminal" if jurisdictions == {"criminal"} else "Civil" if jurisdictions <= {"civil", "bankruptcy"} and jurisdictions else ""
        if not kind:
            warnings.append("Case Type not unambiguously supplied by PCL.")
        titles = {unescape(clean(c.get("caseTitle"))) for c in cases}
        captions = {t.split(" - ")[0] if kind == "Criminal" else t for t in titles}
        if len(captions) != 1:
            warnings.append("Conflicting PCL case captions; first source caption retained.")
        dates = sorted({c.get("dateFiled", "") for c in cases} - {""})
        if len(dates) != 1:
            warnings.append("PCL filing date missing or conflicting.")
        closed = [bool(c.get("effectiveDateClosed") or c.get("dateTermed") or r.get("effectiveDateClosed")) for r, c in zip(entries, cases)]
        status = "Closed" if all(closed) else "Mixed" if any(closed) else "Open"
        links = sorted({c.get("caseLink") or r.get("caseLink") for r, c in zip(entries, cases)} - {None, ""})
        codes = sorted({str(c.get("natureOfSuit") or r.get("natureOfSuit") or "").strip() for r, c in zip(entries, cases)} - {""})
        nature = "Charges not supplied by PCL" if kind == "Criminal" else "; ".join(f"{c} - {NOS[c]}" if c in NOS else c for c in codes) or "Not supplied by PCL"
        if kind != "Criminal" and any(c not in NOS for c in codes):
            warnings.append("Unmapped Nature of Suit code retained for review.")
        if court not in courts:
            warnings.append("District label unavailable; court code retained for review.")
        result.append({"key": key, "courtId": court, "caseNumber": number, "caseTitle": sorted(captions)[0],
                       "caseType": kind, "team": "", "court": "U.S. Bankruptcy Court" if court.endswith("bk") else "U.S. District Court",
                       "district": courts.get(court, court), "dateFiled": dates[0] if dates else "", "nature": nature,
                       "status": status, "pacerLink": links[0] if links else "", "allCaseLinks": links,
                       "warnings": warnings, "sourceRows": entries})
    return sorted(result, key=lambda r: (r["dateFiled"], r["district"], r["caseNumber"]), reverse=True)
