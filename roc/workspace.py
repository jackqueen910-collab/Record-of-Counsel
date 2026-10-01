"""Search/preview/retrieve controller. One explicit operation at a time; no scheduler."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from importlib.resources import files
from pathlib import Path
import os
import re
import threading
import uuid

from .cli import run
from .common import RocError, case_key, now, read_json, write_json
from .connection import BrowserConnection
from .courts import DISTRICT_COURTS, profile_for_case, require_enabled
from .index import build_index, records_from
from .parties import build_party_reports, case_role
from .review import case_issues
from .store import RunStore

DOWNLOADS = {"case-index.xlsx", "case-index.csv", "case-index.html", "evidence.json", "review.json", "party-reports.zip", "party-reports.json"}


def search_config(values):
    """Accept only the form's documented fields, never arbitrary engine configuration."""
    allowed = {"firstName", "lastName", "aliases", "courts", "dateFiledFrom", "dateFiledTo", "budgetCents"}
    if not isinstance(values, dict) or set(values) - allowed:
        raise RocError("Unexpected search fields.")
    names = {}
    for field in ("firstName", "lastName"):
        value = values.get(field)
        if not isinstance(value, str) or not value.strip() or len(value) > 100:
            raise RocError("Enter the lawyer's first and last names.")
        names[field] = value.strip()
    aliases = values.get("aliases", [])
    if not isinstance(aliases, list) or len(aliases) > 30 or any(not isinstance(a, str) or not a.strip() or len(a) > 200 for a in aliases):
        raise RocError("Aliases must be a list of full names.")
    names["aliases"] = list(dict.fromkeys(a.strip() for a in aliases))
    courts = values.get("courts", [])
    if not isinstance(courts, list) or any(not isinstance(c, str) or c not in DISTRICT_COURTS for c in courts):
        raise RocError("Choose courts from the district list.")
    criteria = {"courtId": list(dict.fromkeys(courts))} if courts else {}
    for field in ("dateFiledFrom", "dateFiledTo"):
        value = values.get(field)
        if value:
            try:
                if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
                    raise ValueError()
            except (ValueError, TypeError):
                raise RocError("Filing dates must be YYYY-MM-DD.") from None
            criteria[field] = value
    if criteria.get("dateFiledFrom", "") > criteria.get("dateFiledTo", "9999-12-31"):
        raise RocError("The start date must not follow the end date.")
    cap = values.get("budgetCents")
    if type(cap) is not int or cap < 10:
        raise RocError("Set a run spending cap of at least $0.10.")
    return {"lawyer": names, "search": criteria, "budgetCents": cap, "runDirectory": "."}


