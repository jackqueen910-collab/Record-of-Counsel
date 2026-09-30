"""Select a bounded docket sample from any newly collected attorney index."""
from datetime import date

from .common import RocError, case_key
from .retrieve import court_origin


def validate_options(config):
    options = config.get("dockets")
    if options is None:
        return
    if config.get("retrieveDockets"):
        raise RocError("Use dockets or retrieveDockets, not both.")
    if not isinstance(options, dict):
        raise RocError("dockets must be an object containing order and limit.")
    allowed = {"order", "limit", "courts", "caseTypes", "dateFiledFrom", "dateFiledTo", "exclude"}
    if set(options) - allowed:
        raise RocError("Unknown docket-selection option: " + ", ".join(sorted(set(options) - allowed)))
    if type(options.get("limit")) is not int or options["limit"] < 1:
        raise RocError("Automatic docket selection requires an explicit positive integer limit.")
    if options.get("order", "latest") not in ("latest", "oldest"):
        raise RocError("Docket order must be latest or oldest.")
    for field in ("courts", "caseTypes", "exclude"):
        if field in options and not isinstance(options[field], list):
            raise RocError(field + " must be a list.")
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
            court_origin(by_key[key]["pacerLink"])
            seen.add(key)
            selected.append(by_key[key])
        return selected, {"mode": "explicit", "selected": [c["key"] for c in selected]}
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
            court_origin(case["pacerLink"])
        except RocError:
            skipped.append({"case": case["key"], "reason": "No supported court adapter/link."})
            continue
        eligible.append(case)
    eligible.sort(key=lambda c: (c["dateFiled"], c["key"]), reverse=options.get("order", "latest") == "latest")
    selected = eligible[:options["limit"]]
    return selected, {"mode": "automatic", "options": options, "eligibleCases": len(eligible),
                      "selected": [c["key"] for c in selected], "skipped": skipped}
