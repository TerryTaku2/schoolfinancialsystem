// Payroll: monthly runs (ZIMRA PAYE, AIDS levy, NSSA, ZIMDEF), payslips, remittances, returns and tax tables.
import { api, ApiError } from "../api.js";
import { badge, baseCurrency, confirmDialog, currencies, downloadCSV, esc, fmtDate, formModal, handleError, has, icon, isDual, modal, money, moneyList, printModal, selectHtml, showFieldErrors, state, table, toast, today } from "../ui.js";

// Totals per currency of a run (what is actually paid and withheld in each), e.g. "US$9,500 · ZiG 26,000".
const perCurrency = (run, key) => moneyList((run.by_currency || []).map((x) => ({ currency: x.currency, amount: x[key] })));
const currencyOpts = () => currencies().map((c) => ({ value: c, label: c }));

const TYPE_LABEL = { allowance: "Allowance", benefit: "Taxable benefit (non-cash)", pension: "Pension contribution", medical_aid: "Medical aid", deduction: "Other deduction" };
const PARAMS = [
  ["aids_levy_pct", "AIDS levy (% of PAYE)"], ["nssa_rate_pct", "NSSA rate (% each, employee and employer)"],
  ["nssa_ceiling", "NSSA insurable earnings ceiling (monthly)"], ["wcif_pct", "WCIF (% of insurable earnings, employer)"],
  ["zimdef_pct", "ZIMDEF levy (% of gross, employer)"], ["pension_cap", "Pension deduction cap (monthly)"],
  ["elderly_credit", "Elderly person's credit (monthly)"], ["elderly_age", "Elderly credit from age"],
  ["disabled_credit", "Blind / disabled credit (monthly)"], ["medical_credit_pct", "Medical credit (% of contributions)"],
  ["bonus_exempt", "Bonus exemption (per tax year)"],
];
const monthLabel = (p) => fmtDate(p + "-01", { month: "long", year: "numeric" });

