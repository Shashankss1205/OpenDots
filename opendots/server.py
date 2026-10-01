import json
import re
import signal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
from urllib.parse import urlparse


WEB = Path(__file__).parent / "web"


def make_server(engine, host="127.0.0.1", port=8765):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, status, body, content_type="application/json"):
            if not isinstance(body, bytes):
                body = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(body)

        def local_request(self, mutation=False):
            port_value = self.server.server_address[1]
            allowed = {f"127.0.0.1:{port_value}", f"localhost:{port_value}"}
            request_host = self.headers.get("Host", "")
            if request_host not in allowed:
                self.send(403, {"error": "Local Host header required"})
                return False
            origin = self.headers.get("Origin")
            if origin and origin not in {"http://" + value for value in allowed}:
                self.send(403, {"error": "Same-origin request required"})
                return False
            if mutation and (self.headers.get("X-OpenDots-Request") or self.headers.get("X-Spots-Request")) != "dashboard":
                self.send(403, {"error": "X-OpenDots-Request: dashboard header required"})
                return False
            return True

        def do_GET(self):
            if not self.local_request():
                return
            route = urlparse(self.path).path
            if route == "/api/state":
                self.send(200, engine.snapshot())
            elif route == "/api/health":
                self.send(200, {"status": "ok", "backend": engine.config.backend})
            elif match := re.fullmatch(r"/api/work/([1-9][0-9]*)/patch", route):
                work_id = int(match[1])
                with engine.store.connect() as db:
                    work = db.execute("SELECT status,branch FROM work WHERE id=?", (work_id,)).fetchone()
                if work is None:
                    self.send(404, {"error": "Work item not found"})
                    return
                if work["status"] != "completed":
                    self.send(409, {"error": "Patch is available after work completes"})
                    return
                root = engine.workspaces.root.resolve()
                patch = root / "artifacts" / f"task-{work_id}.patch"
                if patch.is_symlink() or not patch.resolve().is_relative_to(root):
                    self.send(403, {"error": "Artifact must remain inside the managed workspace"})
                    return
                if not patch.is_file():
                    self.send(404, {"error": "Retained patch is unavailable"})
                    return
                if patch.stat().st_size > 1_048_576:
                    self.send(413, {"error": "Patch exceeds the dashboard preview limit"})
                    return
                self.send(200, {"work_id": work_id, "branch": work["branch"], "patch": patch.read_text()})
            elif route in {"/", "/app.js", "/style.css", "/opendots-logo.png"}:
                name = "index.html" if route == "/" else route[1:]
                types = {"index.html": "text/html; charset=utf-8", "app.js": "text/javascript; charset=utf-8", "style.css": "text/css; charset=utf-8", "opendots-logo.png": "image/png"}
                self.send(200, (WEB / name).read_bytes(), types[name])
            else:
                self.send(404, {"error": "Not found"})

        def do_POST(self):
            route = urlparse(self.path).path
            is_webhook = route == "/api/webhooks/github"
            if not self.local_request(mutation=not is_webhook):
                return
            try:
                if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                    raise ValueError("Content-Type must be application/json")
                length = int(self.headers.get("Content-Length", 0))
                if not 0 < length <= 256_000:
                    raise ValueError("Request body must be 1–256000 bytes")
                raw_body = self.rfile.read(length)
                if is_webhook:
                    import os
                    from .sources import verify_github_signature, normalize_github
                    verify_github_signature(raw_body, self.headers.get("X-Hub-Signature-256"), os.environ.get("OPENDOTS_GITHUB_WEBHOOK_SECRET") or os.environ.get("SPOTS_GITHUB_WEBHOOK_SECRET"))
                    delivery = self.headers.get("X-GitHub-Delivery")
                    kind = self.headers.get("X-GitHub-Event")
                    if not delivery or not kind:
                        raise ValueError("GitHub delivery/event headers required")
                    body = json.loads(raw_body)
                    self.send(202, engine.ingest(normalize_github(kind, body, delivery)))
                    return
                body = json.loads(raw_body)
                if route == "/api/events":
                    self.send(202, engine.ingest(body))
                elif route.startswith("/api/work/") and route.endswith("/decision"):
                    work_id = int(route.split("/")[3])
                    if not isinstance(body, dict) or type(body.get("approved")) is not bool:
                        raise ValueError("approved must be a boolean")
                    if not isinstance(body.get("approval_token"), str):
                        raise ValueError("The exact approval_token from /api/state is required")
                    self.send(200, engine.store.decide(work_id, body["approved"], body["approval_token"]))
                else:
                    self.send(404, {"error": "Not found"})
            except (ValueError, TypeError, KeyError) as exc:
                self.send(400, {"error": str(exc)})

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server


def serve(engine, host="127.0.0.1", port=8765):
    from .engine import process_lock
    # Fail synchronously when another process owns the DB, before exposing a dashboard.
    with process_lock(engine.config.database):
        engine.store.recover_interrupted()
        server = make_server(engine, host, port)
        failures = []

        def run():
            try:
                engine.worker_loop()
            except Exception as exc:
                failures.append(exc)
                server.shutdown()

        worker = threading.Thread(target=run, name="opendots-scheduler")
        worker.start()
        print(f"OpenDots: http://127.0.0.1:{server.server_address[1]}  backend={engine.config.backend}", flush=True)
        previous_handlers = {}
        if threading.current_thread() is threading.main_thread():
            def stop(signum, frame):
                engine.stop_event.set()
                threading.Thread(target=server.shutdown, daemon=True).start()
            for signum in (signal.SIGTERM, signal.SIGINT):
                previous_handlers[signum] = signal.signal(signum, stop)
        try:
            server.serve_forever(poll_interval=0.2)
        except KeyboardInterrupt:
            pass
        finally:
            engine.stop_event.set()
            server.server_close()
            worker.join()
            for signum, handler in previous_handlers.items():
                signal.signal(signum, handler)
        if failures:
            raise failures[0]
