import argparse
import json
from pathlib import Path
import sys

from .common import RocError, case_key, now, read_json, write_json
from .docket import enrich, parse_report
from .index import build_index, records_from
from .output import export_local, publish_google
from .pacer import Session, collect_index
from .progress import Progress
from .retrieve import CourtRetriever
from .select import select_dockets, validate_options
from .store import RunStore


def resolve(base, value):
    return (base / value).resolve()


def run(config_path, live=False, publish=False):
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
                "methods": {"index": "saved records" if config.get("indexFile") else "official PCL API",
                            "dockets": "court web reports" if live else "saved reports"}}
    session = None
    stop_reason = None
    with RunStore(run_dir, budget) as store:
        progress = Progress(run_dir)
        progress("starting", f"ROC: {first} {last}. Run-folder spending limit: ${budget / 100:.2f}.")
        store.check_pending()
        source = config.get("indexFile")
        if source:
            records = records_from(read_json(resolve(base, source)))
        elif live:
            if budget < 10:
                raise RocError("Live index search requires a positive configured budget of at least 10 cents.")
            progress("awaiting_sign_in", "Ready for official PACER API sign-in. No new searches have been submitted.")
            session = Session.prompt()
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
                session = Session.prompt()
            retriever = CourtRetriever(session, store, headless=config.get("headless", True), progress=progress)
            for case in selected:
                try:
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
                if case["district"].casefold() not in parsed["heading"].casefold():
                    raise RocError("Docket court heading does not match the configured district; review the source.")
                evidence = enrich(parsed, aliases)
                evidence.update(sourceFile=str(path), sourceHeading=parsed["heading"], parsedDocket=parsed)
                case["enrichment"] = evidence
                case["team"] = evidence["team"]
                case["nature"] = evidence["nature"] or ("Unresolved — see review" if parsed["caseType"] == "Criminal" else case["nature"])
                case["warnings"].extend(evidence["warnings"])
            except RocError as exc:
                case["warnings"].append(str(exc))
        metadata.update(caseCount=len(cases), docketInputs=len(reports), enrichedCases=sum(bool(c.get("enrichment")) for c in cases),
                        resolvedTeamCases=sum(bool(c["team"]) for c in cases), unresolvedTeamCases=sum(not c["team"] for c in cases),
                        chargedCentsThisRunFolder=store.spent, status="stopped" if stop_reason else "complete", stopReason=stop_reason)
        title = f"Record of Counsel (ROC): {first} {last}"
        path = export_local(cases, output_dir, title, metadata)
        if publish:
            if not config.get("googleOAuthFile"):
                raise RocError("Local outputs saved. Standalone Sheets publishing needs googleOAuthFile; this program does not use the chat connector.")
            metadata["spreadsheetUrl"] = publish_google(cases, title, resolve(base, config["googleOAuthFile"]))
        write_json(run_dir / "result.json", metadata | {"workbook": str(path)})
        progress(metadata["status"], f"Saved {len(cases)} cases, enriched {metadata['enrichedCases']} dockets. "
                 f"PACER receipts: ${store.spent / 100:.2f}." + (" Stopped: " + stop_reason if stop_reason else ""),
                 chargedCents=store.spent, workbook=str(path))
        print(json.dumps(metadata | {"workbook": str(path)}, indent=2))
        return 2 if stop_reason else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Record of Counsel — deterministic, on-demand PACER workflow")
    sub = parser.add_subparsers(dest="command", required=True)
    task = sub.add_parser("run", help="Build a case index and enrich selected dockets. Offline by default.")
    task.add_argument("config", type=Path)
    task.add_argument("--live", action="store_true", help="Enable official API searches and bounded court-web report selection within configured budget.")
    task.add_argument("--publish", action="store_true", help="Publish a fresh Google Sheet using your own configured OAuth grant.")
    reconcile = sub.add_parser("reconcile", help="Resolve pending receipts from saved responses only; no network.")
    reconcile.add_argument("run_directory", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "reconcile":
            with RunStore(args.run_directory, 0) as store:
                store.reconcile()
            print("Saved receipts reconciled. No network requests made.")
            return 0
        return run(args.config, args.live, args.publish)
    except (RocError, KeyError, ValueError, FileNotFoundError, EOFError) as exc:
        print("ROC stopped: " + str(exc), file=sys.stderr)
        # The context manager has released our lock. Never overwrite another active run's status.
        if args.command == "run":
            try:
                config = read_json(args.config)
                root = resolve(args.config.resolve().parent, config["runDirectory"])
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
