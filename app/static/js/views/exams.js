import { api } from "../api.js";
import { badge, confirmDialog, esc, fmtDate, formModal, handleError, has, icon, selectHtml, state, table, termOptions, toast } from "../ui.js";

export default async function (el) {
  const admin = has("academics.manage");
  let termId = state.meta.current_term?.id || state.meta.terms[0]?.id;
  const classes = (await api.get("/classes", has("academics.view", "marks.manage") ? {} : { mine: 1 })).items;
  el.innerHTML = `
    <div class="page-head"><div><h1>Exams & Marks</h1><p>Exam weights per term must total no more than 100%. Locked exams can't be edited.</p></div>
      <div class="page-actions">${selectHtml("term", termOptions(), termId)}${admin ? `<button class="btn primary" id="add">${icon("plus")} New exam</button>` : ""}</div></div>
    <div class="card" style="margin-bottom:16px"><div class="card-head"><h3>Exams this term</h3><span id="weights" class="small muted"></span></div><div id="exams"></div></div>
    <div class="card"><div class="card-head"><div><h3>Mark entry</h3><div class="sub">Only the assigned subject teacher can enter marks. Leave blank if a student was absent.</div></div></div>
      <div class="toolbar" id="sel"></div><div id="sheet"><div class="empty">Choose an exam, class and subject.</div></div></div>`;

  let exams = [];
  const fields = [
    { name: "name", label: "Exam name", required: true, placeholder: "e.g. Mid-term Test" },
    { name: "date", label: "Date", type: "date", hint: "Within the term" },
    { name: "weight", label: "Weight (%)", type: "number", min: 1, max: 100, required: true, hint: "Contribution to the term result" },
    { name: "max_score", label: "Out of", type: "number", min: 1, required: true, default: 100 },
  ];

  const loadExams = async () => {
    exams = (await api.get("/exams", { term_id: termId })).items;
    const total = exams.reduce((a, e) => a + e.weight, 0);
    el.querySelector("#weights").innerHTML = `Total weight: <b style="color:${total > 100 ? "var(--bad)" : total === 100 ? "var(--good)" : "inherit"}">${total}%</b>${total < 100 ? " (results are scaled to the exams sat)" : ""}`;
    table(el.querySelector("#exams"), {
      rows: exams, empty: "No exams set for this term.",
      columns: [{ key: "name", label: "Exam", render: (e) => `<b>${esc(e.name)}</b>` }, { key: "date", label: "Date", render: (e) => fmtDate(e.date) },
        { key: "weight", label: "Weight", num: true, render: (e) => `${e.weight}%` }, { key: "max_score", label: "Out of", num: true },
        { key: "marks", label: "Marks entered", num: true }, { key: "locked", label: "Status", render: (e) => (e.locked ? badge("void", "locked") : badge("active", "open")) },
        ...(admin ? [{ key: "id", label: "", sort: false, cls: "actions", render: (e) => `<button class="btn sm" data-lock="${e.id}" data-locked="${e.locked}">${e.locked ? "Unlock" : "Lock"}</button> <button class="btn sm" data-edit="${e.id}">Edit</button> ${e.marks ? "" : `<button class="btn sm danger" data-del="${e.id}">Delete</button>`}` }] : [])],
    });
    el.querySelectorAll("[data-lock]").forEach((b) => (b.onclick = async () => {
      await api.put(`/exams/${b.dataset.lock}`, { locked: b.dataset.locked !== "true" }).catch(handleError);
      loadExams();
    }));
    el.querySelectorAll("[data-edit]").forEach((b) => (b.onclick = async () => {
      const e = exams.find((x) => x.id === +b.dataset.edit);
      if (await formModal({ title: `Edit ${e.name}`, fields, values: e, onSubmit: (d) => api.put(`/exams/${e.id}`, d) })) { toast("Exam saved", "success"); loadExams(); }
    }));
    el.querySelectorAll("[data-del]").forEach((b) => (b.onclick = async () => {
      if (!(await confirmDialog("Delete this exam?", { danger: true, confirmText: "Delete" }))) return;
      try { await api.del(`/exams/${b.dataset.del}`); loadExams(); } catch (err) { handleError(err); }
    }));
    drawSelectors();
  };

  const sel = { exam: "", cls: classes[0]?.id || "", subject: "" };
  const subjectsFor = (cid) => {
    const c = classes.find((x) => String(x.id) === String(cid));
    if (!c) return [];
    return c.subjects.filter((s) => has("marks.manage") || s.teacher_id === state.user.staff_id).map((s) => ({ value: s.subject_id, label: s.subject }));
  };
  const drawSelectors = () => {
    if (!exams.find((e) => String(e.id) === String(sel.exam))) sel.exam = exams.find((e) => !e.locked)?.id || exams[0]?.id || "";
    const subs = subjectsFor(sel.cls);
    if (!subs.find((s) => String(s.value) === String(sel.subject))) sel.subject = subs[0]?.value || "";
    el.querySelector("#sel").innerHTML = `${selectHtml("exam", exams.map((e) => ({ value: e.id, label: `${e.name}${e.locked ? " (locked)" : ""}` })), sel.exam)}
      ${selectHtml("cls", classes.map((c) => ({ value: c.id, label: c.name })), sel.cls)}
      ${selectHtml("subject", subs, sel.subject)}<button class="btn" id="open">Open mark sheet</button>`;
    el.querySelector('[name="exam"]').onchange = (e) => (sel.exam = e.target.value);
    el.querySelector('[name="cls"]').onchange = (e) => { sel.cls = e.target.value; drawSelectors(); };
    el.querySelector('[name="subject"]').onchange = (e) => (sel.subject = e.target.value);
    el.querySelector("#open").onclick = loadSheet;
  };

  const loadSheet = async () => {
    const box = el.querySelector("#sheet");
    if (!sel.exam || !sel.cls || !sel.subject) { box.innerHTML = `<div class="empty">Choose an exam, class and subject${!subjectsFor(sel.cls).length ? " (you don't teach any subject in this class)" : ""}.</div>`; return; }
    try {
      const r = await api.get("/marks", { exam_id: sel.exam, class_id: sel.cls, subject_id: sel.subject });
      const max = r.exam.max_score;
      box.innerHTML = `<div class="table-wrap"><table class="table"><thead><tr><th>#</th><th>Student</th><th class="num">Score / ${max}</th><th class="num">%</th></tr></thead><tbody>
        ${r.rows.map((s, i) => `<tr><td class="muted">${i + 1}</td><td><b>${esc(s.name)}</b> <span class="muted small">${esc(s.admission_no)}</span></td>
          <td class="num"><input class="input" style="width:110px;text-align:right" type="number" min="0" max="${max}" step="0.5" data-sid="${s.student_id}" value="${s.score ?? ""}" ${r.can_edit ? "" : "disabled"} aria-label="Score for ${esc(s.name)}"><div class="err small" data-err="${s.student_id}"></div></td>
          <td class="num" data-pct="${s.student_id}">${s.score !== null ? Math.round((s.score / max) * 100) + "%" : "—"}</td></tr>`).join("")}
        </tbody></table></div>
        <div class="pager"><span id="stats"></span>${r.can_edit ? `<button class="btn primary" id="save-marks">Save marks</button>` : `<span class="badge plain">${r.exam.locked ? "Exam locked" : "View only"}</span>`}</div>`;
      const stats = () => {
        const vals = [...box.querySelectorAll("[data-sid]")].map((i) => i.value).filter((v) => v !== "").map(Number);
        box.querySelector("#stats").textContent = vals.length ? `${vals.length}/${r.rows.length} entered · mean ${(vals.reduce((a, b) => a + b, 0) / vals.length / max * 100).toFixed(1)}% · highest ${Math.max(...vals)} · lowest ${Math.min(...vals)}` : "No marks entered";
      };
      box.querySelectorAll("[data-sid]").forEach((inp) => (inp.oninput = () => {
        const v = inp.value === "" ? null : Number(inp.value);
        const bad = v !== null && (isNaN(v) || v < 0 || v > max);
        inp.classList.toggle("invalid", bad);
        box.querySelector(`[data-err="${inp.dataset.sid}"]`).textContent = bad ? `0–${max}` : "";
        box.querySelector(`[data-pct="${inp.dataset.sid}"]`).textContent = v === null || bad ? "—" : Math.round((v / max) * 100) + "%";
        stats();
      }));
      stats();
      box.querySelector("#save-marks")?.addEventListener("click", async (e) => {
        if (box.querySelector(".invalid")) return toast("Fix the highlighted scores first", "error");
        e.target.disabled = true;
        const entries = [...box.querySelectorAll("[data-sid]")].map((i) => ({ student_id: +i.dataset.sid, score: i.value }));
        try { await api.post("/marks", { exam_id: sel.exam, class_id: sel.cls, subject_id: sel.subject, entries }); toast("Marks saved", "success"); loadExams(); }
        catch (err) {
          handleError(err);
          for (const [sid, msg] of Object.entries(err.fields || {})) { const s = box.querySelector(`[data-err="${sid}"]`); if (s) s.textContent = msg; }
        } finally { e.target.disabled = false; }
      });
    } catch (err) { box.innerHTML = `<div class="empty">${esc(err.message)}</div>`; }
  };

  el.querySelector('[name="term"]').onchange = (e) => { termId = e.target.value; el.querySelector("#sheet").innerHTML = `<div class="empty">Choose an exam, class and subject.</div>`; loadExams(); };
  el.querySelector("#add")?.addEventListener("click", async () => {
    if (await formModal({ title: "New exam", fields, onSubmit: (d) => api.post("/exams", { ...d, term_id: termId }) })) { toast("Exam created", "success"); loadExams(); }
  });
  await loadExams();
}
