from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from acadza.contracts import QuestionSnapshot
from acadza.dataset import load_dataset
from acadza.policy import PolicyError
from acadza.providers import OpenAIProvider, ProviderError, ReferenceProvider
from acadza.triage import Cache, digest, run, validate_reports


class RecordingProvider:
    def __init__(self):
        self.base = ReferenceProvider()
        self.fingerprint = self.base.fingerprint
        self.calls = []
        self.fail = False

    def call(self, stage, payload):
        self.calls.append((stage, payload))
        if self.fail and stage == "triage":
            raise ProviderError("Temporary simulated failure")
        return self.base.call(stage, payload)


class TriageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.cache = Cache(self.directory / "cache.sqlite3")
        self.data = load_dataset()
        self.provider = RecordingProvider()
        case = next(c for c in self.data.cases if c.reports[0].report_id == "R001")
        self.small = replace(self.data, cases=(case,), reports=case.reports)
        self.output = self.directory / "results.json"
        self.manifest = self.directory / "run.json"

    def tearDown(self):
        self.cache.close()
        self.temp.cleanup()

    def run_batch(self, data=None):
        return run(data or self.small, self.provider, self.cache, self.output, self.manifest)

    def test_full_run_and_scored_output(self):
        manifest = self.run_batch(self.data)
        self.assertEqual((manifest["report_count"], manifest["case_count"]), (44, 39))
        self.assertEqual(manifest["status"], "complete")
        results = json.loads(self.output.read_text())
        self.assertEqual({r["reportId"] for r in results}, {r.report_id for r in self.data.reports})
        self.assertEqual(len(results), 44)
        self.assertEqual(manifest["results_digest"], digest(results))
        by_id = {r["reportId"]: r for r in results}
        self.assertEqual(by_id["R004"]["edit"]["replace"], "D")
        self.assertEqual(by_id["R020"]["edit"]["replace"], ["A", "B", "D"])
        self.assertEqual(by_id["R021"]["edit"]["companion_edits"][0]["replace"], "B")
        self.assertEqual(by_id["R017"]["verdict"], "already_fixed")
        self.assertEqual(by_id["R027"]["verdict"], "no_error")
        self.assertEqual(by_id["R034"]["repair"], "unsound")
        self.assertFalse(any(r["route"] == "auto_apply" for r in results))
        self.assertEqual(len(self.provider.calls), 78)

    def test_duplicate_reports_share_solve_and_cache_resume(self):
        self.assertEqual(self.run_batch()["report_count"], 3)
        self.assertEqual(len(self.provider.calls), 2)
        self.assertEqual(self.run_batch()["cache_hits"], 2)
        self.assertEqual(len(self.provider.calls), 2)
        solve_payload = self.provider.calls[0][1]
        self.assertNotIn("record", solve_payload)
        self.assertNotIn("reports", solve_payload)
        self.assertNotIn("solution", solve_payload)
        self.assertNotIn("answer", solve_payload)
        self.assertNotIn("errorHistory", solve_payload)

    def test_failure_preserves_complete_output_and_retries_only_failed_stage(self):
        self.provider.fail = True
        self.output.write_text("previous complete output")
        manifest = self.run_batch()
        self.assertEqual(manifest["status"], "incomplete")
        self.assertEqual(manifest["failures"][0]["reportIds"], ["R001", "R002", "R003"])
        self.assertEqual(self.output.read_text(), "previous complete output")
        self.provider.fail = False
        manifest = self.run_batch()
        self.assertEqual(manifest["status"], "complete")
        self.assertEqual(manifest["cache_hits"], 1)
        self.assertEqual([stage for stage, _ in self.provider.calls], ["solve", "triage", "triage"])

    def test_changed_sources_invalidate_reference_and_cache(self):
        self.run_batch()
        record = self.small.cases[0].question.to_record()
        record["scq"]["answer"] = "D"
        question = QuestionSnapshot.from_record(record)
        changed = replace(self.small, cases=(replace(self.small.cases[0], question=question),))
        manifest = self.run_batch(changed)
        self.assertEqual(manifest["status"], "incomplete")
        self.assertEqual(manifest["cache_hits"], 0)
        self.assertIn("Reference source version changed", manifest["failures"][0]["error"])

    def test_changed_reports_do_not_reuse_triage(self):
        self.run_batch()
        first = replace(self.small.reports[0], comment="A different allegation")
        # Preserve the production representation used in the payload.
        record = first.to_record()
        record["comment"] = first.comment
        first = replace(first, source_json=json.dumps(record))
        reports = (first,) + self.small.reports[1:]
        changed = replace(self.small, cases=(replace(self.small.cases[0], reports=reports),), reports=reports)
        manifest = self.run_batch(changed)
        self.assertEqual(manifest["status"], "incomplete")
        self.assertEqual(manifest["cache_hits"], 1)

    def test_report_coverage_identity_and_scope_are_enforced(self):
        case = self.small.cases[0]
        original = self.provider.base.pack["cases"][case.question.question_id]["results"]
        variants = [
            original[:-1],
            original + [original[0]],
            [dict(original[0], questionId="other")] + original[1:],
            [dict(original[0], edits=[{"field": "solution", "find": "missing", "replace": "x"}])] + original[1:],
            [dict(original[0], verdict="invented")] + original[1:],
        ]
        for rows in variants:
            with self.subTest(rows=rows), self.assertRaises(PolicyError):
                validate_reports({"reports": rows}, case, self.data.taxonomy)

    def test_model_cannot_grant_auto_authority(self):
        case = self.small.cases[0]
        rows = self.provider.base.pack["cases"][case.question.question_id]["results"]
        rows = [dict(row, route="auto_apply") for row in rows]
        validated = validate_reports({"reports": rows}, case, self.data.taxonomy)
        self.assertTrue(all(r["result"]["route"] == "faculty_review" for r in validated))
        self.assertTrue(all(r["route_reasons"] for r in validated))

    def test_missing_distractor_remains_reviewable_without_invented_edit(self):
        case = next(c for c in self.data.cases if c.reports[0].report_id == "R009")
        rows = self.provider.base.pack["cases"][case.question.question_id]["results"]
        result = validate_reports({"reports": rows}, case, self.data.taxonomy)
        self.assertEqual(result[0]["candidate"], case.question.to_record())
        self.assertIsNone(result[0]["result"]["edit"])

    def test_coupled_type_migration_is_validated_as_one_bundle(self):
        case = next(c for c in self.data.cases if c.reports[0].report_id == "R021")
        rows = self.provider.base.pack["cases"][case.question.question_id]["results"]
        value = validate_reports({"reports": rows}, case, self.data.taxonomy)[0]
        self.assertEqual(value["candidate"]["questionType"], "scq")
        self.assertEqual(value["candidate"]["scq"]["answer"], "B")
        self.assertNotIn("mcq", value["candidate"])


