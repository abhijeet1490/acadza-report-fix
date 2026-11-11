"""Bounded provider adapters. Credentials never enter cache keys or logs."""

import json
import os
from pathlib import Path
import socket
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

PROMPTS = Path(__file__).parent / "prompts"


class ProviderError(RuntimeError):
    pass


class OpenAIProvider:
    def __init__(self, model, api_key=None):
        self.model = model
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        if not self.api_key or not model:
            raise ProviderError("Live mode requires OPENAI_API_KEY and an explicit --model")
        self.fingerprint = {"provider": "openai-responses", "model": model}

    def call(self, stage, payload):
        body = {
            "model": self.model,
            "instructions": (PROMPTS / f"{stage}.txt").read_text(),
            "input": json.dumps(payload, ensure_ascii=False),
            "store": False,
            "max_output_tokens": 6000,
            "text": {"format": {"type": "json_object"}},
        }
        request = Request(
            "https://api.openai.com/v1/responses",
            data=json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {self.api_key}",
                     "Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=55) as response:
                raw = response.read(2_000_001)
            if len(raw) > 2_000_000:
                raise ProviderError("Provider response exceeded the size limit")
            envelope = json.loads(raw)
        except HTTPError as exc:
            raise ProviderError(f"Provider HTTP {exc.code}; retry after checking account and limits") from None
        except (URLError, TimeoutError, socket.timeout, json.JSONDecodeError, UnicodeError):
            raise ProviderError("Provider request failed or returned invalid JSON") from None
        if not isinstance(envelope, dict) or envelope.get("status") != "completed":
            raise ProviderError("Provider response did not complete")
        try:
            output = "".join(
                part.get("text", "")
                for item in envelope.get("output", []) if item.get("type") == "message"
                for part in item.get("content", []) if part.get("type") == "output_text"
            )
        except (TypeError, AttributeError):
            raise ProviderError("Provider response has invalid output content") from None
        try:
            value = json.loads(output)
        except json.JSONDecodeError:
            raise ProviderError("Provider did not return a JSON object") from None
        if not isinstance(value, dict):
            raise ProviderError("Provider did not return an object")
        return value


class ReferenceProvider:
    """Replay explicitly authored, source-bound analyses of the supplied cases."""

    def __init__(self, path=None):
        import hashlib
        self.path = path or Path(__file__).parent / "reference_cases.json"
        raw = self.path.read_bytes()
        self.pack = json.loads(raw)
        self.fingerprint = {
            "provider": "precomputed-reference",
            "pack_sha256": hashlib.sha256(raw).hexdigest(),
        }

    def call(self, stage, payload):
        question_id = payload["question_id"]
        try:
            case = self.pack["cases"][question_id]
        except KeyError:
            raise ProviderError("Reference pack has no analysis for this question") from None
        if case["source_version"] != payload["source_version"]:
            raise ProviderError("Reference source version changed; use live mode or review a new analysis")
        if stage == "solve":
            return case["solve"]
        if case["reports"] != payload["reports"]:
            raise ProviderError("Reference reports changed; a new investigation is required")
        return {"reports": case["results"]}