class Workspace:
    def __init__(self, root, session_provider=None):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.process_lock = self.root / ".workspace.lock"
        try:
            fd = os.open(self.process_lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w") as stream:
                stream.write(str(os.getpid()))
        except FileExistsError:
            raise RocError("This workspace is already open, or its prior process ended unexpectedly. Confirm that process has stopped before removing .workspace.lock.") from None
        self.connection = BrowserConnection()
        self.provider = session_provider or self.connection.get
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="roc-workflow")
        self.lock = threading.RLock()
        self.pause_event = threading.Event()
        self.active = None
        self.future = None
        self.stopping = False
        self.closed = False
        self.lock_released = False
        self.close_lock = threading.Lock()

    def close(self, release_lock=True):
        with self.close_lock:
            if not self.closed:
                self.request_stop()
                self.pool.shutdown(wait=True)
                self.closed = True
            if release_lock and not self.lock_released:
                self.process_lock.unlink(missing_ok=True)
                self.lock_released = True

    def request_stop(self):
        with self.lock:
            self.stopping = True
            self.pause_event.set()
            self.connection.stop()

    def sign_in(self, values):
        with self.lock:
            self.idle()
            fields = self.connection.prepare(values)
            self.future = self.pool.submit(self.connection.authenticate, fields)

    def disconnect(self):
        with self.lock:
            self.idle()
            self.connection.disconnect()

    def folder(self, identifier):
        if not isinstance(identifier, str) or not re.fullmatch(r"[0-9a-f]{32}", identifier):
            raise RocError("Unknown run.")
        path = (self.root / identifier).resolve()
        if path.parent != self.root or not (path / "workspace.json").is_file():
            raise RocError("Unknown run.")
        return path

    def manifest(self, identifier):
        return read_json(self.folder(identifier) / "workspace.json")

    def save(self, identifier, manifest):
        write_json(self.folder(identifier) / "workspace.json", manifest | {"updatedUtc": now()})

    def idle(self):
        if self.stopping:
            raise RocError("ROC is stopping. Reopen it to start another operation.")
        if self.connection.status()["connecting"]:
            raise RocError("Wait for the current PACER sign-in attempt to finish.")
        if self.active:
            raise RocError("Another operation is running. Pause it or wait for it to finish.")

    def new(self, values=None, demo=False):
        with self.lock:
            self.idle()
            config = ({"lawyer": {"firstName": "Jordan", "lastName": "Lawyer", "aliases": []},
                       "budgetCents": 0, "runDirectory": ".", "indexFile": "demo-index.json"}
                      if demo else search_config(values))
            identifier = uuid.uuid4().hex
            folder = self.root / identifier
            folder.mkdir()
            if demo:
                for name in ("demo-index.json", "demo-docket.html"):
                    (folder / name).write_bytes(files("roc").joinpath("ui", name).read_bytes())
            write_json(folder / "workspace.json", {"id": identifier, "createdUtc": now(), "updatedUtc": now(),
                "demo": demo, "config": config, "state": "new", "message": "Ready.", "lastAction": "search", "selected": []})
            self._start(identifier, "search")
            return identifier

    def ledger(self, identifier):
        path = self.folder(identifier) / "ledger.json"
        return read_json(path) if path.exists() else {"transactions": []}

    def receipts(self, identifier):
        ledger = self.ledger(identifier)
        txs = ledger["transactions"]
        return {"spentCents": sum(t.get("chargedCents", 0) for t in txs),
                "pendingCents": sum(t["reservedCents"] for t in txs if t["state"] != "complete"),
                "pendingCount": sum(t["state"] != "complete" for t in txs),
                "stoppedReason": ledger.get("stoppedReason", ""),
                "transactions": [{"kind": t["kind"], "state": t["state"], "chargedCents": t.get("chargedCents"),
                                  "reservedCents": t["reservedCents"], "startedUtc": t["startedUtc"]} for t in txs]}

    def completed_reports(self, identifier):
        reports = {}
        for tx in self.ledger(identifier)["transactions"]:
            if tx["kind"] != "docket" or tx["state"] != "complete":
                continue
            p = tx["parameters"]
            # Only the same full-case scope that the court retriever purchases.
            if p.get("scope") != "all-defendants" or p.get("partiesAndCounsel") is not True:
                continue
            key = case_key(p["court"], p["caseNumber"])
            path = (self.folder(identifier) / tx["responseFile"]).resolve()
            if not path.is_relative_to(self.folder(identifier)) or not path.is_file():
                raise RocError("A purchased report is missing. Restore it; do not buy it again.")
            reports[key] = {"courtId": p["court"], "caseNumber": p["caseNumber"], "path": str(path)}
        m = self.manifest(identifier)
        if m["demo"] and m.get("selected"):
            reports["nysdc|1:24-cr-00001"] = {"courtId": "nysdc", "caseNumber": "1:24-cr-00001",
                "path": str(self.folder(identifier) / "demo-docket.html")}
        return reports

    def cases(self, identifier):
        folder = self.folder(identifier)
        path = folder / "output/evidence.json"
        if path.exists():
            return read_json(path)["cases"]
        path = folder / "pcl-records.json"
        return build_index(records_from(read_json(path))) if path.exists() else []

    def summary(self, identifier, detail=False):
        m = self.manifest(identifier)
        receipt = self.receipts(identifier)
        busy = self.active == identifier
        state = m["state"]
        if state == "running" and not busy:
            state = "interrupted"
        folder = self.folder(identifier)
        progress_path = folder / "status.json"
        progress = read_json(progress_path) if progress_path.exists() else {}
        cases = self.cases(identifier) if detail else []
        result_path = folder / "result.json"
        metadata = read_json(result_path) if result_path.exists() else {}
        result = {"id": identifier, "createdUtc": m["createdUtc"], "updatedUtc": m["updatedUtc"],
            "lawyer": m["config"]["lawyer"], "search": m["config"].get("search", {}), "demo": m["demo"],
            "budgetCents": m["config"]["budgetCents"], "state": state, "busy": busy,
            "message": (progress.get("message", m["message"]) if busy else m["message"]),
            "stage": progress.get("stage", ""), "pauseRequested": busy and self.pause_event.is_set(),
            "lastAction": m["lastAction"], "caseCount": len(cases) if detail else metadata.get("caseCount", 0),
            "enrichedCount": sum(bool(c.get("enrichment")) for c in cases) if detail else metadata.get("enrichedCases", 0), **receipt,
            "downloads": [n for n in sorted(DOWNLOADS) if (folder / "output" / n).is_file()],
            "indexReady": (folder / "pcl-records.json").exists() or m["demo"] and (folder / "output/evidence.json").exists()}
        if state == "interrupted":
            result["message"] = "The previous process ended. Resume explicitly to reuse saved work; pending receipts still block purchases."
        if detail:
            rows = []
            for c in cases:
                try:
                    require_enabled(profile_for_case(c))
                    reason = ""
                except RocError as exc:
                    reason = str(exc)
                rows.append({k: c.get(k, "") for k in ("key", "caseNumber", "caseTitle", "caseType", "team", "court",
                    "district", "dateFiled", "nature", "status")} | {"eligible": not reason, "ineligibleReason": reason,
                    "role": case_role(c), "enriched": bool(c.get("enrichment")), "issues": case_issues(c),
                    "representedParties": c.get("enrichment", {}).get("representedParties", [])})
            result["cases"] = rows
            lawyer = m["config"]["lawyer"]
            result["partyReports"] = build_party_reports(cases, [lawyer["firstName"] + " " + lawyer["lastName"], *lawyer.get("aliases", [])])
        return result

    def list(self):
        with self.lock:
            jobs = [self.summary(p.parent.name) for p in self.root.glob("*/workspace.json")]
            return {"active": self.active, "jobs": sorted(jobs, key=lambda j: j["createdUtc"], reverse=True),
                    "connection": self.connection.status(), "stopping": self.stopping, "closed": self.closed}

    def quote(self, identifier, keys):
        with self.lock:
            self.idle()
            m = self.manifest(identifier)
            if not self.summary(identifier)["indexReady"]:
                raise RocError("Finish the case search before selecting dockets.")
            if not isinstance(keys, list) or not keys or any(not isinstance(k, str) for k in keys) or len(set(keys)) != len(keys):
                raise RocError("Select one or more distinct cases.")
            cases = {c["key"]: c for c in self.cases(identifier)}
            selected = []
            for key in keys:
                if key not in cases:
                    raise RocError("A selected case is absent from this search.")
                c = cases[key]
                require_enabled(profile_for_case(c))
                selected.append(c)
            receipt = self.receipts(identifier)
            if receipt["pendingCount"] or receipt["stoppedReason"]:
                raise RocError("Unresolved receipts block retrieval. Use Check saved receipts; do not repeat the request.")
            cached = self.completed_reports(identifier)
            fresh = [] if m["demo"] else [c for c in selected if c["key"] not in cached]
            ceiling = len(fresh) * 300
            return {"keys": keys, "selectedCount": len(keys), "newReports": len(fresh),
                "cachedReports": len(keys) - len(fresh), "maximumAdditionalCents": ceiling,
                "spentCents": receipt["spentCents"], "budgetCents": m["config"]["budgetCents"],
                "fitsBudget": receipt["spentCents"] + ceiling <= m["config"]["budgetCents"],
                "cases": [{"caseNumber": c["caseNumber"], "district": c["district"]} for c in selected]}

    def act(self, identifier, action, values=None):
        values = values or {}
        with self.lock:
            if action == "pause":
                if self.active != identifier:
                    raise RocError("This run is not active.")
                self.pause_event.set()
                return
            self.idle()
            m = self.manifest(identifier)
            if action == "budget":
                cap = values.get("budgetCents")
                r = self.receipts(identifier)
                if m["demo"] or type(cap) is not int or cap < max(10, r["spentCents"] + r["pendingCents"]):
                    raise RocError("The cap must cover confirmed charges and pending reservations.")
                m["config"]["budgetCents"] = cap
                self.save(identifier, m)
                return
            if action == "retrieve":
                quote = self.quote(identifier, values.get("keys"))
                if not quote["fitsBudget"]:
                    raise RocError("The selection's maximum charge exceeds the remaining cap. Select fewer cases or explicitly change the cap.")
                m["selected"] = quote["keys"]
                self.save(identifier, m)
            elif action == "resume":
                if m["state"] not in ("stopped", "running"):
                    raise RocError("Only a stopped or interrupted operation can be resumed.")
                action = m["lastAction"]
            elif action == "export":
                if not self.summary(identifier)["indexReady"]:
                    raise RocError("Finish the search before exporting.")
            elif action == "signin":
                raise RocError("Use Connect PACER in the interface. Sign-in does not restart a saved run.")
            elif action != "reconcile":
                raise RocError("Unknown operation.")
            self._start(identifier, action)

    def _start(self, identifier, action):
        self.idle()
        m = self.manifest(identifier)
        if m["state"] == "running":
            m["needsResume"] = True  # Prior process interruption, not automatic completion.
        if action in ("search", "retrieve"):
            m["lastAction"] = action
        m.update(state="running", message="Starting " + action + ".")
        self.save(identifier, m)
        self.active = identifier
        self.pause_event.clear()
        self.future = self.pool.submit(self._work, identifier, action)

    def checkpoint(self):
        if self.pause_event.is_set():
            raise RocError("Paused at your request. Saved results and receipts are retained.")

    def _work(self, identifier, action):
        folder = self.folder(identifier)
        try:
            m = self.manifest(identifier)
            config = dict(m["config"])
            if action == "reconcile":
                with RunStore(folder, config["budgetCents"]) as store:
                    store.reconcile()
                code, message = 0, "Saved receipts checked. No network requests; choose Resume to continue."
            else:
                if action != "search" and not m["demo"]:
                    config["indexFile"] = "pcl-records.json"
                reports = self.completed_reports(identifier)
                config["savedDockets"] = list(reports.values())
                if action == "retrieve" and not m["demo"]:
                    config["retrieveDockets"] = [{"courtId": key.split("|")[0], "caseNumber": key.split("|")[1]}
                                                for key in m["selected"] if key not in reports]
                write_json(folder / "config.json", config)
                code = run(folder / "config.json", live=not m["demo"] and action != "export",
                           session_provider=self.provider, checkpoint=self.checkpoint)
                progress = read_json(folder / "status.json")
                message = ("Case index ready. Select dockets to enrich, or download the index now." if action == "search" and code == 0
                           else progress["message"])
            with self.lock:
                m = self.manifest(identifier)
                # Auxiliary actions never erase an interrupted search/retrieval's Resume control.
                auxiliary = action in ("reconcile", "export")
                m.update(state="stopped" if code or auxiliary and m.get("needsResume") else "ready", message=message)
                if not auxiliary:
                    m["needsResume"] = bool(code)
                self.save(identifier, m)
        except RocError as exc:
            with self.lock:
                m = self.manifest(identifier)
                m.update(state="stopped", message=str(exc), needsResume=True)
                self.save(identifier, m)
        except Exception as exc:
            # Neither raw exceptions nor credentials are exposed through HTTP or written to job files.
            with self.lock:
                m = self.manifest(identifier)
                m.update(state="stopped", message=f"Unexpected local error ({type(exc).__name__}). Saved receipts are retained; no automatic retry. Check the run files before resuming.", needsResume=True)
                self.save(identifier, m)
        finally:
            with self.lock:
                self.active = None

    def download(self, identifier, name):
        if name not in DOWNLOADS:
            raise RocError("Unknown export.")
        if self.active == identifier:
            raise RocError("Wait for this operation to finish before downloading its outputs.")
        path = (self.folder(identifier) / "output" / name).resolve()
        if not path.is_relative_to(self.folder(identifier)) or not path.is_file():
            raise RocError("This export is not available yet.")
        return path
