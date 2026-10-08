import { api } from "../api.js";
import { badge, confirmDialog, currencies, currencyField, downloadCSV, esc, fmtDate, formModal, handleError, has, icon, isDual, money, moneyList, selectHtml, table, toast, today } from "../ui.js";

export default async function (el) {
  const f = { status: "", category: "" };
  let categories = [];
  el.innerHTML = `
    <div class="page-head"><div><h1>Expenses</h1><p>Workflow: requested → approved (by an administrator other than the requester) → paid</p></div>
      <div class="page-actions"><button class="btn" id="export">${icon("download")} Export CSV</button><button class="btn primary" id="add">${icon("plus")} New expense</button></div></div>
    <div class="grid g-4" id="sum" style="margin-bottom:16px"></div>
    <div class="card"><div class="toolbar" id="filters"></div><div id="tbl"></div></div>`;

  const columns = [
    { key: "expense_date", label: "Date", render: (r) => fmtDate(r.expense_date) },
    { key: "category", label: "Category", render: (r) => `${esc(r.category)}<br><span class="muted small">${esc(r.account)}</span>`, csv: (r) => `${r.category} (${r.account})` },
    { key: "description", label: "Description", render: (r) => `${esc(r.description)}${r.vendor ? `<br><span class="muted small">${esc(r.vendor)}</span>` : ""}` },
    { key: "amount", label: "Amount", num: true, render: (r) => money(r.amount, r.currency) },
    ...(isDual() ? [{ key: "currency", label: "Currency" }] : []),
    { key: "requested_by", label: "Requested by" },
    { key: "status", label: "Status", render: (r) => `${badge(r.status)}${r.decision_note ? `<br><span class="muted small">${esc(r.decision_note)}</span>` : ""}${r.paid_from ? `<br><span class="muted small">Paid ${fmtDate(r.paid_at)} from ${esc(r.paid_from)}</span>` : ""}` },
    { key: "id", label: "", sort: false, cls: "actions", csv: () => "", render: (r) => {
      const b = [];
      if (r.status === "pending" && r.can_decide) b.push(`<button class="btn sm primary" data-act="approved" data-id="${r.id}">Approve</button>`, `<button class="btn sm danger" data-act="rejected" data-id="${r.id}">Reject</button>`);
      if (r.status === "pending" && !r.can_decide && has("expenses.approve")) b.push(`<span class="muted small">Needs another approver</span>`);
      if (r.status === "approved" && has("expenses.manage")) b.push(`<button class="btn sm" data-act="paid" data-id="${r.id}">Mark paid</button>`);
      return b.join(" ");
    } },
  ];

  let rows = [];
  const load = async () => {
    const res = await api.get("/expenses", f);
    rows = res.items;
    categories = res.categories;
    if (!el.querySelector("#filters").innerHTML) {
      el.querySelector("#filters").innerHTML = selectHtml("status", ["pending", "approved", "paid", "rejected"].map((s) => ({ value: s, label: s[0].toUpperCase() + s.slice(1) })), "", { empty: "Any status" })
        + selectHtml("category", categories.map((c) => ({ value: c, label: c })), "", { empty: "All categories" });
      el.querySelectorAll("#filters select").forEach((s) => (s.onchange = () => { f[s.name] = s.value; load(); }));
    }
    // Totals per currency: amounts in different currencies are never added together.
    const by = (st) => Object.fromEntries(currencies().map((c) => [c, rows.filter((r) => r.status === st && r.currency === c).reduce((a, r) => a + r.amount, 0)]).filter(([c, v]) => v || c === currencies()[0]));
    el.querySelector("#sum").innerHTML = [["Pending approval", by("pending")], ["Approved, unpaid", by("approved")], ["Paid", by("paid")], ["Rejected", by("rejected")]]
      .map(([l, v]) => `<div class="card stat"><div class="label">${l}</div><div class="value" style="${Object.keys(v).length > 1 ? "font-size:18px" : ""}">${moneyList(v)}</div></div>`).join("");
    table(el.querySelector("#tbl"), { columns, rows, empty: "No expenses recorded." });
    el.querySelectorAll("[data-act]").forEach((b) => (b.onclick = async () => {
      let note = null;
      if (b.dataset.act === "rejected") {
        note = await confirmDialog("Reject this expense request?", { title: "Reject expense", confirmText: "Reject", danger: true, reason: true });
        if (!note) return;
      } else if (b.dataset.act === "paid") {
        const exp = rows.find((r) => r.id === +b.dataset.id);
        const accts = (await api.get("/accounting/settings")).cash_accounts.filter((a) => a.active && (!a.currency || a.currency === exp.currency));
        const balances = Object.fromEntries((await api.get("/accounting/accounts")).items.map((a) => [a.id, a.balance]));
        const done = await formModal({
          title: `Pay expense · ${money(exp.amount, exp.currency)}`, cols: 1,
          intro: `<p class="muted" style="margin-top:0">${esc(exp.description)}. Posts Dr Accounts Payable / Cr the chosen account. Payment is refused if the account lacks funds.</p>`,
          fields: [{ name: "account_id", label: "Pay from", type: "select", required: true, options: accts.map((a) => ({ value: a.id, label: `${a.name} (balance ${money(balances[a.id], a.currency)})` })), default: (accts.find((a) => a.code === "1010" || a.code === "1011") || accts[0])?.id },
            { name: "paid_on", label: "Payment date", type: "date", required: true, default: today(), min: exp.expense_date, max: today() }],
          onSubmit: (d) => api.post(`/expenses/${exp.id}/status`, { status: "paid", ...d }),
        });
        if (done) { toast("Expense paid", "success"); load(); }
        return;
      }
      try { await api.post(`/expenses/${b.dataset.id}/status`, { status: b.dataset.act, note }); toast(b.dataset.act === "approved" ? "Approved and accrued to Accounts Payable" : "Updated", "success"); load(); }
      catch (err) { handleError(err); }
    }));
  };
  el.querySelector("#add").onclick = async () => {
    const r = await formModal({
      title: "New expense request",
      fields: [
        { name: "category", label: "Category", type: "select", required: true, options: categories },
        { name: "amount", label: "Amount", type: "number", min: 0.01, step: 0.01, required: true },
        ...currencyField({ hint: "Paid later from an account in this currency" }),
        { name: "description", label: "Description", required: true, full: true },
        { name: "vendor", label: "Vendor / payee" },
        { name: "expense_date", label: "Date", type: "date", required: true, default: today(), max: today() },
      ],
      onSubmit: (d) => api.post("/expenses", d),
    });
    if (r) { toast("Expense submitted for approval", "success"); load(); }
  };
  el.querySelector("#export").onclick = () => downloadCSV(`expenses-${today()}`, columns.filter((c) => c.key !== "id"), rows);
  await load();
}
