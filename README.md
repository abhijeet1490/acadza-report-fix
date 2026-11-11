# Acadza Report → Fix

[Open the live faculty console](https://acadza-report-fix-acwx.onrender.com/)

The submission uses precomputed analyses. All defect reports await faculty review.
The public Render demo shares review state, and its free-instance SQLite history
can reset on restart or idle spin-down.

The release includes [PRODUCT.md](PRODUCT.md), [PROMPTS.md](PROMPTS.md), scored
[results.json](results.json), a [three-minute recording](release/walkthrough.mp4)
with [transcript](release/walkthrough.md), and an [email draft](SUBMISSION.md).
After the final files are pushed, the walkthrough player is served at
[the walkthrough URL](https://acadza-report-fix-acwx.onrender.com/walkthrough).

Phases 1–5 provide dataset ingestion, proposal policy, resumable batch triage and a persistent faculty console for all 39 reported questions. Phase 6 adds submission materials, a captioned walkthrough and release verification. See [RELEASE.md](RELEASE.md) for the remaining publication steps.

Requires Python 3.10 or newer. Uses only the standard library.

```sh
python3 -m acadza
python3 -m unittest discover -s tests -v
python3 -m acadza --preview source-preview.html
```

Open the generated HTML file in a browser to inspect one source question and solution per type, including native MathML. Each source fragment uses a sandboxed iframe with scripts and remote resources disabled. The preview is for local inspection. Console browser checks and release evidence are recorded in RELEASE.md.

Use `--data /absolute/path/to/data` to inspect another dataset. Import `load_dataset` from `acadza.dataset` for the same read path.

## Contracts and compatibility

- Source files under `data/` are byte-for-byte copies of the assessment inputs.
- `QuestionSnapshot` retains the complete production record as immutable JSON and returns detached copies, including unknown metadata. Its SHA-256 version covers the full record with canonical key ordering. This is a content version, independent of timestamp freshness.
- Stored question HTML, MathML, option text and answer representations are preserved. Malformed answer content is evidence for future triage. SCQ and integer answers remain strings, MCQ answers remain string lists.
- `Report` preserves each original report, including unknown fields. `Case` groups reports by question and orders them by report ID.
- `ProposedEdit` identifies its question and source version. `ReportResult.to_dict()` exports the assessment field names. These are proposal contracts. Validate and apply them through the policy service described below.
- Missing identities, duplicate identities, unknown report references and unsupported payload shapes produce explicit ingestion errors.
- The taxonomy is loaded from the supplied file. The policy service enforces its fields and automatic-application eligibility.

There are 43 questions, 44 reports and 39 reported-question cases. Four questions have no report and remain available in the dataset.

The console imports the complete precomputed batch while retaining existing review decisions. Phase 4 provenance and Phase 5 workflow details are below.

## Proposal policy

Use `validate_result(result, question, taxonomy)` from `acadza.policy` to obtain a
`Decision` containing an effective route, reasons and a detached candidate snapshot.
Invalid source references, ambiguous matches, unknown taxonomy keys and prohibited
fields raise `PolicyError`. Callers must use the returned route.

Non-defects require `no_change` and no repair or edit. Their application returns
the original snapshot. Review-only repairs return `faculty_review` and cannot
carry edits because the taxonomy gives them no writable fields.

Eligible repairs require a proposed edit. Text replacements match exactly once.
An empty find is supported only when filling an empty field. Answer replacements
compare the complete original answer, including malformed MCQ lists. Changed
answers must use canonical letters or an integer string appropriate to the
resulting type.

Deterministic guards protect the stem during option repair, markup/math/values
during spelling changes, additive answer-form changes and removal-only cleanup.
Meaning still needs a checking service. Each eligible repair names a semantic
check in `REQUIRED_CHECKS`, such as `independent_solve` or
`solution_states_data`. Without a matching `Verification`, validation routes
the proposal to faculty. Confidence alone grants no application authority.

A verification includes the exact source version, `proposal_digest(edits)`,
check name and evidence. These records are a trusted service input. They must
never be accepted directly from a student or treated as established merely
because an LLM emits the check name. Live blind solving is available in Phase 4. A trusted semantic verification service remains future work.
This phase validates the records' binding, not the truth of their evidence.

For coupled fixes, pass `companion_edits` to validate/apply. Every edit uses the
same original source version and a distinct allowed field. A type change moves
the entire active payload to the new type, preserves metadata/images and checks
the final answer representation. A pre-existing destination payload is rejected.
The result JSON exporter retains its existing single-edit format. Internal
companion edits must be retained with the case when persistence is added.

`apply_result` returns a new snapshot only for an effective `auto_apply`, or
the original for `no_change`. Faculty proposals raise `PolicyError`.
There are no storage writes. The review service checks the
version again and commits the accepted edit and audit entry together. Replaying
the original proposal against an already changed snapshot fails as stale.

Verification:

```sh
python3 -m unittest discover -s tests -v
```

## Local faculty console — Phase 3

```sh
python3 -m acadza.server
```

Open http://127.0.0.1:8765. Stop with Ctrl-C. Review state is stored in the ignored
`.local/review.sqlite3` file and survives restarts. Choose another `--db` path
for an independent review workspace, and `--port` if 8765 is occupied.

The original Phase 3 slice used R001–R003 grouped into one question. The precomputed analysis
uses the series-LCR voltage relation: sqrt(40² + (60 − 30)²) = 50 V, option A.
The source solution agrees, while the source key points to C, 130 V.
This is a prepared case for the faculty workflow. It has no provider calls.

The console renders the question and native MathML solution in sandboxed frames,
shows the precise key diff, evidence, all three reports and reply previews.
Approve applies the proposal. Edit previews another answer, requires a faculty
reason and revalidates on save. Reject preserves the question and records a
decision. Recorded replies remain local and are committed with the audit entry.

SQLite transactions own question updates, case revisions, audit entries and
idempotency receipts. Duplicate requests return the original receipt. A new
decision against an old revision or source version returns HTTP 409. Concurrent
reviewers cannot overwrite the first decision. A failed audit insert rolls back
the whole transaction. Original assessment JSON files remain unchanged.

The frontend retains the exact request receipt in session storage for retries
after a lost acknowledgement. Reloading a case preserves review-note drafts
and invalidates an outstanding edit preview. A conflict asks faculty to reload.

This stage uses loopback access and the actor label “Local faculty”. Identity
and shared public hosting require deployment work. Host validation and a
same-origin request token protect the local HTTP mutation endpoints.
The local default binds to 127.0.0.1. Render deployment settings are in RENDER.md.

### Verification evidence

- The Phase 3 qualification used 26 tests covering ingestion, proposal policy, durable approvals/rejections,
  edit validation, duplicate receipts, two concurrent reviewers, audit rollback,
  and HTTP request guards and conflicts.
- Browser verification exercised edit preview and cancel, rendered the stored
  MathML solution, approved the case, reloaded the saved decision, and blocked a
  stale rejection in a second tab while preserving its note.
- Browser actions used an independent SQLite QA file. The default workspace
  remains pending for your review.
- The HTTP integration test opens a temporary loopback listener. Environments
  restricting socket binds need permission to run that test.

## Batch triage — Phase 4

Run the reproducible reference mode:

```sh
python3 -m acadza.triage
```

It processes all 39 reported questions and exports one result per report to
`results.json`. `triage-run.json` records source versions, provenance, the
independent-solve stage response, complete edit bundles, policy reasons, candidate
records and unresolved failures. Scored results and faculty review state are
independent. This command never applies edits to source JSON or the review DB.

The checked-in outputs were generated with the **analyst-authored reference
pack**, not a live model. The pack is an explicit precomputed analysis of the
supplied records, keyed to full question versions and exact report records.
It contains no ground-truth labels. Reference replay establishes reproducibility
and structural validity, not unseen-data accuracy or live orchestration quality.
The precomputed solve entries include the analyst's source comparison notes.
Only live mode performs the blind-solve separation described below.

To run the actual model orchestration, set `OPENAI_API_KEY` securely in your
environment and choose an available model explicitly:

```sh
python3 -m acadza.triage --mode live --model YOUR_MODEL_ID \
  --output live-results.json --manifest .local/live-run.json
```

The adapter uses the OpenAI Responses API with bounded output, a 55-second
request timeout and `store: false`. See the
[official API reference](https://developers.openai.com/api/reference/resources/responses/methods/create).
No API credential was configured for this implementation. The adapter was
tested with mocked HTTP responses. Actual provider availability, model support,
quality and spend remain unverified. Live mode makes up to two requests per
reported question when the cache is cold.

The live solver sees the question and options, without the key, solution,
history or reports. Triage then sees that solve, the full record, grouped reports
and taxonomy. Student comments and embedded content are treated as untrusted
evidence. Product instructions live verbatim in `acadza/prompts/` and below
in `PROMPTS.md`.

All output goes through the existing policy. Eligible repairs with no safe
replacement can now remain in faculty review without an invented edit.
For example, R009–R010 need a missing distractor that the source cannot establish.
This extends the earlier policy while keeping automatic application blocked
without a proposal.

This run produces **27 faculty-review and 17 no-change results**. There are
**zero automatic applications**. No model or reference flag can create a trusted
semantic verification. High confidence alone does not authorize a write.
Adding a qualified verification service can use the existing Phase 2 verification
contract later. Current replies describe proposed changes and pending review.

A type migration can require multiple fields. To preserve the existing scored
single-edit shape, the primary `edit` object adds an optional `companion_edits`
array. R021 changes `questionType` and `answer` together. The run manifest
retains the complete bundle and its validated final candidate. This is an output
extension whose compatibility with Acadza's private scorer has not been tested.

### Resuming and failure behavior

Stage responses are cached in ignored `.local/triage.sqlite3`. Cache identities
include the full input, taxonomy, prompts, model/provider fingerprint and policy
source. A second unchanged reference run reuses all 78 stage responses. Changed
reports invalidate triage while allowing the blind solve to be reused.

Unfinished requests remain retryable. Run the command again to reuse validated
completed stages and retry failures. A failed case is recorded as unresolved;
it never receives a guessed `no_change` verdict. An incomplete run exits with
status 1, publishes an incomplete manifest and preserves any prior complete
results file. Check manifest status and `results_digest` when consuming outputs.
Missing live configuration exits with status 2 before model calls.

Atomic replacement prevents partial JSON files. POSIX file locks serialize runs
writing the same results path and release on interruption. The batch command
supports macOS/Linux. Remote requests can execute more than once after an
uncertain acknowledgement; the cache does not claim exactly-once provider calls.

Phase 5 connects the complete batch to the faculty console, as described below.

## Complete review workflow — Phase 5

```sh
python3 -m acadza.server
```

Open http://127.0.0.1:8765. On a fresh workspace the server validates and imports
the complete `triage-run.json` into the review database. All 39 cases and 44
reports are available. Use `--manifest PATH` to seed a workspace from another
complete batch. Malformed, incomplete, source-mismatched or digest-mismatched
imports are rejected before case data is written.

An existing complete review workspace uses its saved analysis snapshots on
restart, so a later failed batch cannot disable the queue. Imported analyses and
prior decisions are retained. Replacing an existing case analysis is not an
implicit consequence of rerunning triage. An explicitly supplied invalid manifest
still produces an error.

The additive SQLite schema creates `case_analysis` and sets schema version 1.
Original Phase 3 question, case and audit rows are retained. Original request
receipts remain replayable, and the previous default-case API still works.

The queue orders pending cases by grading impact, distinct reporters and oldest
report time. Search report IDs, subjects or chapters, and filter awaiting,
reviewed or all cases. Completed initial reviews remain visible, including cases
whose repair still needs authoring or investigation.

Each case shows its verdict and evidence, original stored answer, rendered
question and MathML solution, per-report comments, individual reply previews,
and a field-level before/after diff. Key changes show their option text. Text
repairs render their exact fragments and offer a full corrected-content preview.
Coupled type/answer edits appear and apply as one bundle.

- **Approve change** applies the validated content bundle.
- **Edit proposed change** prefills the proposal, requires a reason and a rendered
  preview, then revalidates against the current version before saving.
- **Refer for further review** records a decision for a defect without a safe
  writable proposal. Its repair remains pending, and content stays intact.
- **Confirm no change** records the faculty finding for non-defects.
- **Reject proposal** records the rejection and optional note.
- Every decision saves exact before/after records, actual applied edits,
  per-report replies and the request receipt in the same transaction.

Per-case drafts survive navigation and the **Reload latest case** control within
the current page session. A conflict preserves the draft, invalidates its preview
on reload and displays it for reference if another reviewer already decided.
A full browser refresh discards unsaved drafts. Request receipts survive a
refresh within the same browser tab to support safe retries after lost responses.

The console is local and displays the explicitly precomputed analysis origin.
It makes no LLM requests while reviewing. Replies are local previews/records,
with delivery integration pending. Render hosting is documented in RENDER.md. Named faculty authentication remains
separate work.

### Phase 5 verification

```sh
python3 -m unittest discover -s tests
node tests/ui_state.test.cjs
node --check acadza/web/app.js
```

The Python suite now has 49 tests, including all 39 cases through their normal
action entry point, legacy schema/receipt compatibility, cross-case receipt
binding, atomic type migrations, source conflicts and audit rollback.
The shipped-JavaScript checks cover proposal prefilling, raw draft preservation
and clearing another case's faculty note.

Browser checks exercised R001–R003, R004, R012, R021, R027, R034 and R036, covering
the disputed key, a faculty edit, coupled type correction, injection text,
unsound-question referral and display issue. The edit preview gate and reviewed
filter were verified. A second tab completed a decision while the first held a
draft; the stale save failed, and the draft remained after loading the new
decision. Native MathML rendering was inspected. Layout was checked at desktop
and 390 px width; the narrow page had no horizontal overflow.

Browser actions used an isolated QA database. Source dataset files, scored
results and the default workspace's decisions were preserved.

## Render deployment

Follow [RENDER.md](RENDER.md) for GitHub-connected deployment, startup commands,
health checks and the free-versus-persistent SQLite storage choice.
The public deployment is a shared demo using precomputed analysis.
