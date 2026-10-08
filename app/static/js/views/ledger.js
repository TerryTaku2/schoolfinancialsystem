// General ledger workspace: journals, account ledgers, chart of accounts, period close.
import { api, ApiError } from "../api.js";
import { baseCurrency, confirmDialog, currencies, debounce, downloadCSV, esc, fmtDate, formModal, handleError, has, icon, isDual, modal, moneyList, pager, selectHtml, showFieldErrors, table, toast, today } from "../ui.js";
import { acc } from "./statements.js";

const SOURCES = { manual: "Manual journal", invoice: "Invoice", charge: "Invoice charge", payment: "Receipt",
  expense_accrual: "Expense approved", expense_payment: "Expense paid", payroll: "Payroll approved",
  payroll_payment: "Salaries paid", payroll_remittance: "Payroll remittance", asset: "Asset register",
  depreciation: "Depreciation run" };

export default async function (el, params) {
  const [tabParam, codeParam, fromParam, toParam] = params;
  const settings = await api.get("/accounting/settings");
  let accounts = (await api.get("/accounting/accounts")).items;
  const tabs = [["journals", "Journals"], ["account", "Account ledger"], ["chart", "Chart of accounts"], ["close", "Period close"]];
  let tab = tabs.some(([k]) => k === tabParam) ? tabParam : "journals";
  const period = { from: fromParam || settings.fiscal_start, to: toParam || settings.fiscal_end };

  el.innerHTML = `
    <div class="page-head"><div><h1>General Ledger</h1><p>Double-entry books. Automatic postings come from invoices, receipts and expenses; use manual journals for everything else.</p></div>
      <div class="page-actions">${has("accounting.manage") ? `<button class="btn primary" id="new-je">${icon("plus")} New journal entry</button>` : ""}</div></div>
    <div class="tabs">${tabs.map(([k, l]) => `<button data-tab="${k}" class="${k === tab ? "active" : ""}">${l}</button>`).join("")}</div>
    <div id="pane"></div>`;
  const pane = el.querySelector("#pane");
  const acctOptions = (filter = () => true) => accounts.filter((a) => a.active && filter(a)).map((a) => ({ value: a.id, label: `${a.code} · ${a.name}` }));
  const periodInputs = () => `<label class="row small muted">From <input class="input" type="date" name="from" value="${period.from}"></label>
    <label class="row small muted">To <input class="input" type="date" name="to" value="${period.to}" max="${today()}"></label>`;
  const wirePeriod = (reload) => ["from", "to"].forEach((n) => (pane.querySelector(`[name="${n}"]`).onchange = (e) => { period[n] = e.target.value; reload(); }));

  // ------------------------------------------------------------ journals
  async function journals() {
    const f = { q: "", source: "", page: 1 };
    pane.innerHTML = `<div class="card"><div class="toolbar">
        <input class="input search" type="search" id="q" placeholder="Entry no., reference or description" aria-label="Search">
        ${selectHtml("source", Object.entries(SOURCES).map(([value, label]) => ({ value, label })), "", { empty: "All sources" })}
        ${periodInputs()}</div><div id="tbl"></div><div id="pg"></div></div>`;
    const load = async () => {
      const r = await api.get("/accounting/journals", { ...f, from: period.from, to: period.to });
      table(pane.querySelector("#tbl"), {
        rows: r.items, onRowClick: (e) => openEntry(e.id), empty: "No journal entries in this period.",
        columns: [{ key: "entry_no", label: "Entry" }, { key: "date", label: "Date", render: (e) => fmtDate(e.date) },
          { key: "description", label: "Description", render: (e) => `${esc(e.description)}${e.reversed_by ? ` <span class="badge plain">reversed by ${esc(e.reversed_by)}</span>` : ""}` },
          { key: "reference", label: "Reference" }, { key: "source_type", label: "Source", render: (e) => esc(SOURCES[e.source_type] || e.source_type) },
          { key: "amount", label: "Amount", num: true, render: (e) => acc(e.amount) },
          ...(isDual() ? [{ key: "currency", label: "Currency" }] : []), { key: "author", label: "Posted by" }],
      });
      pager(pane.querySelector("#pg"), { page: r.page, perPage: r.per_page, total: r.total, onPage: (pg) => { f.page = pg; load(); } });
    };
    pane.querySelector("#q").oninput = debounce((e) => { f.q = e.target.value; f.page = 1; load(); });
    pane.querySelector('[name="source"]').onchange = (e) => { f.source = e.target.value; f.page = 1; load(); };
    wirePeriod(() => { f.page = 1; load(); });
    await load();
  }

  async function openEntry(id) {
    try {
      const e = await api.get(`/accounting/journals/${id}`);
      const dr = e.lines.reduce((a, l) => a + l.debit, 0), cr = e.lines.reduce((a, l) => a + l.credit, 0);
      const m = modal({
        title: `${e.entry_no} · ${fmtDate(e.date)}${isDual() ? ` · ${e.currency}` : ""}`, wide: true,
        body: `<p style="margin-top:0"><b>${esc(e.description)}</b><br><span class="muted small">${esc(SOURCES[e.source_type] || e.source_type)}${e.reference ? ` · ref ${esc(e.reference)}` : ""} · posted by ${esc(e.author)}${e.reversal_of ? ` · reverses ${esc(e.reversal_of)}` : ""}${e.reversed_by ? ` · reversed by ${esc(e.reversed_by)}` : ""}</span></p>
          <div class="table-wrap card"><table class="table"><thead><tr><th>Account</th><th>Memo</th><th class="num">Debit</th><th class="num">Credit</th></tr></thead><tbody>
          ${e.lines.map((l) => `<tr><td><span class="muted small">${esc(l.code)}</span> ${esc(l.account)}</td><td class="muted">${esc(l.memo || "")}</td><td class="num">${l.debit ? acc(l.debit) : ""}</td><td class="num">${l.credit ? acc(l.credit) : ""}</td></tr>`).join("")}
          </tbody><tfoot><tr><td colspan="2">Totals</td><td class="num">${acc(dr)}</td><td class="num">${acc(cr)}</td></tr></tfoot></table></div>`,
        actions: has("accounting.manage") && e.source_type === "manual" && !e.reversal_of && !e.reversed_by ? [{ label: "Close" }, {
          label: "Reverse entry", cls: "danger",
          onClick: async () => {
            const reason = await confirmDialog("A reversing entry with opposite debits and credits will be posted today. The original stays on record.", { title: "Reverse journal", confirmText: "Reverse", danger: true, reason: true });
            if (!reason) return true;
            await api.post(`/accounting/journals/${id}/reverse`, { reason });
            toast("Reversal posted", "success");
            show();
          },
        }] : [],
      });
      return m;
    } catch (err) { handleError(err); }
  }

  // ------------------------------------------------------------ account ledger
  async function accountLedger() {
    let accountId = accounts.find((a) => a.code === codeParam)?.id || accounts.find((a) => a.code === "1010")?.id;
    let ledgerCur = baseCurrency();
    pane.innerHTML = `<div class="card"><div class="toolbar">${selectHtml("account", accounts.map((a) => ({ value: a.id, label: `${a.code} · ${a.name}` })), accountId)}
      ${isDual() ? selectHtml("lcur", currencies().map((c) => ({ value: c, label: c })), ledgerCur) : ""}${periodInputs()}
      <span style="flex:1"></span><button class="btn sm" id="x">${icon("download")} CSV</button></div><div id="sum" class="toolbar small"></div><div id="tbl"></div></div>`;
    let data = null;
    const cols = [{ key: "date", label: "Date", render: (r) => fmtDate(r.date) }, { key: "entry_no", label: "Entry" }, { key: "reference", label: "Ref" },
      { key: "description", label: "Description", render: (r) => `${esc(r.description)}${r.memo ? `<br><span class="muted small">${esc(r.memo)}</span>` : ""}` },
      { key: "debit", label: "Debit", num: true, render: (r) => (r.debit ? acc(r.debit) : "") }, { key: "credit", label: "Credit", num: true, render: (r) => (r.credit ? acc(r.credit) : "") },
      { key: "balance", label: "Balance", num: true, render: (r) => `<b>${acc(r.balance)}</b>` }];
    const load = async () => {
      data = await api.get("/accounting/ledger", { account_id: accountId, from: period.from, to: period.to, currency: ledgerCur });
      // Cash accounts hold one currency; other accounts are shown per currency.
      const lc = pane.querySelector('[name="lcur"]');
      if (lc) { lc.value = data.currency; lc.disabled = !!data.account.currency; }
      const a = data.account;
      pane.querySelector("#sum").innerHTML = `<span class="muted">${esc(a.type)} · normal ${esc(a.normal_balance)} balance${a.description ? ` · ${esc(a.description)}` : ""}</span><span style="flex:1"></span>
        <span>Opening <b>${acc(data.opening)}</b></span><span>Debits <b>${acc(data.total_debit)}</b></span><span>Credits <b>${acc(data.total_credit)}</b></span><span>Closing <b>${acc(data.closing)}</b></span>`;
      table(pane.querySelector("#tbl"), { rows: data.rows, columns: cols, sortKey: null, onRowClick: (r) => openEntry(r.entry_id), empty: "No postings in this period." });
    };
    pane.querySelector('[name="account"]').onchange = (e) => { accountId = e.target.value; load(); };
    pane.querySelector('[name="lcur"]')?.addEventListener("change", (e) => { ledgerCur = e.target.value; load(); });
    pane.querySelector("#x").onclick = () => data && downloadCSV(`ledger-${data.account.code}-${period.from}-${period.to}`, cols, data.rows);
    wirePeriod(load);
    await load();
  }

  // ------------------------------------------------------------ chart of accounts
  async function chart() {
    accounts = (await api.get("/accounting/accounts")).items;
    pane.innerHTML = `<div class="card"><div class="card-head"><div><h3>Chart of accounts</h3><div class="sub">Codes: 1xxx assets · 2xxx liabilities · 3xxx net assets · 4xxx income · 5xxx expenses. Balances as at today.</div></div>
      ${has("accounting.approve") ? `<button class="btn sm" id="add-acct">${icon("plus")} Add account</button>` : ""}</div><div id="tbl"></div></div>`;
    table(pane.querySelector("#tbl"), {
      rows: accounts, sortKey: "code", onRowClick: (a) => { codeParamSwitch(a.code); },
      columns: [{ key: "code", label: "Code" }, { key: "name", label: "Account", render: (a) => `<b>${esc(a.name)}</b>${a.description ? `<br><span class="muted small">${esc(a.description)}</span>` : ""}` },
        { key: "type", label: "Type", render: (a) => `<span style="text-transform:capitalize">${esc(a.type)}</span>${a.subtype ? ` <span class="muted small">· ${esc(a.subtype.replace("_", " "))}</span>` : ""}` },
        { key: "cash_flow", label: "Cash flow", render: (a) => `<span style="text-transform:capitalize">${esc(a.cash_flow)}</span>` },
        { key: "balance", label: "Balance", num: true, render: (a) => (isDual() ? moneyList(a.balances) : acc(a.balance)), sort: (a) => a.balance },
        ...(isDual() ? [{ key: "currency", label: "Holds", render: (a) => esc(a.currency || "Both") }] : []),
        { key: "active", label: "", sort: false, render: (a) => `${a.is_system ? '<span class="badge plain">system</span>' : ""}${a.active ? "" : ' <span class="badge plain">inactive</span>'}` },
        ...(has("accounting.approve") ? [{ key: "id", label: "", sort: false, cls: "actions", render: (a) => `<button class="btn sm" data-edit="${a.id}">Edit</button>` }] : [])],
    });
    pane.querySelector("#add-acct")?.addEventListener("click", async () => {
      const r = await formModal({
        title: "New account",
        fields: [{ name: "code", label: "Code", required: true, placeholder: "e.g. 5150", hint: "4 digits; first digit must match the type" },
          { name: "name", label: "Name", required: true },
          { name: "type", label: "Type", type: "select", required: true, options: ["asset", "liability", "equity", "income", "expense"].map((t) => ({ value: t, label: t[0].toUpperCase() + t.slice(1) })) },
          ...(isDual() ? [{ name: "currency", label: "Currency (cash/bank accounts only)", type: "select", options: currencies(), default: baseCurrency() }] : []),
          { name: "subtype", label: "Special behaviour", type: "select", empty: "None", options: [{ value: "cash", label: "Cash / bank account" }, { value: "fixed_asset", label: "Fixed asset" }, { value: "contra_asset", label: "Contra asset (e.g. depreciation)" }, { value: "contra_income", label: "Contra income (discounts)" }] },
          { name: "cash_flow", label: "Cash flow class", type: "select", required: true, options: [{ value: "operating", label: "Operating" }, { value: "investing", label: "Investing" }, { value: "financing", label: "Financing" }] },
          { name: "description", label: "Description", full: true }],
        onSubmit: (d) => api.post("/accounting/accounts", d),
      });
      if (r) { toast("Account created", "success"); chart(); }
    });
    pane.querySelectorAll("[data-edit]").forEach((b) => (b.onclick = async () => {
      const a = accounts.find((x) => x.id === +b.dataset.edit);
      const r = await formModal({
        title: `Edit ${a.code} ${a.name}`, cols: 1, values: a,
        fields: [{ name: "name", label: "Name", required: true }, { name: "description", label: "Description" },
          ...(a.is_system ? [] : [{ name: "cash_flow", label: "Cash flow class", type: "select", required: true, options: ["operating", "investing", "financing"] },
            { name: "active", label: "Active (only zero-balance accounts can be deactivated)", type: "checkbox" }])],
        onSubmit: (d) => api.put(`/accounting/accounts/${a.id}`, d),
      });
      if (r) { toast("Account saved", "success"); chart(); }
    }));
  }
  function codeParamSwitch(code) { location.hash = `#/ledger/account/${code}/${period.from}/${period.to}`; }

  // ------------------------------------------------------------ period close
  async function close() {
    const s = await api.get("/accounting/settings");
    pane.innerHTML = `<div class="grid g-2">
      <div class="card"><div class="card-head"><div><h3>Close the books</h3><div class="sub">Prevents any posting dated on or before the lock date</div></div></div><div class="card-body">
        <p style="margin-top:0">Currently: ${s.lock_date ? `<b>closed up to ${fmtDate(s.lock_date)}</b>` : "<b>all periods open</b>"}</p>
        <p class="muted small">Close a month or term once its statements are final. Corrections to a closed period are then made by posting adjusting entries in the current period, and voids post their reversal on the day of the void, so issued statements never change.</p>
        ${has("accounting.approve") ? `<form class="row" id="lock"><input class="input" type="date" name="lock_date" value="${s.lock_date || ""}" max="${today()}" style="width:auto"><button class="btn primary">Save lock date</button>${s.lock_date ? `<button type="button" class="btn" id="reopen">Reopen all</button>` : ""}</form>` : `<p class="muted">Only an administrator can close or reopen periods.</p>`}
      </div></div>
      ${has("accounting.approve") ? `<div class="card"><div class="card-head"><div><h3>Rebuild automatic postings</h3><div class="sub">Maintenance</div></div></div><div class="card-body">
        <p style="margin-top:0" class="muted small">Regenerates every automatic entry from the source invoices, receipts and expenses. Manual journals are kept. Use this after upgrading from a version without the ledger.</p>
        <button class="btn danger" id="rebuild">Rebuild ledger</button></div></div>` : ""}
    </div>`;
    pane.querySelector("#lock")?.addEventListener("submit", async (e) => {
      e.preventDefault();
      const d = e.target.lock_date.value;
      if (d && !(await confirmDialog(`Close all periods up to and including ${fmtDate(d)}? Nothing dated on or before then can be posted afterwards.`, { confirmText: "Close period" }))) return;
      try { await api.put("/accounting/settings", { lock_date: d || null }); toast("Saved", "success"); close(); } catch (err) { handleError(err); }
    });
    pane.querySelector("#reopen")?.addEventListener("click", async () => {
      if (!(await confirmDialog("Reopen all periods? Back-dated postings will be allowed again.", { confirmText: "Reopen", danger: true }))) return;
      await api.put("/accounting/settings", { lock_date: null }).catch(handleError);
      close();
    });
    pane.querySelector("#rebuild")?.addEventListener("click", async () => {
      if (!(await confirmDialog("Regenerate all automatic ledger postings?", { confirmText: "Rebuild", danger: true, typed: "REBUILD" }))) return;
      try { const r = await api.post("/accounting/rebuild", { confirm: "REBUILD" }); toast(`${r.postings} postings regenerated`, "success"); } catch (err) { handleError(err); }
    });
  }

  // ------------------------------------------------------------ manual journal entry
  function newJournal() {
    let cur = baseCurrency();
    // Accounts usable in this currency: cash accounts only in their own currency.
    const opts = () => acctOptions((a) => !["receivable", "payable", "payroll"].includes(a.subtype) && (!a.currency || a.currency === cur));
    let rows = [{}, {}];
    const m = modal({
      title: "New journal entry", wide: true,
      body: `<form class="form" novalidate>
        <div class="cols-3"><label class="field"><span>Date *</span><input class="input" type="date" name="date" value="${today()}" max="${today()}"${settings.lock_date ? ` min="${settings.lock_date}"` : ""}><small class="err" data-err="date"></small></label>
          <label class="field" style="grid-column:span 2"><span>Description *</span><input class="input" name="description" placeholder="e.g. Depreciation for October"><small class="err" data-err="description"></small></label>
          <label class="field"><span>Reference</span><input class="input" name="reference" placeholder="Voucher / PO no."></label>
          ${isDual() ? `<label class="field"><span>Currency *</span>${selectHtml("jcur", currencies().map((c) => ({ value: c, label: c })), cur)}<small class="hint">Every line is in this currency</small></label>` : ""}</div>
        <div class="jl-grid head"><span>Account</span><span>Debit</span><span>Credit</span><span>Memo</span><span></span></div>
        <div id="jl"></div>
        <div class="row" style="justify-content:space-between"><button type="button" class="btn sm" id="add-line">${icon("plus")} Add line</button><span id="totals" class="small"></span></div>
        <p class="muted small" style="margin:0">Student receivables and accounts payable are control accounts and can only change through invoices, receipts and expenses.</p>
      </form>`,
      actions: [{ label: "Cancel" }, {
        label: "Post entry", cls: "primary",
        onClick: async ({ el: mel }) => {
          const form = mel.querySelector("form");
          const lines = rows.filter((r) => r.account_id && (+r.debit || +r.credit)).map((r) => ({ account_id: r.account_id, debit: r.debit || 0, credit: r.credit || 0, memo: r.memo }));
          try {
            const e = await api.post("/accounting/journals", { date: form.date.value, description: form.description.value, reference: form.reference.value, lines, currency: cur });
            toast(`${e.entry_no} posted`, "success");
            show();
          } catch (err) {
            if (err instanceof ApiError) { showFieldErrors(form, err); toast(err.message, "error"); return true; }
            throw err;
          }
        },
      }],
    });
    const draw = () => {
      const box = m.el.querySelector("#jl");
      box.innerHTML = rows.map((r, i) => `<div class="jl-grid" style="margin-bottom:6px">
        ${selectHtml(`a${i}`, opts(), r.account_id, { empty: "Select account…" })}
        <input class="input" type="number" min="0" step="0.01" placeholder="0.00" data-i="${i}" data-k="debit" value="${r.debit || ""}" aria-label="Debit">
        <input class="input" type="number" min="0" step="0.01" placeholder="0.00" data-i="${i}" data-k="credit" value="${r.credit || ""}" aria-label="Credit">
        <input class="input" data-i="${i}" data-k="memo" value="${esc(r.memo || "")}" placeholder="Optional" aria-label="Memo">
        <button type="button" class="btn ghost icon danger" data-rm="${i}" ${rows.length <= 2 ? "disabled" : ""} aria-label="Remove line">✕</button></div>`).join("");
      box.querySelectorAll("select").forEach((s, i) => (s.onchange = () => (rows[i].account_id = s.value)));
      box.querySelectorAll("[data-k]").forEach((inp) => (inp.oninput = () => {
        const r = rows[+inp.dataset.i];
        r[inp.dataset.k] = inp.value;
        // A line is either a debit or a credit: typing one clears the other.
        if (inp.dataset.k !== "memo" && inp.value) {
          const other = inp.dataset.k === "debit" ? "credit" : "debit";
          r[other] = "";
          box.querySelector(`[data-i="${inp.dataset.i}"][data-k="${other}"]`).value = "";
        }
        totals();
      }));
      box.querySelectorAll("[data-rm]").forEach((b) => (b.onclick = () => { rows.splice(+b.dataset.rm, 1); draw(); }));
      totals();
    };
    const totals = () => {
      const dr = rows.reduce((a, r) => a + (+r.debit || 0), 0), cr = rows.reduce((a, r) => a + (+r.credit || 0), 0);
      const diff = Math.round((dr - cr) * 100) / 100;
      m.el.querySelector("#totals").innerHTML = `Debits <b>${acc(dr)}</b> · Credits <b>${acc(cr)}</b> · ${diff === 0 && dr > 0 ? '<span class="check-ok">✓ Balanced</span>' : `<span class="check-bad">Out of balance ${acc(Math.abs(diff))}</span>`}`;
    };
    m.el.querySelector("#add-line").onclick = () => { rows.push({}); draw(); };
    m.el.querySelector('[name="jcur"]')?.addEventListener("change", (e) => {
      cur = e.target.value;
      // Cash accounts of the other currency are no longer valid lines.
      const ok = new Set(opts().map((o) => String(o.value)));
      rows.forEach((r) => { if (r.account_id && !ok.has(String(r.account_id))) r.account_id = ""; });
      draw();
    });
    draw();
  }

  const views = { journals, account: accountLedger, chart, close };
  const show = async () => {
    el.querySelectorAll("[data-tab]").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
    pane.innerHTML = `<div class="empty">Loading…</div>`;
    try { await views[tab](); } catch (err) { handleError(err); }
  };
  el.querySelectorAll("[data-tab]").forEach((b) => (b.onclick = () => { tab = b.dataset.tab; history.replaceState(null, "", `#/ledger/${tab}`); show(); }));
  el.querySelector("#new-je")?.addEventListener("click", newJournal);
  await show();
}
