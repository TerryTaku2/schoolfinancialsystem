// Platform console: the operator's dashboard. Revenue, schools, usage and health across every school.
import { columnChart, hbarChart, lineChart } from "./charts.js";
import { badge, downloadCSV, esc, handleError, icon, money, table } from "./ui.js";

const TYPE = { primary: "Primary", secondary: "Secondary", combined: "Combined" };
// Health is a state: reserved status colours, always with a word.
const HEALTH = { active: ["active", "Active"], quiet: ["pending", "Quiet"], inactive: ["overdue", "Inactive"], new: ["info", "New"],
  suspended: ["void", "Suspended"], "setting up": ["info", "Setting up"], error: ["blocked", "Error"] };
const SUB = { "paid up": ["paid", "Paid up"], due: ["unpaid", "Due"], overdue: ["overdue", "Overdue"], free: ["info", "Free"], "not billed": ["plain", "Not billed"] };
const RISK = { error: 0, inactive: 1, quiet: 2, new: 3, "setting up": 4, active: 5, suspended: 6 };
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

const monthLabel = (ym, i) => { const [y, m] = ym.split("-").map(Number); return m === 1 || i === 0 ? `${MONTHS[m - 1]} ’${String(y).slice(2)}` : MONTHS[m - 1]; };
const moneyList = (rows) => (rows?.length ? rows.map((r) => money(r.amount, r.currency)).join(" · ") : "—");
const size = (b) => (b >= 1073741824 ? `${(b / 1073741824).toFixed(2)} GB` : b >= 1048576 ? `${(b / 1048576).toFixed(1)} MB` : `${Math.round((b || 0) / 1024)} KB`);
const ago = (iso) => {
  if (!iso) return "Never";
  const days = Math.floor((Date.now() - new Date(iso + (iso.endsWith("Z") ? "" : "Z"))) / 86400000);
  return days <= 0 ? "Today" : days === 1 ? "Yesterday" : days < 30 ? `${days} days ago` : days < 365 ? (Math.round(days / 30) === 1 ? "1 month ago" : `${Math.round(days / 30)} months ago`) : "Over a year ago";
};

