"""Optional interactive session; credentials/tokens are never saved or auto-retried."""
import importlib
from pathlib import Path
import sys

from .common import RocError, now, read_json, write_json
from .pacer import Session


class SessionHolder:
    def __init__(self):
        self.session = None

    def get(self):
        if self.session is None:
            self.session = Session.prompt()
        else:
            print("Reusing this process's PACER API session; no new login.", flush=True)
        return self.session

    def clear(self):
        self.session = None


def reload_workflow():
    # Explicit Resume allows code fixes to take effect without re-authentication.
    # Do not reload common/store/pacer: keep exception identities and the session intact.
    importlib.invalidate_caches()
    for name in ("courts", "docket", "index", "output", "retrieve", "select", "validation", "cli"):
        importlib.reload(importlib.import_module("roc." + name))


def run_live_session(config_path, publish=False, *, workflow="run", reader=input, attempt=None, reloader=reload_workflow):
    if attempt is None and (not sys.stdin.isatty() or not sys.stdout.isatty()):
        raise RocError("--keep-session needs an interactive terminal.")
    config_path = Path(config_path).resolve()
    holder = SessionHolder()
    if attempt is None:
        def attempt(provider):
            if workflow == "validate-courts":
                from .validation import execute_validation
                return execute_validation(config_path, True, provider)
            from .cli import execute_run
            return execute_run(config_path, True, publish, provider)
    try:
        while True:
            result = attempt(holder.get)
            if result == 0 or result == 130 or holder.session is None:
                # Failed API authentication NEVER prompts an automatic second attempt.
                return result
            config = read_json(config_path)
            root = (config_path.parent / config["runDirectory"]).resolve()
            old = read_json(root / "status.json") if (root / "status.json").exists() else {}
            write_json(root / "status.json", old | {"updatedUtc": now(), "stage": "paused_session",
                "message": "ROC paused after an error. API session remains only in memory. No requests occur until explicit Resume.",
                "lastStopMessage": old.get("message", "")})
            print("\nROC is paused. Leave this terminal open to keep the API session in memory.", flush=True)
            print("No searches, reports or login retries occur while paused.", flush=True)
            while True:
                choice = reader("After a fix: R = resume with this session; L = new API sign-in; Q = quit: ").strip().lower()
                if choice in ("r", "l", "q"):
                    break
            if choice == "q":
                return result
            if choice == "l":
                holder.clear()
            reloader()
    finally:
        holder.clear()
