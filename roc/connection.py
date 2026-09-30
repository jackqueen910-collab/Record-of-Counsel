"""Browser-owned API connection. Credentials are transient; no prompts or persistence."""
import threading

from .common import RocError
from .pacer import REDACTION_NOTICE, Session, SignInError


class BrowserConnection:
    def __init__(self):
        self.lock = threading.RLock()
        self.session = None
        self.connecting = False
        self.closing = False
        self.message = "Sign in to connect PACER."

    def status(self):
        with self.lock:
            connected = self.session is not None and self.session.usable
            message = self.message
            if self.session is not None and not self.session.usable:
                message = "PACER rejected this connection. Reconnect or check account access; saved work and receipts are retained."
            return {"connected": connected, "connecting": self.connecting,
                    "message": message, "redactionNotice": REDACTION_NOTICE}

    def get(self):
        with self.lock:
            if self.closing:
                raise RocError("ROC is stopping. No new requests will start.")
            if self.session is None or not self.session.usable:
                raise RocError("Sign in using Connect PACER, then resume this saved operation. No terminal is needed.")
            return self.session

    def prepare(self, values):
        with self.lock:
            if self.closing or self.connecting:
                raise RocError("A sign-in is already in progress, or ROC is stopping. No additional attempt was sent.")
            allowed = {"username", "password", "otp", "clientCode", "redact"}
            if not isinstance(values, dict) or set(values) - allowed:
                raise RocError("Unexpected sign-in fields.")
            fields = {k: values.get(k, "") for k in allowed - {"redact"}}
            if any(not isinstance(v, str) or len(v) > 1024 for v in fields.values()):
                raise RocError("Invalid sign-in fields.")
            fields["username"] = fields["username"].strip()
            fields["otp"] = fields["otp"].strip()
            fields["clientCode"] = fields["clientCode"].strip()
            if not fields["username"] or not fields["password"]:
                raise RocError("Enter your PACER username and password. Nothing was sent.")
            if values.get("redact") is not True:
                raise RocError("Acknowledge the redaction notice before signing in. Nothing was sent.")
            self.session = None
            self.connecting = True
            self.message = "Connecting to the official PACER authentication API…"
            return fields

    def authenticate(self, fields):
        session = None
        with self.lock:
            if self.closing:
                fields.clear()
                self.connecting = False
                self.message = "ROC is stopping."
                return
        try:
            session = Session.login(fields["username"], fields["password"], fields["otp"], fields["clientCode"], True)
            message = "Connected to PACER."
        except SignInError as exc:
            message = ("PACER did not accept the username, password or MFA code. Correct the fields and click Connect PACER to try again."
                       if exc.code == "13" else "PACER did not complete sign-in. Check your account and redaction acknowledgment before trying again.")
        except RocError as exc:
            # request_json and Session.login raise curated messages without response bodies.
            message = str(exc)
        except Exception:
            message = "PACER sign-in could not be completed. No automatic retry or search was submitted."
        finally:
            fields.clear()
        with self.lock:
            self.session = None if self.closing else session
            self.message = "ROC is stopping." if self.closing else message
            self.connecting = False

    def disconnect(self):
        with self.lock:
            if self.connecting:
                raise RocError("Wait for the current sign-in attempt to finish.")
            self.session = None
            self.message = "Disconnected from PACER in ROC. Saved runs remain available."

    def stop(self):
        with self.lock:
            self.closing = True
            # Existing workers keep their reference long enough to settle an in-flight receipt.
            self.session = None
