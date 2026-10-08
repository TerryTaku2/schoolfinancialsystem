import { api } from "../api.js";
import { columnChart, hbarChart, lineChart } from "../charts.js";
import { badge, compactMoney, esc, fmtDate, has, isDual, money, moneyList, pct, state } from "../ui.js";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const monthLabel = (m) => MONTHS[+m.slice(5) - 1];

function greeting() {
  const h = new Date().getHours();
  return h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
}

function stat(label, value, foot = "", meter = null) {
  const m = meter === null ? "" : `<div class="meter ${meter < 50 ? "bad" : meter < 75 ? "warn" : ""}"><span style="width:${Math.min(100, meter)}%"></span></div>`;
  return `<div class="card stat"><div class="label">${esc(label)}</div><div class="value">${value}</div>${foot ? `<div class="foot">${foot}</div>` : ""}${m}</div>`;
}

function announcements(list) {
  return `<div class="card"><div class="card-head"><h3>Announcements</h3><a href="#/announcements" class="small">View all</a></div>
    <div>${list.length ? list.map((a) => `<div class="announcement"><div class="row" style="justify-content:space-between"><strong>${a.pinned ? "📌 " : ""}${esc(a.title)}</strong><span class="muted small">${fmtDate(a.created_at)}</span></div><p>${esc(a.body)}</p></div>`).join("") : `<div class="empty">No announcements.</div>`}</div></div>`;
}

export default async function (el) {
  const d = await api.get("/dashboard");
  const name = state.user.full_name.split(" ")[0];
  const head = `<div class="page-head"><div><h1>${greeting()}, ${esc(name)}</h1><p>${d.term ? esc(d.term) : "No current term set"} · ${fmtDate(state.meta.today, { weekday: "long", day: "numeric", month: "long" })}</p></div></div>`;
  if (d.role === "admin" || d.role === "bursar") return office(el, d, head);
  if (d.role === "teacher") return teacher(el, d, head);
  return parent(el, d, head);
}

