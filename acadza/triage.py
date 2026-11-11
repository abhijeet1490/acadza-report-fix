"""Resumable question-level investigations and report-level scored results."""

import argparse
from collections import Counter
from contextlib import contextmanager
import fcntl
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile

from .contracts import DataError, ProposedEdit, ReportResult
from .dataset import DEFAULT_DATA, load_dataset
from .policy import PolicyError, validate_result
from .providers import OpenAIProvider, PROMPTS, ProviderError, ReferenceProvider


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        # Exclusive temporary file plus rename prevents partial publication.
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=f".{path.name}.", delete=False) as output:
            temporary = Path(output.name)
            json.dump(value, output, ensure_ascii=False, indent=2, allow_nan=False)
            output.write("\n")
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


@contextmanager
def publication_lock(output):
    # Serialize writers targeting the same scored artifact on this machine.
    # Kernel ownership releases the lock if a run crashes or is interrupted.
    output = Path(output).resolve()
    locks = output.parent / ".local" / "triage-locks"
    locks.mkdir(parents=True, exist_ok=True)
    lock_path = locks / digest(str(output))
    with lock_path.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


class Cache:
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=10)
        self.db.execute("""CREATE TABLE IF NOT EXISTS triage_jobs(
            key TEXT PRIMARY KEY, state TEXT NOT NULL, response TEXT, error TEXT,
            updated TEXT NOT NULL)""")
        self.db.commit()

    def read(self, key):
        row = self.db.execute("SELECT response FROM triage_jobs WHERE key=? AND state='complete'",
                              (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def write(self, key, state, response=None, error=None):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO triage_jobs VALUES (?,?,?,?,?)",
                            (key, state, json.dumps(response), error,
                             datetime.now(timezone.utc).isoformat()))

    def close(self):
        self.db.close()


def validate_solve(value):
    if not isinstance(value, dict) or set(value) != {"answer", "reasoning", "limitations"}:
        raise PolicyError("Solver response has an invalid shape")
    if not all(isinstance(value[key], str) and value[key].strip()
               for key in ("reasoning", "limitations")):
        raise PolicyError("Solver must provide reasoning and limitations")
    if value["answer"] is not None and not isinstance(value["answer"], (str, list)):
        raise PolicyError("Solver answer has an invalid representation")


def validate_reports(value, case, taxonomy):
    if not isinstance(value, dict) or set(value) != {"reports"} or not isinstance(value["reports"], list):
        raise PolicyError("Triage response must contain a reports list")
    by_id = {r.report_id: r for r in case.reports}
    seen = set()
    validated = []
    allowed = {"reportId", "questionId", "verdict", "repair", "edits", "route",
               "confidence", "evidence", "student_reply"}
    for row in value["reports"]:
        if not isinstance(row, dict) or set(row) != allowed:
            raise PolicyError("Report result fields do not match the contract")
        report_id = row["reportId"]
        if not isinstance(report_id, str) or report_id not in by_id or report_id in seen:
            raise PolicyError("Duplicate or unexpected report ID")
        seen.add(report_id)
        if not isinstance(row["edits"], list):
            raise PolicyError("Edits must be a list")
        edits = []
        for item in row["edits"]:
            if not isinstance(item, dict) or set(item) != {"field", "find", "replace"}:
                raise PolicyError("Edit fields do not match the contract")
            if item["field"] not in ("question", "solution", "answer", "questionType"):
                raise PolicyError("Unknown edit field")
            edits.append(ProposedEdit(case.question.question_id, case.question.version, **item))
        for key in ("questionId", "verdict", "route"):
            if not isinstance(row[key], str):
                raise PolicyError(f"{key} must be a string")
        if row["repair"] is not None and not isinstance(row["repair"], str):
            raise PolicyError("Repair must be a string or null")
        result = ReportResult(
            report_id, row["questionId"], row["verdict"], row["repair"],
            edits[0] if edits else None, row["route"], row["confidence"],
            row["evidence"], row["student_reply"],
        )
        # No model output or replayed claim can mint a trusted verification.
        decision = validate_result(result, case.question, taxonomy,
                                   report=by_id[report_id], companion_edits=tuple(edits[1:]))
        result = replace(result, route=decision.route)
        exported = result.to_dict()
        if len(edits) > 1:
            exported["edit"]["companion_edits"] = [e.to_result_edit() for e in edits[1:]]
        validated.append({
            "result": exported,
            "edits": [e.to_result_edit() for e in edits],
            "route_reasons": list(decision.reasons),
            "candidate": decision.candidate.to_record(),
        })
    if seen != set(by_id):
        raise PolicyError("Triage omitted one or more reports")
    # One case cannot publish competing edits to the same field.
    proposed = {}
    for item in validated:
        for edit in item["edits"]:
            field = edit["field"]
            if field in proposed and proposed[field] != edit:
                raise PolicyError("Reports propose conflicting changes to one question")
            proposed[field] = edit
    return sorted(validated, key=lambda item: item["result"]["reportId"])


def run(dataset, provider, cache, output, manifest):
    with publication_lock(output):
        return _run(dataset, provider, cache, output, manifest)


def _run(dataset, provider, cache, output, manifest):
    prompt_version = digest({path.name: path.read_text() for path in sorted(PROMPTS.glob("*.txt"))})
    policy_version = digest({
        name: (Path(__file__).parent / name).read_text()
        for name in ("policy.py", "contracts.py", "providers.py", "triage.py")
    })
    cases = []
    failures = []
    hits = 0
    for case in dataset.cases:
        question = case.question
        # Strip key, stored solution, history and reports from independent solving.
        solve_input = {
            "question_id": question.question_id, "source_version": question.version,
            "question_type": question.question_type,
            "question": question.payload["question"],
            "quesImages": question.payload.get("quesImages", []),
        }
        stage_key = None
        try:
            stage_responses = {}
            for stage in ("solve", "triage"):
                payload = solve_input if stage == "solve" else {
                    "question_id": question.question_id, "source_version": question.version,
                    "record": question.to_record(), "independent_solve": stage_responses["solve"],
                    "reports": [r.to_record() for r in case.reports],
                    "taxonomy": dataset.taxonomy,
                }
                stage_key = digest({
                    "stage": stage, "payload": payload, "provider": provider.fingerprint,
                    "prompts": prompt_version, "policy": policy_version,
                })
                response = cache.read(stage_key)
                if response is not None:
                    hits += 1
                else:
                    response = provider.call(stage, payload)
                if stage == "solve":
                    validate_solve(response)
                else:
                    validated = validate_reports(response, case, dataset.taxonomy)
                cache.write(stage_key, "complete", response)
                stage_responses[stage] = response
            cases.append({
                "questionId": question.question_id, "source_version": question.version,
                "origin": provider.fingerprint, "solve": stage_responses["solve"],
                "reports": validated,
            })
        except (ProviderError, DataError, ValueError, TypeError, KeyError) as exc:
            # Failed jobs remain retryable, never become guessed no_change results.
            error = type(exc).__name__ + ": " + str(exc)
            if stage_key:
                cache.write(stage_key, "failed", error=error)
            failures.append({"questionId": question.question_id,
                             "reportIds": [r.report_id for r in case.reports],
                             "status": "unresolved", "error": error})
    results = sorted((r["result"] for case in cases for r in case["reports"]),
                     key=lambda row: row["reportId"])
    expected = {r.report_id for r in dataset.reports}
    complete = not failures and {r["reportId"] for r in results} == expected
    report = {
        "origin_note": (
            "Analyst-authored reference replay; no live provider calls or ground-truth accuracy measurement."
            if provider.fingerprint.get("provider") == "precomputed-reference" else
            "Live model output; validated structurally and routed through policy."
        ),
        "status": "complete" if complete else "incomplete",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "provider": provider.fingerprint, "prompt_version": prompt_version,
        "policy_version": policy_version, "taxonomy_version": digest(dataset.taxonomy),
        "report_count": len(results), "case_count": len(cases), "cache_hits": hits,
        "routes": dict(Counter(row["route"] for row in results)),
        "failures": failures, "cases": cases,
        "applied_to_source": False,
        "results_digest": digest(results) if complete else None,
    }
    # Preserve any last complete scored output when this run is incomplete.
    if complete:
        atomic_json(output, results)
    atomic_json(manifest, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("reference", "live"), default="reference")
    parser.add_argument("--model")
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--cache", type=Path, default=Path(".local/triage.sqlite3"))
    parser.add_argument("--output", type=Path, default=Path("results.json"))
    parser.add_argument("--manifest", type=Path, default=Path("triage-run.json"))
    args = parser.parse_args()
    destinations = [p.resolve() for p in (args.output, args.manifest, args.cache)]
    sources = [(args.data / name).resolve()
               for name in ("questions.json", "reports.json", "taxonomy.json")]
    if len(set(destinations)) != len(destinations) or set(destinations) & set(sources):
        parser.error("Output, manifest and cache must be distinct and preserve source files")
    try:
        provider = ReferenceProvider() if args.mode == "reference" else OpenAIProvider(args.model)
        cache = Cache(args.cache)
        try:
            report = run(load_dataset(args.data), provider, cache, args.output, args.manifest)
        finally:
            cache.close()
    except (ProviderError, DataError) as exc:
        parser.exit(2, str(exc) + "\n")
    print(json.dumps({key: report[key] for key in
                      ("status", "report_count", "case_count", "cache_hits", "routes", "failures")},
                     indent=2))
    if report["status"] != "complete":
        parser.exit(1)


if __name__ == "__main__":
    main()
