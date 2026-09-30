"""Official PACER authentication + PCL API. Never falls back to browser search."""
import json
import sys
import time
import urllib.error
import urllib.request

from .common import RocError

AUTH_URL = "https://pacer.login.uscourts.gov/services/cso-auth"
PCL_URL = "https://pcl.uscourts.gov/pcl-public-api/rest/parties/find"
PCL_CASE_URL = "https://pcl.uscourts.gov/pcl-public-api/rest/cases/find"
REDACTION_NOTICE = (
    "All filers must redact Social Security/taxpayer IDs, dates of birth, names of minor children, "
    "financial account numbers, and (in criminal cases) home addresses in accordance with the "
    "applicable federal court redaction rules. This applies to attachments too."
)


class SignInError(RocError):
    def __init__(self, code):
        self.code = str(code)
        super().__init__(f"PACER sign-in failed (code {self.code}). No automatic login retry.")


class SessionExpired(RocError):
    """The server rejected this session. Receipt uncertainty is still preserved."""


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
        if exc.code in (401, 403):
            raise SessionExpired(f"PACER rejected access (HTTP {exc.code}). Sign in again; no automatic request retry.") from None
        raise RocError(f"PACER HTTP {exc.code}; stopped without retry.") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise RocError("PACER connection failed; stopped without retry.") from None


class Session:
    def __init__(self, token, client_code="", requester=request_json):
        self.token, self.client_code, self.requester = token, client_code, requester
        self.usable = True

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
            raise SignInError(result.get("loginResult", "unknown"))
        # Successful login can still indicate a disabled account or missing client code.
        if result.get("errorDescription"):
            raise RocError("PACER returned an account notice after sign-in. Resolve it on PACER before running paid searches.")
        return cls(result["nextGenCSO"], client_code, requester)

    @classmethod
    def prompt(cls):
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            raise RocError("Open ROC in an interactive terminal to sign in. Do not supply credentials in a config file.")
        print("Official PACER API sign-in. Password and MFA typing are visible in this terminal.")
        print(REDACTION_NOTICE)
        acknowledge = input("Acknowledge the redaction notice? [y/N]: ").strip().lower() in ("y", "yes")
        if not acknowledge:
            raise RocError("Sign-in cancelled.")
        while True:
            username = input("PACER username: ").strip()
            password = input("PACER password (visible): ")
            client = input("Client code (Enter if none): ").strip()
            otp = input("Current MFA code (visible; Enter if none): ").strip()
            try:
                if not username or not password:
                    print("Username and password are required. Nothing was sent to PACER.")
                else:
                    print("Contacting the official PACER authentication API...", flush=True)
                    try:
                        return cls.login(username, password, otp, client, True)
                    except SignInError as exc:
                        if exc.code != "13":
                            raise
                        print("PACER did not accept the username, password or MFA code. No search was submitted.")
            finally:
                # Deliberate terminal echo is the only credential display; never write to logs/configs.
                password = otp = ""
            retry = input("Re-enter credentials and try again? [y/N]: ").strip().lower()
            if retry not in ("y", "yes"):
                raise RocError("Sign-in cancelled. No automatic login retry.")

    def search_page(self, criteria, page, store):
        return self._search_page(criteria, page, store, "pcl", PCL_URL)

    def search_cases_page(self, criteria, page, store):
        """Official case search; its cache cannot collide with party searches."""
        return self._search_page(criteria, page, store, "pcl-case", PCL_CASE_URL)

    def _search_page(self, criteria, page, store, kind, endpoint):
        parameters = {"criteria": criteria, "page": page}
        cached = store.cached(kind, parameters)
        self.last_page_cached = bool(cached)
        if cached:
            return json.loads(cached.read_text(encoding="utf-8"))
        t = store.reserve(kind, parameters, 10)
        headers = {"X-NEXT-GEN-CSO": self.token}
        if self.client_code:
            headers["X-CLIENT-CODE"] = self.client_code
        try:
            raw, response_headers = self.requester(f"{endpoint}?page={page}", criteria, headers)
        except SessionExpired:
            self.usable = False
            raise
        path = store.finish(t, raw)
        rotated = next((v for k, v in response_headers.items() if k.lower() == "x-next-gen-cso"), None)
        if rotated:
            self.token = rotated
        return json.loads(path.read_text(encoding="utf-8"))


def collect_index(session, criteria, store, delay=5, max_pages=1000, progress=None):
    rows, total, seen_pages = [], None, set()
    spent_at_start = store.spent
    for page in range(max_pages):
        store.checkpoint()
        if page and store.cached("pcl", {"criteria": criteria, "page": page}) is None:
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
