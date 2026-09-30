"""Bounded court validation: API case discovery, full reports, offline checks.

One results page and at most one docket per court/type. No replacement purchases,
automatic retries, lawyer-specific fixtures, or automatic registry promotion.
"""
from datetime import date
from html import escape
from pathlib import Path
import json
import time

from .common import RocError, fingerprint, name_key, now, read_json, write_json
from .courts import court_profile, profile_for_case, require_enabled
from .docket import enrich, parse_report
from .index import build_index
from .pacer import Session
from .progress import Progress
from .retrieve import CourtRetriever
from .store import RunStore


TYPES = {"Civil": "cv", "Criminal": "cr"}


def validation_plan(config):
    allowed = {"courts", "caseTypes", "dateFiledFrom", "dateFiledTo", "runDirectory",
               "budgetCents", "allowUnverifiedCourts", "requestDelaySeconds", "headless"}
    if set(config) - allowed:
        raise RocError("Unknown validation option: " + ", ".join(sorted(set(config) - allowed)))
    courts = config.get("courts")
    if not isinstance(courts, list) or not courts or any(not isinstance(c, str) for c in courts):
        raise RocError("Validation requires an explicit nonempty list of district court IDs.")
    if len(set(courts)) != len(courts):
        raise RocError("Duplicate validation court IDs are not allowed.")
    if type(config.get("allowUnverifiedCourts", False)) is not bool:
        raise RocError("allowUnverifiedCourts must be a boolean.")
    for code in courts:
        require_enabled(court_profile(code), config.get("allowUnverifiedCourts", False))
    kinds = config.get("caseTypes", list(TYPES))
    if (not isinstance(kinds, list) or not kinds or any(k not in TYPES for k in kinds)
            or len(set(kinds)) != len(kinds)):
        raise RocError("Validation caseTypes must be unique Civil and/or Criminal values.")
    for field in ("dateFiledFrom", "dateFiledTo"):
        try:
            date.fromisoformat(config[field])
        except (KeyError, TypeError, ValueError):
            raise RocError("Validation requires explicit ISO filing dates.") from None
    if config["dateFiledFrom"] > config["dateFiledTo"]:
        raise RocError("Validation filing dates are reversed.")
    if not isinstance(config.get("runDirectory"), str) or not config["runDirectory"].strip():
        raise RocError("Validation requires a runDirectory.")
    if type(config.get("budgetCents")) is not int or config["budgetCents"] < 0:
        raise RocError("Validation budgetCents must be a nonnegative integer.")
    delay = config.get("requestDelaySeconds", 5)
    if type(delay) not in (int, float) or not 1 <= delay <= 60:
        raise RocError("requestDelaySeconds must be between 1 and 60.")
    if type(config.get("headless", True)) is not bool:
        raise RocError("headless must be a boolean.")
    slots = []
    for court in courts:
        for kind in kinds:
            criteria = {"courtId": [court], "jurisdictionType": TYPES[kind],
                        "caseType": [TYPES[kind]], "dateFiledFrom": config["dateFiledFrom"],
                        "dateFiledTo": config["dateFiledTo"]}
            slots.append({"courtId": court, "caseType": kind, "criteria": criteria})
    return {"mode": "court-validation", "courts": courts, "caseTypes": kinds, "slots": slots,
            "maximumSearchPages": len(slots), "maximumDockets": len(slots),
            "maximumSearchCents": len(slots) * 10, "maximumDocketCents": len(slots) * 300,
            "maximumPlannedCents": len(slots) * 310, "budgetCents": config["budgetCents"],
            "methods": {"authentication": "official PACER API", "discovery": "official PCL case API",
                        "dockets": "full court-web reports; parties/counsel and terminated parties included"}}


