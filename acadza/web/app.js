const $ = id => document.getElementById(id);
const names = {
  defect: "Defect", no_error: "No error found", display_issue: "Display issue",
  app_issue: "App issue", language_mode: "Language request", already_fixed: "Already fixed",
  not_actionable: "Needs a clearer report", answer_key: "Answer key", type_mismatch: "Question type",
  spelling: "Spelling", noise: "Stray text", exam_reference: "Exam reference",
  markup_leak: "Formatting", answer_form: "Answer form", notation: "Notation",
  stem_data: "Missing data", option_repair: "Missing option", solution_fix: "Solution correction",
  solution_missing: "Missing solution", identical_options: "Repeated options", diagram: "Missing diagram",
  wrong_question: "Mismatched content", unsound: "Unsound question", wrong_chapter: "Chapter",
  syllabus: "Syllabus review", scq: "Single correct", mcq: "Multiple correct",
  integerQuestion: "Integer answer", question: "Question", solution: "Solution",
  answer: "Answer key", questionType: "Question type", pending: "Awaiting review",
  approved: "Correction applied", rejected: "Rejected", referred: "Further review",
  confirmed: "Confirmed"
};
let queue = [];
let review;
let token;
let selected;
let generation = 0;
let controller;
let busy = false;
let previewHash;
let shownReplies = {};
const drafts = new Map();

