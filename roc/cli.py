import argparse
import json
from pathlib import Path
import sys

from .common import RocError, read_json, write_json
from .courts import registry_summary
from .index import build_index, records_from
from .output import publish_google
from .pacer import Session
from .engine import run_workflow
from .exports import output_directory
from .progress import Progress
from .select import select_dockets, validate_options
from .store import RunStore
from .search import subject_name, counsel_aliases, require_attorney


def resolve(base, value):
    return (base / value).resolve()


def run(config_path, live=False, publish=False, session_provider=None, checkpoint=None):
    """Terminal/file adapter; the browser calls the same engine directly."""
    config_path = Path(config_path).resolve()
    config = read_json(config_path)
    if publish:
        require_attorney(config)
    result = run_workflow(config, config_path.parent, live=live,
        session_provider=session_provider or Session.prompt, checkpoint=checkpoint,
        progress=Progress(resolve(config_path.parent, config["runDirectory"])))
    metadata = dict(result.metadata)
    if publish:
        if not config.get("googleOAuthFile"):
            raise RocError("Local outputs saved. Standalone Sheets publishing needs googleOAuthFile; this program does not use the chat connector.")
        root = resolve(config_path.parent, config["runDirectory"])
        with RunStore(root, config.get('budgetCents', 0)):
            if output_directory(root) != result.workbook.parent:
                raise RocError('This run changed before publication. Review its latest exports before publishing.')
            metadata["spreadsheetUrl"] = publish_google(result.cases, f"Record of Counsel (ROC): {subject_name(config)}",
                resolve(config_path.parent, config["googleOAuthFile"]), counsel_aliases(config), metadata["nameRules"])
            # Keep publication metadata outside the immutable local export set.
            write_json(root / "publication.json", {"spreadsheetUrl": metadata["spreadsheetUrl"], "workbook": str(result.workbook)})
            write_json(root / "result.json", metadata | {"workbook": str(result.workbook)})
    print(json.dumps(metadata | {"workbook": str(result.workbook)}, indent=2))
    return result.exit_code


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
