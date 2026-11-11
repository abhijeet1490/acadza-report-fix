"""SQLite-owned, versioned faculty review transactions."""

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

from .contracts import DataError, ProposedEdit, ReportResult, QuestionSnapshot
from .dataset import load_dataset
from .policy import PolicyError, validate_result
from .review_cases import check_items, combined_candidate, import_manifest, primary_item

CASE_ID = "60f744b3f1b9bc7cc7b4a949"
EVIDENCE = (
    "For a series LCR circuit, resistive and net reactive voltages are perpendicular. "
    "V = sqrt(40² + (60 − 30)²) = sqrt(2500) = 50 V. "
    "Option A is 50 V. The stored solution also ends at 50 V, "
    "but the stored key is C (130 V)."
)


class MissingCase(DataError):
    """Requested case is absent from the review workspace."""


class Conflict(ValueError):
    """The reviewed version or request receipt no longer matches."""


def proposal(question, replacement="A"):
    return ReportResult(
        "R001", question.question_id, "defect", "answer_key",
        ProposedEdit(question.question_id, question.version, "answer",
                     question.payload["answer"], replacement),
        "faculty_review", 0.99, EVIDENCE,
        "Thanks for flagging this. The calculation gives 50 V, option A. "
        "The answer-key correction is awaiting faculty review.",
    )


def decision_replies(reports, status, candidate, edits):
    replacement = candidate.payload["answer"]
    replies = {}
    for report in reports:
        if status == "approved":
            if len(edits) == 1 and edits[0]["field"] == "answer":
                if candidate.question_type == "integerQuestion":
                    reply = f"The numerical answer has been updated to {replacement}. Thanks for reporting it."
                elif candidate.question_type == "mcq":
                    reply = "The answer key has been updated to " + ", ".join(replacement) + ". Thanks for reporting it."
                else:
                    reply = f"The answer key has been updated to option {replacement}. Thanks for reporting it."
            else:
                reply = "Faculty applied the reviewed correction. Thanks for helping improve the question."
        elif status == "rejected":
            reply = "Thanks for reporting this. Faculty reviewed the proposed change and did not apply it."
        elif status == "referred":
            reply = "Faculty recorded this concern for further investigation. The content repair remains pending."
        else:
            reply = report["result"]["student_reply"]
        replies[report["reportId"]] = reply
    return replies


