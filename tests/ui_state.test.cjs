// Exercise the shipped draft logic without a DOM package or a second implementation.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../acadza/web/app.js"), "utf8");
const elements = {
  editor: {hidden: true},
  "edit-fields": {children: [], replaceChildren() { this.children = []; }},
  "edit-note": {value: ""},
  "reject-editor": {hidden: true},
  "reject-note": {value: ""}
};
const context = vm.createContext({
  document: {getElementById: id => elements[id]}
});
vm.runInContext(source.slice(0, source.indexOf('$("approve").onclick')), context);
vm.runInContext(`
  review = {
    id: "test",
    editable_edits: [{field:"answer", find:["A, B"], replace:["A","B"]}]
  };
  saveDraft();
`, context);
const readDraft = () => JSON.parse(vm.runInContext('JSON.stringify(drafts.get("test"))', context));
assert.deepEqual(readDraft().edits, [
  {field: "answer", find: ["A, B"], replace: ["A", "B"]}
], "Opening the editor must retain the supplied proposal");

elements.editor.hidden = false;
elements["edit-note"].value = "My unfinished correction";
elements["edit-fields"].children = [{
  querySelector(selector) {
    return {value: {
      ".edit-field": "answer",
      ".edit-find": '["A, B"]',
      ".edit-replace": '["A",'
    }[selector]};
  }
}];
vm.runInContext("saveDraft()", context);
assert.equal(readDraft().edits[0].replace, '["A",',
  "Invalid unfinished JSON must survive case navigation and reload");
assert.equal(readDraft().note, "My unfinished correction");
assert.equal(readDraft().editing, true);
console.log("UI state checks passed: proposal prefilling and raw draft preservation.");

vm.runInContext('showEditor({editing:false, edits:[], note:""})', context);
assert.equal(elements["edit-note"].value, "",
  "A new case must not inherit another case's faculty reason");
console.log("UI state check passed: case notes are reset before another edit.");
