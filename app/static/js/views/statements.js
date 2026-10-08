// Financial statements: income statement, balance sheet, cash flow, trial balance.
import { api } from "../api.js";
import { navigate } from "../app.js";
import { baseCurrency, currencies, downloadCSV, esc, fmtDate, icon, isDual, selectHtml, state, today } from "../ui.js";

const NUM = new Intl.NumberFormat(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
// Accounting format: negatives in brackets, zero as a dash.
export const acc = (v) => (v === null || v === undefined ? "" : Math.abs(v) < 0.005 ? "–" : v < 0 ? `(${NUM.format(-v)})` : NUM.format(v));
const dayBefore = (iso) => {
  const d = new Date(iso + "T00:00:00");
  d.setDate(d.getDate() - 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
};

export default async function (el, [tabParam]) {
  const settings = await api.get("/accounting/settings");
  const term = state.meta.current_term;
  const presets = [
    { value: "fy", label: "This academic year", from: settings.fiscal_start, to: settings.fiscal_end },
    ...(term ? [{ value: "term", label: `Current term (${term.label})`, from: term.start_date, to: today() < term.end_date ? today() : term.end_date }] : []),
    ...state.meta.terms.filter((t) => t.id !== term?.id && t.end_date < today()).map((t) => ({ value: `t${t.id}`, label: t.label, from: t.start_date, to: t.end_date })),
    { value: "custom", label: "Custom dates" },
  ];
  const p = { preset: "fy", from: settings.fiscal_start, to: settings.fiscal_end, compare: true, currency: baseCurrency() };
  // Each currency has its own complete books; "ALL" adds them up in the base currency at the rate on the report date.
  const currencyOptions = [...currencies().map((c) => ({ value: c, label: `${c} books` })), { value: "ALL", label: `Combined, in ${baseCurrency()} terms` }];
  const tabs = [["income", "Income statement"], ["position", "Balance sheet"], ["cash", "Cash flow"], ["trial", "Trial balance"]];
  let tab = tabs.some(([k]) => k === tabParam) ? tabParam : "income";

  el.innerHTML = `
    <div class="page-head"><div><h1>Financial Statements</h1><p>Prepared automatically from the double-entry general ledger${settings.lock_date ? ` · books closed to ${fmtDate(settings.lock_date)}` : ""}</p></div>
      <div class="page-actions"><button class="btn" id="csv">${icon("download")} CSV</button><button class="btn" id="print">${icon("print")} Print</button></div></div>
    <div class="card" style="margin-bottom:16px"><div class="toolbar" style="border:0">
      ${isDual() ? selectHtml("currency", currencyOptions, p.currency) : ""}
      ${selectHtml("preset", presets.map((x) => ({ value: x.value, label: x.label })), p.preset)}
      <label class="row small muted">From <input class="input" type="date" name="from" value="${p.from}"></label>
      <label class="row small muted">To <input class="input" type="date" name="to" value="${p.to}" max="${today()}"></label>
      <label class="check small"><input type="checkbox" name="compare" checked> Comparative figures</label>
      <span style="flex:1"></span><span id="check"></span>
    </div></div>
    <div class="tabs">${tabs.map(([k, l]) => `<button data-tab="${k}" class="${k === tab ? "active" : ""}">${l}</button>`).join("")}</div>
    <div id="pane"></div>`;
  const pane = el.querySelector("#pane");
  let csv = { name: "statement", rows: [] };

  const header = (title, sub) => `<div class="doc-head"><div><h2>${esc(state.meta.school)}</h2><div class="muted">${esc(title)}</div></div>
    <div style="text-align:right"><div class="muted">${sub}</div><div class="muted small">${p.currency === "ALL"
      ? `Combined: ${esc(currencies().filter((c) => c !== baseCurrency()).join(", "))} translated into ${esc(baseCurrency())} at the rate on the report date`
      : `Amounts in ${esc(p.currency)}`}</div></div></div>`;
  const setCheck = (ok, okText, badText) => {
    el.querySelector("#check").innerHTML = ok ? `<span class="check-ok">✓ ${esc(okText)}</span>` : `<span class="check-bad">✕ ${esc(badText)}</span>`;
  };
  const wireLinks = () => pane.querySelectorAll("[data-code]").forEach((tr) => (tr.onclick = () => navigate(`#/ledger/account/${tr.dataset.code}/${p.from}/${p.to}`)));

  // A statement section: heading, lines, subtotal.
  const section = (s, { cmp, sign = 1, totalLabel, showTotal = true } = {}) => {
    const rows = s.rows.map((r) => {
      csv.rows.push({ section: s.title, line: r.code ? `${r.code} ${r.name}` : r.name, amount: r.amount * sign, prior: cmp ? r.prior * sign : "" });
      return `<tr class="line ${r.code ? "link" : ""}" ${r.code ? `data-code="${esc(r.code)}" title="Open ledger"` : ""}><td>${r.code ? `<span class="code">${esc(r.code)}</span>` : ""}${esc(r.name)}</td>
        <td class="amt">${acc(r.amount * sign)}</td>${cmp ? `<td class="amt prior">${acc(r.prior * sign)}</td>` : ""}</tr>`;
    }).join("") || `<tr class="line"><td class="muted">None</td><td class="amt">–</td>${cmp ? `<td class="amt prior">–</td>` : ""}</tr>`;
    return `<tr class="sec"><td colspan="3">${esc(s.title)}</td></tr>${rows}
      ${showTotal ? `<tr class="sub"><td>${esc(totalLabel || `Total ${s.title.toLowerCase()}`)}</td><td class="amt">${acc(s.total * sign)}</td>${cmp ? `<td class="amt prior">${acc(s.prior_total * sign)}</td>` : ""}</tr>` : ""}`;
  };
  const line = (label, v, pv, cmp, cls = "sub") => `<tr class="${cls}"><td>${esc(label)}</td><td class="amt">${acc(v)}</td>${cmp ? `<td class="amt prior">${acc(pv)}</td>` : ""}</tr>`;
  const head = (cmp, a, b) => `<thead><tr><th></th><th>${esc(a)}</th>${cmp ? `<th>${esc(b)}</th>` : ""}</tr></thead>`;

  const views = {
    async income() {
      const r = await api.get("/accounting/income-statement", { from: p.from, to: p.to, compare: p.compare ? 1 : 0, currency: p.currency });
      const cmp = p.compare;
      csv = { name: `income-statement-${p.from}-${p.to}`, rows: [] };
      pane.innerHTML = `<div class="doc">${header("Statement of Financial Performance (Income & Expenditure)", `For the period ${fmtDate(r.start)} – ${fmtDate(r.end)}`)}
        <table class="fs">${head(cmp, "Current period", `${fmtDate(r.prior_start)} – ${fmtDate(r.prior_end)}`)}<tbody>
        ${section(r.revenue, { cmp, totalLabel: "Gross revenue" })}
        ${r.deductions.rows.length ? section(r.deductions, { cmp, sign: -1, totalLabel: "Total deductions" }) : ""}
        ${line("Net revenue", r.net_revenue, r.prior_net_revenue, cmp)}
        ${section(r.expenses, { cmp, totalLabel: "Total expenditure" })}
        ${line(r.surplus >= 0 ? "Surplus for the period" : "Deficit for the period", r.surplus, r.prior_surplus, cmp, "grand")}
        </tbody></table>
        <p class="muted small">Prepared on the accrual basis: fees are recognised when invoiced, expenses when approved. ${r.margin !== null ? `Operating margin: ${r.margin}%.` : ""}</p></div>`;
      csv.rows.push({ section: "Result", line: "Net revenue", amount: r.net_revenue, prior: r.prior_net_revenue ?? "" },
        { section: "Result", line: "Surplus/(deficit)", amount: r.surplus, prior: r.prior_surplus ?? "" });
      setCheck(true, "From balanced ledger");
    },
    async position() {
      const compareTo = p.compare ? dayBefore(p.from) : "";
      const r = await api.get("/accounting/balance-sheet", { as_at: p.to, compare_to: compareTo, currency: p.currency });
      const cmp = !!r.compare_to;
      csv = { name: `balance-sheet-${p.to}`, rows: [] };
      pane.innerHTML = `<div class="doc">${header("Statement of Financial Position (Balance Sheet)", `As at ${fmtDate(r.as_at)}`)}
        <table class="fs">${head(cmp, fmtDate(r.as_at), r.compare_to ? fmtDate(r.compare_to) : "")}<tbody>
        ${section(r.non_current_assets, { cmp })}
        ${section(r.current_assets, { cmp })}
        ${line("TOTAL ASSETS", r.total_assets, r.prior_total_assets, cmp, "grand")}
        ${section(r.liabilities, { cmp })}
        ${section(r.equity, { cmp })}
        ${line("TOTAL LIABILITIES & NET ASSETS", r.total_liabilities_equity, r.prior_total_liabilities_equity, cmp, "grand")}
        </tbody></table>
        <p class="muted small">Student balances are taken from the per-student receivables ledger: amounts owed are shown as receivables, overpayments as fees received in advance.${r.current_ratio !== null ? ` Current ratio: ${r.current_ratio}.` : ""}</p></div>`;
      csv.rows.push({ section: "Totals", line: "Total assets", amount: r.total_assets, prior: r.prior_total_assets ?? "" },
        { section: "Totals", line: "Total liabilities & net assets", amount: r.total_liabilities_equity, prior: r.prior_total_liabilities_equity ?? "" });
      setCheck(r.balanced, "Assets = liabilities + net assets", "Statement does not balance");
    },
    async cash() {
      const r = await api.get("/accounting/cash-flow", { from: p.from, to: p.to, currency: p.currency });
      csv = { name: `cash-flow-${p.from}-${p.to}`, rows: [] };
      pane.innerHTML = `<div class="doc">${header("Statement of Cash Flows (direct method)", `For the period ${fmtDate(r.start)} – ${fmtDate(r.end)}`)}
        <table class="fs">${head(false, "Amount")}<tbody>
        ${section(r.operating, { totalLabel: "Net cash from operating activities" })}
        ${section(r.investing, { totalLabel: "Net cash from investing activities" })}
        ${section(r.financing, { totalLabel: "Net cash from financing activities" })}
        ${line("Net increase / (decrease) in cash", r.net_change)}
        ${line("Cash and cash equivalents at beginning of period", r.opening_cash, null, false, "line")}
        ${line("Cash and cash equivalents at end of period", r.closing_cash, null, false, "grand")}
        </tbody></table>
        <h3 style="margin-top:18px">Cash and cash equivalents</h3>
        <table class="fs"><thead><tr><th>Account</th><th>Opening</th><th>Closing</th></tr></thead><tbody>
        ${r.by_account.map((a) => `<tr class="line link" data-code="${esc(a.code)}"><td><span class="code">${esc(a.code)}</span>${esc(a.name)}</td><td class="amt">${acc(a.opening)}</td><td class="amt">${acc(a.closing)}</td></tr>`).join("")}
        </tbody></table>
        <p class="muted small">Transfers between cash accounts (e.g. banking cash collections) are excluded because they don't change total cash.</p></div>`;
      csv.rows.push({ section: "Reconciliation", line: "Opening cash", amount: r.opening_cash }, { section: "Reconciliation", line: "Net change", amount: r.net_change },
        { section: "Reconciliation", line: "Closing cash", amount: r.closing_cash });
      setCheck(r.reconciled, "Opening + movement = closing cash", "Cash does not reconcile");
    },
    async trial() {
      const r = await api.get("/accounting/trial-balance", { as_at: p.to, currency: p.currency });
      csv = { name: `trial-balance-${p.to}`, rows: r.rows.map((x) => ({ section: x.type, line: `${x.code} ${x.name}`, amount: x.debit, prior: x.credit })) };
      pane.innerHTML = `<div class="doc">${header("Trial Balance", `As at ${fmtDate(r.as_at)}`)}
        <table class="fs"><thead><tr><th>Account</th><th>Debit</th><th>Credit</th></tr></thead><tbody>
        ${r.rows.map((x) => `<tr class="line link" data-code="${esc(x.code)}"><td><span class="code">${esc(x.code)}</span>${esc(x.name)} <span class="muted small" style="text-transform:capitalize">· ${esc(x.type)}</span></td><td class="amt">${x.debit ? acc(x.debit) : ""}</td><td class="amt">${x.credit ? acc(x.credit) : ""}</td></tr>`).join("")}
        <tr class="grand"><td>Totals</td><td class="amt">${acc(r.total_debit)}</td><td class="amt">${acc(r.total_credit)}</td></tr></tbody></table></div>`;
      setCheck(r.balanced, "Debits = credits", "Trial balance does not balance");
    },
  };

  const show = async () => {
    el.querySelectorAll("[data-tab]").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
    el.querySelector('[name="compare"]').closest("label").style.display = tab === "income" || tab === "position" ? "" : "none";
    pane.innerHTML = `<div class="empty">Preparing statement…</div>`;
    await views[tab]();
    wireLinks();
  };
  el.querySelectorAll("[data-tab]").forEach((b) => (b.onclick = () => { tab = b.dataset.tab; history.replaceState(null, "", `#/statements/${tab}`); show(); }));
  el.querySelector('[name="currency"]')?.addEventListener("change", (e) => { p.currency = e.target.value; show(); });
  el.querySelector('[name="preset"]').onchange = (e) => {
    const pr = presets.find((x) => x.value === e.target.value);
    p.preset = pr.value;
    if (pr.from) { p.from = pr.from; p.to = pr.to; el.querySelector('[name="from"]').value = p.from; el.querySelector('[name="to"]').value = p.to; show(); }
  };
  ["from", "to"].forEach((n) => (el.querySelector(`[name="${n}"]`).onchange = (e) => {
    p[n] = e.target.value;
    p.preset = "custom";
    el.querySelector('[name="preset"]').value = "custom";
    if (p.from && p.to && p.from <= p.to) show();
  }));
  el.querySelector('[name="compare"]').onchange = (e) => { p.compare = e.target.checked; show(); };
  el.querySelector("#print").onclick = () => window.print();
  el.querySelector("#csv").onclick = () => downloadCSV(csv.name, [{ key: "section", label: "Section" }, { key: "line", label: "Line item" },
    { key: "amount", label: tab === "trial" ? "Debit" : "Amount" }, { key: "prior", label: tab === "trial" ? "Credit" : "Comparative" }], csv.rows);
  await show();
}
