"""Application workflow shared by the browser controller and terminal adapter.

Configuration and session callbacks are explicit. No terminal prompt, argument
parser, or chat is needed to use this engine.
"""
from dataclasses import dataclass
from pathlib import Path

from .common import BudgetStop, RocError, case_key, now, read_json, write_json
from .courts import DISTRICT_COURTS
from .docket import enrich, parse_report
from .index import build_index, records_from
from .name_rules import snapshot
from .exports import publish_exports
from .search_collection import collect_searches
from .progress import Progress
from .review import case_issues
from .retrieve import CourtRetriever, pending_confirmation, continuation_case
from .select import select_dockets, validate_options
from .store import RunStore
from .search import search_type, subject, subject_name, counsel_aliases, search_plan, require_attorney, indexed_parties


@dataclass(frozen=True)
class RunResult:
    exit_code: int
    metadata: dict
    workbook: Path
    cases: list[dict]
    message: str


def resolve(base, value):
    return (base / value).resolve()


def get_session(provider):
    if provider is None:
        raise RocError("Connect PACER before starting live work. No authentication was attempted.")
    return provider()


def run_workflow(config, base, *, live=False, session_provider=None, checkpoint=None, progress=None) -> RunResult:
    """Run one explicit operation; callers own credential entry and presentation."""
    base = Path(base).resolve()
    validate_options(config)
    if "nameRulesFile" in config and "nameRules" in config:
        raise RocError("Use nameRules or nameRulesFile, not both.")
    name_rules = snapshot(read_json(resolve(base, config["nameRulesFile"])) if config.get("nameRulesFile") else config.get("nameRules"))
    kind, target = search_type(config), subject(config)
    name, aliases = subject_name(config), counsel_aliases(config)
    if config.get('savedDockets') or config.get('retrieveDockets') or config.get('dockets') is not None:
        require_attorney(config)
    run_dir = resolve(base, config["runDirectory"])
    budget = config.get("budgetCents", 0)
    metadata = {"generatedUtc": now(), "searchType": kind, ('lawyer' if kind == 'attorney' else 'litigant'): target, "mode": "live" if live else "offline",
                "searchQueries": search_plan(config),
                "countPolicy": "all-listed-source-rows", "nameRules": name_rules,
                "methods": {"index": "saved records" if config.get("indexFile") else "official PCL API",
                            "dockets": "court web reports" if live else "saved reports"}}
    session = None
    stop_reason = None
    stop_reason_code = None
    with RunStore(run_dir, budget, checkpoint=checkpoint) as store:
        progress = progress or Progress(run_dir, echo=False)
        progress("starting", f"ROC: {name}. Run-folder spending limit: ${budget / 100:.2f}.")
        continuation = pending_confirmation(store) if live else None
        if not live:
            store.check_pending()
        store.checkpoint()
        source = config.get("indexFile")
        if source:
            records = records_from(read_json(resolve(base, source)))
        elif continuation:
            # No fresh search may run while a report reservation is unresolved.
            source = run_dir / 'pcl-records.json'
            if not source.is_file():
                raise RocError('Saved case index is needed to resume this report confirmation.')
            records = records_from(read_json(source))
        elif live:
            if budget < 10:
                raise RocError("Live index search requires a positive configured budget of at least 10 cents.")
            progress("awaiting_sign_in", "Ready for official PACER API sign-in. No new searches have been submitted.")
            session = get_session(session_provider)
            records = collect_searches(session, config, store, progress=progress)
            write_json(run_dir / "pcl-records.json", records)
        else:
            raise RocError("Offline mode needs indexFile. Network access is only enabled with --live.")
        cases = build_index(records, config.get("courtLabels"))
        if kind == 'litigant':
            for case in cases:
                case['indexedParties'] = indexed_parties(case)
        progress("index_ready", f"Built {len(cases)} cases from {len(records)} {kind} records.", chargedCents=store.spent)
        by_key = {c["key"]: c for c in cases}
        reports = list(config.get("savedDockets", []))
        selected, plan = select_dockets(cases, config)
        resumed_case = continuation_case(store, continuation, selected) if continuation else None
        write_json(run_dir / "docket-plan.json", plan)
        metadata["docketSelection"] = plan
        if selected:
            progress("dockets_selected", "Docket selection: " + ", ".join(c["key"] for c in selected),
                     selectedCases=plan["selected"], chargedCents=store.spent)
        elif config.get("dockets"):
            metadata["selectionNotice"] = "No cases met the docket filters and supported-court requirements."
        if selected and not live:
            metadata["retrievalSkipped"] = "Offline mode; requested live docket selections were not fetched."
        if live and selected:
            if session is None:
                progress("awaiting_sign_in", "Ready for official PACER API sign-in for court reports.")
                session = get_session(session_provider)
            retriever = CourtRetriever(session, store, headless=config.get("headless", True), progress=progress,
                                       allow_unverified=config.get("allowUnverifiedCourts", False))
            # Finish the existing reservation before any other selected case.
            if resumed_case:
                selected = [resumed_case, *[c for c in selected if c is not resumed_case]]
            for case in selected:
                try:
                    store.checkpoint()
                    path = (retriever.resume_confirmation(continuation, case) if case is resumed_case
                            else retriever.retrieve(case))
                    store.check_pending()
                    reports.append({"courtId": case["courtId"], "caseNumber": case["caseNumber"], "path": str(path)})
                except RocError as exc:
                    stop_reason = str(exc)
                    stop_reason_code = "budget" if isinstance(exc, BudgetStop) else "retrieval"
                    case["warnings"].append(stop_reason)
                    progress("retrieval_stopped", stop_reason, chargedCents=store.spent)
                    break
        processed = set()
        for source in reports:
            key = case_key(source["courtId"], source["caseNumber"])
            if key not in by_key:
                raise RocError("Saved docket does not belong to this case index: " + key)
            if key in processed:
                raise RocError("Duplicate docket input for the same court and case: " + key)
            processed.add(key)
            case = by_key[key]
            path = resolve(base, source["path"])
            try:
                parsed = parse_report(path.read_text(encoding="utf-8"))
                if parsed["caseNumber"] != case["caseNumber"]:
                    raise RocError("Saved docket case number differs from the configured case.")
                profile = DISTRICT_COURTS.get(case["courtId"])
                matches_district = (profile.matches_heading(parsed["heading"]) if profile else
                                    case["district"].casefold() in parsed["heading"].casefold())
                if not matches_district:
                    raise RocError("Docket court heading does not match the configured district; review the source.")
                evidence = enrich(parsed, aliases)
                evidence.update(sourceFile=str(path), sourceHeading=parsed["heading"], parsedDocket=parsed)
                if profile:
                    evidence["courtCoverage"] = profile.summary()
                case["enrichment"] = evidence
                case["team"] = evidence["team"]
                case["role"] = evidence["role"]
                case["nature"] = evidence["nature"] or ("Unresolved — see review" if parsed["caseType"] == "Criminal" else case["nature"])
                case["issues"] = evidence["issues"]
                case["fieldStatus"] = evidence["fieldStatus"]
                if parsed["caseType"] == "Civil" and parsed["natureOfSuit"]:
                    case["warnings"] = [w for w in case["warnings"] if w != "Unmapped Nature of Suit code retained for review."]
                case["warnings"].extend(evidence["warnings"])
            except RocError as exc:
                case["warnings"].append(str(exc))
        metadata.update(caseCount=len(cases), docketInputs=len(reports), enrichedCases=sum(bool(c.get("enrichment")) for c in cases),
                        resolvedTeamCases=sum(bool(c["team"]) for c in cases), unresolvedTeamCases=sum(not c["team"] for c in cases),
                        chargedCentsThisRunFolder=store.spent, status="stopped" if stop_reason else "complete",
                        stopReason=stop_reason, stopReasonCode=stop_reason_code)
        metadata["casesByIssueCategory"] = {category: sum(any(i["category"] == category for i in case_issues(c)) for c in cases)
                                            for category in ("missing-source", "not-tested", "needs-review")}
        metadata["resolvedRoleCases"] = metadata["resolvedTeamCases"]
        metadata["unresolvedRoleCases"] = metadata["unresolvedTeamCases"]
        title = f"Record of Counsel (ROC): {name}" + (' — Litigant search' if kind == 'litigant' else '')
        path = publish_exports(cases, run_dir, title, metadata)
        write_json(run_dir / "result.json", metadata | {"workbook": str(path)})
        message = (f"Saved {len(cases)} cases, enriched {metadata['enrichedCases']} dockets. "
                   f"PACER receipts: ${store.spent / 100:.2f}." + (" Stopped: " + stop_reason if stop_reason else ""))
        progress(metadata["status"], message, chargedCents=store.spent, workbook=str(path), stopReasonCode=stop_reason_code)
        return RunResult(2 if stop_reason else 0, metadata, path, cases, message)

