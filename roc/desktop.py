"""Windowless Windows entry point; no credential or console logs are created."""
import http.client
from contextlib import redirect_stdout, redirect_stderr
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit
import webbrowser

from .common import RocError, read_json
from .interface import serve


def existing_url(root):
    path = Path(root) / "interface-connection.json"
    if not path.exists():
        return None
    try:
        url = read_json(path)["url"]
        parsed = urlsplit(url)
        if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or not parsed.port
                or parsed.username or parsed.password or parsed.path != "/" or parsed.query
                or not re.fullmatch(r"[A-Za-z0-9_-]{43}", parsed.fragment)):
            return None
        connection = http.client.HTTPConnection("127.0.0.1", parsed.port, timeout=2)
        try:
            connection.request("GET", "/api/connection", headers={"X-ROC-Token": parsed.fragment})
            reply = connection.getresponse()
            data = json.loads(reply.read(10_000))
            if reply.status == 200 and data.get("app") == "ROC" and not data.get("stopping"):
                return url
        finally:
            connection.close()
    except (OSError, ValueError, KeyError, TypeError, http.client.HTTPException):
        return None
    return None


def show_error(message):
    if os.name == "nt":
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, message, "Record of Counsel", 0x10)
    else:
        print(message, file=sys.stderr)


def main():
    # pythonw has no console streams. Discard engine prints rather than create log files.
    with open(os.devnull, "w") as sink, redirect_stdout(sys.stdout or sink), redirect_stderr(sys.stderr or sink):
        root = Path("runs/workspace").resolve()
        browsers = Path(".venv/browsers").resolve()
        if browsers.is_dir():
            os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(browsers))
        try:
            url = existing_url(root)
            if url:
                if not webbrowser.open(url):
                    raise RocError("ROC could not open the default browser. Set a default browser, then open ROC again.")
                return 0
            return serve(root)
        except RocError as exc:
            show_error(str(exc))
        except Exception:
            show_error("ROC stopped unexpectedly. Saved runs and receipts are retained. Check the local Python installation and whether another ROC process is running before reopening it.")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
