import argparse
import json
from pathlib import Path
import sys

from .common import RocError, case_key, now, read_json, write_json
from .courts import DISTRICT_COURTS, registry_summary
from .docket import enrich, parse_report
from .index import build_index, records_from
from .output import export_local, publish_google
from .pacer import Session, collect_index
from .progress import Progress
from .review import case_issues
from .retrieve import CourtRetriever
from .select import select_dockets, validate_options
from .store import RunStore


def resolve(base, value):
    return (base / value).resolve()


def run(config_path, live=False, publish=False, session_provider=None, checkpoint=None):
    config_path = Path(config_path).resolve()
    config = read_json(config_path)
    validate_options(config)
    base = config_path.parent
    lawyer = config["lawyer"]
    first, last = lawyer["firstName"].strip(), lawyer["lastName"].strip()
    aliases = [first + " " + last] + lawyer.get("aliases", [])
    if not first or not last:
        raise RocError("Both firstName and lastName are required.")
    run_dir = resolve(base, config["runDirectory"])
    output_dir = run_dir / "output"
    budget = config.get("budgetCents", 0)
    metadata = {"generatedUtc": now(), "lawyer": lawyer, "mode": "live" if live else "offline",
                "countPolicy": "all-listed-source-rows",
                "methods": {"index": "saved records" if config.get("indexFile") else "official PCL API",
                            "dockets": "court web reports" if live else "saved reports"}}
    session = None
    stop_reason = None
    with RunStore(run_dir, budget, checkpoint=checkpoint) as store:
        progress = Progress(run_dir)
        progress("starting", f"ROC: {first} {last}. Run-folder spending limit: ${budget / 100:.2f}.")
        store.check_pending()
        store.checkpoint()
        source = config.get("indexFile")
        if source:
            records = records_from(read_json(resolve(base, source)))
        elif live:
            if budget < 10:
                raise RocError("Live index search requires a positive configured budget of at least 10 cents.")
            progress("awaiting_sign_in", "Ready for official PACER API sign-in. No new searches have been submitted.")
            session = (session_provider or Session.prompt)()
            criteria = {"firstName": first, "lastName": last, "partyType": "aty"}
            if config.get("search"):
                criteria["courtCase"] = config["search"]
            records = collect_index(session, criteria, store, delay=max(1, config.get("requestDelaySeconds", 5)), progress=progress)
            write_json(run_dir / "pcl-records.json", records)
        else:
            raise RocError("Offline mode needs indexFile. Network access is only enabled with --live.")
        cases = build_index(records, config.get("courtLabels"))
        progress("index_ready", f"Built {len(cases)} cases from {len(records)} attorney records.", chargedCents=store.spent)
        by_key = {c["key"]: c for c in cases}
        reports = list(config.get("savedDockets", []))
        selected, plan = select_dockets(cases, config)
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
                session = (session_provider or Session.prompt)()
            retriever = CourtRetriever(session, store, headless=config.get("headless", True), progress=progress,
                                       allow_unverified=config.get("allowUnverifiedCourts", False))
            for case in selected:
                try:
                    store.checkpoint()
                    path = retriever.retrieve(case)
                    reports.append({"courtId": case["courtId"], "caseNumber": case["caseNumber"], "path": str(path)})
                except RocError as exc:
                    stop_reason = str(exc)
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
                        chargedCentsThisRunFolder=store.spent, status="stopped" if stop_reason else "complete", stopReason=stop_reason)
        metadata["casesByIssueCategory"] = {category: sum(any(i["category"] == category for i in case_issues(c)) for c in cases)
                                            for category in ("missing-source", "not-tested", "needs-review")}
        metadata["resolvedRoleCases"] = metadata["resolvedTeamCases"]
        metadata["unresolvedRoleCases"] = metadata["unresolvedTeamCases"]
        title = f"Record of Counsel (ROC): {first} {last}"
        path = export_local(cases, output_dir, title, metadata)
        if publish:
            if not config.get("googleOAuthFile"):
                raise RocError("Local outputs saved. Standalone Sheets publishing needs googleOAuthFile; this program does not use the chat connector.")
            metadata["spreadsheetUrl"] = publish_google(cases, title, resolve(base, config["googleOAuthFile"]), aliases)
        write_json(run_dir / "result.json", metadata | {"workbook": str(path)})
        progress(metadata["status"], f"Saved {len(cases)} cases, enriched {metadata['enrichedCases']} dockets. "
                 f"PACER receipts: ${store.spent / 100:.2f}." + (" Stopped: " + stop_reason if stop_reason else ""),
                 chargedCents=store.spent, workbook=str(path))
        print(json.dumps(metadata | {"workbook": str(path)}, indent=2))
        return 2 if stop_reason else 0


