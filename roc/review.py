"""Separate source omissions and sample gaps from actionable review items."""

CATEGORY_LABELS = {"missing-source": "Missing from source",
                   "not-tested": "Not tested by this sample",
                   "needs-review": "Needs review"}


def issue(category, code, message, **context):
    if category not in CATEGORY_LABELS:
        raise ValueError("Unknown review category")
    return {"category": category, "code": code, "message": message, **context}


def issue_text(item):
    context = ": ".join(str(item[k]) for k in ("attorney", "party") if item.get(k))
    return CATEGORY_LABELS[item["category"]] + ": " + (context + ": " if context else "") + item["message"]


def review_warnings(items):
    """Keep legacy warnings limited to items needing a decision or parser review."""
    return [item["message"] for item in items if item["category"] == "needs-review"]


def case_issues(case):
    items = list(case.get("issues", []))
    known = {i["message"] for i in items}
    for warning in case.get("warnings", []):
        if warning not in known:
            items.append(issue("needs-review", "case-warning", warning))
            known.add(warning)
    return items