function office(el, d, head) {
  const k = d.kpis;
  const alerts = [];
  if (d.unrecorded_classes?.length) alerts.push(`<div class="notice warn">Attendance not yet taken today for: <b>${d.unrecorded_classes.map(esc).join(", ")}</b></div>`);
  if (k.pending_expenses && has("expenses.approve")) alerts.push(`<div class="notice">${k.pending_expenses} expense request(s) awaiting approval. <a href="#/expenses">Review</a></div>`);
  el.innerHTML = `${head}
    ${alerts.length ? `<div class="stack" style="margin-bottom:16px">${alerts.join("")}</div>` : ""}
    <div class="grid g-4">
      ${(k.fees_by_currency || [{ currency: undefined, collected: k.collected, billed: k.billed, collection_rate: k.collection_rate, outstanding: k.outstanding }]).length > 1
        ? stat("Fees collected this term", k.fees_by_currency.map((f) => `<div style="font-size:20px">${compactMoney(f.collected, f.currency)}</div>`).join(""),
          k.fees_by_currency.map((f) => `${pct(f.collection_rate)} of ${compactMoney(f.billed, f.currency)}`).join(" · "))
        + stat("Outstanding balance", k.fees_by_currency.map((f) => `<div style="font-size:20px">${compactMoney(f.outstanding, f.currency)}</div>`).join(""), `${k.overdue_invoices ?? 0} overdue invoice(s)`)
        : stat("Fees collected this term", compactMoney(k.collected ?? 0), `of ${compactMoney(k.billed ?? 0)} billed · ${pct(k.collection_rate ?? 0)}`, k.collection_rate ?? 0)
          + stat("Outstanding balance", compactMoney(k.outstanding ?? 0), `${k.overdue_invoices ?? 0} overdue invoice(s)`)}
      ${stat("Active students", k.students.toLocaleString(), `${k.classes} classes · ${k.staff} staff`)}
      ${stat("Attendance today", k.attendance_today === null ? "—" : pct(k.attendance_today), k.attendance_today === null ? "Not recorded yet" : "Present or late")}
    </div>
    <div class="grid g-2" style="margin-top:16px">
      <div class="card"><div class="card-head"><div><h3>Fee collections</h3><div class="sub">Last 6 months${isDual() ? ` · ${esc(d.collections_currency)} receipts` : ""}</div></div></div><div class="card-body"><div id="c-coll"></div></div></div>
      <div class="card"><div class="card-head"><div><h3>Attendance rate</h3><div class="sub">Last 10 school days, %</div></div></div><div class="card-body"><div id="c-att"></div></div></div>
    </div>
    <div class="grid g-3" style="margin-top:16px">
      <div class="card"><div class="card-head"><div><h3>Top overdue accounts</h3><div class="sub">Past due date</div></div><a class="small" href="#/reports">Debtors report</a></div>
        <ul class="list">${d.top_debtors.length ? d.top_debtors.map((x) => `<li><span><a href="#/student/${x.student_id}">${esc(x.student)}</a><br><span class="muted small">${esc(x.class || "")}</span></span><span class="num">${money(x.overdue, x.currency)}</span></li>`).join("") : `<li class="muted">No overdue accounts 🎉</li>`}</ul></div>
      <div class="card"><div class="card-head"><h3>Recent payments</h3><a class="small" href="#/payments">All payments</a></div>
        <ul class="list">${d.recent_payments.map((p) => `<li><span>${esc(p.student)}<br><span class="muted small">${esc(p.receipt_no)} · ${fmtDate(p.paid_on)}</span></span><span class="num">${p.void ? badge("void") : money(p.amount, p.currency)}</span></li>`).join("") || `<li class="muted">No payments yet.</li>`}</ul></div>
      <div class="card"><div class="card-head"><div><h3>Spending this term</h3><div class="sub">Approved & paid, by category${isDual() ? ` · in ${esc(d.expenses_currency)} terms at today's rate` : ""}${d.expenses_note ? `<br>${esc(d.expenses_note)}` : ""}</div></div></div><div class="card-body" id="c-exp"></div></div>
    </div>
    <div style="margin-top:16px">${announcements(d.announcements)}</div>`;
  columnChart(el.querySelector("#c-coll"), {
    labels: d.collections.map((c) => monthLabel(c.month)),
    series: [{ name: "Collected", color: "var(--series-1)", values: d.collections.map((c) => c.amount) }],
    format: compactMoney,
  });
  lineChart(el.querySelector("#c-att"), {
    labels: d.attendance_trend.map((a) => fmtDate(a.date, { day: "numeric", month: "short" })),
    values: d.attendance_trend.map((a) => a.rate), max: 100, format: (v) => `${Math.round(v)}%`, name: "Attendance",
  });
  hbarChart(el.querySelector("#c-exp"), { rows: d.expenses_by_category.map((e) => ({ label: e.category, value: e.amount })), format: compactMoney });
}

function teacher(el, d, head) {
  el.innerHTML = `${head}
    <div class="grid g-3">
      <div class="card span-2"><div class="card-head"><h3>My classes</h3><a href="#/attendance" class="small">Take attendance</a></div>
        <div class="table-wrap"><table class="table"><thead><tr><th>Class</th><th>Role</th><th>Subjects I teach</th><th class="num">Students</th><th>Today's register</th></tr></thead><tbody>
        ${d.classes.map((c) => `<tr><td><b>${esc(c.name)}</b></td><td>${c.is_class_teacher ? badge("active", "Class teacher") : `<span class="muted">Subject teacher</span>`}</td><td>${esc(c.subjects.join(", ") || "—")}</td><td class="num">${c.enrolled}</td>
          <td>${c.is_class_teacher ? (c.attendance_recorded ? badge("paid", "Recorded") : `<a class="btn sm primary" href="#/attendance/${c.id}">Record now</a>`) : "—"}</td></tr>`).join("") || `<tr><td colspan="5" class="empty">No classes assigned.</td></tr>`}
        </tbody></table></div></div>
      <div class="card"><div class="card-head"><h3>Today's lessons</h3><a href="#/timetable" class="small">Timetable</a></div>
        <ul class="list">${d.today_lessons.map((l) => `<li><span><b>${esc(l.subject)}</b><br><span class="muted small">${esc(l.class)} · ${esc(l.room || "")}</span></span><span class="num muted">${esc(l.time)}</span></li>`).join("") || `<li class="muted">No lessons today.</li>`}</ul></div>
    </div>
    <div class="grid g-2" style="margin-top:16px">
      <div class="card"><div class="card-head"><div><h3>Students needing attention</h3><div class="sub">Low attendance or failing average this term</div></div></div>
        <ul class="list">${d.at_risk.map((r) => `<li><span><a href="#/student/${r.student_id}">${esc(r.student)}</a><br><span class="muted small">${esc(r.class)}</span></span><span>${r.reasons.map((x) => `<span class="badge warn">${esc(x)}</span>`).join(" ")}</span></li>`).join("") || `<li class="muted">Everyone is on track.</li>`}</ul></div>
      ${announcements(d.announcements)}
    </div>`;
}

function parent(el, d, head) {
  el.innerHTML = `${head}
    <div class="grid g-2">${d.children.map((c) => `
      <div class="card"><div class="card-head"><div><h3><a href="#/student/${c.id}">${esc(c.name)}</a></h3><div class="sub">${esc(c.class || "Not enrolled")} · ${esc(c.admission_no)}</div></div>${badge(c.status)}</div>
        <div class="card-body"><div class="grid g-3">
          <div><div class="muted small">Attendance</div><div style="font-size:20px;font-weight:650">${pct(c.attendance?.rate)}</div><div class="muted small">${c.attendance ? `${c.attendance.absent} absent · ${c.attendance.late} late` : ""}</div></div>
          <div><div class="muted small">Term average</div><div style="font-size:20px;font-weight:650">${pct(c.average)}</div><div class="muted small">${c.position ? `Position ${c.position}` : ""}</div></div>
          <div><div class="muted small">Fee balance</div><div style="font-size:20px;font-weight:650;color:${c.account.by_currency.some((x) => x.balance > 0) ? "var(--bad)" : "var(--good)"}">${moneyList(c.account.by_currency.map((x) => ({ currency: x.currency, amount: x.balance })))}</div><div class="muted small">${c.account.by_currency.some((x) => x.overdue > 0) ? `${c.account.by_currency.filter((x) => x.overdue > 0).map((x) => money(x.overdue, x.currency)).join(" · ")} overdue` : "Up to date"}</div></div>
        </div>
        <div class="row" style="margin-top:14px"><a class="btn sm" href="#/student/${c.id}">Profile & report card</a><a class="btn sm" href="#/invoices">Invoices</a></div></div></div>`).join("") || `<div class="card empty">No children linked to your account. Please contact the school office.</div>`}
    </div>
    <div style="margin-top:16px">${announcements(d.announcements)}</div>`;
}