class ReviewStore:
    def __init__(self, path, manifest=None):
        self.path = Path(path)
        self.dataset = load_dataset()
        self.case = next(c for c in self.dataset.cases if c.question.question_id == CASE_ID)
        default_manifest = Path(__file__).resolve().parent.parent / "triage-run.json"
        manifest_path = Path(manifest) if manifest is not None else default_manifest
        self.path.parent.mkdir(parents=True, exist_ok=True)
        existing_ids = set()
        if self.path.exists():
            with self.connection() as db:
                available = db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='case_analysis'"
                ).fetchone()
                if available:
                    existing_ids = {row["case_id"] for row in db.execute("SELECT case_id FROM case_analysis")}
        expected_ids = {case.question.question_id for case in self.dataset.cases}
        if manifest is None and expected_ids <= existing_ids:
            # An interrupted new batch must not disable an already imported queue.
            analyses = {}
        elif manifest_path.exists():
            analyses = import_manifest(self.dataset, manifest_path)
        elif manifest is not None:
            raise DataError("Triage run file does not exist")
        else:
            source = self.case.question
            result = proposal(source)
            candidate = validate_result(result, source, self.dataset.taxonomy).candidate
            analyses = {CASE_ID: {
                "question": source.to_record(),
                "reports": [r.to_record() for r in self.case.reports],
                "items": [{"result": result.to_dict(), "edits": [result.edit.to_result_edit()],
                           "candidate": candidate.to_record()}],
                "taxonomy": self.dataset.taxonomy,
                "origin": {"provider": "precomputed-phase3"},
            }}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise DataError("Unsupported review database schema")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS questions (
                    id TEXT PRIMARY KEY, source TEXT NOT NULL, version TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS cases (
                    id TEXT PRIMARY KEY REFERENCES questions(id),
                    source TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
                    status TEXT NOT NULL DEFAULT 'pending', replacement TEXT NOT NULL DEFAULT 'A'
                );
                CREATE TABLE IF NOT EXISTS audit (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT UNIQUE NOT NULL, request_hash TEXT NOT NULL,
                    case_id TEXT NOT NULL REFERENCES cases(id),
                    created_at TEXT NOT NULL, actor TEXT NOT NULL,
                    action TEXT NOT NULL, note TEXT NOT NULL,
                    before_json TEXT NOT NULL, after_json TEXT NOT NULL,
                    response TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS case_analysis (
                    case_id TEXT PRIMARY KEY REFERENCES cases(id),
                    analysis TEXT NOT NULL
                );
            """)
            db.execute("BEGIN IMMEDIATE")
            for question_id, analysis in analyses.items():
                source = QuestionSnapshot.from_record(analysis["question"])
                db.execute("INSERT OR IGNORE INTO questions VALUES (?, ?, ?)",
                           (question_id, source.source_json, source.version))
                db.execute("INSERT OR IGNORE INTO cases(id, source) VALUES (?, ?)",
                           (question_id, source.source_json))
                original = db.execute("SELECT source FROM cases WHERE id=?", (question_id,)).fetchone()
                if original["source"] != source.source_json:
                    raise Conflict("Existing review source differs from the imported batch")
                # Import once. Restarts never replace reviewed evidence or decisions.
                db.execute("INSERT OR IGNORE INTO case_analysis VALUES (?, ?)",
                           (question_id, json.dumps(analysis)))
            db.execute("PRAGMA user_version = 1")

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _view(self, db, case_id=CASE_ID):
        if not isinstance(case_id, str) or not case_id:
            raise PolicyError("Invalid case identity")
        row = db.execute(
            "SELECT c.*, q.source AS current_source, q.version AS current_version, a.analysis "
            "FROM cases c JOIN questions q ON q.id=c.id "
            "JOIN case_analysis a ON a.case_id=c.id WHERE c.id=?", (case_id,)
        ).fetchone()
        if row is None:
            raise MissingCase("Case not found")
        source = QuestionSnapshot.from_record(json.loads(row["source"]))
        analysis = json.loads(row["analysis"])
        items = check_items(source, analysis["reports"], analysis["items"], analysis["taxonomy"])
        history = []
        latest = None
        for entry in db.execute("SELECT * FROM audit WHERE case_id=? ORDER BY sequence DESC", (case_id,)):
            receipt = json.loads(entry["response"])
            if latest is None:
                latest = receipt
            history.append({
                "sequence": entry["sequence"], "created_at": entry["created_at"],
                "actor": entry["actor"], "action": entry["action"], "note": entry["note"],
                "before": json.loads(entry["before_json"]), "after": json.loads(entry["after_json"]),
                "edits": receipt.get("edits", []),
            })
        candidate, edits = combined_candidate(source, items)
        if row["status"] == "approved" and history:
            candidate = QuestionSnapshot.from_record(history[0]["after"])
            # Old Phase 3 receipts omit the surgical bundle.
            edits = (latest or {}).get("edits", [
                {"field": "answer", "find": source.payload["answer"],
                 "replace": candidate.payload["answer"]},
            ])
        primary = primary_item(items)["result"]
        rule = analysis["taxonomy"]["repairs"].get(primary["repair"])
        reports = []
        for report in analysis["reports"]:
            item = next(item for item in items if item["result"]["reportId"] == report["reportId"])
            reply = (latest or {}).get("replies", {}).get(report["reportId"],
                                                        item["result"]["student_reply"])
            reports.append(dict(report, reply=reply, result=item["result"]))
        approval_status = ("approved" if edits else
                           "referred" if primary["verdict"] == "defect" else "confirmed")
        return {
            "approval_status": approval_status,
            "approval_replies": decision_replies(reports, approval_status, candidate, edits),
            "id": case_id, "revision": row["revision"], "status": row["status"],
            "source_version": source.version, "current_version": row["current_version"],
            "question": source.to_record(), "candidate": candidate.to_record(),
            "current": json.loads(row["current_source"]),
            "analysis_origin": ("Precomputed analysis" if analysis["origin"]["provider"].startswith("precomputed")
                                else "Model analysis"),
            "result": primary, "results": [item["result"] for item in items],
            "edits": edits, "editable_edits": primary_item(items)["edits"],
            "editable_fields": rule["fields"] if rule else [],
            "reports": reports, "audit": history,
            "stale": row["status"] == "pending" and row["current_version"] != source.version,
        }

    def view(self, case_id=CASE_ID):
        with self.connection() as db:
            db.execute("BEGIN")
            return self._view(db, case_id)

    def queue(self):
        with self.connection() as db:
            db.execute("BEGIN")
            views = [self._view(db, row["id"]) for row in db.execute("SELECT id FROM cases")]
        priorities = {
            "answer_key": 0, "type_mismatch": 0, "unsound": 0, "identical_options": 0,
            "stem_data": 1, "option_repair": 1, "diagram": 1, "wrong_question": 1,
            "solution_fix": 2, "solution_missing": 2,
        }
        entries = []
        for view in views:
            question = view["question"]
            first = min(r["reportedAt"] for r in view["reports"])
            entries.append({
                "id": view["id"], "status": view["status"], "subject": question["subject"],
                "chapter": question["chapter"], "questionType": question["questionType"],
                "verdict": view["result"]["verdict"], "repair": view["result"]["repair"],
                "reportIds": [r["reportId"] for r in view["reports"]],
                "reporters": len({r["studentId"] for r in view["reports"]}),
                "first_reported": first, "stale": view["stale"],
            })
        entries.sort(key=lambda item: (
            item["status"] != "pending", item["verdict"] != "defect",
            priorities.get(item["repair"], 3), -item["reporters"],
            item["first_reported"], item["id"],
        ))
        return entries

    def _checked_edit(self, view, edits):
        if not view["editable_fields"] or view["result"]["verdict"] != "defect":
            raise PolicyError("This case requires investigation outside content editing")
        if not isinstance(edits, list) or not edits:
            raise PolicyError("Provide at least one surgical edit")
        source = QuestionSnapshot.from_record(view["question"])
        with self.connection() as db:
            analysis = json.loads(db.execute("SELECT analysis FROM case_analysis WHERE case_id=?",
                                            (view["id"],)).fetchone()["analysis"])
        items = check_items(source, analysis["reports"], analysis["items"],
                            analysis["taxonomy"], edits)
        candidate, combined_edits = combined_candidate(source, items)
        return candidate, combined_edits

    def preview(self, replacement):
        # Original Phase 3 API remains available to existing callers.
        if not isinstance(replacement, str):
            raise PolicyError("Choose an answer letter")
        view = self.view()
        candidate, _ = self._checked_edit(view, [
            {"field": "answer", "find": view["question"]["scq"]["answer"], "replace": replacement},
        ])
        return candidate.to_record()

    def preview_edits(self, request):
        case_id = request.get("case_id", CASE_ID)
        view = self.view(case_id)
        self._check_revision(view, request)
        candidate, edits = self._checked_edit(view, request.get("edits"))
        return {"candidate": candidate.to_record(), "edits": edits,
                "revision": view["revision"], "source_version": view["source_version"],
                "approval_replies": decision_replies(view["reports"], "approved", candidate, edits)}

    def _check_revision(self, view, request):
        if (view["status"] != "pending" or type(request.get("revision")) is not int
                or view["revision"] != request["revision"]
                or view["source_version"] != request.get("source_version")
                or view["current_version"] != view["source_version"]):
            raise Conflict("This case changed. Reload before deciding. Your draft is preserved.")

    def act(self, request):
        required = ("request_id", "action", "revision", "source_version")
        if not isinstance(request, dict) or any(k not in request for k in required):
            raise PolicyError("Incomplete review request")
        if not isinstance(request["request_id"], str) or not 1 <= len(request["request_id"]) <= 100:
            raise PolicyError("Invalid request receipt")
        if type(request["revision"]) is not int or not isinstance(request["source_version"], str):
            raise PolicyError("Invalid review version")
        action = request["action"]
        if action not in ("approve", "edit", "reject"):
            raise PolicyError("Unknown review action")
        note = request.get("note", "")
        if not isinstance(note, str) or len(note) > 2000:
            raise PolicyError("Review note must be at most 2000 characters")
        if action == "edit" and not note.strip():
            raise PolicyError("Explain the faculty correction")
        if action != "edit" and "edits" in request:
            raise PolicyError("Edited proposals require an explicit edit action")
        case_id = request.get("case_id", CASE_ID)
        if not isinstance(case_id, str):
            raise PolicyError("Invalid case identity")
        fingerprint = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            receipt = db.execute("SELECT * FROM audit WHERE request_id=?",
                                 (request["request_id"],)).fetchone()
            if receipt:
                if receipt["request_hash"] != fingerprint:
                    raise Conflict("Receipt already used for a different action")
                return json.loads(receipt["response"])
            before = self._view(db, case_id)
            self._check_revision(before, request)
            source = QuestionSnapshot.from_record(before["current"])
            candidate = QuestionSnapshot.from_record(before["candidate"])
            edits = before["edits"]
            if action == "reject":
                after, status = source, "rejected"
            elif action == "edit":
                supplied = request.get("edits")
                if supplied is None and case_id == CASE_ID:
                    supplied = [{"field": "answer", "find": source.payload["answer"],
                                 "replace": request.get("replacement", "A")}]
                candidate, edits = self._checked_edit(before, supplied)
                after, status = candidate, "approved"
            elif edits:
                after, status = candidate, "approved"
            else:
                after = source
                status = "referred" if before["result"]["verdict"] == "defect" else "confirmed"
            if after.version != source.version:
                changed = db.execute(
                    "UPDATE questions SET source=?, version=? WHERE id=? AND version=?",
                    (after.source_json, after.version, case_id, source.version),
                ).rowcount
                if changed != 1:
                    raise Conflict("Question changed before approval")
            replacement = after.payload["answer"]
            legacy_replacement = replacement if isinstance(replacement, str) else json.dumps(replacement)
            db.execute("UPDATE cases SET status=?, replacement=?, revision=revision+1 WHERE id=?",
                       (status, legacy_replacement, case_id))
            replies = decision_replies(before["reports"], status, after, edits)
            response = {
                "case_id": case_id, "replies": replies, "status": status,
                "revision": before["revision"] + 1, "version": after.version,
                "request_id": request["request_id"],
                "edits": edits if status == "approved" else [],
            }
            db.execute(
                "INSERT INTO audit(request_id,request_hash,case_id,created_at,actor,action,note,"
                "before_json,after_json,response) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (request["request_id"], fingerprint, case_id, datetime.now(timezone.utc).isoformat(),
                 "Local faculty", action, note, source.source_json, after.source_json, json.dumps(response)),
            )
            return response
