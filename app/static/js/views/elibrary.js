// Digital library: PDF textbooks, past exam papers, marking schemes and notes uploaded by teachers.
import { api } from "../api.js";
import { badge, confirmDialog, debounce, esc, fieldHtml, fmtDate, formModal, handleError, icon, modal, readForm, selectHtml, showFieldErrors, table, toast } from "../ui.js";

const ROOT = document.querySelector('meta[name="app-root"]')?.content || "";
const size = (b) => (b >= 1048576 ? `${(b / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(b / 1024))} KB`);
const fileUrl = (r, download) => `${ROOT}/api/elibrary/${r.id}/file${download ? "?download=1" : ""}`;

/** POST multipart with upload progress (slow connections deserve a progress bar). */
function uploadWithProgress(form, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${ROOT}/api/elibrary`);
    xhr.setRequestHeader("X-Requested-With", "SchoolMS");
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
    xhr.onload = () => {
      let payload = {};
      try { payload = JSON.parse(xhr.responseText); } catch { /* not JSON */ }
      if (xhr.status >= 200 && xhr.status < 300) return resolve(payload);
      const err = new Error(payload.error || (xhr.status === 413 ? "The file is too large" : `Upload failed (${xhr.status})`));
      err.fields = payload.fields || {};
      reject(err);
    };
    xhr.onerror = () => reject(new Error("Cannot reach the server. Check your connection and try again."));
    xhr.send(form);
  });
}

export default async function (el) {
  const f = { q: "", kind: "", subject_id: "", level: "", mine: "" };
  let meta;
  el.innerHTML = `
    <div class="page-head"><div><h1>Digital Library</h1><p>PDF textbooks, past exam papers and notes, to read on any device</p></div>
      <div class="page-actions" id="actions"></div></div>
    <div class="card"><div class="card-head"><div class="row" style="gap:8px;flex-wrap:wrap;flex:1" id="filters">
      <input class="input" type="search" name="q" placeholder="Search titles, papers, exam boards" style="flex:1;min-width:200px"></div></div>
      <div id="tbl"></div></div>`;
  const filters = el.querySelector("#filters");

  const columns = () => [
    { key: "title", label: "Title", render: (r) => `<b>${esc(r.title)}</b>${r.audience === "staff" ? ` ${badge("pending", "staff only")}` : ""}<br><span class="muted small">${esc(r.kind)}${r.description ? ` · ${esc(r.description.slice(0, 90))}${r.description.length > 90 ? "…" : ""}` : ""}</span>` },
    { key: "subject", label: "Subject / level", render: (r) => `${esc(r.subject || "—")}${r.level ? `<br><span class="muted small">${esc(meta.levels.find((l) => l.code === r.level)?.label || r.level)}</span>` : ""}` },
    { key: "year", label: "Exam", render: (r) => [r.exam_board, r.year, r.paper].filter(Boolean).map(esc).join(" · ") || "—", sort: (r) => r.year || 0 },
    { key: "size_bytes", label: "Size", num: true, render: (r) => size(r.size_bytes) },
    { key: "created_at", label: "Added", render: (r) => `${fmtDate(r.created_at)}<br><span class="muted small">${esc(r.uploaded_by || "")}</span>` },
    { key: "id", label: "", sort: false, cls: "actions", render: (r) => `<a class="btn sm primary" href="${fileUrl(r)}" target="_blank" rel="noopener">Open</a> <a class="btn sm" href="${fileUrl(r, true)}">${icon("download")}</a>${r.can_edit ? ` <button class="btn sm" data-edit="${r.id}">Edit</button> <button class="btn sm danger" data-del="${r.id}">Delete</button>` : ""}` },
  ];

  const load = async () => {
    meta = await api.get("/elibrary", f);
    if (!filters.querySelector('[name="kind"]')) {
      filters.insertAdjacentHTML("beforeend", selectHtml("kind", meta.kinds.map((k) => ({ value: k, label: k })), "", { empty: "All types" })
        + selectHtml("subject_id", meta.subjects.map((s) => ({ value: s.id, label: s.name })), "", { empty: "All subjects" })
        + selectHtml("level", meta.levels.map((l) => ({ value: l.code, label: l.label })), "", { empty: "All levels" })
        + (meta.can_upload ? `<label class="check small"><input type="checkbox" name="mine"> My uploads</label>` : ""));
      filters.querySelectorAll("select").forEach((s) => (s.onchange = () => { f[s.name] = s.value; load(); }));
      filters.querySelector('[name="mine"]')?.addEventListener("change", (e) => { f.mine = e.target.checked ? "1" : ""; load(); });
      el.querySelector("#actions").innerHTML = meta.can_upload ? `<button class="btn primary" id="up">${icon("upload")} Upload PDF</button>` : "";
      el.querySelector("#up")?.addEventListener("click", upload);
    }
    const any = f.q || f.kind || f.subject_id || f.level || f.mine;
    table(el.querySelector("#tbl"), { rows: meta.items, columns: columns(),
      empty: any ? "Nothing matches these filters." : meta.can_upload ? "No files yet. Upload the first textbook or past paper." : "No files have been shared yet." });
    el.querySelectorAll("[data-edit]").forEach((b) => (b.onclick = () => edit(meta.items.find((r) => r.id === +b.dataset.edit))));
    el.querySelectorAll("[data-del]").forEach((b) => (b.onclick = async () => {
      const r = meta.items.find((x) => x.id === +b.dataset.del);
      if (!(await confirmDialog(`Delete "${r.title}"? Nobody will be able to open it any more.`, { danger: true, confirmText: "Delete" }))) return;
      try { await api.del(`/elibrary/${r.id}`); toast("Deleted", "success"); load(); } catch (err) { handleError(err); }
    }));
  };
  filters.querySelector('[name="q"]').oninput = debounce((e) => { f.q = e.target.value.trim(); load(); }, 300);

  const detailFields = () => [
    { name: "title", label: "Title", required: true, full: true, placeholder: "e.g. O Level Mathematics Paper 2, November 2023" },
    { name: "kind", label: "Type", type: "select", required: true, options: meta.kinds, default: "Past exam paper" },
    { name: "subject_id", label: "Subject", type: "select", options: meta.subjects.map((s) => ({ value: s.id, label: s.name })) },
    { name: "level", label: "Level", type: "select", options: meta.levels.map((l) => ({ value: l.code, label: l.label })) },
    { name: "exam_board", label: "Exam board", placeholder: "e.g. ZIMSEC" },
    { name: "year", label: "Year", type: "number", min: 1950, max: new Date().getFullYear() + 1 },
    { name: "paper", label: "Paper / session", placeholder: "e.g. Paper 2, November" },
    { name: "audience", label: "Who can see it", type: "select", required: true, default: "everyone",
      options: [{ value: "everyone", label: "Everyone, including parents" }, { value: "staff", label: "Staff only (e.g. marking schemes)" }] },
    { name: "description", label: "Description", type: "textarea", full: true },
  ];

  async function upload() {
    const fields = detailFields();
    const m = modal({
      title: "Upload a PDF", wide: true,
      body: `<form class="form" novalidate>
        <label class="field"><span>PDF file *</span><input class="input" type="file" name="file" accept="application/pdf,.pdf" required>
          <small class="hint">Up to ${meta.max_mb} MB. Large scanned books: compress the PDF first (e.g. with ilovepdf.com) or upload it in parts.</small><small class="err" data-err="file"></small></label>
        <div class="cols">${fields.map((x) => fieldHtml(x)).join("")}</div>
        <label class="check"><input type="checkbox" name="rights"> The school may share this file: ZIMSEC or school past papers, our own notes, or a book we have the right to distribute.</label>
        <small class="err" data-err="rights"></small>
        <div id="prog" hidden><div class="meter" style="margin:4px 0"><span style="width:0%"></span></div><div class="muted small" id="prog-t"></div></div></form>`,
      actions: [{ label: "Cancel" }, { label: "Upload", cls: "primary", onClick: async () => {
        const form = m.el.querySelector("form");
        const file = form.file.files[0];
        const d = readForm(form, fields);
        const errs = {};
        if (!file) errs.file = "Choose a PDF";
        else if (file.size > meta.max_mb * 1048576) errs.file = `Larger than ${meta.max_mb} MB`;
        if (!d.title) errs.title = "Required";
        if (!form.rights.checked) errs.rights = "Please confirm";
        if (Object.keys(errs).length) { showFieldErrors(form, { fields: errs }); return true; }
        const fd = new FormData();
        fd.append("file", file);
        Object.entries(d).forEach(([k, v]) => fd.append(k, v ?? ""));
        fd.append("rights", "1");
        const prog = m.el.querySelector("#prog");
        prog.hidden = false;
        try {
          const r = await uploadWithProgress(fd, (p) => {
            prog.querySelector(".meter span").style.width = `${Math.round(p * 100)}%`;
            m.el.querySelector("#prog-t").textContent = p < 1 ? `Uploading… ${Math.round(p * 100)}%` : "Saving…";
          });
          toast(r.duplicate_of ? `Uploaded. Note: the same file is already here as "${r.duplicate_of}".` : "Uploaded", "success");
          load();
          return false;
        } catch (err) {
          prog.hidden = true;
          showFieldErrors(form, err);
          toast(err.message, "error");
          return true;
        }
      } }],
    });
    // A sensible title from the file name.
    const form = m.el.querySelector("form");
    form.file.onchange = () => {
      const fl = form.file.files[0];
      if (fl && !form.title.value) form.title.value = fl.name.replace(/\.pdf$/i, "").replace(/[_-]+/g, " ").trim();
    };
    form.kind.onchange = () => { if (form.kind.value === "Marking scheme") form.audience.value = "staff"; };
    form.rights.onchange = () => { if (form.rights.checked) form.querySelector('[data-err="rights"]').textContent = ""; };
  }

  async function edit(r) {
    const ok = await formModal({ title: `Edit · ${r.title}`, values: r, fields: detailFields(),
      onSubmit: (d) => api.put(`/elibrary/${r.id}`, d) });
    if (ok) { toast("Saved", "success"); load(); }
  }

  await load();
}
