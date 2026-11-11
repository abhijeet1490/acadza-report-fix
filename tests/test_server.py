from http.client import HTTPConnection
import json
from pathlib import Path
import tempfile
from threading import Thread
import unittest
from uuid import uuid4
from unittest.mock import patch
import os
import sys

from acadza import server as server_module

from acadza.review import ReviewStore
from acadza.server import make_server


class ServerTests(unittest.TestCase):
    def test_http_review_and_request_guards(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ReviewStore(Path(directory) / "review.sqlite3")
            server = make_server(store, 0)
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            client = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            try:
                client.request("GET", "/")
                response = client.getresponse()
                self.assertEqual(response.status, 200)
                self.assertIn(b"Approve change", response.read())
                client.request("GET", "/api/case")
                response = client.getresponse()
                data = json.loads(response.read())
                client.request("GET", "/api/cases")
                response = client.getresponse()
                cases = json.loads(response.read())["cases"]
                self.assertEqual(len(cases), 39)
                self.assertEqual(sum(len(case["reportIds"]) for case in cases), 44)
                other = next(case for case in cases if "R021" in case["reportIds"])
                client.request("GET", "/api/case?id=" + other["id"])
                response = client.getresponse()
                self.assertEqual(json.loads(response.read())["case"]["id"], other["id"])
                client.request("GET", "/api/case?id=unknown")
                response = client.getresponse()
                self.assertEqual(response.status, 404)
                response.read()
                request = {
                    "request_id": str(uuid4()), "action": "approve",
                    "revision": data["case"]["revision"],
                    "source_version": data["case"]["source_version"],
                }
                client.request("POST", "/api/action", json.dumps(request))
                response = client.getresponse()
                self.assertEqual(response.status, 403)
                response.read()
                headers = {"X-Review-Token": data["token"], "Content-Type": "application/json"}
                with patch("acadza.server.GeminiProvider") as factory:
                    provider = factory.return_value
                    provider.model = "test-model"
                    provider.call.return_value = {"answer": "A", "reasoning": "Derived", "limitations": "None"}
                    for _ in range(2):
                        client.request("POST", "/api/check-answer", json.dumps({"id": other["id"]}), headers)
                        response = client.getresponse()
                        self.assertEqual(response.status, 200)
                        self.assertEqual(json.loads(response.read())["answer"], "A")
                    provider.call.assert_called_once()
                    stage, payload = provider.call.call_args.args
                    self.assertEqual(stage, "solve")
                    self.assertEqual(set(payload), {"question_id", "source_version", "question_type", "question", "quesImages"})
                client.request("POST", "/api/action", json.dumps(request), headers)
                response = client.getresponse()
                self.assertEqual(response.status, 200)
                first = json.loads(response.read())
                client.request("POST", "/api/action", json.dumps(request), headers)
                response = client.getresponse()
                self.assertEqual(json.loads(response.read()), first)
                client.request("POST", "/api/action",
                               json.dumps(dict(request, request_id=str(uuid4()))), headers)
                response = client.getresponse()
                self.assertEqual(response.status, 409)
                response.read()
                client.request("POST", "/api/action", "{bad json", headers)
                response = client.getresponse()
                self.assertEqual(response.status, 400)
                response.read()
                client.request("GET", "/api/case", headers={"Host": "untrusted.example"})
                response = client.getresponse()
                self.assertEqual(response.status, 403)
                response.read()
                self.assertEqual(len(store.view()["audit"]), 1)
            finally:
                client.close()
                server.shutdown()
                thread.join()
                server.server_close()


    def test_public_host_and_health_check(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ReviewStore(Path(directory) / "review.sqlite3", actor="Demo reviewer")
            server = make_server(store, 0, public_host="acadza-example.onrender.com")
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            client = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            try:
                headers = {"Host": "acadza-example.onrender.com"}
                client.request("GET", "/api/case", headers=headers)
                response = client.getresponse()
                self.assertEqual(response.status, 200)
                data = json.loads(response.read())
                request = {
                    "request_id": str(uuid4()), "action": "approve",
                    "revision": data["case"]["revision"],
                    "source_version": data["case"]["source_version"],
                }
                client.request("POST", "/api/action", json.dumps(request),
                               dict(headers, **{"X-Review-Token": data["token"]}))
                response = client.getresponse()
                self.assertEqual(response.status, 200)
                response.read()
                self.assertEqual(store.view()["audit"][0]["actor"], "Demo reviewer")
                client.request("GET", "/api/case", headers={"Host": "other.onrender.com"})
                response = client.getresponse()
                self.assertEqual(response.status, 403)
                response.read()
                client.request("GET", "/healthz", headers={"Host": "health-probe.internal"})
                response = client.getresponse()
                self.assertEqual(response.status, 200)
                self.assertEqual(json.loads(response.read()), {"status": "ok"})
                with store.connection() as db:
                    db.execute("ALTER TABLE cases RENAME TO unavailable_cases")
                client.request("GET", "/healthz")
                response = client.getresponse()
                self.assertEqual(response.status, 503)
                response.read()
            finally:
                client.close()
                server.shutdown()
                thread.join()
                server.server_close()

    def test_render_environment_reaches_the_normal_startup_path(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "persistent" / "review.sqlite3"
            environment = {
                "PORT": "10000", "RENDER_EXTERNAL_HOSTNAME": "example.onrender.com",
                "REVIEW_DB_PATH": str(database),
            }
            with patch.dict(os.environ, environment, clear=True), \
                    patch.object(sys, "argv", ["acadza.server", "--host", "0.0.0.0"]), \
                    patch("acadza.server.make_server") as factory:
                factory.return_value.serve_forever.side_effect = KeyboardInterrupt
                server_module.main()
            store, port = factory.call_args.args
            self.assertEqual(port, 10000)
            self.assertEqual(store.path, database)
            self.assertEqual(store.actor, "Demo reviewer")
            self.assertEqual(factory.call_args.kwargs,
                             {"host": "0.0.0.0", "public_host": "example.onrender.com"})
            self.assertEqual(len(store.queue()), 39)
            factory.return_value.server_close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
