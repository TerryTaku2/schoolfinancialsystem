import { api } from "../api.js";
import { classOptions, confirmDialog, currencyField, esc, fmtDate, formModal, handleError, has, icon, isDual, modal, money, moneyList, selectHtml, state, table, termOptions, toast } from "../ui.js";

export default async function (el) {
  let termId = state.meta.current_term?.id || state.meta.terms[0]?.id || "";
  el.innerHTML = `
    <div class="page-head"><div><h1>Fee Structure</h1><p>Charges per term. "All classes" items apply to every student; class items add on top.</p></div>
      <div class="page-actions">${selectHtml("term", termOptions(), termId)}
        ${has("fees.manage") ? `<button class="btn" id="add">${icon("plus")} Add fee item</button><button class="btn primary" id="gen">Generate invoices</button>` : ""}</div></div>
    <div class="grid g-3">
      <div class="card span-2"><div class="card-head"><h3>Fee items</h3><span class="muted small">Invoiced items are locked</span></div><div id="items"></div></div>
      <div class="card"><div class="card-head"><div><h3>Total per student</h3><div class="sub">Before scholarships</div></div></div><div id="totals"></div></div>
    </div>`;

  const incomeAccounts = (await api.get("/accounting/accounts")).items
    .filter((a) => a.type === "income" && a.subtype !== "contra_income" && a.active)
    .map((a) => ({ value: a.id, label: `${a.code} · ${a.name}` }));
  const fields = (item = {}) => [
    { name: "name", label: "Fee name", required: true, placeholder: "e.g. Tuition" },
    { name: "amount", label: "Amount", type: "number", min: 0.01, step: 0.01, required: true },
    ...currencyField({ hint: "Students get a separate invoice for each currency" }),
    { name: "class_id", label: "Applies to", type: "select", options: classOptions(), empty: "All classes" },
    { name: "due_date", label: "Due date", type: "date", required: true, hint: "Must fall within the term" },
    { name: "account_id", label: "Income account", type: "select", options: incomeAccounts, empty: "Automatic (by fee name)", hint: "Credited in the ledger when invoiced" },
    { name: "discountable", label: "Scholarships reduce this fee", type: "checkbox", default: item.discountable ?? true, full: true },
  ];

  const load = async () => {
    const res = await api.get("/fees", { term_id: termId });
    table(el.querySelector("#items"), {
      rows: res.items, empty: "No fee items for this term yet.",
      columns: [
        { key: "name", label: "Fee", render: (r) => `<b>${esc(r.name)}</b>${r.discountable ? "" : ` <span class="badge plain">No discount</span>`}` },
        { key: "class", label: "Applies to" },
        { key: "account", label: "Income account", render: (r) => `<span class="muted small">${esc(r.account || "Automatic")}</span>` },
        { key: "amount", label: "Amount", num: true, render: (r) => money(r.amount, r.currency) },
        { key: "due_date", label: "Due", render: (r) => fmtDate(r.due_date) },
        { key: "invoiced", label: "Invoiced", num: true },
        { key: "id", label: "", sort: false, cls: "actions", render: (r) => has("fees.manage") && !r.invoiced ? `<button class="btn sm" data-edit="${r.id}">Edit</button> <button class="btn sm danger" data-del="${r.id}">Delete</button>` : "" },
      ],
    });
    el.querySelector("#totals").innerHTML = res.totals.length ? `<ul class="list">${res.totals.map((t) => `<li><span>${esc(t.class)}</span><b class="num">${isDual() ? moneyList(t.by_currency) : money(t.total)}</b></li>`).join("")}</ul>` : `<div class="empty">No classes.</div>`;
    el.querySelectorAll("[data-edit]").forEach((b) => (b.onclick = async () => {
      const item = res.items.find((i) => i.id === +b.dataset.edit);
      if (await formModal({ title: "Edit fee item", fields: fields(item), values: item, onSubmit: (d) => api.put(`/fees/${item.id}`, d) })) { toast("Saved", "success"); load(); }
    }));
    el.querySelectorAll("[data-del]").forEach((b) => (b.onclick = async () => {
      if (!(await confirmDialog("Delete this fee item?", { danger: true, confirmText: "Delete" }))) return;
      try { await api.del(`/fees/${b.dataset.del}`); load(); } catch (err) { handleError(err); }
    }));
  };

  el.querySelector('[name="term"]').onchange = (e) => { termId = e.target.value; load(); };
  el.querySelector("#add")?.addEventListener("click", async () => {
    const term = state.meta.terms.find((t) => String(t.id) === String(termId));
    const r = await formModal({
      title: `New fee item · ${term?.label || ""}`, fields: fields(), values: { due_date: term?.start_date },
      onSubmit: (d) => api.post("/fees", { ...d, term_id: termId }),
    });
    if (r) { toast("Fee item added", "success"); load(); }
  });
  el.querySelector("#gen")?.addEventListener("click", () => {
    const term = state.meta.terms.find((t) => String(t.id) === String(termId));
    const m = modal({
      title: `Generate invoices · ${term?.label}`,
      body: `<p style="margin-top:0">Creates one invoice for every <b>active</b> student who doesn't already have one for this term${isDual() ? " (one per currency that has fee items)" : ""}, using the fee items above and any active scholarships. Existing credit is applied automatically.</p>
        <p class="muted">Safe to run again after admitting new students — already-invoiced students are skipped.</p>
        <label class="field"><span>Limit to class</span>${selectHtml("class_id", classOptions(), "", { empty: "All classes" })}</label>`,
      actions: [{ label: "Cancel" }, {
        label: "Generate", cls: "primary",
        onClick: async ({ el: mel }) => {
          const res = await api.post("/invoices/generate", { term_id: termId, class_id: mel.querySelector('[name="class_id"]').value });
          toast(`${res.created} invoice(s) created, ${res.skipped.length} skipped`, "success");
          if (res.skipped.some((s) => s.reason.startsWith("no fee"))) toast("Some students were skipped because their class has no fee items", "error");
          load();
        },
      }],
    });
    return m;
  });
  await load();
}
