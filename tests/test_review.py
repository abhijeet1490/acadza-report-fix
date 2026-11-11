from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import tempfile
import unittest
from uuid import uuid4

from acadza.policy import PolicyError
from acadza.review import Conflict, ReviewStore


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "review.sqlite3"
        self.store = ReviewStore(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def request(self, action="approve", **values):
        view = self.store.view()
        return dict(request_id=str(uuid4()), action=action, revision=view["revision"],
                    source_version=view["source_version"], **values)

    def test_approval_persists_one_key_and_all_replies(self):
        before = self.store.view()
        self.assertEqual(before["result"]["route"], "faculty_review")
        self.store.act(self.request())
        after = ReviewStore(self.path).view()
        expected = before["current"]
        expected["scq"]["answer"] = "A"
        self.assertEqual(after["current"], expected)
        self.assertEqual(after["question"]["scq"]["answer"], "C")
        self.assertEqual(after["status"], "approved")
        self.assertEqual(len(after["audit"]), 1)
        self.assertEqual(after["audit"][0]["before"], before["question"])
        self.assertTrue(all("updated to option A" in r["reply"] for r in after["reports"]))

    def test_repeated_receipt_is_idempotent_and_bound_to_request(self):
        request = self.request()
        response = self.store.act(request)
        self.assertEqual(self.store.act(request), response)
        self.assertEqual(len(self.store.view()["audit"]), 1)
        with self.assertRaises(Conflict):
            self.store.act(dict(request, action="reject"))

    def test_two_reviewers_cannot_overwrite_each_other(self):
        requests = [self.request(), self.request("reject")]
        def act(request):
            try:
                self.store.act(request)
                return "saved"
            except Conflict:
                return "conflict"
        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(act, requests))
        self.assertCountEqual(outcomes, ["saved", "conflict"])
        self.assertEqual(len(self.store.view()["audit"]), 1)

    def test_reject_preserves_record_and_persists_note(self):
        before = self.store.view()["current"]
        self.store.act(self.request("reject", note="Faculty will investigate further"))
        after = self.store.view()
        self.assertEqual(after["current"], before)
        self.assertEqual(after["status"], "rejected")
        self.assertEqual(after["audit"][0]["note"], "Faculty will investigate further")
        self.assertTrue(all("did not apply" in r["reply"] for r in after["reports"]))

    def test_edit_preview_is_read_only_then_save_revalidates(self):
        before = self.store.view()
        self.assertEqual(self.store.preview("B")["scq"]["answer"], "B")
        self.assertEqual(self.store.view(), before)
        for replacement in ("C", "E", ["A"]):
            with self.subTest(replacement=replacement), self.assertRaises((PolicyError, ValueError)):
                self.store.act(self.request("edit", replacement=replacement, note="Faculty override"))
            self.assertEqual(self.store.view(), before)
        with self.assertRaises(PolicyError):
            self.store.act(self.request("edit", replacement="B"))
        self.store.act(self.request("edit", replacement="B", note="Faculty override"))
        after = self.store.view()
        self.assertEqual(after["current"]["scq"]["answer"], "B")
        self.assertEqual(after["audit"][0]["action"], "edit")
        self.assertTrue(all("option B" in r["reply"] for r in after["reports"]))

    def test_external_question_change_conflicts(self):
        request = self.request()
        with self.store.connection() as db:
            db.execute("UPDATE questions SET version='changed'")
        with self.assertRaises(Conflict):
            self.store.act(request)
        self.assertEqual(self.store.view()["audit"], [])

    def test_failed_audit_rolls_back_question_and_case(self):
        before = self.store.view()
        with self.store.connection() as db:
            db.execute("""CREATE TRIGGER refuse_audit BEFORE INSERT ON audit
                          BEGIN SELECT RAISE(ABORT, 'injected audit failure'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.act(self.request())
        self.assertEqual(self.store.view(), before)


if __name__ == "__main__":
    unittest.main()
