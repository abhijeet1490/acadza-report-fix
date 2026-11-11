from http.client import HTTPConnection
from pathlib import Path
import tempfile
from threading import Thread
import unittest
from unittest.mock import patch

from acadza.review import ReviewStore
from acadza.server import make_server


class WalkthroughTests(unittest.TestCase):
    def test_video_streaming_ranges_head_captions_and_missing_media(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            content = bytes(range(16))
            (root / "walkthrough.mp4").write_bytes(content)
            (root / "walkthrough.vtt").write_text("WEBVTT\n")
            store = ReviewStore(root / "review.sqlite3")
            with patch("acadza.server.RELEASE", root):
                server = make_server(store, 0)
                thread = Thread(target=server.serve_forever, daemon=True)
                thread.start()
                client = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                try:
                    client.request("GET", "/walkthrough")
                    response = client.getresponse()
                    self.assertEqual(response.status, 200)
                    self.assertIn(b"<video", response.read())
                    client.request("GET", "/walkthrough.mp4")
                    response = client.getresponse()
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.getheader("Content-Type"), "video/mp4")
                    self.assertEqual(response.read(), content)
                    for header, expected, span in (
                        ("bytes=0-3", content[:4], "bytes 0-3/16"),
                        ("bytes=12-", content[12:], "bytes 12-15/16"),
                        ("bytes=-4", content[-4:], "bytes 12-15/16"),
                        ("bytes=0-999", content, "bytes 0-15/16"),
                    ):
                        with self.subTest(header=header):
                            client.request("GET", "/walkthrough.mp4", headers={"Range": header})
                            response = client.getresponse()
                            self.assertEqual(response.status, 206)
                            self.assertEqual(response.getheader("Content-Range"), span)
                            self.assertEqual(response.read(), expected)
                    for header in ("bytes=99-", "bytes=-0", "bytes=8-3", "bytes=-",
                                   "bytes=0-1,4-5", "invalid", "bytes=" + "9" * 5000 + "-"):
                        with self.subTest(header=header[:30]):
                            client.request("GET", "/walkthrough.mp4", headers={"Range": header})
                            response = client.getresponse()
                            self.assertEqual(response.status, 416)
                            self.assertEqual(response.read(), b"")
                    client.request("HEAD", "/walkthrough.mp4")
                    response = client.getresponse()
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.getheader("Content-Length"), "16")
                    self.assertEqual(response.read(), b"")
                    client.request("GET", "/walkthrough.vtt")
                    response = client.getresponse()
                    self.assertTrue(response.getheader("Content-Type").startswith("text/vtt"))
                    self.assertEqual(response.read(), b"WEBVTT\n")
                    (root / "walkthrough.mp4").unlink()
                    client.request("GET", "/walkthrough.mp4")
                    response = client.getresponse()
                    self.assertEqual(response.status, 404)
                    response.read()
                finally:
                    client.close()
                    server.shutdown()
                    thread.join()
                    server.server_close()


if __name__ == "__main__":
    unittest.main()