class ResponseContext:
    def __init__(self, envelope):
        self.envelope = envelope

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self, size):
        return json.dumps(self.envelope).encode()


class ProviderTests(unittest.TestCase):
    def test_live_adapter_serialization_and_output_parsing(self):
        provider = OpenAIProvider("test-model", api_key="test-key")
        envelope = {
            "status": "completed",
            "output": [{"type": "message",
                        "content": [{"type": "output_text", "text": '{"answer":"A"}'}]}],
        }
        with patch("acadza.providers.urlopen", return_value=ResponseContext(envelope)) as request:
            self.assertEqual(provider.call("solve", {"question": "example"}), {"answer": "A"})
        args = request.call_args
        body = json.loads(args.args[0].data)
        self.assertEqual(body["model"], "test-model")
        self.assertFalse(body["store"])
        self.assertEqual(args.kwargs["timeout"], 55)
        self.assertNotIn("test-key", json.dumps(provider.fingerprint))
        self.assertIn("withheld", body["instructions"])

    def test_incomplete_and_provider_errors_never_become_results(self):
        provider = OpenAIProvider("test-model", api_key="secret-test-key")
        with patch("acadza.providers.urlopen", return_value=ResponseContext({"status": "incomplete"})):
            with self.assertRaises(ProviderError):
                provider.call("solve", {})
        failure = HTTPError("https://api.openai.com", 429, "secret-test-key", {}, None)
        with patch("acadza.providers.urlopen", side_effect=failure):
            with self.assertRaises(ProviderError) as caught:
                provider.call("solve", {})
        self.assertNotIn("secret-test-key", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
