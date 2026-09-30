"""Official PACER authentication + PCL API. Never falls back to browser search."""
import getpass
import json
import os
import sys
import time
import urllib.error
import urllib.request

from .common import RocError

AUTH_URL = "https://pacer.login.uscourts.gov/services/cso-auth"
PCL_URL = "https://pcl.uscourts.gov/pcl-public-api/rest/parties/find"
REDACTION_NOTICE = (
    "All filers must redact Social Security/taxpayer IDs, dates of birth, names of minor children, "
    "financial account numbers, and (in criminal cases) home addresses in accordance with the "
    "applicable federal court redaction rules. This applies to attachments too."
)


def masked_input(prompt):
    """Give Windows MFA typing feedback without exposing the code."""
    if os.name != "nt":
        return getpass.getpass(prompt)
    import msvcrt
    print(prompt, end="", flush=True)
    chars = []
    while True:
        char = msvcrt.getwch()
        if char in ("\r", "\n"):
            print()
            return "".join(chars)
        if char == "\x03":
            raise KeyboardInterrupt
        if char in ("\x00", "\xe0"):
            msvcrt.getwch()
        elif char in ("\b", "\x7f"):
            if chars:
                chars.pop()
                print("\b \b", end="", flush=True)
        elif char.isprintable():
            chars.append(char)
            print("*", end="", flush=True)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request_json(url, payload, headers=None):
    request = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json", **(headers or {})})
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=75) as response:
            raw = response.read(20_000_001)
            if len(raw) > 20_000_000:
                raise RocError("PACER response exceeded the supported size; no retry.")
            return raw.decode("utf-8"), dict(response.headers)
    except urllib.error.HTTPError as exc:
        raise RocError(f"PACER HTTP {exc.code}; stopped without retry.") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise RocError("PACER connection failed; stopped without retry.") from None


class Session:
    def __init__(self, token, client_code="", requester=request_json):
        self.token, self.client_code, self.requester = token, client_code, requester

    @classmethod
    def login(cls, username, password, otp="", client_code="", redact=False, requester=request_json):
        payload = {"loginId": username, "password": password}
        if otp:
            payload["otpCode"] = otp
        if client_code:
            payload["clientCode"] = client_code
        if redact:
            payload["redactFlag"] = "1"
        try:
            raw, _ = requester(AUTH_URL, payload)
            result = json.loads(raw)
        finally:
            payload.clear()
        if str(result.get("loginResult")) != "0" or not result.get("nextGenCSO"):
            code = str(result.get("loginResult", "unknown"))
            raise RocError(f"PACER sign-in failed (code {code}). No automatic login retry. Check credentials/MFA on PACER.")
        # Successful login can still indicate a disabled account or missing client code.
        if result.get("errorDescription"):
            raise RocError("PACER returned an account notice after sign-in. Resolve it on PACER before running paid searches.")
        return cls(result["nextGenCSO"], client_code, requester)

    @classmethod
    def prompt(cls):
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            raise RocError("Open ROC in an interactive terminal to sign in. Do not supply credentials in a config file.")
        print("Official PACER API sign-in. Credentials and token stay in this process.")
        print(REDACTION_NOTICE)
        acknowledge = input("Acknowledge the redaction notice? [y/N]: ").strip().lower() in ("y", "yes")
        if not acknowledge:
            raise RocError("Sign-in cancelled.")
        username = input("PACER username: ").strip()
        password = getpass.getpass("PACER password (hidden): ")
        client = input("Client code (Enter if none): ").strip()
        otp = masked_input("Current MFA code (typing shows *; Enter if none): ").strip()
        print("Contacting the official PACER authentication API...", flush=True)
        return cls.login(username, password, otp, client, True)

    def search_page(self, criteria, page, store):
        parameters = {"criteria": criteria, "page": page}
        cached = store.cached("pcl", parameters)
        self.last_page_cached = bool(cached)
        if cached:
            return json.loads(cached.read_text(encoding="utf-8"))
        t = store.reserve("pcl", parameters, 10)
        headers = {"X-NEXT-GEN-CSO": self.token}
        if self.client_code:
            headers["X-CLIENT-CODE"] = self.client_code
        raw, response_headers = self.requester(f"{PCL_URL}?page={page}", criteria, headers)
        path = store.finish(t, raw)
        rotated = next((v for k, v in response_headers.items() if k.lower() == "x-next-gen-cso"), None)
        if rotated:
            self.token = rotated
        return json.loads(path.read_text(encoding="utf-8"))


def collect_index(session, criteria, store, delay=5, max_pages=1000, progress=None):
    rows, total, seen_pages = [], None, set()
    spent_at_start = store.spent
    for page in range(max_pages):
        if page:
            time.sleep(delay)
        response = session.search_page(criteria, page, store)
        info, content = response.get("pageInfo", {}), response.get("content", [])
        if not isinstance(content, list) or info.get("number") != page:
            raise RocError("PCL pagination changed or returned an unexpected page; stopped.")
        this_total = info.get("totalElements")
        if total is not None and this_total != total:
            raise RocError("PCL total changed during collection; saved pages require review.")
        total = this_total
        if info.get("numberOfElements") != len(content):
            raise RocError("PCL row count mismatch.")
        signature = json.dumps(content, sort_keys=True)
        if signature in seen_pages:
            raise RocError("Repeated PCL page; no further requests.")
        seen_pages.add(signature)
        rows.extend(content)
        if progress:
            source = "Cached PCL page - no new charge" if session.last_page_cached else "Fresh PCL API response"
            progress("searching", f"{source}: page {page + 1}; {len(rows)} of {total} attorney records collected. "
                     f"New search charges this launch: ${(store.spent - spent_at_start) / 100:.2f}. "
                     f"Job total including earlier launches: ${store.spent / 100:.2f}.", recordsCollected=len(rows),
                     totalRecords=total, chargedCents=store.spent, newSearchChargesCents=store.spent - spent_at_start)
        if info.get("last"):
            if len(rows) != total:
                raise RocError("Incomplete PCL collection.")
            return rows
    raise RocError("Page limit reached; collection is incomplete.")
