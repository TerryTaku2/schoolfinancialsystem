import { api } from "../api.js";
import { rerender } from "../app.js";
import { badge, baseCurrency, confirmDialog, currencies, debounce, downloadCSV, esc, fmtDate, handleError, has, icon, isDual, modal, money, moneyList, pager, selectHtml, showFieldErrors, state, table, toast, today } from "../ui.js";
import { openReceipt } from "./docs.js";

/** Payment entry dialog. Pass a student {id, name, balance, balances} or null to search for one. */
export function recordPaymentModal(student = null) {
  return new Promise((resolve) => {
    let chosen = student;
    // Balance owed in a currency; each currency is paid and cleared on its own.
    const owed = (s, cur) => (s?.balances ? s.balances[cur] ?? 0 : cur === baseCurrency() ? s?.balance ?? 0 : 0);
    const balanceText = (s) => `Current balance: <b>${isDual() ? moneyList(currencies().map((c) => ({ currency: c, amount: owed(s, c) }))) : money(s.balance)}</b>`;
    let result = null;
    const methods = state.meta.payment_methods.map((m) => ({ value: m, label: m[0].toUpperCase() + m.slice(1) }));
    const m = modal({
      title: "Record payment",
      onClose: () => resolve(result),
      body: `<form class="form" novalidate>
        <div class="field"><span>Student *</span>
          <div id="chosen">${chosen ? `<b>${esc(chosen.name)}</b>` : ""}</div>
          ${chosen ? "" : `<input class="input" id="find" type="search" placeholder="Type a name or admission number" autocomplete="off"><div id="matches" class="card" style="margin-top:4px;max-height:200px;overflow:auto;display:none"></div>`}
          <small class="err" data-err="student_id"></small></div>
        <div id="acct" class="notice ${chosen && chosen.balance > 0 ? "warn" : ""}" style="${chosen ? "" : "display:none"}">${chosen ? balanceText(chosen) : ""}</div>
        <div class="cols">
          ${isDual() ? `<label class="field"><span>Currency received *</span>${selectHtml("currency", currencies().map((c) => ({ value: c, label: c === "ZWG" ? "ZWG (ZiG)" : c })), baseCurrency())}<small class="hint">Recorded as received; clears invoices in this currency only</small></label>` : ""}
          <label class="field"><span>Amount *</span><input class="input" name="amount" type="number" min="0.01" step="0.01" required><small class="err" data-err="amount"></small></label>
          <label class="field"><span>Date received *</span><input class="input" name="paid_on" type="date" value="${today()}" max="${today()}"><small class="err" data-err="paid_on"></small></label>
          <label class="field"><span>Method *</span>${selectHtml("method", methods, "cash")}<small class="err" data-err="method"></small></label>
          <label class="field"><span>Reference</span><input class="input" name="reference" placeholder="Bank / mobile transaction ID"><small class="hint">Required for non-cash payments</small><small class="err" data-err="reference"></small></label>
        </div>
        <p class="muted small" style="margin:0">Payments are applied automatically to the oldest outstanding invoice in the same currency first. Any excess is kept as credit for the next invoice in that currency.</p>
      </form>`,
      actions: [
        { label: "Cancel" },
        {
          label: "Save & print receipt", cls: "primary",
          onClick: async ({ el }) => {
            const form = el.querySelector("form");
            if (!chosen) { showFieldErrors(form, { fields: { student_id: "Select a student" } }); return true; }
            const data = { student_id: chosen.id, amount: form.amount.value, method: form.method.value, reference: form.reference.value.trim(), paid_on: form.paid_on.value, currency: form.currency?.value };
            try {
              result = await api.post("/payments", data);
            } catch (err) {
              showFieldErrors(form, err);
              toast(err.message, "error");
              return true;
            }
            toast(`Receipt ${result.receipt_no} saved`, "success");
            setTimeout(() => openReceipt(result.id), 50);
          },
        },
      ],
    });
    const find = m.el.querySelector("#find");
    if (find) {
      const box = m.el.querySelector("#matches");
      find.oninput = debounce(async () => {
        if (find.value.trim().length < 2) { box.style.display = "none"; return; }
        try {
          const res = await api.get("/students", { q: find.value.trim(), status: "all", per_page: 8 });
          box.style.display = "block";
          box.innerHTML = res.items.length ? res.items.map((s) => `<button type="button" class="btn ghost" style="width:100%;justify-content:space-between;border-radius:0" data-id="${s.id}"><span>${esc(s.name)} <span class="muted small">${esc(s.admission_no)} · ${esc(s.class || s.status)}</span></span><span class="num">${s.balances ? moneyList(s.balances) : money(s.balance)}</span></button>`).join("") : `<div class="empty">No match</div>`;
          box.querySelectorAll("[data-id]").forEach((b) => (b.onclick = () => {
            const s = res.items.find((x) => x.id === +b.dataset.id);
            chosen = { id: s.id, name: s.name, balance: s.balance, balances: s.balances };
            m.el.querySelector("#chosen").innerHTML = `<b>${esc(s.name)}</b> <span class="muted">${esc(s.admission_no)}</span>`;
            const acct = m.el.querySelector("#acct");
            acct.style.display = "";
            acct.className = `notice ${s.balance > 0 ? "warn" : ""}`;
            acct.innerHTML = balanceText(chosen);
            find.remove();
            box.remove();
            fillAmount();
            m.el.querySelector('[name="amount"]').focus();
          }));
        } catch (err) { handleError(err); }
      }, 250);
    } else {
      fillAmount();
    }
    // Suggest the amount owed in the chosen currency.
    function fillAmount() {
      const cur = m.el.querySelector('[name="currency"]')?.value || baseCurrency();
      const due = owed(chosen, cur);
      m.el.querySelector('[name="amount"]').value = chosen && due > 0 ? due : "";
    }
    m.el.querySelector('[name="currency"]')?.addEventListener("change", () => chosen && fillAmount());
  });
}

