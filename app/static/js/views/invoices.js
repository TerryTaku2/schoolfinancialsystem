import { api } from "../api.js";
import { badge, classOptions, confirmDialog, currencies, debounce, downloadCSV, esc, fmtDate, formModal, handleError, has, icon, isDual, money, pager, selectHtml, state, table, termOptions, toast, today } from "../ui.js";
import { openInvoice } from "./docs.js";

export default async function (el) {
  const office = has("fees.view");
  const f = { term_id: office ? state.meta.current_term?.id || "" : "", class_id: "", status: "", currency: "", q: "", page: 1 };
  el.innerHTML = `
    <div class="page-head"><div><h1>Invoices</h1><p>${office ? "One invoice per student per term, generated from the fee structure" : "Fee invoices for your children"}</p></div>
      <div class="page-actions">${office ? `<button class="btn" id="export">${icon("download")} Export CSV</button><a class="btn primary" href="#/fees">Generate invoices</a>` : ""}</div></div>
    <div class="grid g-3" id="totals" style="margin-bottom:16px"></div>
    <div class="card">
      <div class="toolbar">
        ${office ? `<input class="input search" type="search" id="q" placeholder="Invoice no. or student" aria-label="Search">` : ""}
        ${selectHtml("term_id", termOptions(), f.term_id, { empty: "All terms" })}
        ${office ? selectHtml("class_id", classOptions(), "", { empty: "All classes" }) : ""}
        ${selectHtml("status", ["unpaid", "partial", "overdue", "paid", "void"].map((s) => ({ value: s, label: s[0].toUpperCase() + s.slice(1) })), "", { empty: "Any status" })}
        ${isDual() ? selectHtml("currency", currencies().map((c) => ({ value: c, label: c })), "", { empty: "All currencies" }) : ""}
      </div>
      <div id="tbl"></div><div id="pg"></div>
    </div>`;
  const columns = [
    { key: "invoice_no", label: "Invoice" },
    { key: "student", label: "Student", render: (r) => `<a href="#/student/${r.student_id}">${esc(r.student)}</a><br><span class="muted small">${esc(r.admission_no)} · ${esc(r.class || "")}</span>` },
    { key: "term", label: "Term" },
    { key: "due_date", label: "Due", render: (r) => fmtDate(r.due_date) },
    { key: "total", label: "Total", num: true, render: (r) => money(r.total, r.currency) },
    { key: "paid", label: "Paid", num: true, render: (r) => money(r.paid, r.currency) },
    { key: "balance", label: "Balance", num: true, render: (r) => `<b>${money(r.balance, r.currency)}</b>` },
    ...(isDual() ? [{ key: "currency", label: "Currency" }] : []),
    { key: "status", label: "Status", render: (r) => badge(r.status) },
    { key: "id", label: "", sort: false, cls: "actions", csv: () => "", render: (r) => `<button class="btn sm" data-view="${r.id}">View</button>${has("fees.manage") && r.status !== "void" ? ` <button class="btn sm" data-charge="${r.id}">+ Charge</button> <button class="btn sm danger" data-void="${r.id}">Void</button>` : ""}` },
  ];
  const load = async () => {
    const res = await api.get("/invoices", f);
    // Each currency is totalled on its own; amounts in different currencies are never added.
    const per = Object.entries(res.totals_by_currency || {});
    const line = (k) => (per.length ? per.map(([c, t]) => `<div>${money(t[k], c)}</div>`).join("") : money(0));
    el.querySelector("#totals").innerHTML = [["Invoiced", "total"], ["Collected", "paid"], ["Outstanding", "balance"]]
      .map(([l, k]) => `<div class="card stat"><div class="label">${l}</div><div class="value" style="${per.length > 1 ? "font-size:20px" : ""}">${line(k)}</div><div class="foot">${res.total} invoice(s) in view</div></div>`).join("");
    table(el.querySelector("#tbl"), { columns, rows: res.items, empty: "No invoices match these filters." });
    pager(el.querySelector("#pg"), { page: res.page, perPage: res.per_page, total: res.total, onPage: (p) => { f.page = p; load(); } });
    el.querySelectorAll("[data-view]").forEach((b) => (b.onclick = () => openInvoice(b.dataset.view)));
    el.querySelectorAll("[data-charge]").forEach((b) => (b.onclick = async () => {
      const r = await formModal({
        title: "Add charge to invoice", cols: 1,
        intro: `<p class="muted" style="margin-top:0">For one-off items such as a lost library book or a trip. Existing credit is applied automatically.</p>`,
        fields: [{ name: "description", label: "Description", required: true }, { name: "amount", label: "Amount", type: "number", min: 0.01, step: 0.01, required: true }],
        onSubmit: (d) => api.post(`/invoices/${b.dataset.charge}/charges`, d),
      });
      if (r) { toast("Charge added", "success"); load(); }
    }));
    el.querySelectorAll("[data-void]").forEach((b) => (b.onclick = async () => {
      const reason = await confirmDialog("Voiding cancels this invoice. Any payments already applied return to the student's account as credit.", { title: "Void invoice", confirmText: "Void invoice", danger: true, reason: true });
      if (!reason) return;
      try { await api.post(`/invoices/${b.dataset.void}/void`, { reason }); toast("Invoice voided", "success"); load(); }
      catch (err) { handleError(err); }
    }));
  };
  el.querySelector("#q")?.addEventListener("input", debounce((e) => { f.q = e.target.value; f.page = 1; load(); }));
  el.querySelectorAll(".toolbar select").forEach((s) => (s.onchange = () => { f[s.name] = s.value; f.page = 1; load(); }));
  el.querySelector("#export")?.addEventListener("click", async () => {
    const all = await api.get("/invoices", { ...f, page: 1, per_page: 500 });
    downloadCSV(`invoices-${today()}`, columns.filter((c) => c.key !== "id"), all.items);
  });
  await load();
}