export default async function (el, [tabParam, idParam]) {
  const tabs = [["runs", "Payroll runs"], ["staff", "Staff pay setup"], ["returns", "ZIMRA returns"], ["tables", "Tax tables"]];
  let tab = tabs.some(([k]) => k === tabParam) ? tabParam : "runs";
  const settings = await api.get("/accounting/settings");
  const cashAccounts = settings.cash_accounts.filter((a) => a.active);
  const bankId = cashAccounts.find((a) => a.code === "1010")?.id;

  el.innerHTML = `
    <div class="page-head"><div><h1>Payroll</h1><p>PAYE under ZIMRA's Final Deduction System, with AIDS levy, NSSA and ZIMDEF. Prepared by the bursar, approved by an administrator.</p></div>
      <div class="page-actions"><button class="btn primary" id="new-run">${icon("plus")} Prepare payroll</button></div></div>
    <div class="tabs">${tabs.map(([k, l]) => `<button data-tab="${k}" class="${k === tab ? "active" : ""}">${l}</button>`).join("")}</div>
    <div id="pane"></div>`;
  const pane = el.querySelector("#pane");

  // ------------------------------------------------------------ runs
  async function runs() {
    if (idParam) return runDetail(+idParam);
    const r = await api.get("/payroll/runs");
    pane.innerHTML = `<div class="card"><div id="tbl"></div></div>`;
    table(pane.querySelector("#tbl"), {
      rows: r.items, sortKey: null, onRowClick: (x) => openRun(x.id), empty: "No payrolls yet. Use Prepare payroll to start.",
      columns: [
        { key: "period", label: "Month", render: (x) => `<b>${esc(monthLabel(x.period))}</b><br><span class="muted small">${esc(x.run_no)}</span>` },
        { key: "status", label: "Status", render: (x) => badge(x.status) },
        { key: "employees", label: "Staff", num: true, render: (x) => x.totals.employees },
        { key: "gross", label: "Gross", num: true, render: (x) => (isDual() ? perCurrency(x, "gross") : money(x.totals.gross)) },
        { key: "tax", label: "PAYE + AIDS levy", num: true, render: (x) => (isDual() ? perCurrency(x, "total_tax") : money(x.totals.total_tax)) },
        { key: "net", label: "Net pay", num: true, render: (x) => (isDual() ? perCurrency(x, "net") : money(x.totals.net)) },
        { key: "cost", label: "Employer cost", num: true, render: (x) => (isDual() ? perCurrency(x, "employer_cost") : money(x.totals.employer_cost)) },
        { key: "zimra_due", label: "ZIMRA due", render: (x) => (x.status === "void" ? "—" : fmtDate(x.zimra_due)) },
      ],
    });
  }

  function openRun(id) { idParam = String(id); history.replaceState(null, "", `#/payroll/runs/${id}`); show(); }

  async function runDetail(id) {
    const run = await api.get(`/payroll/runs/${id}`);
    const t = run.totals;
    const draft = run.status === "draft";
    const canApprove = draft && has("payroll.approve") && run.created_by !== state.user.id;
    const btns = [
      `<button class="btn" id="back">${icon("back")} All payrolls</button>`,
      `<button class="btn" id="csv">${icon("download")} Payroll CSV</button>`,
      `<button class="btn" id="nssa">${icon("download")} NSSA schedule</button>`,
      draft ? `<button class="btn" id="recalc">Recalculate</button>` : "",
      canApprove ? `<button class="btn primary" id="approve">Approve</button>` : "",
      run.status === "approved" ? `<button class="btn primary" id="pay">Pay salaries</button>` : "",
      ((draft && has("payroll.manage")) || (run.status === "approved" && has("payroll.approve"))) && !run.remittances.some((r) => r.paid_on) ? `<button class="btn danger" id="void">${draft ? "Discard draft" : "Void"}</button>` : "",
    ].join("");
    pane.innerHTML = `
      <div class="card" style="margin-bottom:16px"><div class="card-head"><div><h3>${esc(monthLabel(run.period))} · ${esc(run.run_no)} ${badge(run.status)}</h3>
        <div class="sub">Pay date ${fmtDate(run.pay_date)} · ${esc(run.tax_table)} · prepared by ${esc(run.prepared_by || "—")}${run.approved_by ? ` · approved by ${esc(run.approved_by)} on ${fmtDate(run.approved_at)}` : ""}${run.paid_at ? ` · paid ${fmtDate(run.paid_at)} from ${esc(run.paid_from)}` : ""}${run.void_reason ? ` · void: ${esc(run.void_reason)}` : ""}</div></div>
        <div class="row" style="flex-wrap:wrap;gap:6px">${btns}</div></div>
        ${draft && !canApprove && has("payroll.approve") ? `<div class="card-body"><div class="notice warn">You prepared this payroll, so someone else with approval rights must approve it.</div></div>` : ""}
        ${draft && !has("payroll.approve") ? `<div class="card-body"><div class="notice">Draft. Enter overtime, bonuses and one-off deductions with Adjust, then ask someone with payroll approval rights to approve it.</div></div>` : ""}</div>
      <div class="grid g-4" style="margin-bottom:16px">${[["Gross pay", t.gross, `${t.employees} employees`], ["PAYE + AIDS levy", t.total_tax, `Due to ZIMRA by ${fmtDate(run.zimra_due)}`],
        ["NSSA (employee + employer)", t.nssa + t.nssa_employer + t.wcif, t.wcif ? `incl. WCIF ${money(t.wcif)}` : "4.5% + 4.5% up to the ceiling"], ["Net pay", t.net, `Employer cost ${money(t.employer_cost)}`]]
        .map(([l, v, f], i) => {
          // With pay in two currencies, show what is paid/withheld in each.
          const key = ["gross", "total_tax", null, "net"][i];
          const split = (run.by_currency || []).length > 1 && key;
          return `<div class="card stat"><div class="label">${l}</div><div class="value" style="${split ? "font-size:18px" : ""}">${split ? (run.by_currency.map((x) => `<div>${money(x[key], x.currency)}</div>`).join("")) : money(v, run.tax_currency)}</div><div class="foot">${esc(f)}</div></div>`;
        }).join("")}</div>
      ${(run.by_currency || []).length > 1 ? `<div class="notice" style="margin-bottom:16px">Pay is in ${run.by_currency.map((x) => esc(x.currency)).join(", ")}. PAYE is worked out on the total in ${esc(run.tax_currency)} at 1 USD = ${Object.entries(run.exchange_rates || {}).filter(([c]) => run.by_currency.some((x) => x.currency === c) || c === run.tax_currency).map(([c, r]) => `${r} ${esc(c)}`).join(" and ") || "—"} (rates for ${fmtDate(run.pay_date)}) and withheld from each currency in proportion to the pay in it, as ZIMRA requires. Table figures are in ${esc(run.tax_currency)} terms.</div>` : ""}
      <div class="card" style="margin-bottom:16px"><div id="slips"></div></div>
      ${run.status !== "draft" && run.status !== "void" ? `<div class="card"><div class="card-head"><div><h3>Statutory and third-party remittances</h3><div class="sub">PAYE (ZIMRA P2 return) and NSSA are due by the 10th of the following month</div></div></div><div id="rem"></div></div>` : ""}`;
    const cols = [
      { key: "name", label: "Employee", render: (p) => `<b>${esc(p.name)}</b><br><span class="muted small">${esc(p.staff_no)} · ${esc(p.position)}${p.tax_number ? "" : " · <span class=\"badge warn\">no BP no.</span>"}</span>`, csv: (p) => p.name },
      { key: "staff_no", label: "Staff no.", csv: (p) => p.staff_no, render: (p) => esc(p.staff_no) },
      { key: "gross", label: "Gross", num: true, render: (p) => `${money(p.gross)}${p.bonus || p.overtime ? `<br><span class="muted small">${p.bonus ? `bonus ${money(p.bonus)}` : ""}${p.overtime ? ` OT ${money(p.overtime)}` : ""}</span>` : ""}`, csv: (p) => p.gross },
      { key: "nssa", label: "NSSA", num: true, render: (p) => money(p.nssa) },
      { key: "taxable", label: "Taxable", num: true, render: (p) => money(p.taxable) },
      { key: "paye", label: "PAYE", num: true, render: (p) => `${money(p.paye)}${p.credits ? `<br><span class="muted small">after credits ${money(p.credits)}</span>` : ""}`, csv: (p) => p.paye },
      { key: "aids_levy", label: "AIDS levy", num: true, render: (p) => money(p.aids_levy) },
      { key: "other", label: "Other deductions", num: true, render: (p) => money(p.pension + p.medical_aid + p.other_deductions), csv: (p) => (p.pension + p.medical_aid + p.other_deductions).toFixed(2) },
      { key: "net", label: "Net pay", num: true, render: (p) => `<b class="${p.parts.some((x) => x.net < 0) ? "check-bad" : ""}">${p.parts.length > 1 ? p.parts.map((x) => money(x.net, x.currency)).join("<br>") : money(p.parts[0]?.net ?? p.net, p.parts[0]?.currency)}</b>`, csv: (p) => p.parts.map((x) => `${x.currency} ${x.net}`).join("; ") },
      { key: "id", label: "", sort: false, cls: "actions", csv: () => "", render: (p) => `<button class="btn sm" data-slip="${p.id}">Payslip</button>${draft ? ` <button class="btn sm" data-adj="${p.id}">Adjust</button>` : ""}` },
    ];
    table(pane.querySelector("#slips"), { rows: run.payslips, columns: cols.filter((c) => c.key !== "staff_no"), sortKey: "name",
      footer: `<td>Totals</td><td class="num">${money(t.gross)}</td><td class="num">${money(t.nssa)}</td><td class="num">${money(t.taxable)}</td><td class="num">${money(t.paye)}</td><td class="num">${money(t.aids_levy)}</td><td class="num">${money(t.pension + t.medical_aid + t.other_deductions)}</td><td class="num">${money(t.net)}</td><td></td>` });
    pane.querySelectorAll("[data-slip]").forEach((b) => (b.onclick = () => openPayslip(+b.dataset.slip)));
    pane.querySelectorAll("[data-adj]").forEach((b) => (b.onclick = async () => {
      const p = run.payslips.find((x) => x.id === +b.dataset.adj);
      const r = await formModal({
        title: `Adjust ${p.name} · ${monthLabel(run.period)}`,
        intro: `<p class="muted small" style="margin-top:0">One-off amounts for this month only. Recurring allowances and deductions are set under Staff pay setup. Bonuses are tax-free up to the annual exemption.</p>`,
        fields: [{ name: "overtime", label: "Overtime / acting allowance", type: "number", min: 0, step: 0.01 },
          ...(isDual() ? [{ name: "overtime_currency", label: "Overtime paid in", type: "select", required: true, options: currencyOpts() }] : []),
          { name: "bonus", label: "Bonus", type: "number", min: 0, step: 0.01 },
          ...(isDual() ? [{ name: "bonus_currency", label: "Bonus paid in", type: "select", required: true, options: currencyOpts() }] : []),
          { name: "extra_deduction", label: "One-off deduction", type: "number", min: 0, step: 0.01 },
          ...(isDual() ? [{ name: "extra_deduction_currency", label: "Deducted in", type: "select", required: true, options: currencyOpts() }] : []),
          { name: "extra_deduction_note", label: "Deduction description", placeholder: "e.g. Salary advance recovery" }],
        values: p, onSubmit: (d) => api.put(`/payroll/payslips/${p.id}`, d),
      });
      if (r) { toast("Payslip recalculated", "success"); runDetail(id); }
    }));
    if (run.remittances && pane.querySelector("#rem")) {
      table(pane.querySelector("#rem"), { rows: run.remittances, sortKey: null, empty: "Nothing to remit.", columns: [
        { key: "label", label: "Payee" }, { key: "due", label: "Amount", num: true, render: (r) => money(r.due, r.currency) },
        ...(isDual() ? [{ key: "currency", label: "Currency" }] : []),
        { key: "paid_on", label: "Paid", render: (r) => (r.paid_on ? `${fmtDate(r.paid_on)} · ${esc(r.reference || "")}<br><span class="muted small">${esc(r.account)}</span>` : `<span class="badge warn">outstanding</span>`) },
        { key: "type", label: "", sort: false, cls: "actions", render: (r) => (r.paid_on ? "" : `<button class="btn sm" data-remit="${r.type}" data-cur="${r.currency}">Record payment</button>`) }] });
      pane.querySelectorAll("[data-remit]").forEach((b) => (b.onclick = async () => {
        const rem = run.remittances.find((x) => x.type === b.dataset.remit && x.currency === b.dataset.cur);
        const accts = cashAccounts.filter((a) => !a.currency || a.currency === rem.currency);
        const r = await formModal({
          title: `Pay ${rem.label} · ${money(rem.due, rem.currency)}`, cols: 1,
          fields: [{ name: "account_id", label: "Paid from", type: "select", required: true, options: accts.map((a) => ({ value: a.id, label: a.name })), default: (accts.find((a) => a.code === "1010" || a.code === "1011") || accts[0])?.id },
            { name: "paid_on", label: "Payment date", type: "date", required: true, default: today(), max: today() },
            { name: "reference", label: "Reference", required: true, placeholder: rem.type === "zimra" ? "ZIMRA payment / assessment no." : "Bank reference" }],
          onSubmit: (d) => api.post(`/payroll/runs/${id}/remit`, { ...d, type: rem.type, currency: rem.currency }),
        });
        if (r) { toast("Remittance recorded", "success"); runDetail(id); }
      }));
    }
    pane.querySelector("#back").onclick = () => { idParam = null; history.replaceState(null, "", "#/payroll/runs"); show(); };
    pane.querySelector("#csv").onclick = () => downloadCSV(`payroll-${run.period}`, [
      { key: "staff_no", label: "Staff no." }, { key: "name", label: "Employee" }, { key: "national_id", label: "National ID" }, { key: "tax_number", label: "ZIMRA BP no." },
      ...["basic", "gross", "benefits", "nssa", "pension", "medical_aid", "taxable", "credits", "paye", "aids_levy", "other_deductions", "net", "nssa_employer", "wcif", "zimdef"].map((k) => ({ key: k, label: k.replace(/_/g, " ") }))], run.payslips);
    pane.querySelector("#nssa").onclick = () => downloadCSV(`nssa-schedule-${run.period}`, [
      { key: "nssa_number", label: "NSSA no." }, { key: "national_id", label: "National ID" }, { key: "name", label: "Employee" },
      { key: "gross", label: "Gross earnings" }, { key: "nssa", label: "Employee contribution" }, { key: "nssa_employer", label: "Employer contribution" }, { key: "wcif", label: "WCIF" },
      { key: "total", label: "Total", csv: (p) => (p.nssa + p.nssa_employer + p.wcif).toFixed(2) }], run.payslips);
    const act = async (path, data, msg) => { try { await api.post(path, data); toast(msg, "success"); runDetail(id); } catch (err) { handleError(err); } };
    pane.querySelector("#recalc")?.addEventListener("click", () => act(`/payroll/runs/${id}/recalculate`, {}, "Recalculated from current staff records"));
    pane.querySelector("#approve")?.addEventListener("click", async () => {
      if (await confirmDialog(`Approve the ${monthLabel(run.period)} payroll? Net pay ${perCurrency(run, "net")}, PAYE and AIDS levy ${perCurrency(run, "total_tax")}. It is posted to the ledger and can no longer be edited.`, { confirmText: "Approve" }))
        act(`/payroll/runs/${id}/approve`, {}, "Payroll approved and posted");
    });
    pane.querySelector("#pay")?.addEventListener("click", async () => {
      const balances = Object.fromEntries((await api.get("/accounting/accounts")).items.map((a) => [a.id, a.balance]));
      // Each currency's net pay comes out of an account in that currency.
      const due = (run.by_currency || []).filter((x) => x.net > 0);
      const r = await formModal({
        title: `Pay salaries · ${perCurrency(run, "net")}`, cols: 1,
        intro: `<p class="muted" style="margin-top:0">Posts Dr Net Salaries Payable / Cr the chosen account${due.length > 1 ? " for each currency" : ""}. Refused if an account lacks funds.</p>`,
        fields: [...due.map((x) => {
          const accts = cashAccounts.filter((a) => !a.currency || a.currency === x.currency);
          return { name: `acct_${x.currency}`, label: `Pay ${money(x.net, x.currency)} from`, type: "select", required: true,
            options: accts.map((a) => ({ value: a.id, label: `${a.name} (balance ${money(balances[a.id], a.currency)})` })),
            default: (accts.find((a) => a.code === "1010" || a.code === "1011") || accts[0])?.id };
        }),
          { name: "paid_on", label: "Payment date", type: "date", required: true, default: run.pay_date < today() ? run.pay_date : today(), max: today() }],
        onSubmit: (d) => api.post(`/payroll/runs/${id}/pay`, { paid_on: d.paid_on, accounts: Object.fromEntries(due.map((x) => [x.currency, d[`acct_${x.currency}`]])) }),
      });
      if (r) { toast("Salaries paid", "success"); runDetail(id); }
    });
    pane.querySelector("#void")?.addEventListener("click", async () => {
      const reason = await confirmDialog(draft ? "Discard this draft payroll?" : "Void this approved payroll? The ledger accrual is reversed today.", { title: draft ? "Discard draft" : "Void payroll", confirmText: draft ? "Discard" : "Void", danger: true, reason: true });
      if (reason) act(`/payroll/runs/${id}/void`, { reason }, draft ? "Draft discarded" : "Payroll voided");
    });
  }

  async function openPayslip(id) {
    try {
      const p = await api.get(`/payroll/payslips/${id}`);
      const rows = (items) => items.map((i) => `<tr><td>${esc(i.name)}</td><td style="text-align:right">${money(i.amount, i.currency)}</td></tr>`).join("");
      const T = p.tax_currency;
      const tm = (v) => money(v, T);
      const m = modal({
        title: `Payslip · ${p.name}`, wide: true,
        body: `<div class="row no-print" style="justify-content:flex-end;margin-bottom:12px"><button class="btn" data-print>${icon("print")} Print</button></div>
        <div class="doc"><div class="doc-head"><div><h2>${esc(state.meta.school)}</h2><div class="muted">Payslip for ${esc(monthLabel(p.period))}</div></div>
          <div style="text-align:right"><h2>PAYSLIP</h2><div class="muted">${esc(p.run_no)} · paid ${fmtDate(p.pay_date)}</div></div></div>
        <table><tr><th>Employee</th><td>${esc(p.name)} (${esc(p.staff_no)})</td><th>Position</th><td>${esc(p.position)}${p.department ? `, ${esc(p.department)}` : ""}</td></tr>
          <tr><th>National ID</th><td>${esc(p.national_id || "—")}</td><th>ZIMRA BP no.</th><td>${esc(p.tax_number || "—")}</td></tr>
          <tr><th>NSSA no.</th><td>${esc(p.nssa_number || "—")}</td><th>Bank</th><td>${esc([p.bank_name, p.bank_account].filter(Boolean).join(" ") || "—")}</td></tr></table>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">
          <table><thead><tr><th>Earnings</th><th style="text-align:right">Amount</th></tr></thead><tbody>${rows(p.earnings)}</tbody>
            <tfoot>${p.parts.map((x) => `<tr><th>Gross pay${p.parts.length > 1 ? ` (${esc(x.currency)})` : ""}</th><th style="text-align:right">${money(x.gross, x.currency)}</th></tr>`).join("")}</tfoot></table>
          <table><thead><tr><th>Deductions</th><th style="text-align:right">Amount</th></tr></thead><tbody>${rows(p.deductions)}</tbody>
            <tfoot>${p.parts.map((x) => `<tr><th>Total deductions${p.parts.length > 1 ? ` (${esc(x.currency)})` : ""}</th><th style="text-align:right">${money(x.gross - x.net, x.currency)}</th></tr>`).join("")}</tfoot></table></div>
        <p style="font-size:18px"><b>Net pay: ${p.parts.map((x) => money(x.net, x.currency)).join(" + ")}</b></p>
        <h3>Tax calculation (ZIMRA FDS)</h3>
        <table><tbody>
          <tr><td>Gross pay${p.benefit_items.length ? " + taxable benefits" : ""}</td><td style="text-align:right">${tm(p.gross + p.benefits)}</td></tr>
          ${p.benefit_items.map((b) => `<tr><td class="muted">&nbsp;&nbsp;incl. ${esc(b.name)} (non-cash)</td><td style="text-align:right" class="muted">${money(b.amount, b.currency)}</td></tr>`).join("")}
          ${p.exempt ? `<tr><td>Less exempt income (bonus exemption, exempt allowances)</td><td style="text-align:right">(${tm(p.exempt)})</td></tr>` : ""}
          <tr><td>Less NSSA (4.5% of insurable earnings ${tm(p.insurable)})</td><td style="text-align:right">(${tm(p.nssa)})</td></tr>
          ${p.pension_deductible ? `<tr><td>Less pension contributions (deductible portion)</td><td style="text-align:right">(${tm(p.pension_deductible)})</td></tr>` : ""}
          <tr><th>Taxable income</th><th style="text-align:right">${tm(p.taxable)}</th></tr>
          <tr><td>Income tax per ZIMRA bands</td><td style="text-align:right">${tm(p.tax_before_credits)}</td></tr>
          ${p.credit_items.map((c) => `<tr><td>Less ${esc(c.name)}</td><td style="text-align:right">(${tm(c.amount)})</td></tr>`).join("")}
          <tr><td>PAYE</td><td style="text-align:right">${tm(p.paye)}</td></tr>
          <tr><td>AIDS levy (3% of PAYE)</td><td style="text-align:right">${tm(p.aids_levy)}</td></tr>
          <tr><th>Total tax</th><th style="text-align:right">${tm(p.total_tax)}</th></tr>
          ${p.parts.length > 1 ? p.parts.map((x) => `<tr><td class="muted">&nbsp;&nbsp;withheld in ${esc(x.currency)}</td><td style="text-align:right" class="muted">${money(x.paye + x.aids_levy, x.currency)}</td></tr>`).join("") : ""}</tbody></table>
        ${p.notes.length ? `<p class="muted">${p.notes.map(esc).join("<br>")}</p>` : ""}
        <h3>Year to date</h3>
        <table><tr><th>Gross</th><th>Taxable</th><th>PAYE</th><th>AIDS levy</th><th>NSSA</th><th>Pension</th><th>Net</th></tr>
          <tr>${["gross", "taxable", "paye", "aids_levy", "nssa", "pension", "net"].map((k) => `<td>${tm(p.ytd[k])}</td>`).join("")}</tr></table>${p.parts.length > 1 ? `<p class="muted small">Year-to-date figures are in ${esc(T)} terms.</p>` : ""}
        <p class="muted small">Employer contributions this month: NSSA ${tm(p.nssa_employer)}${p.wcif ? `, WCIF ${tm(p.wcif)}` : ""}, ZIMDEF ${tm(p.zimdef)}.</p></div>`,
      });
      m.el.querySelector("[data-print]").onclick = printModal;
    } catch (err) { handleError(err); }
  }

  async function newRun() {
    const r = await formModal({
      title: "Prepare payroll", cols: 1,
      intro: `<p class="muted" style="margin-top:0">Creates a draft with a payslip for every active staff member with a salary, using the tax table in force for that month. Nothing is posted until an administrator approves it.</p>`,
      fields: [{ name: "period", label: "Month", type: "month", required: true, default: today().slice(0, 7), max: today().slice(0, 7) },
        { name: "pay_date", label: "Pay date", type: "date", hint: "Defaults to the 25th" },
        { name: "notes", label: "Notes" }],
      onSubmit: (d) => api.post("/payroll/runs", d),
    });
    if (r) { toast(`${r.run_no} prepared`, "success"); tab = "runs"; openRun(r.id); }
  }

  // ------------------------------------------------------------ staff pay setup
  async function staffSetup() {
    const r = await api.get("/payroll/staff");
    pane.innerHTML = `<div class="card"><div class="card-head"><div><h3>Staff pay setup</h3><div class="sub">Basic salary is set on the staff record (administrators can also change it here). Recurring items apply every month until switched off.</div></div></div><div id="tbl"></div></div>`;
    table(pane.querySelector("#tbl"), {
      rows: r.items, sortKey: "name",
      columns: [
        { key: "name", label: "Employee", render: (s) => `<b>${esc(s.name)}</b><br><span class="muted small">${esc(s.staff_no)} · ${esc(s.position)}</span>`, sort: (s) => s.name },
        { key: "salary", label: "Basic", num: true, render: (s) => money(s.salary, s.salary_currency) },
        { key: "items", label: "Recurring items", sort: false, render: (s) => s.items.filter((i) => i.active).map((i) => `<span class="badge plain">${esc(i.name)} ${money(i.amount, i.currency)}</span>`).join(" ") || '<span class="muted small">—</span>' },
        { key: "tax_number", label: "Statutory details", sort: false, render: (s) => (s.missing.length ? `<span class="badge warn">missing ${esc(s.missing.join(", "))}</span>` : `<span class="muted small">BP ${esc(s.tax_number)} · NSSA ${esc(s.nssa_number)}</span>`) + (s.disabled ? ' <span class="badge info">disabled credit</span>' : "") },
        { key: "id", label: "", sort: false, cls: "actions", render: (s) => `<button class="btn sm" data-det="${s.id}">Details</button> <button class="btn sm" data-items="${s.id}">Pay items</button>` },
      ],
    });
    pane.querySelectorAll("[data-det]").forEach((b) => (b.onclick = async () => {
      const s = r.items.find((x) => x.id === +b.dataset.det);
      const ok = await formModal({
        title: `Statutory details · ${s.name}`, values: s,
        fields: [
          ...(has("payroll.approve") ? [{ name: "salary", label: "Basic monthly salary", type: "number", min: 0, step: 0.01 }] : []),
          ...(has("payroll.approve") && isDual() ? [{ name: "salary_currency", label: "Basic salary paid in", type: "select", required: true, options: currencyOpts(), hint: "Add pay in the other currency as an allowance" }] : []),
          { name: "national_id", label: "National ID" }, { name: "tax_number", label: "ZIMRA BP number / TIN" },
          { name: "nssa_number", label: "NSSA number" }, { name: "dob", label: "Date of birth", type: "date", max: today(), hint: "Elderly credit applies from 55" },
          { name: "bank_name", label: "Bank" }, { name: "bank_account", label: "Account number" },
          { name: "disabled", label: "Blind or disabled (qualifies for the tax credit)", type: "checkbox", full: true }],
        onSubmit: (d) => api.put(`/payroll/staff/${s.id}`, d),
      });
      if (ok) { toast("Saved", "success"); staffSetup(); }
    }));
    pane.querySelectorAll("[data-items]").forEach((b) => (b.onclick = () => payItems(r.items.find((x) => x.id === +b.dataset.items), r.types)));
  }

  function payItems(s, types) {
    const m = modal({ title: `Recurring pay items · ${s.name}`, wide: true, body: `<div id="it"></div>
      <form class="form" id="add-item" novalidate style="margin-top:14px"><h3 style="margin:0">Add item</h3><div class="cols-3">
        <label class="field"><span>Type *</span>${selectHtml("type", types.map((t) => ({ value: t, label: TYPE_LABEL[t] })), "allowance")}</label>
        <label class="field"><span>Name *</span><input class="input" name="name" placeholder="e.g. Transport allowance"><small class="err" data-err="name"></small></label>
        <label class="field"><span>Monthly amount *</span><input class="input" name="amount" type="number" min="0.01" step="0.01"><small class="err" data-err="amount"></small></label>
        ${isDual() ? `<label class="field"><span>Currency *</span>${selectHtml("currency", currencyOpts(), baseCurrency())}</label>` : ""}</div>
        <label class="check"><input type="checkbox" name="taxable" checked> Taxable (allowances only; untick for exempt allowances)</label>
        <div class="row"><button class="btn primary">Add</button></div></form>`, actions: [{ label: "Done" }], onClose: () => staffSetup() });
    const draw = () => {
      table(m.el.querySelector("#it"), { rows: s.items, sortKey: null, empty: "No recurring items.", columns: [
        { key: "type", label: "Type", render: (i) => esc(TYPE_LABEL[i.type]) },
        { key: "name", label: "Item", render: (i) => `${esc(i.name)}${i.type === "allowance" && !i.taxable ? ' <span class="badge info">exempt</span>' : ""}` },
        { key: "amount", label: "Amount", num: true, render: (i) => money(i.amount, i.currency) },
        { key: "active", label: "", sort: false, cls: "actions", render: (i) => `<button class="btn sm" data-tog="${i.id}">${i.active ? "Switch off" : "Switch on"}</button> <button class="btn sm danger" data-del="${i.id}">Remove</button>` }] });
      m.el.querySelectorAll("[data-tog]").forEach((b) => (b.onclick = async () => {
        const i = s.items.find((x) => x.id === +b.dataset.tog);
        try { Object.assign(i, await api.put(`/payroll/items/${i.id}`, { active: !i.active })); draw(); } catch (err) { handleError(err); }
      }));
      m.el.querySelectorAll("[data-del]").forEach((b) => (b.onclick = async () => {
        try { await api.del(`/payroll/items/${b.dataset.del}`); s.items = s.items.filter((x) => x.id !== +b.dataset.del); draw(); } catch (err) { handleError(err); }
      }));
    };
    m.el.querySelector("#add-item").onsubmit = async (e) => {
      e.preventDefault();
      const f = e.target;
      try {
        s.items.push(await api.post(`/payroll/staff/${s.id}/items`, { type: f.type.value, name: f.name.value, amount: f.amount.value, taxable: f.taxable.checked, currency: f.currency?.value }));
        f.reset();
        draw();
      } catch (err) {
        if (err instanceof ApiError) showFieldErrors(f, err);
        handleError(err);
      }
    };
    draw();
  }

  // ------------------------------------------------------------ returns
  async function returns(year = new Date(today()).getFullYear()) {
    const r = await api.get("/payroll/returns", { year });
    const years = Array.from({ length: 5 }, (_, i) => new Date(today()).getFullYear() - i);
    pane.innerHTML = `<div class="toolbar" style="margin-bottom:12px">${selectHtml("year", years.map((y) => ({ value: y, label: `Tax year ${y}` })), year)}</div>
      <div class="card" style="margin-bottom:16px"><div class="card-head"><div><h3>Monthly PAYE returns (ZIMRA P2)</h3><div class="sub">PAYE and AIDS levy withheld each month, due by the 10th of the following month</div></div><button class="btn sm" id="xp2">${icon("download")} CSV</button></div><div id="p2"></div></div>
      <div class="card"><div class="card-head"><div><h3>Annual employee tax summary</h3><div class="sub">Figures for P6 tax certificates and the ITF16 annual return</div></div><button class="btn sm" id="xan">${icon("download")} CSV</button></div><div id="an"></div></div>`;
    const p2cols = [
      { key: "period", label: "Month", render: (x) => esc(monthLabel(x.period)) }, { key: "employees", label: "Employees", num: true },
      ...(isDual() ? [{ key: "currency", label: "Paid in" }] : []),
      { key: "gross_remuneration", label: "Gross remuneration", num: true, render: (x) => money(x.gross_remuneration, x.currency) },
      { key: "paye", label: "PAYE", num: true, render: (x) => money(x.paye, x.currency) }, { key: "aids_levy", label: "AIDS levy", num: true, render: (x) => money(x.aids_levy, x.currency) },
      { key: "total_due", label: "Total due", num: true, render: (x) => `<b>${money(x.total_due, x.currency)}</b>` },
      { key: "due_date", label: "Due", render: (x) => fmtDate(x.due_date) },
      { key: "remitted_on", label: "Remitted", render: (x) => (x.remitted_on ? `${fmtDate(x.remitted_on)}${x.late ? ' <span class="badge bad">late</span>' : ""}<br><span class="muted small">${esc(x.reference || "")}</span>` : x.late ? '<span class="badge bad">overdue</span>' : '<span class="badge warn">not yet</span>'), csv: (x) => x.remitted_on || "" },
    ];
    table(pane.querySelector("#p2"), { rows: r.p2, columns: p2cols, sortKey: null, empty: "No approved payrolls in this year." });
    const ancols = [
      { key: "name", label: "Employee", render: (x) => `<b>${esc(x.name)}</b><br><span class="muted small">${esc(x.staff_no)}${x.tax_number ? ` · BP ${esc(x.tax_number)}` : ""}</span>`, csv: (x) => x.name },
      { key: "national_id", label: "National ID" }, { key: "months", label: "Months", num: true },
      ...[["gross", "Gross"], ["benefits", "Benefits"], ["exempt", "Exempt"], ["nssa", "NSSA"], ["pension", "Pension"], ["taxable", "Taxable"], ["credits", "Credits"], ["paye", "PAYE"], ["aids_levy", "AIDS levy"], ["total_tax", "Total tax"]]
        .map(([k, l]) => ({ key: k, label: l, num: true, render: (x) => money(x[k]) })),
    ];
    table(pane.querySelector("#an"), { rows: r.annual, columns: ancols, sortKey: "name", empty: "No approved payrolls in this year." });
    pane.querySelector('[name="year"]').onchange = (e) => returns(+e.target.value);
    pane.querySelector("#xp2").onclick = () => downloadCSV(`zimra-p2-${year}`, p2cols, r.p2);
    pane.querySelector("#xan").onclick = () => downloadCSV(`employee-tax-summary-${year}`, [{ key: "staff_no", label: "Staff no." }, { key: "tax_number", label: "ZIMRA BP no." }, ...ancols], r.annual);
  }

  // ------------------------------------------------------------ tax tables
  async function tables() {
    const r = await api.get("/payroll/tax-tables");
    const cur = r.items.find((t) => t.effective_from <= today()) || r.items[r.items.length - 1];
    const cfg = cur.config;
    pane.innerHTML = `<div class="grid g-2">
      <div class="card"><div class="card-head"><div><h3>${esc(cur.name)}</h3><div class="sub">In force from ${fmtDate(cur.effective_from)} · monthly ${esc(cfg.currency)} bands</div></div>${has("payroll.approve") ? `<button class="btn sm" id="new-table">${icon("plus")} New tax table</button>` : ""}</div>
        <div class="table-wrap"><table class="table"><thead><tr><th>Monthly income</th><th class="num">Rate</th><th class="num">ZIMRA "less"</th></tr></thead><tbody>
        ${cfg.bands.map((b, i) => `<tr><td>${i ? `${money(cfg.bands[i - 1].upto + 0.01)} – ` : "0 – "}${b.upto === null ? "and above" : money(b.upto)}</td><td class="num">${b.rate}%</td><td class="num">${money(cur.less[i])}</td></tr>`).join("")}
        </tbody></table></div>
        <div class="table-wrap"><table class="table"><tbody>${PARAMS.map(([k, l]) => `<tr><td>${esc(l)}</td><td class="num">${k.endsWith("_pct") ? `${cfg[k]}%` : k === "elderly_age" ? cfg[k] : money(cfg[k])}</td></tr>`).join("")}</tbody></table></div>
        <div class="card-body muted small">Tax = income × rate − "less", which equals the progressive calculation. When a Finance Act changes the rates, add a new table effective from the date of change; earlier payrolls keep the table they were calculated with.</div></div>
      <div><div class="card" style="margin-bottom:16px"><div class="card-head"><h3>PAYE calculator</h3></div><div class="card-body">
        <form class="row" id="calc"><input class="input" type="number" name="gross" min="0" step="0.01" placeholder="Monthly gross" style="width:auto"><button class="btn">Calculate</button></form><div id="calc-out" style="margin-top:12px"></div>
        <p class="muted small">Basic salary only, no benefits, pension or credits.</p></div></div>
        <div class="card"><div class="card-head"><h3>All tables</h3></div><div id="all"></div></div></div></div>`;
    table(pane.querySelector("#all"), { rows: r.items, sortKey: null, columns: [{ key: "name", label: "Table" }, { key: "effective_from", label: "From", render: (t) => fmtDate(t.effective_from) }] });
    pane.querySelector("#calc").onsubmit = async (e) => {
      e.preventDefault();
      try {
        const c = await api.get("/payroll/calculator", { gross: e.target.gross.value || 0 });
        pane.querySelector("#calc-out").innerHTML = `<table class="table"><tbody>${[["Gross", c.gross], ["NSSA", -c.nssa], ["Taxable income", c.taxable], ["PAYE", -c.paye], ["AIDS levy", -c.aids_levy], ["Net pay", c.net]]
          .map(([l, v]) => `<tr><td>${l}</td><td class="num">${money(v)}</td></tr>`).join("")}</tbody></table>`;
      } catch (err) { handleError(err); }
    };
    pane.querySelector("#new-table")?.addEventListener("click", () => newTable(cfg));
  }

  function newTable(base) {
    let bands = base.bands.map((b) => ({ ...b }));
    const m = modal({
      title: "New tax table", wide: true,
      body: `<form class="form" novalidate><div class="cols"><label class="field"><span>Name *</span><input class="input" name="name" placeholder="e.g. ZIMRA USD tax tables 2027"><small class="err" data-err="name"></small></label>
        <label class="field"><span>Effective from *</span><input class="input" type="date" name="effective_from"><small class="err" data-err="effective_from"></small></label></div>
        <h3 style="margin:6px 0 0">Monthly bands</h3><div id="bands"></div><button type="button" class="btn sm" id="add-band">${icon("plus")} Add band</button>
        <h3 style="margin:6px 0 0">Other rates</h3><div class="cols">${PARAMS.map(([k, l]) => `<label class="field"><span>${esc(l)}</span><input class="input" type="number" step="0.01" min="0" name="${k}" value="${base[k]}"><small class="err" data-err="${k}"></small></label>`).join("")}</div></form>`,
      actions: [{ label: "Cancel" }, { label: "Save table", cls: "primary", onClick: async ({ el: mel }) => {
        const f = mel.querySelector("form");
        const config = { currency: base.currency, bands: bands.map((b, i) => ({ upto: i === bands.length - 1 ? null : b.upto, rate: b.rate })) };
        PARAMS.forEach(([k]) => (config[k] = f[k].value));
        try {
          await api.post("/payroll/tax-tables", { name: f.name.value, effective_from: f.effective_from.value, config });
          toast("Tax table saved", "success");
          tables();
        } catch (err) {
          if (err instanceof ApiError) { showFieldErrors(f, err); toast(err.message, "error"); return true; }
          throw err;
        }
      } }],
    });
    const draw = () => {
      const box = m.el.querySelector("#bands");
      box.innerHTML = bands.map((b, i) => `<div class="row" style="margin-bottom:6px">
        <span class="muted small" style="width:110px">${i ? `above ${esc(String(bands[i - 1].upto ?? ""))}` : "from 0"}</span>
        ${i === bands.length - 1 ? `<span class="input" style="width:140px;display:flex;align-items:center">and above</span>` : `<input class="input" type="number" min="0" step="0.01" style="width:140px" data-i="${i}" data-k="upto" value="${b.upto ?? ""}" aria-label="Up to">`}
        <input class="input" type="number" min="0" max="100" step="0.01" style="width:100px" data-i="${i}" data-k="rate" value="${b.rate}" aria-label="Rate %"> <span>%</span>
        <button type="button" class="btn ghost icon danger" data-rm="${i}" ${bands.length <= 2 ? "disabled" : ""} aria-label="Remove band">✕</button></div>`).join("");
      box.querySelectorAll("[data-k]").forEach((inp) => (inp.onchange = () => { bands[+inp.dataset.i][inp.dataset.k] = inp.value === "" ? null : +inp.value; draw(); }));
      box.querySelectorAll("[data-rm]").forEach((b) => (b.onclick = () => { bands.splice(+b.dataset.rm, 1); draw(); }));
    };
    m.el.querySelector("#add-band").onclick = () => { const last = bands[bands.length - 1]; bands.splice(bands.length - 1, 0, { upto: null, rate: last.rate }); draw(); };
    draw();
  }

  const views = { runs, staff: staffSetup, returns: () => returns(), tables };
  const show = async () => {
    el.querySelectorAll("[data-tab]").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
    pane.innerHTML = `<div class="empty">Loading…</div>`;
    try { await views[tab](); } catch (err) { handleError(err); }
  };
  el.querySelectorAll("[data-tab]").forEach((b) => (b.onclick = () => { tab = b.dataset.tab; idParam = null; history.replaceState(null, "", `#/payroll/${tab}`); show(); }));
  el.querySelector("#new-run").onclick = newRun;
  await show();
}
