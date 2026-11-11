from http.client import HTTPConnection
import json
from pathlib import Path
import tempfile
from threading import Thread
import unittest
from uuid import uuid4

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


if __name__ == "__main__":
    unittest.main()
