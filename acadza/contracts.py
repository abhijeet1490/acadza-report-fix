"""Detached source snapshots and workflow contracts. No I/O or provider calls."""

from dataclasses import dataclass
import hashlib
import json
from typing import Literal

QuestionType = Literal["scq", "mcq", "integerQuestion"]
Route = Literal["auto_apply", "faculty_review", "no_change"]
Answer = str | list[str]


class DataError(ValueError):
    """An input record cannot be safely interpreted."""


@dataclass(frozen=True)
class QuestionSnapshot:
    question_id: str
    question_type: QuestionType
    source_json: str
    version: str

    @classmethod
    def from_record(cls, record: dict) -> "QuestionSnapshot":
        question_id = record.get("_id")
        question_type = record.get("questionType")
        if not isinstance(question_id, str) or not question_id:
            raise DataError("Question requires a nonempty _id")
        if question_type not in ("scq", "mcq", "integerQuestion"):
            raise DataError(f"{question_id}: unsupported questionType")
        payload = record.get(question_type)
        if not isinstance(payload, dict):
            raise DataError(f"{question_id}: missing active type payload")
        for field in ("question", "solution"):
            if not isinstance(payload.get(field), str):
                raise DataError(f"{question_id}: {field} must be a string")
        answer = payload.get("answer")
        valid_answer = isinstance(answer, str)
        if question_type == "mcq":
            valid_answer = isinstance(answer, list) and all(
                isinstance(value, str) for value in answer
            )
        if not valid_answer:
            raise DataError(f"{question_id}: unexpected answer representation")
        source_json = json.dumps(
            record, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        version = hashlib.sha256(source_json.encode("utf-8")).hexdigest()
        return cls(question_id, question_type, source_json, version)

    def to_record(self) -> dict:
        """Return a new copy, including unknown production metadata."""
        return json.loads(self.source_json)

    @property
    def payload(self) -> dict:
        return self.to_record()[self.question_type]


@dataclass(frozen=True)
class Report:
    report_id: str
    question_id: str
    student_id: str
    source: str
    reported_at: str
    comment: str
    source_json: str

    @classmethod
    def from_record(cls, record: dict) -> "Report":
        fields = ("reportId", "questionId", "studentId", "source", "reportedAt", "comment")
        if any(not isinstance(record.get(field), str) for field in fields):
            raise DataError("Report fields must be strings")
        if not record["reportId"] or not record["questionId"]:
            raise DataError("Report identity cannot be empty")
        return cls(
            *(record[field] for field in fields),
            json.dumps(record, ensure_ascii=False),
        )

    def to_record(self) -> dict:
        return json.loads(self.source_json)


@dataclass(frozen=True)
class Case:
    question: QuestionSnapshot
    reports: tuple[Report, ...]


@dataclass(frozen=True)
class ProposedEdit:
    """A source-bound proposal validated by acadza.policy."""

    question_id: str
    source_version: str
    field: str
    find: Answer
    replace: Answer

    def to_result_edit(self) -> dict:
        # Copy nested values so exporting never exposes contract state.
        return json.loads(json.dumps({
            "field": self.field, "find": self.find, "replace": self.replace,
        }))


@dataclass(frozen=True)
class ReportResult:
    """The assessment output shape. Validate through acadza.policy before use."""

    report_id: str
    question_id: str
    verdict: str
    repair: str | None
    edit: ProposedEdit | None
    route: Route
    confidence: float
    evidence: str
    student_reply: str

    def to_dict(self) -> dict:
        return {
            "reportId": self.report_id,
            "questionId": self.question_id,
            "verdict": self.verdict,
            "repair": self.repair,
            "edit": self.edit.to_result_edit() if self.edit else None,
            "route": self.route,
            "confidence": self.confidence,
            "evidence": self.evidence,
            "student_reply": self.student_reply,
        }
