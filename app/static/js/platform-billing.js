// Platform console: subscriptions. Bill schools per term, record EcoCash/cash payments,
// confirm payments schools report, and send WhatsApp reminders.
import { badge, confirmDialog, esc, fmtDate, formModal, handleError, icon, modal, money, selectHtml, table, toast } from "./ui.js";

const STATUS = { unpaid: ["unpaid", "due"], partial: ["partial", "part paid"], overdue: ["overdue", "overdue"], paid: ["paid", "paid"], void: ["void", "void"] };
const today = () => new Date().toISOString().slice(0, 10);

export async function billingTab(el, call) {
  let status = "open";
  el.innerHTML = `<div class="grid g-4" id="b-stats" style="margin-bottom:16px"></div>
    <div class="card"><div class="card-head"><div class="row" style="gap:8px;flex-wrap:wrap">
      ${selectHtml("b-status", [["open", "To collect"], ["overdue", "Overdue"], ["paid", "Paid"], ["all", "All invoices"]].map(([value, label]) => ({ value, label })), status)}</div>
      <div class="row" style="gap:8px"><button class="btn" id="b-one">${icon("plus")} Bill one school</button><button class="btn primary" id="b-run">Bill a term</button></div></div>
      <div id="b-tbl"></div></div>`;

  const load = async () => {
    const r = await call("GET", `/billing/invoices?status=${status}`);
    const t = r.totals, cur = t.currency;
    el.querySelector("#b-stats").innerHTML = `
      <div class="card stat"><div class="label">Billed this year</div><div class="value">${money(t.billed, cur)}</div></div>
      <div class="card stat"><div class="label">Collected this year</div><div class="value">${money(t.collected, cur)}</div></div>
      <div class="card stat"><div class="label">Outstanding</div><div class="value" style="${t.overdue ? "color:var(--bad)" : ""}">${money(t.outstanding, cur)}</div><div class="foot">${t.overdue} overdue</div></div>
      <div class="card stat"><div class="label">Payments to confirm</div><div class="value" style="${t.pending ? "color:var(--warn)" : ""}">${t.pending}</div><div class="foot">Reported by schools (EcoCash)</div></div>`;
    table(el.querySelector("#b-tbl"), {
      rows: r.items, empty: status === "open" ? "Nothing to collect. Use “Bill a term” at the start of each term." : "No invoices.",
      columns: [
        { key: "school", label: "School", render: (i) => `<b>${esc(i.school)}</b><br><span class="muted small">${esc(i.number)} · ${esc(i.period)}</span>` },
        { key: "learners", label: "Learners", num: true },
        { key: "amount", label: "Amount", num: true, render: (i) => money(i.amount, i.currency) },
        { key: "balance", label: "Balance", num: true, render: (i) => money(i.balance, i.currency) },
        { key: "due_on", label: "Due", render: (i) => fmtDate(i.due_on) },
        { key: "status", label: "Status", render: (i) => `${badge(...STATUS[i.status])}${i.pending ? `<br>${badge("pending", `${i.pending} to confirm`)}` : ""}` },
        { key: "id", label: "", sort: false, cls: "actions", render: (i) => i.status === "void" ? "" : `<button class="btn sm" data-open="${i.id}">Open</button>${i.status !== "paid" ? ` <button class="btn sm primary" data-pay="${i.id}">Record payment</button> <button class="btn sm" data-remind="${i.id}">Remind</button>` : ""}` },
      ] });
    const find = (id) => r.items.find((x) => x.id === +id);
    el.querySelectorAll("[data-open]").forEach((b) => (b.onclick = () => openInvoice(find(b.dataset.open))));
    el.querySelectorAll("[data-pay]").forEach((b) => (b.onclick = () => recordPayment(find(b.dataset.pay))));
    el.querySelectorAll("[data-remind]").forEach((b) => (b.onclick = () => remind(find(b.dataset.remind))));
  };

  async function recordPayment(inv) {
    const r = await formModal({ title: `Record payment · ${inv.school}`, cols: 1, submitText: "Record",
      intro: `<p class="muted" style="margin-top:0">${esc(inv.number)} · ${esc(inv.period)} · balance ${money(inv.balance, inv.currency)}</p>`,
      fields: [{ name: "method", label: "Paid by", type: "select", required: true, options: [{ value: "ecocash", label: "EcoCash" }, { value: "cash", label: "Cash" }, { value: "bank", label: "Bank transfer" }], default: "ecocash" },
        { name: "amount", label: `Amount (${inv.currency})`, type: "number", min: 0.01, step: 0.01, required: true, default: inv.balance },
        { name: "reference", label: "EcoCash transaction ID / receipt no.", hint: "Required for EcoCash" },
        { name: "paid_on", label: "Date received", type: "date", required: true, default: today(), max: today() },
        { name: "note", label: "Note" }],
      onSubmit: (d) => call("POST", `/billing/invoices/${inv.id}/payments`, d) });
    if (r) { toast(r.status === "paid" ? `${inv.school}: paid in full` : "Payment recorded", "success"); load(); }
  }

  async function remind(inv) {
    let r;
    try { r = await call("GET", `/billing/invoices/${inv.id}/reminder`); } catch (err) { return handleError(err); }
    const m = modal({ title: `Reminder · ${inv.school}`, body: `
      <p class="muted" style="margin-top:0">${r.phone ? `Opens WhatsApp with this message for ${esc(r.phone)}.` : "No contact phone saved for this school: WhatsApp will ask you to choose the chat. Add one under Schools → Billing."}</p>
      <textarea class="input" readonly rows="8" style="width:100%">${esc(r.text)}</textarea>`,
      actions: [{ label: "Copy text", onClick: async () => { await navigator.clipboard?.writeText(r.text); toast("Copied", "success"); return true; } },
        ...(r.email ? [{ label: "Email", onClick: () => { location.href = `mailto:${r.email}?subject=${encodeURIComponent(`Subscription ${inv.number}`)}&body=${encodeURIComponent(r.text)}`; } }] : []),
        { label: "Open WhatsApp", cls: "primary", onClick: () => { window.open(r.whatsapp, "_blank", "noopener"); } }] });
    return m;
  }

  async function openInvoice(inv) {
    const m = modal({ title: `${inv.number} · ${inv.school}`, wide: true, body: `
      <dl class="kv"><dt>Period</dt><dd>${esc(inv.period)}</dd><dt>Learners</dt><dd>${inv.learners} × ${money(inv.rate, inv.currency)}</dd>
        <dt>Amount</dt><dd>${money(inv.amount, inv.currency)}</dd><dt>Paid</dt><dd>${money(inv.paid, inv.currency)}</dd>
        <dt>Due</dt><dd>${fmtDate(inv.due_on)}</dd><dt>Status</dt><dd>${badge(...STATUS[inv.status])}</dd>${inv.notes ? `<dt>Notes</dt><dd>${esc(inv.notes)}</dd>` : ""}</dl>
      <h3 style="margin:16px 0 8px">Payments</h3><div id="pay-t"></div>
      ${inv.status !== "paid" && !inv.payments.some((p) => p.status === "confirmed") ? `<div class="row" style="margin-top:14px"><button class="btn sm danger" id="void">Void invoice</button></div>` : ""}` });
    table(m.el.querySelector("#pay-t"), { rows: inv.payments, empty: "No payments yet.", columns: [
      { key: "paid_on", label: "Date", render: (p) => fmtDate(p.paid_on) },
      { key: "method", label: "Method", render: (p) => (p.method === "ecocash" ? "EcoCash" : p.method === "cash" ? "Cash" : "Bank") },
      { key: "reference", label: "Reference" },
      { key: "amount", label: "Amount", num: true, render: (p) => money(p.amount, inv.currency) },
      { key: "status", label: "Status", render: (p) => `${badge(p.status === "confirmed" ? "paid" : p.status === "pending" ? "pending" : "void", p.status)}${p.reported_by ? `<br><span class="muted small">by ${esc(p.reported_by)}</span>` : ""}` },
      { key: "id", label: "", sort: false, cls: "actions", render: (p) => p.status === "pending" ? `<button class="btn sm primary" data-ok="${p.id}">Confirm</button> <button class="btn sm danger" data-no="${p.id}">Reject</button>` : "" }] });
    const review = async (id, action) => {
      let reason;
      if (action === "reject") {
        const d = await formModal({ title: "Reject payment", cols: 1, fields: [{ name: "reason", label: "Reason", required: true, placeholder: "e.g. transaction ID not found on the EcoCash statement" }], onSubmit: (x) => x });
        if (!d) return;
        reason = d.reason;
      }
      try { await call("POST", `/billing/payments/${id}/${action}`, { reason }); toast(action === "confirm" ? "Payment confirmed" : "Payment rejected", "success"); m.close(); load(); }
      catch (err) { handleError(err); }
    };
    m.el.querySelectorAll("[data-ok]").forEach((b) => (b.onclick = () => review(b.dataset.ok, "confirm")));
    m.el.querySelectorAll("[data-no]").forEach((b) => (b.onclick = () => review(b.dataset.no, "reject")));
    m.el.querySelector("#void")?.addEventListener("click", async () => {
      const d = await formModal({ title: `Void ${inv.number}`, cols: 1, fields: [{ name: "reason", label: "Reason", required: true }], onSubmit: (x) => call("POST", `/billing/invoices/${inv.id}/void`, x) });
      if (d) { toast("Invoice voided", "success"); m.close(); load(); }
    });
  }

  el.querySelector('[name="b-status"]').onchange = (e) => { status = e.target.value; load(); };
  el.querySelector("#b-run").onclick = async () => {
    const r = await formModal({ title: "Bill a term", cols: 1, submitText: "Create invoices",
      intro: `<p class="muted" style="margin-top:0">Creates one invoice for every active school (except free ones) from its number of active learners today. Schools already billed for this period are skipped. Their administrators and bursars see the notice in the app from 14 days before the due date (change this in Payment settings).</p>`,
      fields: [{ name: "period", label: "Period", required: true, placeholder: "e.g. Term 1 2027" },
        { name: "due_on", label: "Due date", type: "date", required: true }],
      onSubmit: (d) => call("POST", "/billing/run", d) });
    if (!r) return;
    toast(`${r.created.length} invoice${r.created.length === 1 ? "" : "s"} created${r.skipped.length ? `, ${r.skipped.length} skipped` : ""}`, "success");
    if (r.skipped.length) modal({ title: "Skipped", body: `<ul class="list">${r.skipped.map((s) => `<li><span>${esc(s.school)}</span><span class="muted">${esc(s.reason)}</span></li>`).join("")}</ul>`, actions: [{ label: "OK", cls: "primary" }] });
    status = "open";
    load();
  };
  el.querySelector("#b-one").onclick = async () => {
    const schools = (await call("GET", "/schools")).items.filter((s) => s.status === "active");
    const r = await formModal({ title: "Bill one school", cols: 1, submitText: "Create invoice",
      intro: `<p class="muted" style="margin-top:0">For a school that joined mid-term, a negotiated price or a setup fee. Leave learners and amount empty to use the school's price.</p>`,
      fields: [{ name: "school_id", label: "School", type: "select", required: true, options: schools.map((s) => ({ value: s.id, label: s.name })) },
        { name: "period", label: "Period / description", required: true, placeholder: "e.g. Term 1 2027, or Setup and training" },
        { name: "due_on", label: "Due date", type: "date", required: true },
        { name: "learners", label: "Learners", type: "number", min: 0, hint: "Empty: count active learners now" },
        { name: "amount", label: "Amount", type: "number", min: 0, step: 0.01, hint: "Empty: learners × price (at least the minimum)" },
        { name: "notes", label: "Notes" }],
      onSubmit: (d) => call("POST", "/billing/invoices", d) });
    if (r) { toast(`${r.number} created for ${r.school}`, "success"); load(); }
  };
  await load();
}