export async function dashboardTab(el, call, { refresh = false } = {}) {
  el.innerHTML = `<div class="empty">Gathering figures from every school…</div>`;
  let d;
  try { d = await call("GET", `/dashboard${refresh ? "?refresh=1" : ""}`); } catch (err) { el.innerHTML = ""; return handleError(err); }
  const k = d.kpi, cur = d.currency, m = (v) => money(v, cur), whole = (v) => money(v, { currency: cur, whole: true });
  const updated = new Date(d.generated_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

  el.innerHTML = `
    <div class="row" style="justify-content:space-between;align-items:center;gap:8px;margin-bottom:12px;flex-wrap:wrap">
      <span class="muted small">Figures as of ${updated} · refreshed automatically every 10 minutes</span>
      <button class="btn sm" id="d-refresh">Refresh now</button></div>

    <h3 class="sec">Money</h3>
    <div class="grid g-4" style="margin-bottom:16px">
      <div class="card stat"><div class="label">Collected this year</div><div class="value">${m(k.collected_year)}</div><div class="foot">${m(k.collected_30)} in the last 30 days</div></div>
      <div class="card stat"><div class="label">Collection rate this year</div><div class="value">${k.collection_rate === null ? "—" : `${k.collection_rate}%`}</div>
        ${k.collection_rate === null ? `<div class="foot">Nothing billed yet this year</div>` : `<div class="meter" style="margin-top:8px"><span style="width:${Math.min(100, k.collection_rate)}%"></span></div><div class="foot">of ${m(k.billed_year)} billed</div>`}</div>
      <div class="card stat"><div class="label">Outstanding</div><div class="value">${m(k.outstanding)}</div><div class="foot">${k.overdue > 0 ? `<b style="color:var(--bad)">${m(k.overdue)} overdue</b> · ${k.overdue_schools} school${k.overdue_schools === 1 ? "" : "s"}` : "Nothing overdue"}</div></div>
      <div class="card stat"><div class="label">Next term, if billed today</div><div class="value">${m(k.forecast_next_term)}</div><div class="foot">Active paying schools at today's learner numbers</div></div>
    </div>

    <h3 class="sec">Schools and usage</h3>
    <div class="grid g-4" style="margin-bottom:16px">
      <div class="card stat"><div class="label">Active schools</div><div class="value">${k.schools_active}</div><div class="foot">of ${k.schools_total}${k.schools_suspended ? ` · ${k.schools_suspended} suspended` : ""}${k.schools_free ? ` · ${k.schools_free} free` : ""}</div></div>
      <div class="card stat"><div class="label">Learners on the platform</div><div class="value">${k.learners.toLocaleString()}</div><div class="foot">${k.staff.toLocaleString()} staff</div></div>
      <div class="card stat"><div class="label">People using it (30 days)</div><div class="value">${k.users_active_30.toLocaleString()}</div><div class="foot">of ${k.users.toLocaleString()} accounts · ${k.users_active_7} this week</div></div>
      <div class="card stat"><div class="label">School fees processed this year</div><div class="value" style="font-size:20px">${moneyList(k.fees_processed_ytd)}</div><div class="foot">${moneyList(k.fees_processed_30)} in the last 30 days</div></div>
    </div>

    ${d.attention.length ? `<div class="card" style="margin-bottom:16px"><div class="card-head"><div><h3>Needs your attention</h3><div class="sub">${d.attention.length} item${d.attention.length === 1 ? "" : "s"}</div></div></div>
      <ul class="list">${d.attention.map((a) => `<li><span>${badge(...({ confirm: ["pending", "Confirm"], overdue: ["overdue", "Overdue"], inactive: ["pending", "Inactive"], error: ["blocked", "Error"] })[a.kind])} ${esc(a.text)}</span></li>`).join("")}</ul></div>`
      : `<div class="notice" style="margin-bottom:16px">All clear: no overdue subscriptions, payments to confirm or inactive schools.</div>`}

    <div class="grid g-2" style="margin-bottom:16px">
      <div class="card"><div class="card-head"><div><h3>Subscription income by month</h3><div class="sub">Confirmed payments, last 12 months · ${esc(cur)}</div></div></div><div class="card-body"><div id="c-month"></div></div></div>
      <div class="card"><div class="card-head"><div><h3>Billed and collected, by term</h3><div class="sub">Last ${d.periods.length || 0} billing period${d.periods.length === 1 ? "" : "s"} · ${esc(cur)}</div></div></div><div class="card-body"><div id="c-period"></div></div></div>
      <div class="card"><div class="card-head"><div><h3>Outstanding by age</h3><div class="sub">Unpaid subscription balances · ${esc(cur)}</div></div></div><div class="card-body" id="c-aging"></div></div>
      <div class="card"><div class="card-head"><div><h3>Schools on the platform</h3><div class="sub">Total at the end of each month</div></div></div><div class="card-body"><div id="c-growth"></div></div></div>
    </div>

    <div class="card" style="margin-bottom:16px"><div class="card-head"><div><h3>School health</h3>
        <div class="sub">${Object.entries(d.health).sort((a, b) => (RISK[a[0]] ?? 9) - (RISK[b[0]] ?? 9)).map(([h, n]) => `${n} ${esc((HEALTH[h] || [0, h])[1].toLowerCase())}`).join(" · ")} · active = used in the last 7 days, quiet = 8–30 days, inactive = over 30</div></div>
        <button class="btn sm" id="d-csv">${icon("download")} CSV</button></div><div id="d-schools"></div></div>

    <div class="grid g-2">
      <div class="card"><div class="card-head"><h3>Schools by type</h3></div><div class="card-body" id="c-type"></div></div>
      <div class="card"><div class="card-head"><h3>Platform</h3></div><div class="card-body"><dl class="kv">
        <dt>Storage used</dt><dd>${size(k.storage_bytes)} <span class="muted small">(databases and uploaded PDFs)</span></dd>
        <dt>Digital library</dt><dd>${k.resources.toLocaleString()} PDF${k.resources === 1 ? "" : "s"}</dd>
        <dt>Library loans (30 days)</dt><dd>${k.loans_30.toLocaleString()}</dd>
        <dt>Billing currency</dt><dd>${esc(cur)}</dd></dl></div></div>
    </div>`;

  columnChart(el.querySelector("#c-month"), { labels: d.revenue_by_month.labels.map(monthLabel), height: 220, format: (v) => m(v), axisFormat: whole,
    series: [{ name: "Collected", color: "var(--series-1)", values: d.revenue_by_month.values }] });
  if (d.periods.length) {
    columnChart(el.querySelector("#c-period"), { labels: d.periods.map((p) => p.period), height: 220, format: (v) => m(v), axisFormat: whole,
      series: [{ name: "Billed", color: "var(--series-1)", values: d.periods.map((p) => p.billed) },
        { name: "Collected", color: "var(--series-2)", values: d.periods.map((p) => p.collected) }] });
  } else el.querySelector("#c-period").innerHTML = `<div class="empty">Nothing billed yet. Use “Bill a term” in the Billing tab.</div>`;
  hbarChart(el.querySelector("#c-aging"), { rows: d.aging, format: (v) => m(v) });
  lineChart(el.querySelector("#c-growth"), { labels: d.growth.labels.map(monthLabel), values: d.growth.values, height: 200, name: "Schools",
    format: (v) => `${Math.round(v)}`, max: Math.max(4, ...d.growth.values) });
  hbarChart(el.querySelector("#c-type"), { rows: d.by_type.map((r) => ({ label: TYPE[r.label] || r.label, value: r.value })), format: (v) => `${v} school${v === 1 ? "" : "s"}` });

  const rows = [...d.schools].sort((a, b) => (RISK[a.health] ?? 9) - (RISK[b.health] ?? 9) || a.name.localeCompare(b.name));
  table(el.querySelector("#d-schools"), { rows, empty: "No schools yet.", columns: [
    { key: "name", label: "School", render: (r) => `<b>${esc(r.name)}</b><br><span class="muted small">${esc(r.slug)} · ${esc(TYPE[r.type] || r.type)}</span>` },
    { key: "health", label: "Health", render: (r) => badge(...(HEALTH[r.health] || ["plain", r.health])), sort: (r) => RISK[r.health] ?? 9 },
    { key: "last_activity", label: "Last used", render: (r) => ago(r.last_activity), sort: (r) => r.last_activity || "" },
    { key: "learners", label: "Learners", num: true, render: (r) => (r.learners ?? "—") },
    { key: "users_active_30", label: "Active users", num: true, render: (r) => (r.users == null ? "—" : `${r.users_active_30} of ${r.users}`) },
    { key: "payments_30", label: "Receipts (30d)", num: true, render: (r) => (r.payments_30 ?? "—") },
    { key: "attendance_days_30", label: "Register days (30d)", num: true, render: (r) => (r.attendance_days_30 ?? "—") },
    { key: "fees_ytd", label: "Fees this year", num: true, sort: (r) => r.fees_ytd?.[0]?.amount || 0, render: (r) => moneyList(r.fees_ytd) },
    { key: "subscription", label: "Subscription", render: (r) => `${badge(...SUB[r.subscription])}${r.balance > 0 ? `<br><span class="small">${m(r.balance)}</span>` : ""}` },
    { key: "storage_bytes", label: "Storage", num: true, render: (r) => size(r.storage_bytes) },
  ] });

  el.querySelector("#d-refresh").onclick = () => dashboardTab(el, call, { refresh: true });
  el.querySelector("#d-csv").onclick = () => downloadCSV(`schools-${new Date().toISOString().slice(0, 10)}.csv`, [
    { label: "School", key: "name" }, { label: "Code", key: "slug" }, { label: "Type", key: "type" }, { label: "Health", key: "health" },
    { label: "Last used", key: "last_activity" }, { label: "Learners", key: "learners" }, { label: "Staff", key: "staff" },
    { label: "Accounts", key: "users" }, { label: "Active users (30d)", key: "users_active_30" }, { label: "Receipts (30d)", key: "payments_30" },
    { label: "Register days (30d)", key: "attendance_days_30" }, { label: "Fees this year", key: "fees_ytd", csv: (r) => moneyList(r.fees_ytd) },
    { label: "Subscription", key: "subscription" }, { label: `Balance (${cur})`, key: "balance" }, { label: "Storage (bytes)", key: "storage_bytes" }], rows);
}