function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}
function notice(message, error = false) {
  $("notice").textContent = message;
  $("notice").className = error ? "error" : "";
}
function payload(record) {
  return record[record.questionType];
}
function readable(fragment) {
  const document = new DOMParser().parseFromString(fragment, "text/html");
  function text(element) {
    if (element.nodeType === 3) return element.textContent;
    const parts = [...element.childNodes].map(text);
    if (element.localName === "mfrac") return "(" + parts[0] + ")/(" + parts[1] + ")";
    if (element.localName === "msqrt") return "√(" + parts.join("") + ")";
    if (element.localName === "msup") return parts[0] + "^(" + parts[1] + ")";
    if (element.localName === "msub") return parts[0] + "_" + parts[1];
    if (element.localName === "br") return "\n";
    return parts.join("") + (["h3", "p"].includes(element.localName) ? "\n" : "");
  }
  return text(document.body).replace(/[ \t]+/g, " ").trim();
}
function optionText(record, answer) {
  const question = readable(payload(record).question);
  const letters = Array.isArray(answer) ? answer : [answer];
  if (!letters.every(letter => /^[ABCD]$/.test(letter))) return "";
  return letters.map(letter => {
    const match = question.match(new RegExp("\\(" + letter + "\\)([\\s\\S]*?)(?=\\([ABCD]\\)|$)"));
    return match ? letter + " · " + match[1].trim() : letter;
  }).join(" · ");
}
function frame(element, fragment, compact = false) {
  element.srcdoc = '<!doctype html><meta charset="utf-8">' +
    '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'">' +
    '<style>body{font:15px/1.7 system-ui;color:#263c32;margin:0;padding:4px;overflow-wrap:anywhere}' +
    'h3{font-size:15px;font-weight:400;margin:8px 0}math{font-size:18px}' +
    'img{max-width:100%}</style>' + fragment;
  const size = readable(fragment).length;
  element.style.height = Math.min(600, Math.max(compact ? 100 : 220, size / 2.2)) + "px";
}
function valueText(value) {
  if (Array.isArray(value)) return JSON.stringify(value);
  return names[value] || String(value);
}
function displayDiff(candidate, edits) {
  $("diffs").replaceChildren();
  $("no-change").hidden = edits.length > 0;
  if (!edits.length) {
    $("no-change").textContent = review.result.verdict === "defect" ?
      "No safe content edit is proposed. Record a referral for further investigation or authoring." :
      "The analysis proposes no content change. Confirm the finding or reject it for further investigation.";
  }
  for (const edit of edits) {
    const section = node("section", undefined, "diff");
    section.append(node("h3", names[edit.field]));
    const pair = node("div", undefined, "diff-pair");
    for (const side of ["before", "after"]) {
      const box = node("div", undefined, side);
      box.append(node("span", side === "before" ? "BEFORE" :
        review.status === "approved" ? "APPLIED" : "PROPOSED", "diff-label"));
      const value = side === "before" ? edit.find : edit.replace;
      if (["question", "solution"].includes(edit.field)) {
        const iframe = document.createElement("iframe");
        iframe.title = names[edit.field] + (side === "before" ? " before" : " after");
        iframe.setAttribute("sandbox", "");
        frame(iframe, value, true);
        box.append(iframe);
      } else {
        box.append(node("div", valueText(value), "diff-value"));
        if (edit.field === "answer") {
          const option = optionText(side === "before" ? review.question : candidate, value);
          if (option) box.append(node("p", option, "diff-option"));
        }
      }
      pair.append(box);
      if (side === "before") pair.append(node("span", "→", "arrow"));
    }
    section.append(pair);
    $("diffs").append(section);
  }
  const old = payload(review.question);
  const after = payload(candidate);
  $("full-preview").hidden = old.question === after.question;
  $("solution-preview").hidden = old.solution === after.solution;
  if (!$("full-preview").hidden) frame($("candidate-question"), after.question);
  if (!$("solution-preview").hidden) frame($("candidate-solution"), after.solution);
}
function renderQueue() {
  const search = $("search").value.toLowerCase().trim();
  const filter = $("queue-filter").value;
  const filtered = queue.filter(item => {
    const matchesStatus = filter === "all" ||
      (filter === "pending" ? item.status === "pending" : item.status !== "pending");
    const words = [...item.reportIds, item.subject, item.chapter,
      names[item.repair] || names[item.verdict]].join(" ").toLowerCase();
    return matchesStatus && words.includes(search);
  });
  $("queue-count").textContent = String(filtered.length);
  $("queue-list").replaceChildren();
  for (const item of filtered) {
    const button = node("button", undefined, "case");
    button.classList.toggle("selected", item.id === selected);
    button.setAttribute("aria-current", item.id === selected ? "true" : "false");
    button.disabled = busy;
    const label = item.reportIds.join(", ") + " · " + (names[item.repair] || names[item.verdict]);
    button.append(node("span", label, "case-label"), node("strong", item.chapter),
      node("span", item.subject + " · " + item.reporters + (item.reporters === 1 ? " reporter" : " reporters")),
      node("span", item.stale ? "Source changed · reload required" : names[item.status]));
    button.onclick = () => loadCase(item.id);
    $("queue-list").append(button);
  }
  if (!filtered.length) $("queue-list").textContent = "No cases match this view.";
  const pending = queue.filter(item => item.status === "pending").length;
  const reports = queue.reduce((count, item) => count + item.reportIds.length, 0);
  $("batch-summary").textContent = queue.length + " questions · " + reports + " reports · " + pending + " awaiting review";
}
function renderReply() {
  $("reply").textContent = shownReplies[$("reply-report").value] || "";
}
function showEditor(saved) {
  $("editor").hidden = !saved.editing;
  $("edit-fields").replaceChildren();
  $("edit-note").value = saved.note;
  if (!saved.editing) return;
  for (const [index, edit] of saved.edits.entries()) {
    const row = node("div", undefined, "edit-row");
    const fieldId = "field-" + index;
    const fieldLabel = node("label", "Field");
    fieldLabel.htmlFor = fieldId;
    const select = document.createElement("select");
    select.id = fieldId;
    select.className = "edit-field";
    for (const field of review.editable_fields) {
      const option = node("option", names[field]);
      option.value = field;
      select.append(option);
    }
    select.value = edit.field;
    row.append(fieldLabel, select);
    for (const key of ["find", "replace"]) {
      const id = key + "-" + index;
      const label = node("label", key === "find" ? "Existing fragment or value" : "Replacement");
      label.htmlFor = id;
      const input = document.createElement("textarea");
      input.id = id;
      input.className = "edit-" + key;
      input.rows = 3;
      input.value = Array.isArray(edit[key]) ? JSON.stringify(edit[key]) : edit[key];
      row.append(label, input);
    }
    row.oninput = () => {
      previewHash = null;
      $("preview-state").textContent = "Preview required before applying.";
    };
    row.onchange = row.oninput;
    $("edit-fields").append(row);
  }
  $("preview-state").textContent = review.status === "pending" ?
    "Preview required before applying." : "This case was decided. Your unsaved draft is retained here.";
}
function collectEdits() {
  return [...$("edit-fields").children].map(row => {
    const field = row.querySelector(".edit-field").value;
    function value(key) {
      const input = row.querySelector(".edit-" + key).value;
      return field === "answer" && input.trim().startsWith("[") ? JSON.parse(input) : input;
    }
    return {field, find: value("find"), replace: value("replace")};
  });
}
function saveDraft() {
  if (!review) return;
  const previous = drafts.get(review.id);
  const editing = !$("editor").hidden;
  const edits = editing ? [...$("edit-fields").children].map(row => ({
    field: row.querySelector(".edit-field").value,
    find: row.querySelector(".edit-find").value,
    replace: row.querySelector(".edit-replace").value
  })) : previous?.edits || review.editable_edits;
  drafts.set(review.id, {
    editing, edits, note: $("edit-note").value,
    rejecting: !$("reject-editor").hidden, rejectNote: $("reject-note").value
  });
}
function render() {
  const pending = review.status === "pending";
  const saved = drafts.get(review.id) || {
    editing: false, edits: review.editable_edits, note: "", rejecting: false, rejectNote: ""
  };
  previewHash = null;
  $("subject").textContent = review.question.subject + " / " + review.question.chapter;
  $("case-title").textContent = names[review.result.repair] || names[review.result.verdict];
  $("case-meta").textContent = review.reports.map(report => report.reportId).join(" · ") +
    " · " + new Set(review.reports.map(report => report.studentId)).size + (new Set(review.reports.map(report => report.studentId)).size === 1 ? " distinct reporter" : " distinct reporters");
  $("status").textContent = names[review.status];
  $("status").classList.toggle("done", !pending);
  $("verdict").textContent = names[review.result.verdict];
  $("origin").textContent = review.analysis_origin;
  $("evidence").textContent = review.result.evidence;
  $("other-evidence").replaceChildren();
  for (const result of review.results) {
    if (result.evidence === review.result.evidence) continue;
    $("other-evidence").append(node("p", result.reportId + " · " + result.evidence));
  }
  $("change-title").textContent = review.edits.length ? "Proposed content change" : "Content decision";
  $("change-state").textContent = pending ? "Awaiting faculty decision" : names[review.status];
  displayDiff(review.candidate, review.edits);
  frame($("question"), payload(review.question).question);
  frame($("solution"), payload(review.question).solution);
  $("question-type").textContent = names[review.question.questionType];
  $("stored-answer").textContent = "Stored answer as reported · " + valueText(payload(review.question).answer);
  $("action-title").textContent = pending ? "Review the finding" : "Decision recorded";
  $("action-help").textContent = review.stale ? "The live record changed. Reload and reconcile before applying." :
    review.approval_status === "approved" ? "Applies the exact changes shown and records the decision." :
    review.approval_status === "referred" ? "Records a referral. The content repair will remain pending." :
    "Confirms the finding and keeps the stored content.";
  $("approve").textContent = saved.editing ? "Save & approve correction" :
    {approved: "Approve change", referred: "Refer for further review", confirmed: "Confirm no change"}[review.approval_status];
  $("edit").textContent = saved.editing ? "Cancel edit" : review.edits.length ? "Edit proposed change" : "Create a repair";
  $("approve").hidden = !pending;
  $("edit").hidden = !pending || !review.editable_fields.length;
  $("reject").hidden = !pending;
  $("reject-editor").hidden = !saved.rejecting || !pending;
  $("reject-note").value = saved.rejectNote;
  showEditor(saved);
  $("preview").disabled = !pending || busy;
  $("reports").replaceChildren();
  $("report-count").textContent = String(review.reports.length);
  $("reply-report").replaceChildren();
  shownReplies = pending ? review.approval_replies :
    Object.fromEntries(review.reports.map(report => [report.reportId, report.reply]));
  $("reply-label").textContent = pending ?
    {approved: "After approval", referred: "After referral", confirmed: "After confirmation"}[review.approval_status] :
    "Saved with decision";
  for (const report of review.reports) {
    const entry = node("div", undefined, "report");
    entry.append(node("strong", report.reportId),
      node("small", report.source + " · " + new Date(report.reportedAt).toLocaleDateString("en-IN", {timeZone: "Asia/Kolkata"})),
      node("p", report.comment), node("small", names[report.result.verdict]));
    $("reports").append(entry);
    const option = node("option", report.reportId);
    option.value = report.reportId;
    $("reply-report").append(option);
  }
  renderReply();
  $("audit-count").textContent = String(review.audit.length);
  $("audit").replaceChildren();
  for (const entry of review.audit) {
    const row = node("div", undefined, "audit-entry");
    const verb = {approve: "Approved finding", edit: "Edited and approved", reject: "Rejected"}[entry.action];
    row.append(node("strong", verb), node("small", entry.actor + " · " +
      new Date(entry.created_at).toLocaleString("en-IN", {timeZone: "Asia/Kolkata"})));
    const changed = entry.edits.length ? entry.edits.map(edit => names[edit.field]).join(", ") :
      JSON.stringify(entry.before) === JSON.stringify(entry.after) ? "Content unchanged" : "Answer key";
    row.append(node("p", changed), node("p", entry.note));
    if (entry.edits.length) {
      const details = document.createElement("details");
      details.append(node("summary", "Exact recorded edits"),
        node("pre", JSON.stringify(entry.edits, null, 2)));
      row.append(details);
    }
    $("audit").append(row);
  }
  if (!review.audit.length) $("audit").textContent = "No decision recorded yet.";
  $("case-view").hidden = false;
  renderQueue();
}
async function api(path, body, signal) {
  const response = await fetch(path, {
    method: body ? "POST" : "GET", signal,
    headers: body ? {"Content-Type": "application/json", "X-Review-Token": token} : {},
    body: body ? JSON.stringify(body) : undefined
  });
  const data = await response.json();
  if (!response.ok) {
    const error = new Error(data.error || "Request failed");
    error.status = response.status;
    throw error;
  }
  return data;
}
async function refreshQueue(context) {
  const data = await api("/api/cases");
  if (context !== undefined && context !== generation) return;
  queue = data.cases;
  token = data.token;
  renderQueue();
}
async function loadCase(id) {
  if (busy) return;
  saveDraft();
  selected = id;
  const current = ++generation;
  if (controller) controller.abort();
  controller = new AbortController();
  $("loading").hidden = false;
  $("case-view").hidden = true;
  notice("");
  renderQueue();
  try {
    const data = await api("/api/case?id=" + encodeURIComponent(id), undefined, controller.signal);
    if (current !== generation) return;
    await refreshQueue(current);
    if (current !== generation) return;
    review = data.case;
    token = data.token;
    history.replaceState(null, "", "#" + id);
    render();
  } catch (error) {
    if (error.name !== "AbortError" && current === generation) notice(error.message, true);
  } finally {
    if (current === generation) $("loading").hidden = true;
  }
}
function lock(value) {
  busy = value;
  for (const id of ["approve", "edit", "reject", "confirm-reject", "preview", "reload"]) {
    $(id).disabled = value;
  }
  for (const element of $("edit-fields").querySelectorAll("select,textarea")) element.disabled = value;
  $("search").disabled = value;
  $("queue-filter").disabled = value;
  $("preview").disabled = value || review?.status !== "pending";
  renderQueue();
}
async function act(action) {
  if (busy || !review) return;
  const editing = !$("editor").hidden;
  let edits;
  try { edits = editing ? collectEdits() : undefined; }
  catch { notice("Answer lists must be valid JSON, such as [\"A\", \"B\"].", true); return; }
  if (action === "edit" && previewHash !== JSON.stringify(edits)) {
    notice("Preview your correction before applying it.", true);
    return;
  }
  if (action === "edit" && !$("edit-note").value.trim()) {
    notice("Record the reason for your correction before applying it.", true);
    $("edit-note").focus();
    return;
  }
  const draft = {
    case_id: review.id, action, revision: review.revision, source_version: review.source_version,
    note: action === "edit" ? $("edit-note").value : action === "reject" ? $("reject-note").value : ""
  };
  if (action === "edit") draft.edits = edits;
  const key = "acadza-pending-action-" + review.id;
  const stored = sessionStorage.getItem(key);
  const pending = stored ? JSON.parse(stored) : null;
  const same = pending && JSON.stringify(pending.draft) === JSON.stringify(draft);
  const request = {...draft, request_id: same ? pending.id : crypto.randomUUID()};
  sessionStorage.setItem(key, JSON.stringify({draft, id: request.request_id}));
  saveDraft();
  lock(true);
  try {
    const response = await api("/api/action", request);
    sessionStorage.removeItem(key);
    drafts.delete(review.id);
    const data = await api("/api/case?id=" + encodeURIComponent(review.id));
    review = data.case;
    token = data.token;
    render();
    await refreshQueue();
    notice({
      approved: "Correction applied. The exact edits, replies and decision are saved.",
      referred: "Referral recorded. The content repair remains pending.",
      confirmed: "Finding confirmed. The stored content is unchanged.",
      rejected: "Proposal rejected. The decision and replies are saved."
    }[response.status]);
    $("audit-panel").open = true;
  } catch (error) {
    notice(error.message, true);
    if (error.status && error.status < 500) sessionStorage.removeItem(key);
  } finally { lock(false); }
}
$("approve").onclick = () => act($("editor").hidden ? "approve" : "edit");
$("edit").onclick = () => {
  saveDraft();
  const saved = drafts.get(review.id);
  saved.editing = !saved.editing;
  if (saved.editing && !saved.edits.length) {
    const field = review.editable_fields[0];
    saved.edits = [{field, find: "", replace: ""}];
  }
  render();
  if (saved.editing) $("edit-fields").querySelector("textarea").focus();
};
$("preview").onclick = async () => {
  let edits;
  try { edits = collectEdits(); }
  catch { notice("Answer lists must be valid JSON.", true); return; }
  lock(true);
  const context = {case_id: review.id, revision: review.revision, source_version: review.source_version};
  try {
    const data = await api("/api/preview", {...context, edits});
    displayDiff(data.candidate, data.edits);
    shownReplies = data.approval_replies;
    renderReply();
    previewHash = JSON.stringify(edits);
    $("preview-state").textContent = "Preview ready. Review the changes and record your reason before applying.";
    $("reply-label").textContent = "After approval";
    notice("");
  } catch (error) { notice(error.message, true); }
  finally { lock(false); }
};
$("reject").onclick = () => {
  $("reject-editor").hidden = !$("reject-editor").hidden;
  if (!$("reject-editor").hidden) $("reject-note").focus();
};
$("confirm-reject").onclick = () => act("reject");
$("reload").onclick = () => loadCase(review.id);
$("reply-report").onchange = renderReply;
$("search").oninput = renderQueue;
$("queue-filter").onchange = renderQueue;
(async () => {
  try {
    await refreshQueue();
    const requested = location.hash.slice(1);
    const id = queue.some(item => item.id === requested) ? requested : queue[0]?.id;
    if (id) await loadCase(id);
    else notice("No imported cases are available.");
  } catch (error) { notice(error.message, true); }
})();