export async function paymentSettingsTab(el, call) {
  const { settings: s } = await call("GET", "/billing/settings");
  const fields = [
    { name: "currency", label: "Billing currency", type: "select", options: ["USD", "ZWG", "ZAR"], value: s.currency },
    { name: "default_rate", label: "Price per learner per term", type: "number", step: 0.01, min: 0, value: s.default_rate_cents / 100 },
    { name: "default_minimum", label: "Minimum per term", type: "number", step: 0.01, min: 0, value: s.default_minimum_cents / 100 },
    { name: "notice_days", label: "Show the notice in schools from (days before due)", type: "number", step: 1, min: 0, value: s.notice_days },
    { name: "ecocash_number", label: "Your EcoCash number", value: s.ecocash_number, placeholder: "e.g. 0771 234 567" },
    { name: "ecocash_name", label: "Name on the EcoCash account", value: s.ecocash_name },
    { name: "cash_instructions", label: "How to pay in cash", value: s.cash_instructions, placeholder: "e.g. at our office, 12 Main St, Harare, Mon-Fri 8-5" },
    { name: "support_phone", label: "Support phone / WhatsApp", value: s.support_phone },
  ];
  el.innerHTML = `<div class="grid g-2"><div class="card"><div class="card-head"><h3>Prices and payment details</h3></div><div class="card-body">
    <form class="form" id="ps"><div class="cols">${fields.map((f) => `<label class="field"><span>${esc(f.label)}</span>${f.type === "select"
      ? selectHtml(f.name, f.options.map((o) => ({ value: o, label: o })), f.value)
      : `<input class="input" name="${f.name}" type="${f.type || "text"}" ${f.step ? `step="${f.step}"` : ""} ${f.min !== undefined ? `min="${f.min}"` : ""} value="${esc(f.value ?? "")}" placeholder="${esc(f.placeholder || "")}">`}<small class="err" data-err="${f.name}"></small></label>`).join("")}</div>
    <div class="row"><button class="btn primary">Save</button></div></form></div></div>
    <div class="card"><div class="card-head"><h3>How billing works</h3></div><div class="card-body small">
      <p style="margin-top:0"><b>Each term:</b> open Billing and click <b>Bill a term</b>. Every active school gets an invoice: active learners × its price, at least the minimum. Give a school its own price, or mark it free, under Schools → Billing.</p>
      <p><b>Schools are told in the app.</b> Administrators and bursars see a notice with your EcoCash number and the invoice reference, from the number of days set here before the due date; it turns red when overdue.</p>
      <p><b>Getting paid:</b> when a school pays by EcoCash it enters the transaction ID under Subscription; check it against your EcoCash messages and <b>Confirm</b> it. Cash and other payments you record yourself with <b>Record payment</b>.</p>
      <p style="margin-bottom:0"><b>Reminders:</b> <b>Remind</b> opens WhatsApp with a ready message. A school that doesn't pay can be suspended under Schools; its data is kept.</p></div></div></div>`;
  el.querySelector("#ps").onsubmit = async (e) => {
    e.preventDefault();
    const f = e.target;
    const data = Object.fromEntries(fields.map((x) => [x.name, f[x.name].value.trim()]));
    data.default_rate_cents = Math.round(Number(data.default_rate || 0) * 100);
    data.default_minimum_cents = Math.round(Number(data.default_minimum || 0) * 100);
    delete data.default_rate; delete data.default_minimum;
    try { await call("PUT", "/billing/settings", data); toast("Saved", "success"); } catch (err) { handleError(err); }
  };
}

export async function editSchoolBilling(s, call, defaults) {
  const b = s.billing || {};
  return formModal({ title: `Billing · ${s.name}`, cols: 2, values: { ...b, rate: b.rate ?? "", minimum: b.minimum ?? "" },
    intro: `<p class="muted" style="margin-top:0">Leave the price empty to use the default (${money(defaults.default_rate_cents / 100, defaults.currency)} per learner, minimum ${money(defaults.default_minimum_cents / 100, defaults.currency)} per term).</p>`,
    fields: [{ name: "rate", label: "Price per learner per term", type: "number", min: 0, step: 0.01 },
      { name: "minimum", label: "Minimum per term", type: "number", min: 0, step: 0.01 },
      { name: "free", label: "Free: don't bill this school (e.g. a pilot)", type: "checkbox", full: true },
      { type: "heading", name: "_c", label: "Billing contact" },
      { name: "contact_name", label: "Name", placeholder: "e.g. the bursar" },
      { name: "contact_phone", label: "Phone / WhatsApp", placeholder: "e.g. 0771 234 567" },
      { name: "contact_email", label: "Email", type: "email" }],
    onSubmit: (d) => call("PUT", `/schools/${s.id}/billing`, d) });
}