def pick_case(response, slot):
    """Choose from exactly the purchased first page, not a complete case index."""
    info, rows = response.get("pageInfo", {}), response.get("content")
    if (not isinstance(rows, list) or info.get("number") != 0
            or info.get("numberOfElements") != len(rows) or len(rows) > 54):
        raise RocError("Unexpected case-search page; no docket selected.")
    total = info.get("totalElements")
    if type(total) is not int or total < len(rows):
        raise RocError("Unexpected case-search total; no docket selected.")
    candidates = build_index(rows)
    for case in candidates:
        if case["courtId"] != slot["courtId"] or case["caseType"] != slot["caseType"]:
            raise RocError("PCL returned a different court or case type; stop before retrieval.")
        if not slot["criteria"]["dateFiledFrom"] <= case["dateFiled"] <= slot["criteria"]["dateFiledTo"]:
            raise RocError("PCL returned a case outside the requested filing dates.")
        profile_for_case(case)
    # Prefer a normal named adversarial case. Avoid pro se-heavy NOS categories
    # when the purchased page offers another choice. Never buy a replacement.
    candidates = [c for c in candidates if c["caseTitle"] and not any(
        token in c["caseTitle"].casefold() for token in ("sealed", "john doe", "jane doe"))]
    def rank(case):
        source = case["sourceRows"][0]
        nos = str(source.get("courtCase", source).get("natureOfSuit", ""))
        preferred = bool(nos) and not nos.startswith(("5", "8"))
        return (not preferred if slot["caseType"] == "Civil" else False,
                " v. " not in case["caseTitle"].lower(), case["key"])
    candidates.sort(key=rank)
    return (candidates[0] if candidates else None), {"rowsInspected": len(rows),
        "totalMatchingRecords": total, "completeSearch": bool(info.get("last")),
        "selectionRule": "One candidate from first API page; no further pages or replacement reports."}


def assess_report(raw, case):
    report = parse_report(raw)
    profile = profile_for_case(case)
    if report["caseNumber"] != case["caseNumber"] or not profile.matches_heading(report["heading"]):
        raise RocError("Purchased report identity differs from the selected case.")
    if report["caseType"] != case["caseType"]:
        raise RocError("Purchased report case type differs from PCL.")
    warnings = list(report["warnings"])
    if not report["parties"]:
        warnings.append("No party blocks parsed.")
    trials = []
    names = sorted({n for p in report["parties"] for n in p["counsel"] if name_key(n)})
    for counsel in names:
        evidence = enrich(report, [counsel])
        # This is a consistency check against parsed blocks, not independent proof
        # that the parser segmented the source correctly. Preserve it for review.
        expected = [p["name"] for p in report["parties"] if any(
            name_key(n) == name_key(counsel) for n in p["counsel"])]
        if evidence["representedParties"] != expected:
            raise RocError("Attorney-to-party consistency check failed.")
        trials.append({"attorney": counsel, **evidence})
        warnings.extend(counsel + ": " + warning for warning in evidence["warnings"])
    roles = sorted({t["team"] for t in trials if t["team"]})
    required = {"Civil Plaintiff", "Civil Defense"} if case["caseType"] == "Civil" else {"Prosecution", "Criminal Defense"}
    missing = required - set(roles)
    if missing:
        warnings.append("Sample does not exercise: " + ", ".join(sorted(missing)))
    if case["caseType"] == "Civil" and not report["natureOfSuit"]:
        warnings.append("Civil Nature of Suit missing from parsed report.")
    if case["caseType"] == "Criminal":
        defense = [t for t in trials if t["team"] == "Criminal Defense"]
        if not any(t["nature"] and t["selectedCounts"] for t in defense):
            warnings.append("No unambiguous client-specific criminal count summary in this sample.")
    return {"automatedAssessment": "review-needed" if warnings else "checks-passed",
            "independentlyReviewed": False, "rolesExercised": roles,
            "warnings": warnings, "attorneyTrials": trials, "parsedDocket": report,
            "note": "Automated structural/consistency checks only. Source spot-check required; registry unchanged."}


def save_results(root, plan, samples, store, status, reason=None):
    summary = {"generatedUtc": now(), "status": status, "stopReason": reason,
               "plan": plan, "samples": samples, "chargedCents": store.spent,
               "downloadedDockets": sum(bool(s.get("sourceFile")) for s in samples),
               "automatedChecksPassed": sum(s.get("assessment", {}).get("automatedAssessment") == "checks-passed" for s in samples),
               "registryPromoted": False}
    write_json(root / "validation-results.json", summary)
    table = []
    for s in samples:
        case, assessment = s.get("case") or {}, s.get("assessment", {})
        cells = [s["courtId"], s["caseType"], case.get("caseNumber", ""), case.get("caseTitle", ""),
                 assessment.get("automatedAssessment", s.get("status", "pending")),
                 ", ".join(assessment.get("rolesExercised", [])),
                 "; ".join(assessment.get("warnings", []) or [s.get("message", "")])]
        table.append("<tr>" + "".join("<td>" + escape(str(v)) + "</td>" for v in cells) + "</tr>")
    html = """<!doctype html><meta charset="utf-8"><title>ROC court validation</title>
    <style>body{font:15px system-ui;margin:32px;color:#182630}table{border-collapse:collapse;width:100%}
    th,td{text-align:left;padding:9px;border-bottom:1px solid #ccd6de;vertical-align:top}th{background:#e7eff4}</style>
    <h1>ROC court validation</h1>"""
    html += f"<p>Status: {escape(status)}. Saved dockets: {summary['downloadedDockets']}/{plan['maximumDockets']}. Receipts: ${store.spent / 100:.2f}.</p>"
    html += "<p>Automated checks are provisional. Review source reports before promoting court coverage.</p>"
    if reason:
        html += "<p>" + escape(reason) + "</p>"
    html += "<table><thead><tr>" + "".join("<th>" + h + "</th>" for h in
        ["Court", "Type", "Case", "Title", "Assessment", "Roles", "Review"]) + "</tr></thead><tbody>"
    (root / "validation-report.html").write_text(html + "".join(table) + "</tbody></table>", encoding="utf-8")
    return summary