export default async function (el) {
  const office = has("payments.view");
  const f = { q: "", method: "", from: "", to: "", page: 1 };
  el.innerHTML = `
    <div class="page-head"><div><h1>Payments</h1><p>${office ? "Receipts are permanent. Mistakes are corrected by voiding (needs permission to void receipts) with a reason." : "Payments received for your children"}</p></div>
      <div class="page-actions">${office ? `<button class="btn" id="export">${icon("download")} Export CSV</button>${has("payments.manage") ? `<button class="btn primary" id="add">${icon("plus")} Record payment</button>` : ""}` : ""}</div></div>
    <div class="card">
      ${office ? `<div class="toolbar">
        <input class="input search" type="search" id="q" placeholder="Receipt, reference or student" aria-label="Search">
        ${selectHtml("method", state.meta.payment_methods.map((m) => ({ value: m, label: m[0].toUpperCase() + m.slice(1) })), "", { empty: "All methods" })}
        ${isDual() ? selectHtml("currency", currencies().map((c) => ({ value: c, label: c })), "", { empty: "All currencies" }) : ""}
        <label class="row small muted">From <input class="input" type="date" name="from"></label>
        <label class="row small muted">To <input class="input" type="date" name="to"></label>
        <span class="spacer" style="flex:1"></span><span id="sum" class="muted"></span>
      </div>` : ""}
      <div id="tbl"></div><div id="pg"></div>
    </div>`;

  const columns = [
    { key: "receipt_no", label: "Receipt" },
    { key: "paid_on", label: "Date", render: (r) => fmtDate(r.paid_on) },
    { key: "student", label: "Student", render: (r) => `<a href="#/student/${r.student_id}">${esc(r.student)}</a><br><span class="muted small">${esc(r.admission_no)}</span>` },
    { key: "method", label: "Method", render: (r) => `<span style="text-transform:capitalize">${esc(r.method)}</span>` },
    { key: "reference", label: "Reference" },
    { key: "amount", label: "Amount", num: true, render: (r) => (r.void ? `<s class="muted">${money(r.amount, r.currency)}</s>` : money(r.amount, r.currency)) },
    ...(isDual() ? [{ key: "currency", label: "Currency" }] : []),
    { key: "received_by", label: "Received by" },
    { key: "void", label: "Status", render: (r) => (r.void ? `${badge("void")}<br><span class="muted small">${esc(r.void_reason)}</span>` : r.unallocated > 0 ? badge("unpaid", `${money(r.unallocated, r.currency)} credit`) : badge("paid", "applied")), csv: (r) => (r.void ? "void" : "valid") },
    { key: "id", label: "", sort: false, cls: "actions", render: (r) => `<button class="btn sm" data-receipt="${r.id}">${icon("print")}</button>${has("payments.approve") && !r.void ? ` <button class="btn sm danger" data-void="${r.id}">Void</button>` : ""}`, csv: () => "" },
  ];
  const load = async () => {
    const res = await api.get("/payments", f);
    table(el.querySelector("#tbl"), { columns, rows: res.items, empty: "No payments found." });
    pager(el.querySelector("#pg"), { page: res.page, perPage: res.per_page, total: res.total, onPage: (p) => { f.page = p; load(); } });
    const sum = el.querySelector("#sum");
    if (sum) sum.innerHTML = `Total (excl. void): <b>${isDual() ? moneyList(res.sum_by_currency) : money(res.sum)}</b>`;
    el.querySelectorAll("[data-receipt]").forEach((b) => (b.onclick = () => openReceipt(b.dataset.receipt)));
    el.querySelectorAll("[data-void]").forEach((b) => (b.onclick = async () => {
      const reason = await confirmDialog("Voiding removes this payment from the student's account and re-opens the invoices it paid. This cannot be undone.", { title: "Void payment", confirmText: "Void payment", danger: true, reason: true });
      if (!reason) return;
      try { await api.post(`/payments/${b.dataset.void}/void`, { reason }); toast("Payment voided", "success"); load(); }
      catch (err) { handleError(err); }
    }));
  };
  if (office) {
    el.querySelector("#q").oninput = debounce((e) => { f.q = e.target.value; f.page = 1; load(); });
    ["method", "currency", "from", "to"].filter((n) => el.querySelector(`[name="${n}"]`)).forEach((n) => (el.querySelector(`[name="${n}"]`).onchange = (e) => { f[n] = e.target.value; f.page = 1; load(); }));
    el.querySelector("#add")?.addEventListener("click", async () => { if (await recordPaymentModal()) rerender(); });
    el.querySelector("#export").onclick = async () => {
      const all = await api.get("/payments", { ...f, page: 1, per_page: 500 });
      downloadCSV(`payments-${today()}`, columns.filter((c) => c.key !== "id"), all.items);
    };
  }
  await load();
}
