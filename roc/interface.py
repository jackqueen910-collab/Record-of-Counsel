"""Loopback-only interface. Credential entry calls the official authentication API."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
import json
import mimetypes
import secrets
import threading
from urllib.parse import urlsplit
import webbrowser

from .common import RocError, write_json
from .courts import DISTRICT_COURTS
from .workspace import Workspace


def make_server(workspace, port=0):
    token = secrets.token_urlsafe(32)
    stopping = threading.Event()

    def shutdown():
        workspace.close(release_lock=False)  # Keep ownership until the HTTP server exits.
        # Give connected pages a chance to observe the completed stop before closing HTTP.
        timer = threading.Timer(2.5, server.shutdown)
        timer.daemon = True
        timer.start()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass  # Never log URLs, headers or the local access token.

        def send(self, code, body, content_type="application/json", filename=None):
            if isinstance(body, (dict, list)):
                body = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
            if filename:
                self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.end_headers()
            self.wfile.write(body)

        def guard(self, authenticated=True):
            expected = f"127.0.0.1:{self.server.server_port}"
            if self.headers.get("Host") != expected:
                raise RocError("Invalid local host.")
            origin = self.headers.get("Origin")
            if origin and origin != "http://" + expected:
                raise RocError("Cross-origin access refused.")
            if authenticated and not secrets.compare_digest(self.headers.get("X-ROC-Token", ""), token):
                raise RocError("Reopen the interface from the ROC launcher to connect to this session.")

        def do_GET(self):
            try:
                path = urlsplit(self.path).path
                asset = {"/": "index.html", "/app.js": "app.js", "/style.css": "style.css"}.get(path)
                self.guard(authenticated=asset is None)
                if asset:
                    kind = {"index.html": "text/html; charset=utf-8", "app.js": "text/javascript; charset=utf-8", "style.css": "text/css; charset=utf-8"}[asset]
                    return self.send(200, files("roc").joinpath("ui", asset).read_bytes(), kind)
                parts = path.strip("/").split("/")
                if path == "/api/runs":
                    return self.send(200, workspace.list())
                if path == "/api/connection":
                    return self.send(200, {"app": "ROC", **workspace.connection.status(), "stopping": workspace.stopping, "closed": workspace.closed})
                if path == "/api/courts":
                    return self.send(200, [{"id": c.court_id, "name": c.district} for c in sorted(DISTRICT_COURTS.values(), key=lambda c: c.district)])
                if len(parts) == 3 and parts[:2] == ["api", "runs"]:
                    with workspace.lock:
                        return self.send(200, workspace.summary(parts[2], detail=True))
                if len(parts) == 5 and parts[:2] == ["api", "runs"] and parts[3] == "download":
                    with workspace.lock:
                        file = workspace.download(parts[2], parts[4])
                        return self.send(200, file.read_bytes(), mimetypes.guess_type(file.name)[0] or "application/octet-stream", file.name)
                self.send(404, {"error": "Not found."})
            except RocError as exc:
                self.send(400, {"error": str(exc)})
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception:
                self.send(500, {"error": "Local read failed. Refresh after the current operation finishes."})

        def do_POST(self):
            value = None
            try:
                self.guard()
                if self.headers.get("Content-Type") != "application/json":
                    raise RocError("JSON required.")
                size = int(self.headers.get("Content-Length", "0"))
                if size < 2 or size > 2_000_000 or self.headers.get("Transfer-Encoding"):
                    raise RocError("Invalid request size.")
                value = json.loads(self.rfile.read(size))
                if not isinstance(value, dict):
                    raise RocError("Expected a JSON object.")
                parts = urlsplit(self.path).path.strip("/").split("/")
                if parts == ["api", "connection", "sign-in"]:
                    workspace.sign_in(value)
                    return self.send(200, {"ok": True})
                if parts == ["api", "connection", "disconnect"]:
                    workspace.disconnect()
                    return self.send(200, {"ok": True})
                if parts == ["api", "stop"]:
                    if not stopping.is_set():
                        stopping.set()
                        workspace.request_stop()
                        threading.Thread(target=shutdown, daemon=True).start()
                    return self.send(200, {"stopping": True})
                if parts == ["api", "runs"]:
                    return self.send(200, {"id": workspace.new(value)})
                if parts == ["api", "demo"]:
                    return self.send(200, {"id": workspace.new(demo=True)})
                if len(parts) == 4 and parts[:2] == ["api", "runs"]:
                    if parts[3] == "quote":
                        return self.send(200, workspace.quote(parts[2], value.get("keys")))
                    workspace.act(parts[2], parts[3], value)
                    return self.send(200, {"ok": True})
                self.send(404, {"error": "Not found."})
            except (RocError, ValueError) as exc:
                self.send(400, {"error": str(exc) if isinstance(exc, RocError) else "Invalid request."})
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception:
                self.send(500, {"error": "Local operation failed. Inspect saved status before trying again."})
            finally:
                if isinstance(value, dict):
                    value.clear()  # In particular, discard the HTTP credential body.

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    server.timeout = 1
    return server, f"http://127.0.0.1:{server.server_port}/#{token}"


def serve(directory, port=0, open_browser=True):
    workspace = Workspace(directory)
    try:
        server, url = make_server(workspace, port)
    except Exception:
        workspace.close()
        raise
    print("ROC interface: " + url, flush=True)
    print("Connect PACER and Stop ROC are in the browser interface. Closing a tab does not pause a job.", flush=True)
    connection_file = workspace.root / "interface-connection.json"
    try:
        # This is a local UI capability for reopening a tab, never a PACER session token.
        write_json(connection_file, {"url": url})
        if open_browser and not webbrowser.open(url):
            raise RocError("ROC could not open the default browser. Set a default browser, then open ROC again.")
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("Stopping after the current operation reaches a safe boundary...", flush=True)
    finally:
        server.server_close()
        connection_file.unlink(missing_ok=True)
        workspace.close()
    return 0
