import io
import json
import unittest
from unittest.mock import patch

from acadza.providers import GeminiProvider, ProviderError


class GeminiTests(unittest.TestCase):
    @patch("acadza.providers.urlopen")
    def test_json_and_blocked_response(self, send):
        provider = GeminiProvider("test-model", "test-key")
        candidate = {"finishReason": "STOP", "content": {"parts": [{"text": '{"answer":"A"}'}]}}
        send.return_value = io.BytesIO(json.dumps({"candidates": [candidate]}).encode())
        self.assertEqual(provider.call("solve", {"question": "example"}), {"answer": "A"})
        request = send.call_args.args[0]
        self.assertEqual(request.get_header("X-goog-api-key"), "test-key")
        self.assertIn("systemInstruction", json.loads(request.data))
        send.return_value = io.BytesIO(b'{"candidates":[]}')
        with self.assertRaises(ProviderError):
            provider.call("triage", {})
