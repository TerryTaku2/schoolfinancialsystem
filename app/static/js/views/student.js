import { api } from "../api.js";
import { navigate, rerender } from "../app.js";
import { badge, confirmDialog, esc, fmtDate, formModal, has, icon, isDual, money, moneyList, pct, selectHtml, state, table, termOptions, toast } from "../ui.js";
import { openInvoice, openReceipt, openReportCard, openStatement, reportCardHtml } from "./docs.js";
import { recordPaymentModal } from "./payments.js";
import { STUDENT_FIELDS } from "./students.js";

export default async function (el, [id]) {
  const s = await api.get(`/students/${id}`);
  const fin = !!s.account;
  const tabs = [["overview", "Overview"], ["attendance", "Attendance"], ["academics", "Academics"], ...(fin ? [["finance", "Finance"]] : [])];
  el.innerHTML = `
    <div class="page-head"><div><a href="#/students" class="small">${icon("back").replace("<svg", '<svg style="width:14px;height:14px;vertical-align:-2px"')} Students</a>
      <h1 style="margin-top:6px">${esc(s.name)} ${badge(s.status)}</h1><p>${esc(s.admission_no)} · ${esc(s.class || "No class")} · ${esc(s.gender)}, ${s.age} yrs</p></div>
      <div class="page-actions">
        ${has("payments.manage") && fin ? `<button class="btn primary" id="pay">${icon("payments")} Record payment</button>` : ""}
        ${has("students.manage") ? `<button class="btn" id="edit">Edit</button><button class="btn danger" id="del">Delete</button>` : ""}
      </div></div>
    <div class="tabs" role="tablist">${tabs.map(([k, l], i) => `<button role="tab" data-tab="${k}" class="${i === 0 ? "active" : ""}">${l}</button>`).join("")}</div>
    <div id="tab"></div>`;

  const panes = {
    overview: () => {
      const g = s.guardian_detail;
      return `<div class="grid g-2">
        <div class="card"><div class="card-head"><h3>Student details</h3></div><div class="card-body"><dl class="kv">
          <dt>Admission no.</dt><dd>${esc(s.admission_no)}</dd><dt>Date of birth</dt><dd>${fmtDate(s.dob)} (${s.age} yrs)</dd>
          <dt>Class</dt><dd>${esc(s.class || "—")}</dd><dt>Admitted</dt><dd>${fmtDate(s.admission_date)}</dd>
          <dt>Address</dt><dd>${esc(s.address || "—")}</dd><dt>Medical notes</dt><dd>${esc(s.medical_notes || "None recorded")}</dd></dl></div></div>
        <div class="card"><div class="card-head"><h3>Guardian</h3></div><div class="card-body">${g ? `<dl class="kv">
          <dt>Name</dt><dd>${esc(g.name)}</dd><dt>Relationship</dt><dd>${esc(g.relationship || "—")}</dd>
          <dt>Phone</dt><dd><a href="tel:${esc(g.phone)}">${esc(g.phone)}</a></dd><dt>Email</dt><dd>${g.email ? `<a href="mailto:${esc(g.email)}">${esc(g.email)}</a>` : "—"}</dd>
          <dt>Address</dt><dd>${esc(g.address || "—")}</dd></dl>` : `<span class="muted">No guardian linked.</span>`}</div></div>
        ${s.attendance ? `<div class="card"><div class="card-head"><h3>This term at a glance</h3></div><div class="card-body grid g-3">
          <div><div class="muted small">Attendance</div><div style="font-size:22px;font-weight:650">${pct(s.attendance.rate)}</div></div>
          <div><div class="muted small">Days absent</div><div style="font-size:22px;font-weight:650">${s.attendance.absent}</div></div>
          ${fin ? `<div><div class="muted small">Fee balance</div><div style="font-size:22px;font-weight:650;color:${s.account.by_currency.some((x) => x.balance > 0) ? "var(--bad)" : "var(--good)"}">${moneyList(s.account.by_currency.map((x) => ({ currency: x.currency, amount: x.balance })))}</div></div>` : ""}
        </div></div>` : ""}
      </div>`;
    },
    attendance: () => `<div class="grid g-2">
      <div class="card"><div class="card-head"><h3>Current term summary</h3></div><div class="card-body">${s.attendance ? `<dl class="kv">
        <dt>Days recorded</dt><dd>${s.attendance.days}</dd><dt>Present</dt><dd>${s.attendance.present}</dd><dt>Late</dt><dd>${s.attendance.late}</dd>
        <dt>Absent</dt><dd>${s.attendance.absent}</dd><dt>Excused</dt><dd>${s.attendance.excused}</dd><dt>Attendance rate</dt><dd><b>${pct(s.attendance.rate)}</b></dd></dl>
        ${s.attendance.rate !== null && s.attendance.rate < state.meta.attendance_threshold ? `<div class="notice bad" style="margin-top:12px">Below the required ${state.meta.attendance_threshold}% attendance.</div>` : ""}` : `<span class="muted">No current term.</span>`}</div></div>
      <div class="card"><div class="card-head"><h3>Last 10 records</h3></div><ul class="list">${s.recent_attendance.map((a) => `<li><span>${fmtDate(a.date, { weekday: "short", day: "numeric", month: "short" })}${a.remark ? `<br><span class="muted small">${esc(a.remark)}</span>` : ""}</span>${badge(a.status)}</li>`).join("") || `<li class="muted">No attendance recorded.</li>`}</ul></div></div>`,
    academics: () => `<div class="card"><div class="card-head"><h3>Report card</h3><div class="row">${selectHtml("term", termOptions(), state.meta.current_term?.id)}<button class="btn" id="print-rc">${icon("print")} Open printable</button></div></div>
      <div class="card-body" id="rc"><div class="empty">Loading…</div></div></div>`,
    finance: () => `<div class="stack">
      ${s.account.by_currency.map((a) => `${s.account.by_currency.length > 1 ? `<h3 style="margin:0">${esc(a.currency)} account</h3>` : ""}<div class="grid g-4">${[["Total billed", a.billed], ["Total paid", a.paid], ["Overdue", a.overdue], ["Balance", a.balance]].map(([l, v]) =>
        `<div class="card stat"><div class="label">${l}</div><div class="value" style="${l === "Balance" && v > 0 ? "color:var(--bad)" : ""}">${money(v, a.currency)}</div>${l === "Balance" && a.credit > 0 ? `<div class="foot">${money(a.credit, a.currency)} credit on account</div>` : ""}</div>`).join("")}</div>`).join("")}
      <div class="card"><div class="card-head"><h3>Invoices</h3><button class="btn sm" id="stmt">${icon("print")} Statement</button></div><div id="inv"></div></div>
      <div class="card"><div class="card-head"><h3>Payments</h3></div><div id="pay-t"></div></div>
      <div class="card"><div class="card-head"><div><h3>Scholarships</h3><div class="sub">Applied as a discount to tuition when invoices are generated</div></div>${has("fees.manage") ? `<button class="btn sm" id="add-sch">${icon("plus")} Add</button>` : ""}</div>
        <ul class="list">${s.scholarships.map((x) => `<li><span><b>${esc(x.name)}</b> · ${x.percent}%</span><span class="row">${badge(x.active ? "active" : "void", x.active ? "active" : "inactive")}${has("fees.manage") ? `<button class="btn sm" data-sch="${x.id}" data-active="${x.active}">${x.active ? "Deactivate" : "Activate"}</button>` : ""}</span></li>`).join("") || `<li class="muted">None.</li>`}</ul></div>
    </div>`,
  };

  const wire = {
    academics: async (pane) => {
      const load = async () => {
        const rc = pane.querySelector("#rc");
        try {
          rc.innerHTML = reportCardHtml(await api.get(`/report-card/${s.id}`, { term_id: pane.querySelector('[name="term"]').value }));
        } catch (err) { rc.innerHTML = `<div class="empty">${esc(err.message)}</div>`; }
      };
      pane.querySelector('[name="term"]').onchange = load;
      pane.querySelector("#print-rc").onclick = () => openReportCard(s.id, pane.querySelector('[name="term"]').value);
      load();
    },
    finance: (pane) => {
      table(pane.querySelector("#inv"), {
        rows: s.invoices, onRowClick: (r) => openInvoice(r.id), empty: "No invoices yet.",
        columns: [{ key: "invoice_no", label: "Invoice" }, { key: "term", label: "Term" }, { key: "due_date", label: "Due", render: (r) => fmtDate(r.due_date) },
          { key: "total", label: "Total", num: true, render: (r) => money(r.total, r.currency) }, { key: "paid", label: "Paid", num: true, render: (r) => money(r.paid, r.currency) },
          { key: "balance", label: "Balance", num: true, render: (r) => money(r.balance, r.currency) }, { key: "status", label: "Status", render: (r) => badge(r.status) }],
      });
      table(pane.querySelector("#pay-t"), {
        rows: s.payments, onRowClick: (r) => openReceipt(r.id), empty: "No payments yet.",
        columns: [{ key: "receipt_no", label: "Receipt" }, { key: "paid_on", label: "Date", render: (r) => fmtDate(r.paid_on) },
          { key: "method", label: "Method", render: (r) => `<span style="text-transform:capitalize">${esc(r.method)}</span>` }, { key: "reference", label: "Reference" },
          { key: "amount", label: "Amount", num: true, render: (r) => money(r.amount, r.currency) }, { key: "void", label: "", render: (r) => (r.void ? badge("void") : "") }],
      });
      pane.querySelector("#stmt").onclick = () => openStatement(s.id);
      pane.querySelector("#add-sch")?.addEventListener("click", async () => {
        const ok = await formModal({
          title: "Add scholarship", cols: 1,
          fields: [{ name: "name", label: "Scholarship name", required: true, placeholder: "e.g. Academic merit" },
            { name: "percent", label: "Discount %", type: "number", min: 1, max: 100, required: true, hint: "Applies to future invoices only" }],
          onSubmit: (d) => api.post(`/students/${s.id}/scholarships`, d),
        });
        if (ok) { toast("Scholarship added", "success"); rerender(); }
      });
      pane.querySelectorAll("[data-sch]").forEach((b) => (b.onclick = async () => {
        await api.put(`/scholarships/${b.dataset.sch}`, { active: b.dataset.active !== "true" });
        rerender();
      }));
    },
  };

  const show = (k) => {
    el.querySelectorAll("[data-tab]").forEach((b) => b.classList.toggle("active", b.dataset.tab === k));
    const pane = el.querySelector("#tab");
    pane.innerHTML = panes[k]();
    wire[k]?.(pane);
  };
  el.querySelectorAll("[data-tab]").forEach((b) => (b.onclick = () => show(b.dataset.tab)));
  show("overview");

  el.querySelector("#pay")?.addEventListener("click", async () => {
    const p = await recordPaymentModal({ id: s.id, name: s.name, balance: s.account.balance,
      balances: Object.fromEntries(s.account.by_currency.map((x) => [x.currency, x.balance])) });
    if (p) rerender();
  });
  el.querySelector("#edit")?.addEventListener("click", async () => {
    const r = await formModal({
      title: `Edit ${s.name}`, fields: STUDENT_FIELDS(false), values: s, wide: true,
      onSubmit: (d) => api.put(`/students/${s.id}`, d),
    });
    if (r) { toast("Student updated", "success"); rerender(); }
  });
  el.querySelector("#del")?.addEventListener("click", async () => {
    if (!(await confirmDialog(`Permanently delete ${s.name}? Students with any history must be withdrawn instead.`, { danger: true, confirmText: "Delete" }))) return;
    try { await api.del(`/students/${s.id}`); toast("Student deleted", "success"); navigate("#/students"); }
    catch (err) { toast(err.message, "error"); }
  });
}
