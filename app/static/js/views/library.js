// Library: the issue/return desk, loans and overdue books, the catalogue and library rules.
import { api } from "../api.js";
import { badge, baseCurrency, classOptions, confirmDialog, currencyField, debounce, downloadCSV, esc, fmtDate, formModal, handleError, has, icon, modal, money, selectHtml, table, toast, today } from "../ui.js";

const STATUS = { on_loan: ["info", "on loan"], overdue: ["overdue", "overdue"], returned: ["plain", "returned"], lost: ["blocked", "lost"],
  available: ["active", "available"], withdrawn: ["void", "withdrawn"] };
const FINE = { unpaid: ["unpaid", "fine owed"], charged: ["approved", "added to fees"], paid: ["paid", "paid"], waived: ["void", "waived"] };
const st = (s) => badge(...(STATUS[s] || ["plain", s]));
const fineBadge = (l) => (l.fine_status && l.fine_status !== "none" ? `${badge(...FINE[l.fine_status])}<br><span class="small">${money(l.fine, l.fine_currency)}</span>` : "");
const borrowerCell = (l) => `${esc(l.borrower)}<br><span class="muted small">${esc(l.borrower_ref)}${l.class ? ` · ${esc(l.class)}` : l.borrower_type === "staff" ? " · staff" : ""}</span>`;
const bookCell = (l) => `<b>${esc(l.title)}</b><br><span class="muted small">${esc(l.accession_no)}${l.author ? ` · ${esc(l.author)}` : ""}</span>`;

