// The school's subscription: what is due, how to pay (EcoCash or cash), and "I've paid".
import { api } from "../api.js";
import { refreshSubscription } from "../app.js";
import { badge, esc, fmtDate, formModal, money, table, toast, today } from "../ui.js";

const STATUS = { unpaid: ["unpaid", "due"], partial: ["partial", "part paid"], overdue: ["overdue", "overdue"], paid: ["paid", "paid"], void: ["void", "cancelled"] };

/** How to pay, as shown in the banner and on the page. */
export function howToPay(pay, inv) {
  const parts = [];
  if (pay.ecocash_number) parts.push(`EcoCash to <b>${esc(pay.ecocash_number)}</b>${pay.ecocash_name ? ` (${esc(pay.ecocash_name)})` : ""}, reference <b>${esc(inv.number)}</b>`);
  if (pay.cash_instructions) parts.push(`cash: ${esc(pay.cash_instructions)}`);
  return parts.length ? `Pay by ${parts.join(", or ")}.` : "Contact the system provider to pay.";
}

export default async function (el) {
  el.innerHTML = `<div class="page-head"><div><h1>Subscription</h1><p>The school's subscription for using this system</p></div></div><div id="body"><div class="empty">Loading…</div></div>`;
  const draw = (s) => {
    const box = el.querySelector("#body");
    if (!s.enabled) { box.innerHTML = `<div class="empty">There is nothing to pay here.</div>`; return; }
    const open = s.invoices;
    box.innerHTML = `
      ${open.length ? open.map((inv) => `<div class="card" style="margin-bottom:16px"><div class="card-head"><div><h3>${esc(inv.period)} · ${esc(inv.number)}</h3>
          <div class="sub">${inv.learners} learners${inv.rate ? ` × ${money(inv.rate, inv.currency)}` : ""} · due ${fmtDate(inv.due_on)}</div></div>${badge(...STATUS[inv.status])}</div>
        <div class="card-body"><div class="grid g-3" style="margin-bottom:12px">
          <div><div class="muted small">Amount</div><div style="font-size:22px;font-weight:650">${money(inv.amount, inv.currency)}</div></div>
          <div><div class="muted small">Paid</div><div style="font-size:22px;font-weight:650">${money(inv.paid, inv.currency)}</div></div>
          <div><div class="muted small">Balance</div><div style="font-size:22px;font-weight:650;color:${inv.status === "overdue" ? "var(--bad)" : "inherit"}">${money(inv.balance, inv.currency)}</div></div></div>
          <p style="margin:0 0 12px">${howToPay(s.pay, inv)}</p>
          ${inv.payments.filter((p) => p.status === "pending").map((p) => `<div class="notice" style="margin-bottom:10px">Payment of ${money(p.amount, inv.currency)} (${esc(p.method)} ${esc(p.reference || "")}) reported on ${fmtDate(p.paid_on)}, awaiting confirmation.</div>`).join("")}
          <button class="btn primary" data-paid="${inv.id}">I've paid by EcoCash</button></div></div>`).join("")
        : `<div class="notice" style="margin-bottom:16px">Nothing is due. Thank you!</div>`}
      <div class="card"><div class="card-head"><h3>History</h3></div><div id="hist"></div></div>
      ${s.pay.support_phone ? `<p class="muted small" style="margin-top:12px">Questions about the subscription? Call or WhatsApp ${esc(s.pay.support_phone)}.</p>` : ""}`;
    table(box.querySelector("#hist"), { rows: s.history, empty: "No subscription invoices yet.", columns: [
      { key: "number", label: "Invoice" }, { key: "period", label: "Period" },
      { key: "due_on", label: "Due", render: (i) => fmtDate(i.due_on) },
      { key: "amount", label: "Amount", num: true, render: (i) => money(i.amount, i.currency) },
      { key: "paid", label: "Paid", num: true, render: (i) => money(i.paid, i.currency) },
      { key: "status", label: "Status", render: (i) => badge(...STATUS[i.status]) }] });
    box.querySelectorAll("[data-paid]").forEach((b) => (b.onclick = async () => {
      const inv = open.find((i) => i.id === +b.dataset.paid);
      const r = await formModal({ title: `Report a payment · ${inv.number}`, cols: 1, submitText: "Send for confirmation",
        intro: `<p style="margin-top:0" class="muted">Enter the details from the EcoCash confirmation message. The payment counts once the provider confirms it.</p>`,
        fields: [{ name: "amount", label: `Amount paid (${inv.currency})`, type: "number", min: 0.01, step: 0.01, required: true, default: inv.balance },
          { name: "reference", label: "EcoCash transaction ID", required: true, placeholder: "e.g. MP231012.1450.A12345" },
          { name: "paid_on", label: "Date paid", type: "date", required: true, default: today(), max: today() },
          { name: "note", label: "Note (optional)" }],
        onSubmit: (d) => api.post("/subscription/payments", { ...d, invoice_id: inv.id, method: "ecocash" }) });
      if (r) { toast("Thank you. The payment will be confirmed shortly.", "success"); draw(r); refreshSubscription(); }
    }));
  };
  draw(await api.get("/subscription"));
}
