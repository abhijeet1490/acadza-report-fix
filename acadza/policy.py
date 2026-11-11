"""Pure proposal validation and copy-on-write application.

Verification records must be supplied by the trusted checking service. A model's
confidence or its evidence string does not establish those checks.
"""

from dataclasses import dataclass
import hashlib
import json
import math
import re

from .contracts import DataError, ProposedEdit, QuestionSnapshot, Report, ReportResult

# Semantic checks that cannot be established by a string replacement alone.
REQUIRED_CHECKS = {
    "spelling": "words_only",
    "noise": "stray_text_only",
    "exam_reference": "exam_tag_only",
    "markup_leak": "equivalent_rendering",
    "answer_form": "answer_form_supported",
    "notation": "notation_supported",
    "stem_data": "solution_states_data",
    "option_repair": "option_content_supported",
    "solution_fix": "solution_verified",
    "solution_missing": "ends_in_verified_key",
    "answer_key": "independent_solve",
    "type_mismatch": "type_and_answer_verified",
}


class PolicyError(DataError):
    """The proposed operation violates its structural or source contract."""


@dataclass(frozen=True)
class Verification:
    question_version: str
    proposal_digest: str
    check: str
    evidence: str


@dataclass(frozen=True)
class Decision:
    route: str
    reasons: tuple[str, ...]
    candidate: QuestionSnapshot


def proposal_digest(edits: tuple[ProposedEdit, ...]) -> str:
    value = [edit.to_result_edit() for edit in edits]
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _fail(condition: bool, message: str):
    if condition:
        raise PolicyError(message)


def _taxonomy(taxonomy: dict):
    _fail(not isinstance(taxonomy, dict), "Taxonomy must be an object")
    verdicts = taxonomy.get("verdicts")
    repairs = taxonomy.get("repairs")
    _fail(not isinstance(verdicts, dict) or not isinstance(repairs, dict),
          "Taxonomy requires verdicts and repairs")
    for key, repair in repairs.items():
        _fail(not isinstance(repair, dict), f"Invalid repair {key}")
        fields = repair.get("fields")
        _fail(not isinstance(fields, list) or
              any(field not in ("question", "solution", "answer", "questionType") for field in fields),
              f"Invalid fields for {key}")
        _fail(type(repair.get("auto")) is not bool, f"Invalid auto flag for {key}")
    return verdicts, repairs


def _replace_text(value: str, edit: ProposedEdit) -> str:
    _fail(not isinstance(edit.find, str) or not isinstance(edit.replace, str),
          "Text edits require string find and replace values")
    if not edit.find:
        _fail(value != "", "Empty find is allowed only for an empty field")
        return edit.replace
    _fail(value.count(edit.find) != 1, "Text find must match exactly once")
    return value.replace(edit.find, edit.replace, 1)


def _option_boundary(value: str) -> int:
    # Only recognize literal production option markers. Unclear boundaries need
    # a revised proposal with a mechanically identifiable option location.
    marker = re.search(r"\(A\)", value)
    _fail(marker is None, "Cannot identify option boundary")
    return marker.start()


def _scope(repair: str, old: str, new: str, edit: ProposedEdit):
    if repair == "option_repair":
        old_boundary = _option_boundary(old)
        new_boundary = _option_boundary(new)
        _fail(old[:old_boundary] != new[:new_boundary], "Option repair changed the stem")
        _fail(old.find(edit.find) < old_boundary, "Option repair must target an option")
    if repair == "answer_form":
        # An additive replacement must retain the entire matched source fragment.
        _fail(bool(edit.find) and edit.find not in edit.replace,
              "Answer form must be additive")
    if repair == "spelling":
        # Protect markup, entities, math, numbers, whitespace and punctuation.
        # Word substitutions still require a semantic words_only verification.
        protected = r"<math\b[\s\S]*?</math>|<[^>]*>|&[^;\s]+;|[0-9]+|[^A-Za-z]"
        _fail(re.findall(protected, old) != re.findall(protected, new),
              "Spelling changed protected markup, symbols or values")
    if repair in ("noise", "exam_reference"):
        _fail(edit.replace != "", "Removal repair must only remove its target")


