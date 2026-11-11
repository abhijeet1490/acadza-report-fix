"""Local faculty console. Run python3 -m acadza.server."""

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
from urllib.parse import parse_qs, urlsplit

from .contracts import DataError
from .review import Conflict, MissingCase, ReviewStore

WEB = Path(__file__).parent / "web"


def make_server(store, port=8765):
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
            if self.headers.get("Host") not in allowed:
                self.send(403, '{"error":"Local access required"}')
                return False
            return True

        def do_GET(self):
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

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path(".local/review.sqlite3"))
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--manifest", type=Path, help="Complete batch run to import")
    args = parser.parse_args()
    try:
        store = ReviewStore(args.db, args.manifest)
    except (DataError, Conflict) as exc:
        parser.exit(2, str(exc) + "\n")
    server = make_server(store, args.port)
    print(f"Faculty console http://127.0.0.1:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
