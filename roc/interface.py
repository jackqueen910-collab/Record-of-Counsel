"""Loopback-only interface. Credential entry calls the official authentication API."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie
from contextlib import nullcontext
from importlib.resources import files
import json
import mimetypes
import secrets
import threading
from urllib.parse import urlsplit
import webbrowser

from .common import RocError, write_json
from .courts import DISTRICT_COURTS
from .accounts import Accounts


def make_server(workspace, port=0, *, enable_demo=False):
    token = secrets.token_urlsafe(32)
    stopping = threading.Event()

    def shutdown():
        workspace.close(release_lock=False)  # Keep ownership until the HTTP server exits.
        # Give connected pages a chance to observe the completed stop before closing HTTP.
        timer = threading.Timer(2.5, server.shutdown)
        timer.daemon = True
        timer.start()

    class Handler(BaseHTTPRequestHandler):
        new_cookie = None

        def cookie(self):
            try:
                parsed = SimpleCookie(self.headers.get('Cookie', ''))
                value = parsed.get(f'roc-session-{self.server.server_port}')
                return value.value if value else ''
            except Exception:
                return ''

        def scope(self, mutating=False, require_account=False):
            if isinstance(workspace, Accounts):
                return workspace.scope(self.cookie(), mutating, require_account, self.headers.get('X-ROC-View', ''))
            # Direct Workspace servers remain an internal offline test harness.
            # The shipped launcher/CLI always constructs Accounts below.
            return nullcontext(workspace)

        def log_message(self, *_):
            pass  # Never log URLs, headers or the local access token.

        def send(self, code, body, content_type="application/json", filename=None):
            if isinstance(body, (dict, list)):
                body = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            if self.new_cookie:
                self.send_header('Set-Cookie', f'roc-session-{self.server.server_port}={self.new_cookie}; Path=/; HttpOnly; SameSite=Strict')
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
                asset = {"/": "index.html", "/app.js": "app.js", "/documents.js": "documents.js", "/style.css": "style.css"}.get(path)
                self.guard(authenticated=asset is None)
                if asset:
                    kind = 'text/javascript; charset=utf-8' if asset.endswith('.js') else {"index.html": "text/html; charset=utf-8", "style.css": "text/css; charset=utf-8"}[asset]
                    return self.send(200, files("roc").joinpath("ui", asset).read_bytes(), kind)
                parts = path.strip("/").split("/")
                if path == "/api/runs":
                    listing = workspace.list(self.cookie()) if isinstance(workspace, Accounts) else workspace.list()
                    return self.send(200, listing | {'demoEnabled': enable_demo})
                if path == "/api/connection":
                    status = workspace.status(self.cookie()) if isinstance(workspace, Accounts) else workspace.connection.status()
                    return self.send(200, {"app": "ROC", **status, "stopping": workspace.stopping, "closed": workspace.closed})
                if path == "/api/courts":
                    return self.send(200, [{"id": c.court_id, "name": c.district} for c in sorted(DISTRICT_COURTS.values(), key=lambda c: c.district)])
                with self.scope() as current:
                    with current.lock:
                        if path == '/api/name-rules':
                            return self.send(200, current.name_rules.public())
                        if len(parts) == 3 and parts[:2] == ['api', 'runs']:
                            return self.send(200, current.summary(parts[2], detail=True))
                        if len(parts) == 4 and parts[:2] == ['api', 'runs'] and parts[3] == 'documents':
                            return self.send(200, current.grabber.state(parts[2]))
                        if len(parts) == 4 and parts[:2] == ['api', 'runs'] and parts[3] == 'document-bundle':
                            bundle = current.grabber.bundle(parts[2])
                            return self.send(200, bundle.read_bytes(), 'application/zip', bundle.name)
                        if len(parts) == 5 and parts[:2] == ['api', 'runs'] and parts[3] == 'download':
                            file = current.download(parts[2], parts[4])
                            return self.send(200, file.read_bytes(), mimetypes.guess_type(file.name)[0] or 'application/octet-stream', file.name)
                self.send(404, {"error": "Not found."})
            except RocError as exc:
                self.send(400, {"error": str(exc)})
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass
            except Exception:
                self.send(500, {"error": "Local read failed. Refresh after the current operation finishes."})

        def do_POST(self):
            value = None
            raw = None
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if size < 2 or size > 2_000_000 or self.headers.get("Transfer-Encoding"):
                    raise RocError("Invalid request size.")
                # Consume the bounded body before closing a rejected connection.
                # Windows may otherwise reset it before the client sees our 400.
                # Host/origin/token checks still precede parsing and all dispatch.
                self.connection.settimeout(10)
                raw = self.rfile.read(size)
                self.guard()
                if self.headers.get("Content-Type") != "application/json":
                    raise RocError("JSON required.")
                value = json.loads(raw)
                if not isinstance(value, dict):
                    raise RocError("Expected a JSON object.")
                parts = urlsplit(self.path).path.strip("/").split("/")
                if parts == ["api", "connection", "sign-in"]:
                    if isinstance(workspace, Accounts):
                        self.new_cookie = workspace.sign_in(self.cookie(), value, self.headers.get('X-ROC-View', ''))
                        return self.send(200, {'ok': True, 'viewId': workspace.status(self.new_cookie)['viewId']})
                    workspace.sign_in(value)
                    return self.send(200, {'ok': True})
                if parts == ["api", "connection", "disconnect"]:
                    if isinstance(workspace, Accounts):
                        self.new_cookie = workspace.disconnect(self.cookie(), self.headers.get('X-ROC-View', ''))
                        return self.send(200, {'ok': True, 'viewId': workspace.status(self.new_cookie)['viewId']})
                    workspace.disconnect()
                    return self.send(200, {'ok': True})
                if parts == ["api", "stop"]:
                    if not stopping.is_set():
                        stopping.set()
                        workspace.request_stop()
                        threading.Thread(target=shutdown, daemon=True).start()
                    return self.send(200, {"stopping": True})
                if parts == ["api", "demo"]:
                    if not enable_demo:
                        return self.send(404, {'error': 'Demo is available only in the development test interface.'})
                    if isinstance(workspace, Accounts):
                        self.new_cookie, identifier = workspace.demo(self.cookie(), self.headers.get('X-ROC-View', ''))
                        return self.send(200, {'id': identifier, 'viewId': workspace.status(self.new_cookie)['viewId']})
                    return self.send(200, {"id": workspace.new(demo=True)})
                with self.scope(mutating=True, require_account=parts == ['api', 'runs']) as current:
                    if parts == ['api', 'runs']:
                        return self.send(200, {'id': current.new(value)})
                    if parts == ['api', 'name-rules', 'preview']:
                        return self.send(200, current.preview_name_rule(value))
                    if parts == ['api', 'name-rules', 'apply']:
                        return self.send(200, current.apply_name_rule(value))
                    if len(parts) == 4 and parts[:2] == ['api', 'runs']:
                        if parts[3] in ('documents-analysis-quote', 'documents-purchase-quote'):
                            with current.lock:
                                fn = current.grabber.analysis_quote if parts[3] == 'documents-analysis-quote' else current.grabber.purchase_quote
                                return self.send(200, fn(parts[2], value))
                        if parts[3] == 'quote':
                            return self.send(200, current.quote(parts[2], value.get('keys')))
                        current.act(parts[2], parts[3], value)
                        return self.send(200, {'ok': True})
                self.send(404, {"error": "Not found."})
            except (RocError, ValueError) as exc:
                self.send(400, {"error": str(exc) if isinstance(exc, RocError) else "Invalid request."})
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass
            except Exception:
                self.send(500, {"error": "Local operation failed. Inspect saved status before trying again."})
            finally:
                raw = None
                if isinstance(value, dict):
                    value.clear()  # In particular, discard the HTTP credential body.

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    server.timeout = 1
    return server, f"http://127.0.0.1:{server.server_port}/#{token}"


def serve(directory, port=0, open_browser=True):
    workspace = Accounts(directory)
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
