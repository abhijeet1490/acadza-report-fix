import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from uuid import uuid4
from unittest.mock import patch

from acadza.contracts import DataError
from acadza.dataset import load_dataset
from acadza.policy import PolicyError
from acadza.review import CASE_ID, Conflict, MissingCase, ReviewStore


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "review.sqlite3"
        self.store = ReviewStore(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def case_for(self, report_id):
        item = next(row for row in self.store.queue() if report_id in row["reportIds"])
        return self.store.view(item["id"])

    def request(self, view, action="approve", **extra):
        return dict(case_id=view["id"], request_id=str(uuid4()), action=action,
                    revision=view["revision"], source_version=view["source_version"], **extra)

    def test_full_queue_and_priority(self):
        queue = self.store.queue()
        self.assertEqual(len(queue), 39)
        self.assertEqual(sum(len(row["reportIds"]) for row in queue), 44)
        self.assertEqual(queue[0]["reportIds"], ["R001", "R002", "R003"])
        self.assertEqual(queue[0]["reporters"], 3)
        self.assertEqual(len({row["id"] for row in queue}), 39)

    def test_text_and_solution_repairs_preserve_other_fields(self):
        for report_id, field in (("R012", "question"), ("R016", "solution")):
            with self.subTest(report=report_id):
                view = self.case_for(report_id)
                self.store.act(self.request(view))
                after = self.store.view(view["id"])
                expected = view["question"]
                expected[expected["questionType"]][field] = view["candidate"][expected["questionType"]][field]
                self.assertEqual(after["current"], expected)
                self.assertEqual(after["audit"][0]["after"], expected)

    def test_mcq_and_type_change_are_atomic_and_persistent(self):
        mcq = self.case_for("R020")
        self.store.act(self.request(mcq))
        self.assertEqual(self.store.view(mcq["id"])["current"]["mcq"]["answer"], ["A", "B", "D"])
        typed = self.case_for("R021")
        self.assertEqual(len(typed["edits"]), 2)
        self.store.act(self.request(typed))
        after = ReviewStore(self.path).view(typed["id"])
        self.assertEqual(after["current"]["questionType"], "scq")
        self.assertEqual(after["current"]["scq"]["answer"], "B")
        self.assertNotIn("mcq", after["current"])
        self.assertEqual(len(after["audit"]), 1)
        self.assertEqual(len(after["audit"][0]["edits"]), 2)
        self.assertEqual(after["question"]["questionType"], "mcq")

    def test_review_only_and_missing_edit_refer_without_claiming_a_fix(self):
        for report_id in ("R009", "R025", "R034", "R042"):
            with self.subTest(report=report_id):
                view = self.case_for(report_id)
                self.assertEqual(view["edits"], [])
                self.store.act(self.request(view))
                after = self.store.view(view["id"])
                self.assertEqual(after["status"], "referred")
                self.assertEqual(after["current"], view["current"])
                self.assertTrue(all("remains pending" in r["reply"] for r in after["reports"]))

    def test_nondefects_confirm_and_cannot_mutate(self):
        for report_id in ("R017", "R027", "R036", "R038", "R039", "R041"):
            with self.subTest(report=report_id):
                view = self.case_for(report_id)
                with self.assertRaises(PolicyError):
                    self.store.act(self.request(view, "edit", note="Attempted override",
                        edits=[{"field": "answer", "find": "A", "replace": "C"}]))
                self.store.act(self.request(view))
                after = self.store.view(view["id"])
                self.assertEqual(after["status"], "confirmed")
                self.assertEqual(after["current"], view["current"])

    def test_preview_binds_to_case_revision_and_rechecks_field_scope(self):
        view = self.case_for("R012")
        request = self.request(view, "edit", edits=view["editable_edits"], note="Checked spelling")
        preview = self.store.preview_edits(request)
        self.assertEqual(preview["candidate"], view["candidate"])
        self.assertEqual(self.store.view(view["id"])["status"], "pending")
        with self.assertRaises(PolicyError):
            self.store.preview_edits(dict(request, edits=[
                {"field": "answer", "find": "A", "replace": "B"}]))
        self.store.act(request)
        with self.assertRaises(Conflict):
            self.store.preview_edits(request)

    def test_receipt_cannot_be_replayed_into_another_case(self):
        first = self.case_for("R001")
        second = self.case_for("R004")
        request = self.request(first)
        response = self.store.act(request)
        self.assertEqual(self.store.act(request), response)
        with self.assertRaises(Conflict):
            self.store.act(dict(request, case_id=second["id"]))
        self.assertEqual(self.store.view(second["id"])["status"], "pending")

    def test_changed_import_does_not_replace_persisted_decision(self):
        view = self.case_for("R001")
        self.store.act(self.request(view))
        before = self.store.view(view["id"])
        reopened = ReviewStore(self.path)
        self.assertEqual(reopened.view(view["id"]), before)
        self.assertEqual(len(reopened.queue()), 39)

    def test_existing_queue_survives_a_failed_new_default_batch(self):
        view = self.case_for("R001")
        self.store.act(self.request(view))
        with patch("acadza.review.import_manifest", side_effect=DataError("New batch incomplete")) as importer:
            reopened = ReviewStore(self.path)
        importer.assert_not_called()
        self.assertEqual(len(reopened.queue()), 39)
        self.assertEqual(reopened.view()["status"], "approved")

    def test_all_cases_follow_the_normal_action_entry_point(self):
        for entry in self.store.queue():
            with self.subTest(reports=entry["reportIds"]):
                view = self.store.view(entry["id"])
                self.store.act(self.request(view))
                after = self.store.view(entry["id"])
                self.assertEqual(after["status"], view["approval_status"])
                expected = view["candidate"] if view["edits"] else view["current"]
                self.assertEqual(after["current"], expected)
                self.assertEqual(len(after["audit"]), 1)

    def test_unknown_case_and_incomplete_manifest(self):
        with self.assertRaises(MissingCase):
            self.store.view("missing")
        path = Path(self.temp.name) / "incomplete.json"
        path.write_text('{"status":"incomplete"}')
        with self.assertRaises(DataError):
            ReviewStore(self.path, path)
        self.assertEqual(len(self.store.queue()), 39)

    def test_legacy_database_upgrade_preserves_decision_and_receipt(self):
        path = Path(self.temp.name) / "legacy.sqlite3"
        source = next(q for q in load_dataset().questions if q.question_id == CASE_ID)
        after = source.to_record()
        after["scq"]["answer"] = "A"
        from acadza.contracts import QuestionSnapshot
        accepted = QuestionSnapshot.from_record(after)
        request = dict(request_id="legacy-request", action="approve",
                       revision=1, source_version=source.version)
        receipt = {"status": "approved", "revision": 2, "version": accepted.version,
                   "request_id": "legacy-request",
                   "replies": {"R001": "Legacy saved reply"}}
        db = sqlite3.connect(path)
        db.executescript("""
            CREATE TABLE questions(id TEXT PRIMARY KEY, source TEXT NOT NULL, version TEXT NOT NULL);
            CREATE TABLE cases(id TEXT PRIMARY KEY REFERENCES questions(id), source TEXT NOT NULL,
                revision INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT 'pending',
                replacement TEXT NOT NULL DEFAULT 'A');
            CREATE TABLE audit(sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id TEXT UNIQUE NOT NULL, request_hash TEXT NOT NULL,
                case_id TEXT NOT NULL REFERENCES cases(id), created_at TEXT NOT NULL,
                actor TEXT NOT NULL, action TEXT NOT NULL, note TEXT NOT NULL,
                before_json TEXT NOT NULL, after_json TEXT NOT NULL, response TEXT NOT NULL);
        """)
        db.execute("INSERT INTO questions VALUES (?,?,?)", (CASE_ID, accepted.source_json, accepted.version))
        db.execute("INSERT INTO cases VALUES (?,?,2,'approved','A')", (CASE_ID, source.source_json))
        db.execute("INSERT INTO audit VALUES (1,?,?,?,?,?,?,?,?,?,?)", (
            "legacy-request", hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest(),
            CASE_ID, "2026-11-11T10:00:00Z", "Local faculty", "approve", "",
            source.source_json, accepted.source_json, json.dumps(receipt)))
        db.commit()
        db.close()
        migrated = ReviewStore(path)
        view = migrated.view()
        self.assertEqual(view["status"], "approved")
        self.assertEqual(view["reports"][0]["reply"], "Legacy saved reply")
        self.assertEqual(view["current"], accepted.to_record())
        self.assertEqual(view["audit"][0]["sequence"], 1)
        self.assertEqual(migrated.act(request), receipt)
        self.assertEqual(len(migrated.queue()), 39)


if __name__ == "__main__":
    unittest.main()
