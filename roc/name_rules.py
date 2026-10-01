"""Explicit, reversible report-name groupings; never counsel-matching aliases."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import unicodedata
import uuid

from .common import RocError, clean, now, read_json, write_json


def party_key(name):
    return clean(unicodedata.normalize("NFKC", name)).casefold()


def normalize_rules(rules):
    if not isinstance(rules, list) or len(rules) > 1000:
        raise RocError("Name rules must be a list of at most 1,000 groups.")
    normalized, used, identifiers = [], {}, set()
    for rule in rules:
        if not isinstance(rule, dict) or set(rule) != {"id", "label", "kind", "names"}:
            raise RocError("A name rule needs id, label, kind and names.")
        identifier = rule["id"]
        if not isinstance(identifier, str) or not re.fullmatch(r"[0-9a-f]{32}", identifier) or identifier in identifiers:
            raise RocError("Name rule identifiers must be distinct.")
        identifiers.add(identifier)
        if rule["kind"] not in ("name-correction", "organization-group"):
            raise RocError("Choose Name correction or Organization group.")
        names = rule["names"]
        if not isinstance(names, list) or not 1 <= len(names) <= 500:
            raise RocError("Enter between 1 and 500 source names, one per line.")
        values = [rule["label"], *names]
        if any(not isinstance(n, str) or not clean(n) or len(n) > 500 or any(ord(c) < 32 for c in n) for n in values):
            raise RocError("Names must be nonempty single lines of at most 500 characters.")
        label = clean(rule["label"])
        # The preferred label is a member too: no hidden chains or cycles.
        members = sorted({clean(n) for n in [label, *names]}, key=lambda n: (party_key(n), n))
        if len(members) > 500:
            raise RocError("A rule can contain at most 500 source names including its preferred name.")
        for key in {party_key(n) for n in members}:
            if key in used:
                raise RocError("A name is already in another rule: " + used[key] + ". Edit that group instead.")
            used[key] = label
        normalized.append({"id": identifier, "label": label, "kind": rule["kind"], "names": members})
    return sorted(normalized, key=lambda r: (party_key(r["label"]), r["id"]))


def snapshot(value=None):
    if value is None:
        return {"revision": 0, "rules": []}
    if not isinstance(value, dict) or type(value.get("revision")) is not int or value["revision"] < 0:
        raise RocError("Name rules need a nonnegative revision and a rules list.")
    return {"revision": value["revision"], "rules": normalize_rules(value.get("rules"))}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class NameRules:
    """Workspace lock serializes writers; revision checks reject stale browser tabs."""
    def __init__(self, path):
        self.path = Path(path)

    def read(self):
        if not self.path.exists():
            return {"version": 1, "revision": 0, "rules": [], "history": []}
        try:
            state = read_json(self.path)
            if state.get("version") != 1 or not isinstance(state.get("history"), list) or len(state["history"]) > 20:
                raise ValueError()
            current = snapshot(state)
            history = [normalize_rules(rules) for rules in state["history"]]
            return {**state, **current, "history": history}
        except (ValueError, TypeError, KeyError, AttributeError):
            raise RocError("Saved name rules are unreadable. Restore name-rules.json before editing; no rules were discarded.") from None

    def public(self):
        state = self.read()
        return {**snapshot(state), "canUndo": bool(state["history"])}

    def propose(self, request):
        if not isinstance(request, dict) or set(request) - {"revision", "operation", "rule", "runId", "previewId"}:
            raise RocError("Unexpected name-rule fields.")
        state = self.read()
        if type(request.get("revision")) is not int or request["revision"] != state["revision"]:
            raise RocError("Name rules changed in another tab. Reload the rules and preview again.")
        rules = deepcopy(state["rules"])
        operation, rule = request.get("operation"), request.get("rule", {})
        if operation in ("save", "delete"):
            if not isinstance(rule, dict):
                raise RocError("Expected a name rule.")
            identifier = rule.get("id")
            existing = next((r for r in rules if r["id"] == identifier), None)
            if identifier and not existing:
                raise RocError("This rule no longer exists. Reload the rules.")
            if operation == "delete":
                if not existing or set(rule) != {"id"}:
                    raise RocError("Select an existing rule to remove.")
                rules.remove(existing)
            else:
                if set(rule) - {"id", "label", "kind", "names"}:
                    raise RocError("Unexpected name rule fields.")
                if existing:
                    rules.remove(existing)
                # Stable across Preview and Save for the same submitted revision.
                identifier = identifier or uuid.uuid5(uuid.NAMESPACE_URL, fingerprint([state["revision"], rule])).hex
                rules.append({**rule, "id": identifier})
        elif operation == "undo":
            if not state["history"]:
                raise RocError("There is no saved name-rule change to undo.")
            rules = state["history"][-1]
        else:
            raise RocError("Unknown name-rule operation.")
        rules = normalize_rules(rules)
        if rules == state["rules"]:
            raise RocError("This does not change any name rules.")
        return state, {"revision": state["revision"] + 1, "rules": rules}

    def commit(self, request, proposed):
        state, current_proposal = self.propose(request)
        if current_proposal != proposed:
            raise RocError("The preview changed. Preview the rule again.")
        history = (state["history"][:-1] if request["operation"] == "undo" else
                   [*state["history"], state["rules"]][-20:])
        write_json(self.path, {"version": 1, **proposed, "updatedUtc": now(), "history": history})
        return self.public()