def _candidate(question: QuestionSnapshot, repair: str, allowed: list[str],
               edits: tuple[ProposedEdit, ...]) -> QuestionSnapshot:
    record = question.to_record()
    payload = record[question.question_type]
    fields = [edit.field for edit in edits]
    _fail(len(set(fields)) != len(fields), "Use one edit per field")
    target_type = question.question_type
    for edit in edits:
        _fail(edit.question_id != question.question_id, "Edit question mismatch")
        _fail(edit.source_version != question.version, "Stale source version")
        _fail(edit.field not in allowed, "Repair cannot touch this field")
        if edit.field == "questionType":
            _fail(edit.find != question.question_type, "Question type precondition failed")
            _fail(edit.replace not in ("scq", "mcq", "integerQuestion"),
                  "Unsupported target question type")
            target_type = edit.replace
        elif edit.field == "answer":
            _fail(payload["answer"] != edit.find, "Answer precondition failed")
            payload["answer"] = json.loads(json.dumps(edit.replace))
        else:
            old = payload[edit.field]
            new = _replace_text(old, edit)
            _scope(repair, old, new, edit)
            payload[edit.field] = new
    if target_type != question.question_type:
        _fail(target_type in record, "Target payload already exists")
        record[target_type] = record.pop(question.question_type)
        record["questionType"] = target_type
    try:
        candidate = QuestionSnapshot.from_record(record)
    except DataError as exc:
        raise PolicyError(str(exc)) from exc
    _fail(candidate.version == question.version, "Proposal makes no change")
    if "answer" in fields or target_type != question.question_type:
        answer = candidate.payload["answer"]
        if target_type == "scq":
            _fail(answer not in ("A", "B", "C", "D"), "Invalid SCQ answer")
        elif target_type == "mcq":
            _fail(not answer or any(a not in ("A", "B", "C", "D") for a in answer)
                  or len(set(answer)) != len(answer), "Invalid MCQ answer")
        else:
            _fail(re.fullmatch(r"[+-]?[0-9]+", answer) is None, "Invalid integer answer")
    return candidate


def validate_result(result: ReportResult, question: QuestionSnapshot, taxonomy: dict,
                    *, report: Report | None = None,
                    companion_edits: tuple[ProposedEdit, ...] = (),
                    verifications: tuple[Verification, ...] = ()) -> Decision:
    """Reject invalid proposals; route unverified semantics to faculty.

    Companion edits support atomic type migrations and coupled key/solution fixes.
    Every edit is checked against the same source version and repair scope.
    """
    verdicts, repairs = _taxonomy(taxonomy)
    _fail(result.question_id != question.question_id, "Result question mismatch")
    if report is not None:
        _fail(result.report_id != report.report_id or report.question_id != question.question_id,
              "Result report mismatch")
    _fail(result.verdict not in verdicts, "Unknown verdict")
    _fail(result.route not in ("auto_apply", "faculty_review", "no_change"), "Unknown route")
    _fail(type(result.confidence) not in (int, float) or
          not math.isfinite(result.confidence) or not 0 <= result.confidence <= 1,
          "Confidence must be finite and between zero and one")
    _fail(not isinstance(result.evidence, str) or not result.evidence.strip(),
          "Evidence is required")
    _fail(not isinstance(result.student_reply, str) or not result.student_reply.strip(),
          "Student reply is required")
    edits = ((result.edit,) if result.edit is not None else ()) + tuple(companion_edits)
    if result.verdict != "defect":
        _fail(result.repair is not None or bool(edits), "Non-defect cannot propose a repair")
        _fail(result.route != "no_change", "Non-defect must use no_change")
        return Decision("no_change", (), question)
    _fail(result.repair not in repairs, "Defect requires a known repair")
    _fail(result.route == "no_change", "Defect requires an action route")
    rule = repairs[result.repair]
    if not rule["auto"]:
        _fail(bool(edits), "Review-only repair cannot carry content edits")
        return Decision("faculty_review", ("Taxonomy requires faculty approval",), question)
    if not edits:
        _fail(result.route != "faculty_review", "Automatic repair requires a proposal")
        return Decision("faculty_review", ("No safe edit established; faculty investigation required",), question)
    candidate = _candidate(question, result.repair, rule["fields"], edits)
    required = REQUIRED_CHECKS.get(result.repair)
    digest = proposal_digest(edits)
    verified = required is not None and any(
        v.question_version == question.version and v.proposal_digest == digest
        and v.check == required and isinstance(v.evidence, str) and bool(v.evidence.strip())
        for v in verifications
    )
    if not verified:
        return Decision("faculty_review", (f"Verification required: {required or 'unsupported repair'}",), candidate)
    return Decision(result.route, (), candidate)


def apply_result(result: ReportResult, question: QuestionSnapshot, taxonomy: dict,
                 **validation_options) -> QuestionSnapshot:
    """Return a new snapshot only for a validated automatic operation.

    This function performs no storage writes. Phase 3 persistence must commit
    against the same version atomically with its audit entry.
    """
    decision = validate_result(result, question, taxonomy, **validation_options)
    if decision.route == "no_change":
        return question
    _fail(decision.route != "auto_apply", "Proposal requires faculty review")
    return decision.candidate
