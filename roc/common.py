from __future__ import annotations

import hashlib
import json
import re
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path


class RocError(Exception):
    """An actionable stop, without secret request data in the message."""


class BudgetStop(RocError):
    """The next paid request cannot fit within the approved spending limit."""


def now():
    return datetime.now(timezone.utc).isoformat()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    # Windows readers (including our UI status polling) briefly deny deletion
    # of the old file. Retry only this atomic local rename, never the operation
    # that produced the data or any paid/authentication request.
    for attempt in range(7):
        try:
            temp.replace(path)
            break
        except PermissionError as exc:
            if getattr(exc, "winerror", None) not in (5, 32) or attempt == 6:
                raise
            time.sleep(0.02 * 2 ** attempt)


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def clean(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def name_key(name):
    # Conservative matching: exact normalized aliases, never fuzzy surnames or initials.
    name = clean(name)
    if name.count(",") == 1:
        last, rest = name.split(",")
        name = rest + " " + last
    name = unicodedata.normalize("NFKC", name).casefold()
    return clean(re.sub(r"[^\w\s'-]", "", name))


def normalize_case_number(value):
    match = re.match(r"^(\d+):(\d{4}|\d{2})-?([a-z]+)-?(\d+)(?:-.*)?$", clean(value), re.I)
    if not match:
        raise RocError(f"Unrecognized case number: {value}")
    office, year, kind, number = match.groups()
    return f"{int(office)}:{int(year) % 100:02d}-{kind.lower()}-{int(number):05d}"


def case_key(court, number):
    return court.lower() + "|" + normalize_case_number(number)
