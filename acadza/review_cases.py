"""Import batch evidence and reuse policy checks for faculty edit bundles."""

import json
from pathlib import Path

from .contracts import Case, DataError, QuestionSnapshot, Report
from .triage import digest, validate_reports


def primary_item(items):
    return next((item for item in items if item["result"]["verdict"] == "defect"), items[0])


def check_items(source, reports, items, taxonomy, override=None):
    primary = primary_item(items)
    rows = []
    for item in items:
        row = {key: value for key, value in item["result"].items() if key != "edit"}
        row["edits"] = item["edits"]
        if override is not None and row["repair"] == primary["result"]["repair"]:
            row["edits"] = override
        rows.append(row)
    case = Case(source, tuple(Report.from_record(r) for r in reports))
    return validate_reports({"reports": rows}, case, taxonomy)


def combined_candidate(source, items):
    """Merge nonconflicting, individually validated field changes."""
    record = source.to_record()
    payload = record[source.question_type]
    target_type = source.question_type
    edits = {}
    for item in items:
        candidate = QuestionSnapshot.from_record(item["candidate"])
        for edit in item["edits"]:
            field = edit["field"]
            if field in edits and edits[field] != edit:
                raise DataError("Conflicting case edits")
            edits[field] = edit
            if field == "questionType":
                target_type = candidate.question_type
            else:
                payload[field] = candidate.payload[field]
    if target_type != source.question_type:
        if target_type in record:
            raise DataError("Destination type payload already exists")
        record[target_type] = record.pop(source.question_type)
        record["questionType"] = target_type
    return QuestionSnapshot.from_record(record), list(edits.values())


def import_manifest(dataset, path):
    try:
        return _import_manifest(dataset, path)
    except (OSError, json.JSONDecodeError, KeyError, TypeError, IndexError):
        raise DataError("Invalid triage run file. Regenerate the batch output.") from None


def _import_manifest(dataset, path):
    manifest = json.loads(Path(path).read_text())
    if manifest.get("status") != "complete" or manifest.get("failures"):
        raise DataError("A complete triage run is required. Run python3 -m acadza.triage.")
    if manifest.get("taxonomy_version") != digest(dataset.taxonomy):
        raise DataError("Triage taxonomy changed. Regenerate the batch output.")
    cases_by_id = {case.question.question_id: case for case in dataset.cases}
    imported = {}
    results = []
    for entry in manifest["cases"]:
        question_id = entry["questionId"]
        if question_id not in cases_by_id or question_id in imported:
            raise DataError("Unexpected or duplicate case in triage run")
        origin = entry["origin"]
        if not isinstance(origin, dict) or not isinstance(origin.get("provider"), str):
            raise DataError("Invalid triage origin")
        case = cases_by_id[question_id]
        if entry["source_version"] != case.question.version:
            raise DataError("Triage source changed. Regenerate the batch output.")
        reports = [report.to_record() for report in case.reports]
        items = check_items(case.question, reports, entry["reports"], dataset.taxonomy)
        combined_candidate(case.question, items)
        results.extend(item["result"] for item in items)
        imported[question_id] = {
            "question": case.question.to_record(), "reports": reports, "items": items,
            "taxonomy": dataset.taxonomy, "origin": entry["origin"],
        }
    if set(imported) != set(cases_by_id):
        raise DataError("Triage run does not cover every reported question")
    if digest(sorted(results, key=lambda row: row["reportId"])) != manifest["results_digest"]:
        raise DataError("Triage results digest does not match the validated output")
    return imported
