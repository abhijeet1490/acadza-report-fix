"""Faculty console with local defaults and explicit Render hosting settings."""

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import re
import sqlite3
from pathlib import Path
from threading import Lock
import secrets
from urllib.parse import parse_qs, urlsplit

from .providers import GeminiProvider, ProviderError
from .triage import solve_payload, validate_solve
from .contracts import DataError
from .review import Conflict, MissingCase, ReviewStore

WEB = Path(__file__).parent / "web"
RELEASE = Path(__file__).resolve().parent.parent / "release"


def make_server(store, port=8765, *, host="127.0.0.1", public_host=None):
    if public_host is not None:
        if not re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?", public_host):
            raise ValueError("Public host must be a hostname without a scheme, port or path")
        public_host = public_host.lower()
    token = secrets.token_urlsafe(32)

    questions = {case.question.question_id: case.question for case in store.dataset.cases}
    answer_checks = {}
    check_lock = Lock()

    class Handler(BaseHTTPRequestHandler):
        def send(self, status, content, content_type="application/json"):
            body = content.encode("utf-8")
            self.send_headers(status, len(body), content_type + "; charset=utf-8")
            if self.command != "HEAD":
                self.wfile.write(body)

        def send_headers(self, status, length, content_type, extra=None):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(length))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                             "frame-src 'self' about:; object-src 'none'; base-uri 'none'; "
                             "frame-ancestors 'none'")
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()

        def send_media(self, path, content_type):
            if not path.is_file():
                self.send(404, '{"error":"Walkthrough file is unavailable"}')
                return
            size = path.stat().st_size
            start, end = 0, size - 1
            range_header = self.headers.get("Range")
            status = 200
            extra = {"Accept-Ranges": "bytes"}
            if range_header:
                match = re.fullmatch(r"bytes=([0-9]*)-([0-9]*)", range_header)
                if not match or not any(match.groups()):
                    self.send_headers(416, 0, content_type, {"Content-Range": f"bytes */{size}"})
                    return
                first, last = match.groups()
                try:
                    if first:
                        start = int(first)
                        end = min(int(last), size - 1) if last else size - 1
                    else:
                        count = int(last)
                        start = max(0, size - count)
                except ValueError:
                    self.send_headers(416, 0, content_type, {"Content-Range": f"bytes */{size}"})
                    return
                if size == 0 or start > end or start >= size:
                    self.send_headers(416, 0, content_type, {"Content-Range": f"bytes */{size}"})
                    return
                status = 206
                extra["Content-Range"] = f"bytes {start}-{end}/{size}"
            length = max(0, end - start + 1)
            self.send_headers(status, length, content_type, extra)
            if self.command == "HEAD":
                return
            with path.open("rb") as media:
                media.seek(start)
                remaining = length
                while remaining:
                    chunk = media.read(min(65536, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)

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
            elif path in ("/walkthrough.mp4", "/walkthrough.vtt"):
                content_type = "video/mp4" if path.endswith(".mp4") else "text/vtt; charset=utf-8"
                self.send_media(RELEASE / path.lstrip("/"), content_type)
            elif path in ("/", "/app.js", "/style.css", "/walkthrough"):
                name, content_type = {
                    "/": ("index.html", "text/html"),
                    "/app.js": ("app.js", "text/javascript"),
                    "/style.css": ("style.css", "text/css"),
                    "/walkthrough": ("walkthrough.html", "text/html"),
                }[path]
                self.send(200, (WEB / name).read_text(), content_type)
            else:
                self.send(404, '{"error":"Page not found"}')

        def do_HEAD(self):
            self.do_GET()

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
                if self.path == "/api/check-answer":
                    question = questions.get(request.get("id"))
                    if question is None:
                        raise MissingCase("Unknown question")
                    if not check_lock.acquire(blocking=False):
                        self.send(429, json.dumps({"error": "Another answer check is running. Try again shortly."}))
                        return
                    try:
                        if question.version not in answer_checks:
                            provider = GeminiProvider()
                            result = provider.call("solve", solve_payload(question))
                            validate_solve(result)
                            answer_checks[question.version] = {**result, "model": provider.model}
                        response = answer_checks[question.version]
                    finally:
                        check_lock.release()
                elif self.path == "/api/preview":
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
            except ProviderError as exc:
                self.send(503, json.dumps({"error": str(exc)}))
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