def run_validation(config_path, live=False, session_provider=None):
    path = Path(config_path).resolve()
    config = read_json(path)
    plan = validation_plan(config)
    if not live:
        print(json.dumps({**plan, "networkRequests": 0}, indent=2))
        return 0
    root = (path.parent / config["runDirectory"]).resolve()
    with RunStore(root, config["budgetCents"]) as store:
        store.check_pending()
        progress = Progress(root)
        manifest_path = root / "validation-plan.json"
        identity = fingerprint({"slots": plan["slots"], "methods": plan["methods"]})
        if manifest_path.exists() and read_json(manifest_path).get("identity") != identity:
            raise RocError("Validation scope differs from this run folder. Use a new folder for a different batch.")
        write_json(manifest_path, {"identity": identity, **plan})
        progress("awaiting_sign_in", f"Validation: {len(plan['courts'])} courts, at most {plan['maximumDockets']} full reports. "
                 f"Total run cap ${config['budgetCents'] / 100:.2f}. Official API sign-in required.")
        session = (session_provider or Session.prompt)()
        retriever = CourtRetriever(session, store, headless=config.get("headless", True), progress=progress,
                                   allow_unverified=config.get("allowUnverifiedCourts", False))
        samples = []
        delay = config.get("requestDelaySeconds", 5)
        reason = None
        # Complete API discovery before any court report. An API error cannot
        # trigger web search or court retrieval as a substitute.
        try:
            for slot in plan["slots"]:
                params = {"criteria": slot["criteria"], "page": 0}
                if store.cached("pcl-case", params) is None:
                    time.sleep(delay)
                response = session.search_cases_page(slot["criteria"], 0, store)
                case, selection = pick_case(response, slot)
                samples.append({"courtId": slot["courtId"], "caseType": slot["caseType"], "case": case,
                                "selection": selection, "status": "selected" if case else "no-candidate"})
                progress("selecting_validation_cases", f"API sample {len(samples)}/{len(plan['slots'])}: "
                         f"{slot['courtId']} {slot['caseType']}; receipts ${store.spent / 100:.2f}.", chargedCents=store.spent)
                save_results(root, plan, samples, store, "selecting")
            for sample in samples:
                case = sample["case"]
                if case is None:
                    continue
                time.sleep(delay)
                source = retriever.retrieve(case)
                sample["sourceFile"] = str(source)
                sample["status"] = "downloaded"
                sample["assessment"] = assess_report(source.read_text(encoding="utf-8"), case)
                save_results(root, plan, samples, store, "retrieving")
        except RocError as exc:
            reason = str(exc)
        status = "stopped" if reason else "complete"
        result = save_results(root, plan, samples, store, status, reason)
        progress(status, f"Validation {status}: {result['downloadedDockets']}/{plan['maximumDockets']} reports saved; "
                 f"receipts ${store.spent / 100:.2f}." + (" " + reason if reason else " Review results before promoting coverage."),
                 chargedCents=store.spent, downloadedDockets=result["downloadedDockets"])
        return 2 if reason else 0


def execute_validation(config_path, live=False, session_provider=None):
    try:
        return run_validation(config_path, live, session_provider)
    except (RocError, KeyError, ValueError, FileNotFoundError, EOFError) as exc:
        print("ROC validation stopped: " + str(exc))
        try:
            path = Path(config_path).resolve()
            config = read_json(path)
            root = (path.parent / config["runDirectory"]).resolve()
            if not (root / ".run.lock").exists():
                Progress(root)("stopped", str(exc))
        except (KeyError, ValueError, OSError):
            pass
        return 2
    except KeyboardInterrupt:
        print("ROC validation interrupted. Check saved receipts before resuming.")
        return 130