export default async function (el) {
  const manage = has("library.manage"), approve = has("library.approve");
  const tabs = [...(manage ? [["desk", "Issue & return"]] : []), ["loans", "Loans"], ["catalogue", "Catalogue"], ...(approve ? [["rules", "Rules"]] : [])];
  let tab = tabs[0][0];
  el.innerHTML = `
    <div class="page-head"><div><h1>Library</h1><p>Books, loans to students and staff, overdue books and fines</p></div>
      <div class="page-actions" id="actions"></div></div>
    <div class="grid g-4" id="stats" style="margin-bottom:16px"></div>
    <div class="tabs" role="tablist">${tabs.map(([k, l]) => `<button role="tab" data-tab="${k}" class="${k === tab ? "active" : ""}">${l}</button>`).join("")}</div>
    <div id="pane"></div>`;
  const pane = el.querySelector("#pane"), actions = el.querySelector("#actions");

  const stats = (s) => {
    el.querySelector("#stats").innerHTML = `
      <div class="card stat"><div class="label">Titles</div><div class="value">${s.titles}</div><div class="foot">${s.copies} copies · ${s.available} on the shelf</div></div>
      <div class="card stat"><div class="label">On loan</div><div class="value">${s.on_loan}</div><div class="foot">${s.due_today} due back today</div></div>
      <div class="card stat"><div class="label">Overdue</div><div class="value" style="${s.overdue ? "color:var(--bad)" : ""}">${s.overdue}</div><div class="foot">${s.lost} lost</div></div>
      <div class="card stat"><div class="label">Fines owed</div><div class="value">${s.fines_owed.length ? s.fines_owed.map((f) => money(f.amount, f.currency)).join(" · ") : money(0)}</div><div class="foot">Not yet paid, charged or waived</div></div>`;
  };
  const refreshStats = async () => stats((await api.get("/library/loans", { status: "overdue" })).summary);

  // ---------------------------------------------------------------- loan actions (shared)
  async function loanAction(l, after) {
    const acts = [];
    if (l.status === "on_loan" || l.status === "overdue") {
      acts.push(["return", "Return"]);
      if (l.status === "on_loan") acts.push(["renew", "Renew"]);
      acts.push(["lost", "Mark lost"]);
    }
    if (l.fine_status === "unpaid") {
      if (l.borrower_type === "student") acts.push(["charge", "Add fine to fees"]);
      acts.push(["paid", "Fine paid"]);
      if (approve) acts.push(["waive", "Waive fine"]);
    }
    return acts.map(([k, label]) => `<button class="btn sm ${k === "lost" || k === "waive" ? "danger" : k === "return" ? "primary" : ""}" data-act="${k}" data-loan="${l.id}">${label}</button>`).join(" ");
  }
  async function runAction(kind, l, done) {
    try {
      if (kind === "return") {
        const r = await formModal({ title: `Return · ${l.title}`, cols: 1, submitText: "Return",
          intro: `<p style="margin-top:0">${esc(l.accession_no)} from ${esc(l.borrower)}, due ${fmtDate(l.due_on)}${l.days_overdue ? ` (<b>${l.days_overdue} day${l.days_overdue > 1 ? "s" : ""} late</b>)` : ""}.</p>`,
          fields: [{ name: "condition", label: "Condition now", type: "select", options: ["New", "Good", "Fair", "Poor", "Damaged"], default: l.condition_out || "Good" }],
          onSubmit: (d) => api.post(`/library/loans/${l.id}/return`, d) });
        if (!r) return;
        toast(r.fine_status === "unpaid" ? `Returned. Fine: ${money(r.fine, r.fine_currency)} (${r.fine_note})` : "Returned", r.fine_status === "unpaid" ? "" : "success");
      } else if (kind === "renew") {
        const r = await api.post(`/library/loans/${l.id}/renew`);
        toast(`Renewed until ${fmtDate(r.due_on)}`, "success");
      } else if (kind === "lost") {
        if (!(await confirmDialog(`Mark ${l.accession_no} (${l.title}) as lost by ${l.borrower}? The copy's replacement cost becomes a fine.`, { title: "Lost book", danger: true, confirmText: "Mark lost" }))) return;
        const r = await api.post(`/library/loans/${l.id}/lost`);
        toast(r.fine_status === "unpaid" ? `Marked lost. Replacement: ${money(r.fine, r.fine_currency)}` : "Marked lost", "success");
      } else if (kind === "charge") {
        if (!(await confirmDialog(`Add the fine of ${money(l.fine, l.fine_currency)} to ${l.borrower}'s fees invoice for this term? It then appears on their statement and is paid like any fee.`, { title: "Add fine to fees", confirmText: "Add to fees" }))) return;
        const r = await api.post(`/library/loans/${l.id}/fine`, { action: "charge" });
        toast(`Added to invoice ${r.fine_invoice}`, "success");
      } else if (kind === "paid") {
        const r = await formModal({ title: "Fine paid", cols: 1, submitText: "Mark paid",
          intro: `<p style="margin-top:0">${money(l.fine, l.fine_currency)} from ${esc(l.borrower)}. Hand any cash received to the bursar so it is recorded in the accounts.</p>`,
          fields: [{ name: "note", label: "Note", placeholder: "e.g. cash received by the librarian" }],
          onSubmit: (d) => api.post(`/library/loans/${l.id}/fine`, { action: "paid", note: d.note }) });
        if (!r) return;
        toast("Fine marked paid", "success");
      } else if (kind === "waive") {
        const r = await formModal({ title: "Waive fine", cols: 1, submitText: "Waive",
          fields: [{ name: "note", label: "Reason", required: true, placeholder: "e.g. book returned late because of illness" }],
          onSubmit: (d) => api.post(`/library/loans/${l.id}/fine`, { action: "waive", note: d.note }) });
        if (!r) return;
        toast("Fine waived", "success");
      }
      await done();
      refreshStats();
    } catch (err) { handleError(err); }
  }
  const wireActions = (root, rows, done) => root.querySelectorAll("[data-act]").forEach((b) => (b.onclick = () => runAction(b.dataset.act, rows.find((x) => x.id === +b.dataset.loan), done)));

  // ---------------------------------------------------------------- desk
  async function desk() {
    actions.innerHTML = "";
    pane.innerHTML = `<div class="grid g-2">
      <div class="card"><div class="card-head"><div><h3>Scan or type a book</h3><div class="sub">The accession number on the book's label, e.g. LIB-00012</div></div></div>
        <div class="card-body"><form id="scan" class="row" style="gap:8px"><input class="input" name="code" placeholder="Accession number" autocomplete="off" autofocus style="flex:1;min-width:0"><button class="btn primary">Find</button></form>
        <div id="found" style="margin-top:14px"></div></div></div>
      <div class="card"><div class="card-head"><h3>This session</h3></div><ul class="list" id="log"><li class="muted">Books you issue or take back appear here.</li></ul></div></div>`;
    const form = pane.querySelector("#scan"), found = pane.querySelector("#found"), log = pane.querySelector("#log");
    const logLine = (text) => {
      if (log.querySelector(".muted")) log.innerHTML = "";
      log.insertAdjacentHTML("afterbegin", `<li><span>${text}</span><span class="muted small">${new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span></li>`);
    };
    const again = () => { form.code.value = ""; form.code.focus(); };
    const show = async (code) => {
      let c;
      try { c = await api.get("/library/copies/lookup", { code }); } catch (err) { found.innerHTML = `<div class="notice warn">${esc(err.message)}</div>`; return; }
      const head = `<div style="margin-bottom:10px"><b>${esc(c.title)}</b>${c.author ? ` · ${esc(c.author)}` : ""}<br><span class="muted small">${esc(c.accession_no)} · ${esc(c.category)} · ${esc(c.condition)}</span> ${st(c.loan ? (c.loan.status) : c.status)}</div>`;
      if (c.loan) {
        const l = c.loan;
        found.innerHTML = `${head}<dl class="kv"><dt>Borrower</dt><dd>${borrowerCell(l)}</dd><dt>Issued</dt><dd>${fmtDate(l.issued_on)}</dd><dt>Due</dt><dd>${fmtDate(l.due_on)}${l.days_overdue ? ` · <b style="color:var(--bad)">${l.days_overdue} days late</b>` : ""}</dd></dl>
          <div class="row" style="gap:8px;margin-top:10px">${await loanAction(l)}</div>`;
        wireActions(found, [l], async () => { logLine(`${esc(l.accession_no)} ${esc(l.title)}: updated (${esc(l.borrower)})`); found.innerHTML = ""; again(); });
        return;
      }
      if (c.status !== "available") { found.innerHTML = `${head}<div class="notice warn">This copy is ${esc(c.status)} and can't be lent. Change it in the catalogue if it has been found.</div>`; return; }
      found.innerHTML = `${head}<label class="field"><span>Lend to</span><input class="input" id="who" type="search" placeholder="Student or staff name, admission or staff number" autocomplete="off"></label><div id="who-list"></div>`;
      const who = found.querySelector("#who"), list = found.querySelector("#who-list");
      who.focus();
      who.oninput = debounce(async () => {
        if (who.value.trim().length < 2) { list.innerHTML = ""; return; }
        const { items } = await api.get("/library/borrowers", { q: who.value.trim() });
        list.innerHTML = items.length ? `<ul class="list">${items.map((b, i) => `<li><span><b>${esc(b.name)}</b> ${b.type === "staff" ? badge("info", "staff") : ""}<br><span class="muted small">${esc(b.ref)} · ${esc(b.detail)} · ${b.on_loan} book${b.on_loan === 1 ? "" : "s"} out${b.overdue ? ` · <b style="color:var(--bad)">${b.overdue} overdue</b>` : ""}${b.fines.length ? ` · owes ${b.fines.map((f) => money(f.amount, f.currency)).join(", ")}` : ""}</span></span><button class="btn sm primary" data-i="${i}">Lend</button></li>`).join("")}</ul>` : `<div class="muted small" style="padding:8px 0">No matching student or staff member.</div>`;
        list.querySelectorAll("[data-i]").forEach((b) => (b.onclick = async () => {
          const p = items[+b.dataset.i];
          try {
            const l = await api.post("/library/loans", { copy_id: c.id, [p.type === "student" ? "student_id" : "staff_id"]: p.id });
            toast(`Lent to ${p.name}, due ${fmtDate(l.due_on)}`, "success");
            logLine(`${esc(c.accession_no)} ${esc(c.title)} → ${esc(p.name)}, due ${fmtDate(l.due_on)}`);
            found.innerHTML = ""; again(); refreshStats();
          } catch (err) { handleError(err); }
        }));
      }, 250);
    };
    form.onsubmit = (e) => { e.preventDefault(); if (form.code.value.trim()) show(form.code.value.trim()); };
  }

  // ---------------------------------------------------------------- loans
  async function loans() {
    let status = "open", cls = "";
    actions.innerHTML = `<button class="btn" id="csv">${icon("download")} Export CSV</button>`;
    pane.innerHTML = `<div class="card"><div class="card-head"><div class="row" style="gap:8px;flex-wrap:wrap">
        ${selectHtml("status", [["open", "Out now"], ["overdue", "Overdue"], ["fines", "Fines owed"], ["returned", "Returned"], ["lost", "Lost"], ["all", "All loans"]].map(([value, label]) => ({ value, label })), status)}
        ${selectHtml("class", classOptions(), "", { empty: "All classes and staff" })}</div></div><div id="tbl"></div></div>`;
    let rows = [];
    const cols = [
      { key: "title", label: "Book", render: bookCell },
      { key: "borrower", label: "Borrower", render: borrowerCell },
      { key: "issued_on", label: "Issued", render: (l) => fmtDate(l.issued_on) },
      { key: "due_on", label: "Due", render: (l) => `${fmtDate(l.due_on)}${l.days_overdue && !l.returned_on ? `<br><b class="small" style="color:var(--bad)">${l.days_overdue} days late</b>` : ""}` },
      { key: "status", label: "Status", render: (l) => `${st(l.status)}${l.returned_on ? `<br><span class="muted small">${fmtDate(l.returned_on)}</span>` : ""}` },
      { key: "fine_status", label: "Fine", sort: false, render: fineBadge },
      ...(manage ? [{ key: "id", label: "", sort: false, cls: "actions", render: (l) => `<span data-row="${l.id}"></span>` }] : []),
    ];
    const load = async () => {
      const r = await api.get("/library/loans", { status, class_id: cls });
      rows = r.items;
      stats(r.summary);
      const t = pane.querySelector("#tbl");
      table(t, { rows, columns: cols, empty: status === "overdue" ? "Nothing overdue." : "No loans to show." });
      if (manage) {
        for (const span of t.querySelectorAll("[data-row]")) span.outerHTML = await loanAction(rows.find((x) => x.id === +span.dataset.row));
        wireActions(t, rows, load);
      }
    };
    pane.querySelector('[name="status"]').onchange = (e) => { status = e.target.value; load(); };
    pane.querySelector('[name="class"]').onchange = (e) => { cls = e.target.value; load(); };
    actions.querySelector("#csv").onclick = () => downloadCSV(`library-${status}-${today()}.csv`, [
      { label: "Accession no", key: "accession_no" }, { label: "Title", key: "title" }, { label: "Borrower", key: "borrower" },
      { label: "Ref", key: "borrower_ref" }, { label: "Class", key: "class" }, { label: "Issued", key: "issued_on" }, { label: "Due", key: "due_on" },
      { label: "Returned", key: "returned_on" }, { label: "Status", key: "status" }, { label: "Days overdue", key: "days_overdue" },
      { label: "Fine", key: "fine" }, { label: "Fine currency", key: "fine_currency" }, { label: "Fine status", key: "fine_status" }], rows);
    await load();
  }

  // ---------------------------------------------------------------- catalogue
  async function catalogue() {
    let q = "", category = "", meta;
    actions.innerHTML = manage ? `<button class="btn primary" id="add">${icon("plus")} Add book</button>` : "";
    pane.innerHTML = `<div class="card"><div class="card-head"><div class="row" style="gap:8px;flex-wrap:wrap;flex:1">
      <input class="input" type="search" id="q" placeholder="Search title, author, ISBN or accession no." style="flex:1;min-width:200px"><span id="cat"></span></div></div><div id="tbl"></div></div>`;
    const load = async () => {
      meta = await api.get("/library/books", { q, category });
      pane.querySelector("#cat").innerHTML = selectHtml("category", meta.categories.map((c) => ({ value: c, label: c })), category, { empty: "All categories" });
      pane.querySelector('[name="category"]').onchange = (e) => { category = e.target.value; load(); };
      table(pane.querySelector("#tbl"), {
        rows: meta.items, sortKey: "title", empty: q || category ? "No books match." : "The catalogue is empty. Add the first book.",
        onRowClick: (b) => openBook(b.id, load, meta),
        columns: [
          { key: "title", label: "Title", render: (b) => `<b>${esc(b.title)}</b><br><span class="muted small">${esc(b.author || "—")}${b.year ? ` · ${b.year}` : ""}</span>` },
          { key: "category", label: "Category" },
          { key: "subject", label: "Subject / level", render: (b) => `${esc(b.subject || "—")}${b.level ? `<br><span class="muted small">${esc(meta.levels.find((l) => l.code === b.level)?.label || b.level)}</span>` : ""}` },
          { key: "shelf", label: "Shelf" },
          { key: "available", label: "On shelf", num: true, render: (b) => `<b>${b.available}</b> of ${b.copies}` },
          { key: "on_loan", label: "Out", num: true },
        ] });
    };
    pane.querySelector("#q").oninput = debounce((e) => { q = e.target.value.trim(); load(); }, 300);
    const ready = load();
    actions.querySelector("#add")?.addEventListener("click", async () => {
      await ready;  // the form needs the category, subject and level lists
      const r = await formModal({ title: "Add book", fields: bookFields(meta, true), onSubmit: (d) => api.post("/library/books", d) });
      if (r) { toast(`Added with ${r.copy_list.length} cop${r.copy_list.length === 1 ? "y" : "ies"}: ${r.copy_list.map((c) => c.accession_no).join(", ")}`, "success"); load(); refreshStats(); }
    });
    await ready;
  }

  function bookFields(meta, isNew) {
    return [
      { name: "title", label: "Title", required: true, full: true },
      { name: "author", label: "Author" },
      { name: "isbn", label: "ISBN" },
      { name: "category", label: "Category", type: "select", required: true, options: meta.categories, default: "Textbook" },
      { name: "subject_id", label: "Subject", type: "select", options: meta.subjects.map((s) => ({ value: s.id, label: s.name })) },
      { name: "level", label: "Level", type: "select", options: meta.levels.map((l) => ({ value: l.code, label: l.label })) },
      { name: "shelf", label: "Shelf / location", placeholder: "e.g. FIC-3" },
      { name: "publisher", label: "Publisher" },
      { name: "year", label: "Year", type: "number", min: 1400, max: new Date().getFullYear() + 1 },
      ...(isNew ? [
        { type: "heading", name: "_c", label: "Copies" },
        { name: "copies", label: "Number of copies", type: "number", min: 0, max: 500, default: 1, required: true },
        { name: "replacement_cost", label: "Replacement cost (each)", type: "number", min: 0, step: 0.01, default: 0, hint: "Charged if a copy is lost" },
        ...currencyField({ label: "Currency of the cost" }),
        { name: "accession_numbers", label: "Accession numbers (optional)", type: "textarea", full: true, hint: "Leave empty to number them automatically (LIB-00001...), or list existing label numbers, one per line" },
      ] : []),
      { name: "notes", label: "Notes", type: "textarea", full: true },
    ];
  }

  async function openBook(id, reload, meta) {
    const b = await api.get(`/library/books/${id}`);
    const m = modal({ title: b.title, wide: true, body: `
      <p class="muted" style="margin-top:0">${esc(b.author || "Unknown author")}${b.publisher ? ` · ${esc(b.publisher)}` : ""}${b.year ? ` · ${b.year}` : ""}${b.isbn ? ` · ISBN ${esc(b.isbn)}` : ""}<br>
        ${esc(b.category)}${b.subject ? ` · ${esc(b.subject)}` : ""}${b.shelf ? ` · shelf ${esc(b.shelf)}` : ""}</p>
      ${manage ? `<div class="row" style="gap:8px;flex-wrap:wrap;margin-bottom:12px"><button class="btn sm" id="b-edit">Edit</button><button class="btn sm" id="b-copies">${icon("plus")} Add copies</button><button class="btn sm" id="b-class">Issue to a class</button>${approve && !b.history.length ? `<button class="btn sm danger" id="b-del">Delete</button>` : ""}</div>` : ""}
      <h3 style="margin:8px 0">Copies</h3><div id="b-copy-t"></div>
      <h3 style="margin:16px 0 8px">Recent loans</h3><div id="b-hist"></div>` });
    table(m.el.querySelector("#b-copy-t"), { rows: b.copy_list, empty: "No copies.", columns: [
      { key: "accession_no", label: "Accession no." },
      { key: "status", label: "Status", render: (c) => `${st(c.loan ? c.loan.status : c.status)}${c.loan ? `<br><span class="muted small">${esc(c.loan.borrower)}, due ${fmtDate(c.loan.due_on)}</span>` : ""}` },
      { key: "condition", label: "Condition" },
      { key: "replacement", label: "Replacement", num: true, render: (c) => money(c.replacement, c.currency) },
      ...(manage ? [{ key: "id", label: "", sort: false, cls: "actions", render: (c) => c.status === "available" ? `<button class="btn sm" data-copy="${c.id}" data-to="withdrawn">Withdraw</button>` : c.status === "withdrawn" || c.status === "lost" ? `<button class="btn sm" data-copy="${c.id}" data-to="available">${c.status === "lost" ? "Found" : "Put back"}</button>` : "" }] : []),
    ] });
    table(m.el.querySelector("#b-hist"), { rows: b.history, empty: "Never borrowed yet.", columns: [
      { key: "borrower", label: "Borrower", render: borrowerCell }, { key: "accession_no", label: "Copy" },
      { key: "issued_on", label: "Issued", render: (l) => fmtDate(l.issued_on) }, { key: "status", label: "Status", render: (l) => st(l.status) }] });
    const again = async () => { m.close(); await reload(); openBook(id, reload, meta); refreshStats(); };
    m.el.querySelectorAll("[data-copy]").forEach((btn) => (btn.onclick = async () => {
      try { await api.put(`/library/copies/${btn.dataset.copy}`, { status: btn.dataset.to }); toast("Copy updated", "success"); again(); } catch (err) { handleError(err); }
    }));
    m.el.querySelector("#b-edit")?.addEventListener("click", async () => {
      if (await formModal({ title: "Edit book", values: b, fields: bookFields(meta, false), onSubmit: (d) => api.put(`/library/books/${id}`, d) })) { toast("Saved", "success"); again(); }
    });
    m.el.querySelector("#b-copies")?.addEventListener("click", async () => {
      const r = await formModal({ title: `Add copies · ${b.title}`, cols: 1, fields: [
        { name: "count", label: "Number of copies", type: "number", min: 0, max: 500, default: 1 },
        { name: "replacement_cost", label: "Replacement cost (each)", type: "number", min: 0, step: 0.01, default: b.copy_list[0]?.replacement || 0 },
        ...currencyField({ label: "Currency of the cost", default: b.copy_list[0]?.currency || baseCurrency() }),
        { name: "condition", label: "Condition", type: "select", options: ["New", "Good", "Fair", "Poor"], default: "New" },
        { name: "accession_numbers", label: "Accession numbers (optional)", type: "textarea", hint: "One per line; leave empty to number automatically" }],
        onSubmit: (d) => api.post(`/library/books/${id}/copies`, d) });
      if (r) { toast("Copies added", "success"); again(); }
    });
    m.el.querySelector("#b-class")?.addEventListener("click", async () => {
      const r = await formModal({ title: `Issue to a class · ${b.title}`, cols: 1, submitText: "Issue",
        intro: `<p style="margin-top:0" class="muted">Lends one copy to every active student in the class who doesn't already have this book, in accession-number order. Usually textbooks for the term (due at the end of term). ${b.available} cop${b.available === 1 ? "y is" : "ies are"} on the shelf.</p>`,
        fields: [{ name: "class_id", label: "Class", type: "select", required: true, options: classOptions() },
          { name: "due_on", label: "Due back", type: "date", hint: "Leave empty for the end of the current term" }],
        onSubmit: (d) => api.post("/library/issue-class", { ...d, book_id: id }) });
      if (!r) return;
      toast(`Issued ${r.issued.length}${r.already ? ` (${r.already} already had it)` : ""}${r.skipped.length ? `; ${r.skipped.length} not issued: ${r.skipped[0].reason}` : ""}`, r.skipped.length ? "" : "success");
      again();
    });
    m.el.querySelector("#b-del")?.addEventListener("click", async () => {
      if (!(await confirmDialog(`Delete "${b.title}" and its ${b.copy_list.length} copies?`, { danger: true, confirmText: "Delete" }))) return;
      try { await api.del(`/library/books/${id}`); m.close(); toast("Deleted", "success"); reload(); refreshStats(); } catch (err) { handleError(err); }
    });
  }

  // ---------------------------------------------------------------- rules
  async function rules() {
    actions.innerHTML = "";
    const { settings: s, labels, currency } = await api.get("/library/settings");
    const fields = Object.keys(s).map((k) => k === "fine_per_day"
      ? { name: k, label: `Fine per day late (${currency})`, type: "number", min: 0, step: 0.01, hint: "0 = no fines for late returns", value: s[k] / 100 }
      : { name: k, label: labels[k], type: "number", min: k.endsWith("_days") ? 1 : 0, step: 1, value: s[k] });
    pane.innerHTML = `<div class="grid g-2"><div class="card"><div class="card-head"><h3>Borrowing rules</h3></div><div class="card-body">
      <form class="form" id="rules"><div class="cols">${fields.map((f) => `<label class="field"><span>${esc(f.label)}</span><input class="input" type="number" name="${f.name}" min="${f.min}" step="${f.step}" value="${f.value}">${f.hint ? `<small class="hint">${esc(f.hint)}</small>` : ""}<small class="err" data-err="${f.name}"></small></label>`).join("")}</div>
      <div class="row"><button class="btn primary">Save rules</button></div></form></div></div>
      <div class="card"><div class="card-head"><h3>How the library works</h3></div><div class="card-body small">
        <p style="margin-top:0"><b>Labels.</b> Every copy has an accession number (LIB-00001...). Stick it on the book; the desk finds a book by typing or scanning it (a USB barcode scanner types the number and presses Enter).</p>
        <p><b>Lending.</b> Nobody can borrow more while they have an overdue book. Textbooks issued to a whole class don't count towards the limit.</p>
        <p><b>Fines.</b> Late returns are fined per day; a lost book costs its replacement value. A student's fine can be added to their fees invoice for the term (paid like any fee and booked to Sundry Income), marked paid, or waived with a reason.</p>
        <p style="margin-bottom:0"><b>Who can do what.</b> Library permissions are set under Users &amp; Permissions: view, manage (issue, return, add books) and approve (waive fines, delete books, change these rules). Create a "Librarian" role with Library manage for the school librarian.</p></div></div></div>`;
    pane.querySelector("#rules").onsubmit = async (e) => {
      e.preventDefault();
      const f = e.target, data = {};
      for (const k of Object.keys(s)) data[k] = k === "fine_per_day" ? Math.round(Number(f[k].value || 0) * 100) : f[k].value;
      try { await api.put("/library/settings", data); toast("Rules saved", "success"); } catch (err) { handleError(err); }
    };
  }

  const views = { desk, loans, catalogue, rules };
  el.querySelectorAll("[data-tab]").forEach((b) => (b.onclick = () => {
    el.querySelectorAll("[data-tab]").forEach((x) => x.classList.toggle("active", x === b));
    tab = b.dataset.tab;
    views[tab]().catch(handleError);
  }));
  await refreshStats();
  await views[tab]();
}
