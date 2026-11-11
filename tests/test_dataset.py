import json
from pathlib import Path
import tempfile
import unittest

from acadza.contracts import DataError, QuestionSnapshot
from acadza.dataset import DEFAULT_DATA, load_dataset


class DatasetTests(unittest.TestCase):
    def test_production_round_trip_and_grouping(self):
        dataset = load_dataset()
        originals = json.loads((DEFAULT_DATA / "questions.json").read_text())
        self.assertEqual([q.to_record() for q in dataset.questions], originals)
        reports = json.loads((DEFAULT_DATA / "reports.json").read_text())
        self.assertEqual([r.to_record() for r in dataset.reports], reports)
        self.assertEqual((len(dataset.questions), len(dataset.reports), len(dataset.cases)), (43, 44, 39))
        self.assertEqual(
            {r.report_id for case in dataset.cases for r in case.reports},
            {r.report_id for r in dataset.reports},
        )
        case = next(c for c in dataset.cases if any(r.report_id == "R001" for r in c.reports))
        self.assertEqual([r.report_id for r in case.reports], ["R001", "R002", "R003"])

    def test_detached_snapshot_and_stable_version(self):
        original = load_dataset().questions[0].to_record()
        snapshot = QuestionSnapshot.from_record(original)
        exported = snapshot.to_record()
        exported["scq"]["answer"] = "changed"
        self.assertEqual(snapshot.to_record(), original)
        original["scq"]["answer"] = "changed"
        self.assertNotEqual(QuestionSnapshot.from_record(original).version, snapshot.version)
        self.assertEqual(
            QuestionSnapshot.from_record(snapshot.to_record()).version, snapshot.version
        )

    def test_invalid_identity_and_missing_payload(self):
        for mutation in ("orphan", "duplicate_report", "duplicate_question", "missing_payload"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                path = Path(directory)
                for filename in ("questions.json", "reports.json", "taxonomy.json"):
                    (path / filename).write_bytes((DEFAULT_DATA / filename).read_bytes())
                reports = json.loads((path / "reports.json").read_text())
                questions = json.loads((path / "questions.json").read_text())
                if mutation == "orphan":
                    reports[0]["questionId"] = "missing"
                elif mutation == "duplicate_report":
                    reports.append(reports[0])
                elif mutation == "duplicate_question":
                    questions.append(questions[0])
                else:
                    del questions[0][questions[0]["questionType"]]
                (path / "reports.json").write_text(json.dumps(reports))
                (path / "questions.json").write_text(json.dumps(questions))
                with self.assertRaises(DataError):
                    load_dataset(path)


if __name__ == "__main__":
    unittest.main()
