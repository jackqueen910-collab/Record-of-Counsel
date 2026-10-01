"""Durable spending reservations and response cache; paid requests never auto-retry."""
import os
from pathlib import Path

from .common import BudgetStop, RocError, fingerprint, now, read_json, write_json
from .docket import receipt_cents


def api_fee(raw):
    from decimal import Decimal, InvalidOperation
    import json
    try:
        fee = Decimal(str(json.loads(raw)["receipt"]["searchFee"]))
        if not fee.is_finite() or fee < 0 or fee * 100 != int(fee * 100):
            raise ValueError()
        return int(fee * 100)
    except (KeyError, ValueError, TypeError, InvalidOperation) as exc:
        raise RocError("PCL receipt missing or invalid; do not repeat this request.") from exc


class RunStore:
    def __init__(self, root, budget_cents, checkpoint=None):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.limit = budget_cents
        self.checkpoint = checkpoint or (lambda: None)
        if type(budget_cents) is not int or budget_cents < 0:
            raise RocError("Budget must be a nonnegative integer number of cents.")
        self.lock = self.root / ".run.lock"
        self.path = self.root / "ledger.json"

    def __enter__(self):
        try:
            fd = os.open(self.lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w") as stream:
                stream.write(str(os.getpid()))
        except FileExistsError as exc:
            raise RocError("Run folder is locked. Confirm its prior process has stopped before removing .run.lock.") from exc
        self.ledger = read_json(self.path) if self.path.exists() else {"transactions": []}
        return self

    def __exit__(self, *_):
        self.lock.unlink(missing_ok=True)

    @property
    def spent(self):
        return sum(t.get("chargedCents", 0) for t in self.ledger["transactions"])

    def save(self):
        write_json(self.path, self.ledger)

    def check_pending(self):
        if any(t["state"] != "complete" for t in self.ledger["transactions"]):
            raise RocError("An earlier paid request has an unresolved receipt. Run reconcile; do not retry retrieval.")
        if self.ledger.get("stoppedReason"):
            raise RocError(self.ledger["stoppedReason"])

    def cached(self, kind, parameters):
        key = fingerprint({"kind": kind, "parameters": parameters})
        for t in self.ledger["transactions"]:
            if t["key"] == key:
                if t["state"] != "complete":
                    raise RocError("This request already has an unresolved receipt; no retry.")
                path = self.root / t["responseFile"]
                if not path.exists():
                    raise RocError("Previously purchased response is missing; restore it instead of buying it again.")
                return path
        return None

    def check_budget(self, max_cents):
        if self.spent + max_cents > self.limit:
            raise BudgetStop(f"Budget stop: ${self.spent / 100:.2f} spent; next request reserves ${max_cents / 100:.2f}; limit ${self.limit / 100:.2f}.")

    def reserve(self, kind, parameters, max_cents):
        self.checkpoint()
        self.check_pending()
        self.check_budget(max_cents)
        key = fingerprint({"kind": kind, "parameters": parameters})
        if any(t["key"] == key for t in self.ledger["transactions"]):
            raise RocError("Duplicate paid request refused; use the saved response.")
        t = {"key": key, "kind": kind, "parameters": parameters, "reservedCents": max_cents,
             "state": "pending", "startedUtc": now(), "responseFile": f"responses/{key}.{'html' if kind == 'docket' else 'json'}"}
        self.ledger["transactions"].append(t)
        self.save()
        return t

    def save_response(self, transaction, raw):
        """Save an authentic reply without resolving its outstanding reservation."""
        path = self.root / transaction["responseFile"]
        path.parent.mkdir(parents=True, exist_ok=True)
        # Save the full reply BEFORE interpreting the receipt.
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(raw, encoding="utf-8")
        temp.replace(path)
        return path

    def finish(self, transaction, raw):
        path = self.save_response(transaction, raw)
        cost = receipt_cents(raw) if transaction["kind"] == "docket" else api_fee(raw)
        transaction.update(state="complete", chargedCents=cost, completedUtc=now())
        if cost > transaction["reservedCents"]:
            self.ledger["stoppedReason"] = "Receipt exceeded the reserved charge; reconcile before further paid requests."
        self.save()
        self.check_pending()
        return path

    def reconcile(self):
        # Only a saved authentic response resolves an unknown charge; never assume zero.
        for t in self.ledger["transactions"]:
            if t["state"] != "complete":
                path = self.root / t["responseFile"]
                if not path.exists():
                    raise RocError("No saved reply for pending request. Check PACER billing before any manual reconciliation.")
                self.finish(t, path.read_text(encoding="utf-8"))
        self.check_pending()
