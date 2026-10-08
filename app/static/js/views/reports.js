import { api } from "../api.js";
import { columnChart, hbarChart } from "../charts.js";
import { badge, baseCurrency, compactMoney, currencies, downloadCSV, esc, has, icon, isDual, money, pct, selectHtml, state, table, termOptions, today } from "../ui.js";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export default async function (el) {
  const tabs = [...(has("reports.view") ? [["fees", "Fees & debtors"], ["cash", "Cash flow"]] : []), ...(has("results.view") ? [["academic", "Academic performance"]] : [])];
  let termId = state.meta.current_term?.id || "";
  let cur = baseCurrency();
  el.innerHTML = `
    <div class="page-head"><div><h1>Reports</h1><p>Financial and academic analysis${isDual() ? ". Financial reports are per currency; amounts in different currencies are never added." : ""}</p></div>
      <div class="page-actions">${isDual() ? selectHtml("currency", currencies().map((c) => ({ value: c, label: c })), cur) : ""}${selectHtml("term", termOptions(), termId)}</div></div>
    <div class="tabs">${tabs.map(([k, l], i) => `<button data-tab="${k}" class="${i ? "" : "active"}">${l}</button>`).join("")}</div>
    <div id="pane"></div>`;
  const pane = el.querySelector("#pane");
  let current = "fees";

  const views = {
    async fees() {
      const r = await api.get("/reports/fees", { term_id: termId, currency: cur });
      const m = (v) => money(v, r.currency), cm = (v) => compactMoney(v, r.currency);
      pane.innerHTML = `<div class="grid g-2">
        <div class="card"><div class="card-head"><div><h3>Outstanding by age</h3><div class="sub">Days past invoice due date</div></div></div><div class="card-body" id="aging"></div></div>
        <div class="card"><div class="card-head"><div><h3>Collections by method</h3><div class="sub">${esc(r.term)}</div></div></div><div class="card-body" id="methods"></div></div>
        <div class="card span-2"><div class="card-head"><h3>Collection by class</h3><button class="btn sm" id="x-cls">${icon("download")} CSV</button></div><div id="cls"></div></div>
        <div class="card span-2"><div class="card-head"><div><h3>Debtors list</h3><div class="sub">${r.debtors.length} student(s) with a balance this term</div></div><button class="btn sm" id="x-debt">${icon("download")} CSV</button></div><div id="debt"></div></div></div>`;
      hbarChart(pane.querySelector("#aging"), { rows: r.aging.map((a) => ({ label: a.bucket === "current" ? "Not yet due" : `${a.bucket} days`, value: a.amount })), format: cm });
      hbarChart(pane.querySelector("#methods"), { rows: r.methods.map((x) => ({ label: x.method[0].toUpperCase() + x.method.slice(1), value: x.amount })).sort((a, b) => b.value - a.value), format: cm });
      const clsCols = [{ key: "class", label: "Class" }, { key: "invoices", label: "Invoices", num: true },
        { key: "billed", label: "Billed", num: true, render: (x) => m(x.billed) }, { key: "collected", label: "Collected", num: true, render: (x) => m(x.collected) },
        { key: "outstanding", label: "Outstanding", num: true, render: (x) => m(x.outstanding) },
        { key: "rate", label: "Collection rate", num: true, render: (x) => `<span class="row" style="justify-content:flex-end;gap:8px"><span class="meter" style="width:80px;margin:0"><span style="width:${x.rate}%"></span></span>${x.rate}%</span>`, csv: (x) => x.rate }];
      const sum = (k) => r.classes.reduce((a, x) => a + x[k], 0);
      const billed = sum("billed"), coll = sum("collected");
      table(pane.querySelector("#cls"), { rows: r.classes, columns: clsCols,
        footer: `<td>Total</td><td class="num">${sum("invoices")}</td><td class="num">${m(billed)}</td><td class="num">${m(coll)}</td><td class="num">${m(billed - coll)}</td><td class="num">${billed ? ((coll / billed) * 100).toFixed(1) : 0}%</td>` });
      const debtCols = [{ key: "student", label: "Student", render: (x) => `<a href="#/student/${x.student_id}">${esc(x.student)}</a>` }, { key: "admission_no", label: "Adm. No." },
        { key: "class", label: "Class" }, { key: "guardian", label: "Guardian" }, { key: "phone", label: "Phone" }, { key: "invoice_no", label: "Invoice" },
        { key: "days_overdue", label: "Days overdue", num: true, render: (x) => (x.days_overdue ? `<span style="color:var(--bad)">${x.days_overdue}</span>` : "—") },
        { key: "balance", label: "Balance", num: true, render: (x) => `<b>${m(x.balance)}</b>` }];
      table(pane.querySelector("#debt"), { rows: r.debtors, columns: debtCols, empty: "No outstanding balances 🎉" });
      pane.querySelector("#x-cls").onclick = () => downloadCSV(`fee-collection-${today()}`, clsCols, r.classes);
      pane.querySelector("#x-debt").onclick = () => downloadCSV(`debtors-${today()}`, debtCols.map((c) => ({ ...c, csv: undefined })), r.debtors);
    },
    async cash() {
      const r = await api.get("/reports/cashflow", { months: 12, currency: cur });
      const m = (v) => money(v, r.currency);
      pane.innerHTML = `<div class="grid g-3" style="margin-bottom:16px">${[["Income (12 months)", r.totals.income], ["Expenses paid", r.totals.expenses], ["Net surplus", r.totals.net]]
        .map(([l, v]) => `<div class="card stat"><div class="label">${l}${isDual() ? ` · ${esc(r.currency)}` : ""}</div><div class="value" style="${v < 0 ? "color:var(--bad)" : ""}">${m(v)}</div></div>`).join("")}</div>
        <div class="card"><div class="card-head"><div><h3>Income vs expenses</h3><div class="sub">Fee receipts compared with paid expenses, by month</div></div><button class="btn sm" id="x">${icon("download")} CSV</button></div>
          <div class="card-body"><div id="chart"></div></div><div id="tbl"></div></div>`;
      columnChart(pane.querySelector("#chart"), {
        labels: r.rows.map((x) => MONTHS[+x.month.slice(5) - 1]),
        series: [{ name: "Income", color: "var(--series-1)", values: r.rows.map((x) => x.income) },
          { name: "Expenses", color: "var(--series-2)", values: r.rows.map((x) => x.expenses) }],
        format: (v) => compactMoney(v, r.currency), height: 260,
      });
      const cols = [{ key: "month", label: "Month" }, { key: "income", label: "Income", num: true, render: (x) => m(x.income) },
        { key: "expenses", label: "Expenses", num: true, render: (x) => m(x.expenses) },
        { key: "net", label: "Net", num: true, render: (x) => `<span style="color:${x.net < 0 ? "var(--bad)" : "inherit"}">${m(x.net)}</span>` }];
      table(pane.querySelector("#tbl"), { rows: r.rows, columns: cols });
      pane.querySelector("#x").onclick = () => downloadCSV(`cashflow-${today()}`, cols, r.rows);
    },
    async academic() {
      const r = await api.get("/reports/academic", { term_id: termId });
      pane.innerHTML = `<div class="stack">
        <div class="card"><div class="card-head"><div><h3>Class performance</h3><div class="sub">${esc(r.term)} · pass mark ${state.meta.pass_mark}%</div></div></div><div id="cls"></div></div>
        <div class="card"><div class="card-head"><div><h3>At-risk students</h3><div class="sub">Attendance below ${state.meta.attendance_threshold}% or failing average</div></div><button class="btn sm" id="x">${icon("download")} CSV</button></div><div id="risk"></div></div></div>`;
      table(pane.querySelector("#cls"), { rows: r.rows, columns: [{ key: "class", label: "Class" }, { key: "students", label: "Students", num: true },
        { key: "mean", label: "Mean score", num: true, render: (x) => pct(x.mean) }, { key: "pass_rate", label: "Pass rate", num: true, render: (x) => pct(x.pass_rate) },
        { key: "attendance", label: "Attendance", num: true, render: (x) => pct(x.attendance) }, { key: "top_student", label: "Top student" }] });
      const riskCols = [{ key: "student", label: "Student", render: (x) => `<a href="#/student/${x.student_id}">${esc(x.student)}</a>` }, { key: "class", label: "Class" },
        { key: "reasons", label: "Concerns", render: (x) => x.reasons.map((y) => badge("late", y)).join(" "), csv: (x) => x.reasons.join("; ") }];
      table(pane.querySelector("#risk"), { rows: r.at_risk, columns: riskCols, empty: "No at-risk students." });
      pane.querySelector("#x").onclick = () => downloadCSV(`at-risk-${today()}`, riskCols, r.at_risk);
    },
  };
  const show = async (k) => {
    current = k;
    el.querySelectorAll("[data-tab]").forEach((b) => b.classList.toggle("active", b.dataset.tab === k));
    pane.innerHTML = `<div class="empty">Loading…</div>`;
    await views[k]();
  };
  el.querySelectorAll("[data-tab]").forEach((b) => (b.onclick = () => show(b.dataset.tab)));
  el.querySelector('[name="term"]').onchange = (e) => { termId = e.target.value; show(current); };
  el.querySelector('[name="currency"]')?.addEventListener("change", (e) => { cur = e.target.value; show(current); });
  await show("fees");
}