def plan_run(config_path):
    """Read a saved index and print a selection plan. No login, purchases or writes."""
    config_path = Path(config_path).resolve()
    config = read_json(config_path)
    validate_options(config)
    base = config_path.parent
    source = (resolve(base, config["indexFile"]) if config.get("indexFile") else
              resolve(base, config["runDirectory"]) / "pcl-records.json")
    if not source.exists():
        raise RocError("Offline plan needs indexFile or an existing run's pcl-records.json. No PACER request was made.")
    cases = build_index(records_from(read_json(source)), config.get("courtLabels"))
    selected, plan = select_dockets(cases, config)
    return {"mode": "offline-plan", "networkRequests": 0, "caseCount": len(cases),
            "selectedDockets": len(selected), "docketSelection": plan}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Record of Counsel — deterministic, on-demand PACER workflow")
    sub = parser.add_subparsers(dest="command", required=True)
    interface = sub.add_parser("ui", help="Open the local search, preview, docket selection and export interface.")
    interface.add_argument("--directory", type=Path, default=Path("runs/workspace"))
    interface.add_argument("--port", type=int, default=0)
    interface.add_argument("--no-open", action="store_true", help="Print the local URL without opening a browser.")
    task = sub.add_parser("run", help="Build a case index and enrich selected dockets. Offline by default.")
    task.add_argument("config", type=Path)
    task.add_argument("--live", action="store_true", help="Enable official API searches and bounded court-web report selection within configured budget.")
    task.add_argument("--publish", action="store_true", help="Publish a fresh Google Sheet using your own configured OAuth grant.")
    task.add_argument("--keep-session", action="store_true", help="Pause after errors with the API token in memory; resume only on explicit terminal input.")
    reconcile = sub.add_parser("reconcile", help="Resolve pending receipts from saved responses only; no network.")
    reconcile.add_argument("run_directory", type=Path)
    inventory = sub.add_parser("courts", help="List registered district courts and validation status; no network.")
    inventory.add_argument("--json", action="store_true", help="Print the court registry as JSON.")
    plan = sub.add_parser("plan", help="Preview docket selection from saved index data; no login, network or purchases.")
    plan.add_argument("config", type=Path)
    validate = sub.add_parser("validate-courts", help="One API results page and one full docket per court/type; preview by default.")
    validate.add_argument("config", type=Path)
    validate.add_argument("--live", action="store_true")
    validate.add_argument("--keep-session", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "ui":
            from .interface import serve
            return serve(args.directory, args.port, not args.no_open)
        if args.command == "validate-courts":
            if args.keep_session:
                if not args.live:
                    raise RocError("--keep-session requires --live.")
                from .console import run_live_session
                return run_live_session(args.config, workflow="validate-courts")
            from .validation import execute_validation
            return execute_validation(args.config, args.live)
        if args.command == "courts":
            summary = registry_summary()
            if args.json:
                print(json.dumps(summary, indent=2))
            else:
                print(f"{summary['registeredDistrictCourts']} registered district courts; "
                      f"{summary['sampleVerifiedCourts']} have a live-verified sample. No network requests.")
                for court in summary["courts"]:
                    print(f"{court['courtId']:<7} {court['validationStatus']:<16} {court['district']}")
                print("Unverified courts require allowUnverifiedCourts: true for controlled testing.")
            return 0
        if args.command == "plan":
            print(json.dumps(plan_run(args.config), indent=2))
            return 0
        if args.command == "reconcile":
            with RunStore(args.run_directory, 0) as store:
                store.reconcile()
            print("Saved receipts reconciled. No network requests made.")
            return 0
        if args.keep_session:
            if not args.live:
                raise RocError("--keep-session requires --live.")
            from .console import run_live_session
            return run_live_session(args.config, args.publish)
        return execute_run(args.config, args.live, args.publish)
    except (RocError, KeyError, ValueError, FileNotFoundError, EOFError) as exc:
        print("ROC stopped: " + str(exc), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("ROC interrupted. Check the saved ledger before any paid retry.", file=sys.stderr)
        return 130


def execute_run(config_path, live=False, publish=False, session_provider=None):
    """One attempt. The optional console owns the session across explicit resumes."""
    try:
        return run(config_path, live, publish, session_provider)
    except (RocError, KeyError, ValueError, FileNotFoundError, EOFError) as exc:
        print("ROC stopped: " + str(exc), file=sys.stderr)
        # The context manager has released our lock. Never overwrite another active run's status.
        try:
            config_path = Path(config_path).resolve()
            config = read_json(config_path)
            root = resolve(config_path.parent, config["runDirectory"])
            if not (root / ".run.lock").exists():
                Progress(root)("stopped", str(exc))
        except (KeyError, ValueError, OSError):
            pass
        return 2
    except KeyboardInterrupt:
        print("ROC interrupted. Check the saved ledger before any paid retry.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
