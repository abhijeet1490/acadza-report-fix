"""Read-only ingestion. Deliberately preserves imperfect answer content."""

from dataclasses import dataclass
import json
from pathlib import Path

from .contracts import Case, DataError, QuestionSnapshot, Report

DEFAULT_DATA = Path(__file__).resolve().parent.parent / "data"


@dataclass(frozen=True)
class Dataset:
    questions: tuple[QuestionSnapshot, ...]
    reports: tuple[Report, ...]
    cases: tuple[Case, ...]
    taxonomy_json: str

    @property
    def taxonomy(self) -> dict:
        return json.loads(self.taxonomy_json)


def _read(path: Path, expected_type: type):
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, expected_type):
        raise DataError(f"{path.name}: expected {expected_type.__name__}")
    if expected_type is list and any(not isinstance(row, dict) for row in value):
        raise DataError(f"{path.name}: records must be objects")
    return value


def load_dataset(directory: Path = DEFAULT_DATA) -> Dataset:
    directory = Path(directory)
    questions = tuple(
        QuestionSnapshot.from_record(row)
        for row in _read(directory / "questions.json", list)
    )
    reports = tuple(
        Report.from_record(row)
        for row in _read(directory / "reports.json", list)
    )
    taxonomy = _read(directory / "taxonomy.json", dict)
    if not all(isinstance(taxonomy.get(key), dict) for key in ("verdicts", "repairs")):
        raise DataError("Taxonomy requires verdicts and repairs mappings")
    questions_by_id = {q.question_id: q for q in questions}
    if len(questions_by_id) != len(questions):
        raise DataError("Duplicate question _id")
    if len({r.report_id for r in reports}) != len(reports):
        raise DataError("Duplicate reportId")
    grouped = {}
    for report in reports:
        if report.question_id not in questions_by_id:
            raise DataError(f"{report.report_id}: unknown questionId")
        grouped.setdefault(report.question_id, []).append(report)
    cases = tuple(
        Case(questions_by_id[key], tuple(sorted(grouped[key], key=lambda r: r.report_id)))
        for key in sorted(grouped)
    )
    return Dataset(questions, reports, cases, json.dumps(taxonomy, ensure_ascii=False))
