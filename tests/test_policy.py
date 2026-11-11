from dataclasses import replace
import unittest

from acadza.contracts import ProposedEdit, QuestionSnapshot, ReportResult
from acadza.dataset import load_dataset
from acadza.policy import (
    PolicyError, Verification, apply_result, proposal_digest, validate_result,
)


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.taxonomy = load_dataset().taxonomy
        self.question = QuestionSnapshot.from_record({
            "_id": "test", "questionType": "scq", "unknown": {"preserve": True},
            "scq": {
                "question": "<p>A cubee has side 2 m.</p>(A) 4 (B) 8 (C) 6 (D) 2",
                "solution": "<p>The volume is 8.</p>", "answer": "A",
                "quesImages": [], "solutionImages": [],
            },
        })
        self.edit = ProposedEdit("test", self.question.version, "answer", "A", "B")
        self.result = ReportResult(
            "R1", "test", "defect", "answer_key", self.edit, "auto_apply",
            0.99, "Volume = 2 cubed = 8, option B.", "Thanks for reporting this.",
        )

    def proof(self, result=None, companions=(), check="independent_solve"):
        result = result or self.result
        return (Verification(
            self.question.version,
            proposal_digest((result.edit,) + companions),
            check, "Checked separately against the stem and options.",
        ),)

    def test_independent_verification_and_copy_on_write(self):
        self.assertEqual(validate_result(self.result, self.question, self.taxonomy).route,
                         "faculty_review")
        with self.assertRaises(PolicyError):
            apply_result(self.result, self.question, self.taxonomy)
        new = apply_result(self.result, self.question, self.taxonomy,
                           verifications=self.proof())
        self.assertEqual(new.payload["answer"], "B")
        self.assertEqual(self.question.payload["answer"], "A")
        expected = self.question.to_record()
        expected["scq"]["answer"] = "B"
        self.assertEqual(new.to_record(), expected)
        with self.assertRaises(PolicyError):
            apply_result(self.result, new, self.taxonomy, verifications=self.proof())

    def test_proof_cannot_be_reused_for_different_proposal(self):
        changed = replace(self.result, edit=replace(self.edit, replace="C"))
        decision = validate_result(changed, self.question, self.taxonomy,
                                   verifications=self.proof())
        self.assertEqual(decision.route, "faculty_review")

    def test_scope_and_answer_preconditions(self):
        cases = [
            replace(self.edit, source_version="old"),
            replace(self.edit, question_id="other"),
            replace(self.edit, find="D"),
            replace(self.edit, replace="E"),
            replace(self.edit, field="chapter"),
        ]
        for edit in cases:
            with self.subTest(edit=edit), self.assertRaises(PolicyError):
                validate_result(replace(self.result, edit=edit), self.question, self.taxonomy)

    def test_no_change_preserves_original(self):
        result = replace(self.result, verdict="no_error", repair=None, edit=None,
                         route="no_change")
        self.assertIs(apply_result(result, self.question, self.taxonomy), self.question)
        with self.assertRaises(PolicyError):
            validate_result(replace(result, edit=self.edit), self.question, self.taxonomy)

    def test_every_review_only_repair(self):
        for key, repair in self.taxonomy["repairs"].items():
            if repair["auto"]:
                continue
            with self.subTest(repair=key):
                result = replace(self.result, repair=key, edit=None)
                self.assertEqual(validate_result(result, self.question, self.taxonomy).route,
                                 "faculty_review")
                with self.assertRaises(PolicyError):
                    validate_result(replace(result, edit=self.edit), self.question, self.taxonomy)
                with self.assertRaises(PolicyError):
                    apply_result(result, self.question, self.taxonomy)

    def text_result(self, repair, find, replacement, field="question"):
        edit = replace(self.edit, field=field, find=find, replace=replacement)
        return replace(self.result, repair=repair, edit=edit)

    def test_exact_text_match_and_surgical_edit(self):
        result = self.text_result("spelling", "cubee", "cube")
        new = apply_result(result, self.question, self.taxonomy,
                           verifications=self.proof(result, check="words_only"))
        self.assertIn("A cube has", new.payload["question"])
        for find in ("", "missing", "2"):
            with self.subTest(find=find), self.assertRaises(PolicyError):
                validate_result(self.text_result("notation", find, "3"),
                                self.question, self.taxonomy)

    def test_spelling_protects_math_and_values(self):
        for find, replacement in (("2 m", "3 m"), ("<p>", "<div>")):
            with self.subTest(find=find), self.assertRaises(PolicyError):
                validate_result(self.text_result("spelling", find, replacement),
                                self.question, self.taxonomy)
        record = self.question.to_record()
        record["scq"]["question"] += '<math><mi>x</mi></math>'
        question = QuestionSnapshot.from_record(record)
        result = self.text_result("spelling", "<mi>x</mi>", "<mi>y</mi>")
        result = replace(result, edit=replace(result.edit, source_version=question.version))
        with self.assertRaises(PolicyError):
            validate_result(result, question, self.taxonomy)

    def test_option_repair_preserves_stem(self):
        result = self.text_result("option_repair", "(B) 8", "(B) 9")
        candidate = validate_result(result, self.question, self.taxonomy).candidate
        self.assertIn("(B) 9", candidate.payload["question"])
        with self.assertRaises(PolicyError):
            validate_result(self.text_result("option_repair", "side 2 m", "side 3 m"),
                            self.question, self.taxonomy)

    def test_additive_answer_form_and_removal_only(self):
        valid = self.text_result("answer_form", "2 m.", "2 m. Give volume in cubic metres.")
        self.assertEqual(validate_result(valid, self.question, self.taxonomy).route,
                         "faculty_review")
        for result in (
            self.text_result("answer_form", "2 m.", "3 m."),
            self.text_result("noise", "cubee", "cube"),
        ):
            with self.assertRaises(PolicyError):
                validate_result(result, self.question, self.taxonomy)

    def test_empty_solution_can_be_filled(self):
        record = self.question.to_record()
        record["scq"]["solution"] = ""
        question = QuestionSnapshot.from_record(record)
        result = self.text_result("solution_missing", "", "2 cubed is 8, option B.", "solution")
        result = replace(result, edit=replace(result.edit, source_version=question.version))
        self.assertEqual(
            validate_result(result, question, self.taxonomy).candidate.payload["solution"],
            "2 cubed is 8, option B.",
        )

    def test_atomic_type_migration(self):
        edit = replace(self.edit, field="questionType", find="scq", replace="mcq")
        result = replace(self.result, repair="type_mismatch", edit=edit)
        answer = replace(self.edit, replace=["A", "B"])
        original = self.question.to_record()
        new = apply_result(result, self.question, self.taxonomy,
                           companion_edits=(answer,),
                           verifications=self.proof(result, (answer,), "type_and_answer_verified"))
        self.assertEqual(new.question_type, "mcq")
        self.assertEqual(new.payload["answer"], ["A", "B"])
        self.assertNotIn("scq", new.to_record())
        self.assertEqual(new.to_record()["unknown"], original["unknown"])
        self.assertEqual(self.question.to_record(), original)
        with self.assertRaises(PolicyError):
            validate_result(result, self.question, self.taxonomy)
        with self.assertRaises(PolicyError):
            validate_result(result, self.question, self.taxonomy,
                            companion_edits=(replace(answer, replace=["A, B"]),))

    def test_allowed_field_still_requires_correct_repair(self):
        result = self.text_result("answer_key", "cubee", "cube")
        with self.assertRaises(PolicyError):
            validate_result(result, self.question, self.taxonomy)

    def test_explicit_review_route_is_preserved(self):
        result = replace(self.result, route="faculty_review")
        self.assertEqual(validate_result(
            result, self.question, self.taxonomy, verifications=self.proof(result)
        ).route, "faculty_review")

    def test_duplicate_fields_and_stale_proof_are_rejected(self):
        with self.assertRaises(PolicyError):
            validate_result(self.result, self.question, self.taxonomy,
                            companion_edits=(self.edit,))
        proof = replace(self.proof()[0], question_version="old")
        self.assertEqual(validate_result(
            self.result, self.question, self.taxonomy, verifications=(proof,)
        ).route, "faculty_review")

    def test_invalid_result_values(self):
        for result in (
            replace(self.result, verdict="invented"),
            replace(self.result, repair="invented"),
            replace(self.result, confidence=float("nan")),
            replace(self.result, confidence=True),
            replace(self.result, route="invented"),
            replace(self.result, evidence=""),
        ):
            with self.subTest(result=result), self.assertRaises(PolicyError):
                validate_result(result, self.question, self.taxonomy)


if __name__ == "__main__":
    unittest.main()
