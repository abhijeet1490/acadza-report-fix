"""Faculty console with local defaults and explicit Render hosting settings."""

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import re
import sqlite3
from pathlib import Path
import secrets
from urllib.parse import parse_qs, urlsplit

from .contracts import DataError
from .review import Conflict, MissingCase, ReviewStore

WEB = Path(__file__).parent / "web"


def make_server(store, port=8765, *, host="127.0.0.1", public_host=None):
    if public_host is not None:
        if not re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?", public_host):
            raise ValueError("Public host must be a hostname without a scheme, port or path")
        public_host = public_host.lower()
    token = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def send(self, status, content, content_type="application/json"):
            body = content.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type + "; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                             "frame-src 'self' about:; object-src 'none'; base-uri 'none'; "
                             "frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def valid_host(self):
            allowed = {f"127.0.0.1:{self.server.server_port}",
                       f"localhost:{self.server.server_port}"}
            if public_host:
                allowed.update({public_host, f"{public_host}:443",
                                f"{public_host}:{self.server.server_port}"})
            if self.headers.get("Host", "").lower() not in allowed:
                self.send(403, '{"error":"Host is not configured for this console"}')
                return False
            return True

        def do_GET(self):
            if urlsplit(self.path).path == "/healthz":
                try:
                    with store.connection() as db:
                        count = db.execute("SELECT COUNT(*) FROM cases").fetchone()[0]
                    self.send(200 if count else 503, json.dumps({"status": "ok" if count else "unavailable"}))
                except sqlite3.Error:
                    self.send(503, '{"status":"unavailable"}')
                return
            if not self.valid_host():
                return
            parsed = urlsplit(self.path)
            path = parsed.path
            if path == "/api/cases":
                self.send(200, json.dumps({"cases": store.queue(), "token": token}))
            elif path == "/api/case":
                try:
                    case_id = parse_qs(parsed.query).get("id", [None])[0]
                    case = store.view(case_id) if case_id is not None else store.view()
                    self.send(200, json.dumps({"case": case, "token": token}))
                except MissingCase as exc:
                    self.send(404, json.dumps({"error": str(exc)}))
            elif path in ("/", "/app.js", "/style.css"):
                name, content_type = {
                    "/": ("index.html", "text/html"),
                    "/app.js": ("app.js", "text/javascript"),
                    "/style.css": ("style.css", "text/css"),
                }[path]
                self.send(200, (WEB / name).read_text(), content_type)
            else:
                self.send(404, '{"error":"Page not found"}')

        def do_POST(self):
            if not self.valid_host():
                return
            if self.headers.get("X-Review-Token") != token:
                self.send(403, '{"error":"Reload the console before reviewing"}')
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 131072:
                    raise ValueError("Invalid request size")
                request = json.loads(self.rfile.read(size))
                if not isinstance(request, dict):
                    raise ValueError("Expected a request object")
                if self.path == "/api/preview":
                    response = (store.preview_edits(request) if "edits" in request else
                                {"candidate": store.preview(request.get("replacement"))})
                elif self.path == "/api/action":
                    response = store.act(request)
                else:
                    self.send(404, '{"error":"Action not found"}')
                    return
                self.send(200, json.dumps(response))
            except MissingCase as exc:
                self.send(404, json.dumps({"error": str(exc)}))
            except Conflict as exc:
                self.send(409, json.dumps({"error": str(exc)}))
            except (DataError, ValueError, TypeError) as exc:
                self.send(400, json.dumps({"error": str(exc)}))
            except Exception:
                self.log_error("Review operation failed")
                self.send(500, '{"error":"Could not save this decision. Retry the same action."}')

    return ThreadingHTTPServer((host, port), Handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path(os.environ.get("REVIEW_DB_PATH", ".local/review.sqlite3")))
    parser.add_argument("--port", type=int, default=os.environ.get("PORT", "8765"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--public-host", default=os.environ.get("RENDER_EXTERNAL_HOSTNAME"),
                        help="Exact public hostname accepted by the HTTP host guard")
    parser.add_argument("--actor", default=os.environ.get("REVIEW_ACTOR"),
                        help="Audit label for this workspace; does not authenticate users")
    parser.add_argument("--manifest", type=Path, help="Complete batch run to import")
    args = parser.parse_args()
    try:
        actor = args.actor or ("Demo reviewer" if args.public_host else "Local faculty")
        store = ReviewStore(args.db, args.manifest, actor=actor)
    except (DataError, Conflict) as exc:
        parser.exit(2, str(exc) + "\n")
    try:
        server = make_server(store, args.port, host=args.host, public_host=args.public_host)
    except ValueError as exc:
        parser.exit(2, str(exc) + "\n")
    print(f"Faculty console http://{args.host}:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
