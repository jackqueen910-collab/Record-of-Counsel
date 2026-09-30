"""Select a bounded docket sample from any newly collected attorney index."""
from datetime import date

from .common import RocError, case_key
from .courts import court_profile, profile_for_case, require_enabled, validate_policy


def validate_options(config):
    validate_policy(config)
    explicit = config.get("retrieveDockets", [])
    if not isinstance(explicit, list):
        raise RocError("retrieveDockets must be a list.")
    for selection in explicit:
        if not isinstance(selection, dict) or not selection.get("caseNumber") or not selection.get("courtId"):
            raise RocError("Each explicit docket needs courtId and caseNumber.")
        case_key(selection["courtId"], selection["caseNumber"])
        require_enabled(court_profile(selection["courtId"]), config.get("allowUnverifiedCourts", False))
    options = config.get("dockets")
    if options is None:
        return
    if config.get("retrieveDockets"):
        raise RocError("Use dockets or retrieveDockets, not both.")
    if not isinstance(options, dict):
        raise RocError("dockets must be an object containing order and limit.")
    allowed = {"order", "limit", "maxPerCourt", "courts", "caseTypes", "dateFiledFrom", "dateFiledTo", "exclude"}
    if set(options) - allowed:
        raise RocError("Unknown docket-selection option: " + ", ".join(sorted(set(options) - allowed)))
    if type(options.get("limit")) is not int or options["limit"] < 1:
        raise RocError("Automatic docket selection requires an explicit positive integer limit.")
    if "maxPerCourt" in options and (type(options["maxPerCourt"]) is not int or options["maxPerCourt"] < 1):
        raise RocError("maxPerCourt must be a positive integer.")
    if options.get("order", "latest") not in ("latest", "oldest"):
        raise RocError("Docket order must be latest or oldest.")
    for field in ("courts", "caseTypes", "exclude"):
        if field in options and not isinstance(options[field], list):
            raise RocError(field + " must be a list.")
    for field in ("courts", "caseTypes"):
        if any(not isinstance(s, str) for s in options.get(field, [])):
            raise RocError(field + " must contain strings.")
    for court in options.get("courts", []):
        court_profile(court)
    for excluded in options.get("exclude", []):
        if not isinstance(excluded, dict) or not excluded.get("courtId") or not excluded.get("caseNumber"):
            raise RocError("Each exclusion needs courtId and caseNumber.")
        case_key(excluded["courtId"], excluded["caseNumber"])
    if set(options.get("caseTypes", [])) - {"Criminal", "Civil"}:
        raise RocError("Docket caseTypes must contain Criminal and/or Civil.")
    for field in ("dateFiledFrom", "dateFiledTo"):
        if field in options:
            try:
                date.fromisoformat(options[field])
            except (TypeError, ValueError):
                raise RocError(field + " must be an ISO date (YYYY-MM-DD).") from None
    if options.get("dateFiledFrom", "") > options.get("dateFiledTo", "9999-12-31"):
        raise RocError("Docket dateFiledFrom must not follow dateFiledTo.")


def select_dockets(cases, config):
    validate_options(config)
    allow_unverified = config.get("allowUnverifiedCourts", False)
    policy = "include-unverified" if allow_unverified else "sample-verified-only"
    def coverage(selected):
        return {code: court_profile(code).summary() for code in sorted({c["courtId"] for c in selected})}
    options = config.get("dockets")
    if options is None:
        by_key = {c["key"]: c for c in cases}
        selected, seen = [], set()
        for value in config.get("retrieveDockets", []):
            key = case_key(value["courtId"], value["caseNumber"])
            if key not in by_key:
                raise RocError("Docket selection is absent from this case index: " + key)
            if key in seen:
                raise RocError("Duplicate docket selection: " + key)
            require_enabled(profile_for_case(by_key[key]), allow_unverified)
            seen.add(key)
            selected.append(by_key[key])
        return selected, {"mode": "explicit", "validationPolicy": policy,
                          "selected": [c["key"] for c in selected], "courtCoverage": coverage(selected)}
    courts = {s.lower() for s in options.get("courts", [])}
    kinds = set(options.get("caseTypes", []))
    excluded = {case_key(v["courtId"], v["caseNumber"]) for v in options.get("exclude", [])}
    eligible, skipped = [], []
    for case in cases:
        if (courts and case["courtId"] not in courts) or (kinds and case["caseType"] not in kinds):
            continue
        if case["key"] in excluded:
            continue
        filed = case["dateFiled"]
        if not filed:
            skipped.append({"case": case["key"], "reason": "Missing filing date; cannot rank."})
            continue
        if not options.get("dateFiledFrom", "") <= filed <= options.get("dateFiledTo", "9999-12-31"):
            continue
        try:
            require_enabled(profile_for_case(case), allow_unverified)
        except RocError as exc:
            skipped.append({"case": case["key"], "reason": str(exc)})
            continue
        eligible.append(case)
    eligible.sort(key=lambda c: (c["dateFiled"], c["key"]), reverse=options.get("order", "latest") == "latest")
    selected, per_court = [], {}
    for case in eligible:
        count = per_court.get(case["courtId"], 0)
        if count >= options.get("maxPerCourt", options["limit"]):
            continue
        selected.append(case)
        per_court[case["courtId"]] = count + 1
        if len(selected) == options["limit"]:
            break
    return selected, {"mode": "automatic", "options": options, "eligibleCases": len(eligible),
                      "validationPolicy": policy, "courtCoverage": coverage(selected),
                      "selected": [c["key"] for c in selected], "skipped": skipped}
