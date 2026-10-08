// App shell: authentication, permission-based navigation, hash router.
import { api, setUnauthorizedHandler } from "./api.js";
import { $, $$, esc, handleError, has, icon, initials, state, toast } from "./ui.js";

// Each menu item shows when show() is true: by permission (see services/permissions.py), or for teachers
// (their own classes) and parents (their own children).
const parent = () => state.user.role === "parent";
const teacher = () => state.user.role === "teacher";
const staff = () => !parent();
const NAV = [
  { group: "Overview" },
  { route: "dashboard", label: "Dashboard", icon: "dashboard", show: () => true },
  { group: "People" },
  { route: "students", label: "Students", icon: "students", show: () => has("students.view", "fees.view") || teacher() },
  { route: "students", label: "My Children", icon: "students", show: parent },
  { route: "guardians", label: "Guardians", icon: "guardians", show: () => has("guardians.view") },
  { route: "staff", label: "Staff", icon: "staff", show: () => has("staff.view") },
  { group: "Academics" },
  { route: "classes", label: "Classes & Subjects", icon: "classes", show: () => has("academics.view") || teacher() },
  { route: "timetable", label: "Timetable", icon: "timetable", show: () => has("academics.view") || teacher() || parent() },
  { route: "attendance", label: "Attendance", icon: "attendance", show: () => has("attendance.manage") || teacher() },
  { route: "exams", label: "Exams & Marks", icon: "exams", show: () => has("academics.view", "marks.manage") || teacher() },
  { route: "results", label: "Results", icon: "results", show: () => has("results.view") || teacher() },
  { group: "Finance" },
  { route: "fees", label: "Fee Structure", icon: "fees", show: () => has("fees.view") },
  { route: "invoices", label: "Invoices", icon: "invoices", show: () => has("fees.view") || parent() },
  { route: "payments", label: "Payments", icon: "payments", show: () => has("payments.view") || parent() },
  { route: "rates", label: "Exchange Rates", icon: "rates", show: () => staff() && (state.meta?.profile?.currencies || []).length > 1 && has("payments.view", "accounting.view") },
  { route: "expenses", label: "Expenses", icon: "expenses", show: () => has("expenses.view") },
  { route: "payroll", label: "Payroll", icon: "payroll", show: () => has("payroll.view") },
  { route: "assets", label: "Asset Register", icon: "assets", show: () => has("assets.view") },
  { route: "reports", label: "Reports", icon: "reports", show: () => has("reports.view", "results.view") },
  { group: "Accounting" },
  { route: "statements", label: "Financial Statements", icon: "statements", show: () => has("accounting.view") },
  { route: "ledger", label: "General Ledger", icon: "ledger", show: () => has("accounting.view") },
  { route: "bank", label: "Bank Reconciliation", icon: "bank", show: () => has("banking.view") },
  { group: "School" },
  { route: "announcements", label: "Announcements", icon: "announcements", show: () => true },
  { route: "users", label: "Users & Permissions", icon: "users", show: () => has("users.manage") },
  { route: "audit", label: "Audit Log", icon: "audit", show: () => has("audit.view") },
  { route: "settings", label: "Settings", icon: "settings", show: () => has("settings.manage", "academics.manage", "promotion.approve") },
];

const VIEWS = {
  dashboard: () => import("./views/dashboard.js"),
  students: () => import("./views/students.js"),
  student: () => import("./views/student.js"),
  guardians: () => import("./views/guardians.js"),
  staff: () => import("./views/staff.js"),
  classes: () => import("./views/classes.js"),
  timetable: () => import("./views/timetable.js"),
  attendance: () => import("./views/attendance.js"),
  exams: () => import("./views/exams.js"),
  results: () => import("./views/results.js"),
  fees: () => import("./views/fees.js"),
  invoices: () => import("./views/invoices.js"),
  payments: () => import("./views/payments.js"),
  rates: () => import("./views/rates.js"),
  expenses: () => import("./views/expenses.js"),
  payroll: () => import("./views/payroll.js"),
  assets: () => import("./views/assets.js"),
  reports: () => import("./views/reports.js"),
  statements: () => import("./views/statements.js"),
  ledger: () => import("./views/ledger.js"),
  bank: () => import("./views/bank.js"),
  announcements: () => import("./views/announcements.js"),
  users: () => import("./views/users.js"),
  audit: () => import("./views/audit.js"),
  settings: () => import("./views/settings.js"),
  profile: () => import("./views/profile.js"),
};

const root = $("#root");
const SCHOOL = root.dataset.school;

export async function refreshMeta() {
  state.meta = await api.get("/meta");
  const pill = $("#term-pill");
  if (pill) pill.textContent = state.meta.current_term ? state.meta.current_term.label : "No current term";
}

// ---------------------------------------------------------------- login
function renderLogin(message) {
  document.title = `Sign in · ${SCHOOL}`;
  root.innerHTML = `
  <div class="login-wrap">
    <form class="login-card" id="login-form" novalidate>
      <div class="brand"><div class="logo">${esc(initials(SCHOOL))}</div><div><h1>${esc(SCHOOL)}</h1><div class="muted">School Management System</div></div></div>
      ${message ? `<div class="notice warn" style="margin-bottom:14px">${esc(message)}</div>` : ""}
      <div class="form">
        <label class="field"><span>Username</span><input class="input" name="username" autocomplete="username" required autofocus></label>
        <label class="field"><span>Password</span><input class="input" name="password" type="password" autocomplete="current-password" required></label>
        <label class="check"><input type="checkbox" name="remember"> Keep me signed in</label>
        <button class="btn primary" type="submit" style="height:40px">Sign in</button>
      </div>
      <div class="demo" id="demo" hidden></div>
    </form>
  </div>`;
  const form = $("#login-form");
  // Demo schools: sign straight in as a demo account, no password needed.
  api.get("/auth/demo").then((d) => {
    if (!d.enabled || !d.accounts.length) return;
    const box = $("#demo");
    box.hidden = false;
    box.innerHTML = `<b>Demo school</b> · sign in without a password as<br>${d.accounts.map((a) => `<button type="button" data-u="${esc(a.username)}">${esc(a.label)}</button>`).join(" · ")}`;
    $$("button", box).forEach((b) => (b.onclick = async () => {
      try {
        const { user } = await api.post("/auth/demo-login", { username: b.dataset.u });
        state.user = user;
        await boot();
      } catch (err) { handleError(err); }
    }));
  }).catch(() => {});
  form.onsubmit = async (e) => {
    e.preventDefault();
    const btn = form.querySelector('[type="submit"]');
    btn.disabled = true;
    try {
      const { user } = await api.post("/auth/login", {
        username: form.username.value, password: form.password.value, remember: form.remember.checked,
      });
      state.user = user;
      await boot();
    } catch (err) {
      handleError(err);
      btn.disabled = false;
    }
  };
}

// ---------------------------------------------------------------- shell
function renderShell() {
  const u = state.user;
  const visible = (n) => n.show();
  const items = NAV.filter((n, i) => {
    if (n.group) {
      // Show a group heading only if at least one item below it is visible.
      for (let j = i + 1; j < NAV.length && !NAV[j].group; j++) if (visible(NAV[j])) return true;
      return false;
    }
    return visible(n);
  });
  root.innerHTML = `
  <div class="shell">
    <aside class="sidebar" id="sidebar">
      <div class="brand"><div class="logo">${esc(initials(SCHOOL))}</div><div><strong>${esc(SCHOOL)}</strong><span class="muted small">School Management</span></div></div>
      <nav aria-label="Main">${items.map((n) => n.group
        ? `<div class="nav-group">${esc(n.group)}</div>`
        : `<a class="nav-link" href="#/${n.route}" data-route="${n.route}">${icon(n.icon)}<span>${esc(n.label)}</span></a>`).join("")}</nav>
      <div class="me">
        <div class="avatar">${esc(initials(u.full_name))}</div>
        <div style="min-width:0;flex:1"><a href="#/profile" style="color:var(--text);font-weight:600;display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(u.full_name)}</a><span class="muted small" style="text-transform:capitalize">${esc(u.role_name || u.role)}</span></div>
        <button class="btn ghost icon" id="logout" title="Sign out" aria-label="Sign out">${icon("logout")}</button>
      </div>
    </aside>
    <div class="main">
      <header class="topbar">
        <button class="btn ghost icon menu-btn" id="menu" aria-label="Open menu">${icon("menu")}</button>
        <span class="term-pill" id="term-pill">${esc(state.meta.current_term?.label || "No current term")}</span>
        <span class="spacer"></span>
        <button class="btn ghost icon" id="theme" title="Toggle dark mode" aria-label="Toggle dark mode">${icon("moon")}</button>
      </header>
      <main class="content" id="view" tabindex="-1"></main>
      <div class="backdrop" id="backdrop" aria-hidden="true"></div>
    </div>
  </div>`;
  $("#logout").onclick = async () => {
    try { await api.post("/auth/logout"); } catch { /* ignore */ }
    state.user = null;
    location.hash = "";
    renderLogin();
  };
  // Phones/tablets: the menu slides over the page; tapping outside it (the backdrop) closes it.
  const setNav = (open) => { $("#sidebar").classList.toggle("open", open); document.body.classList.toggle("nav-open", open); };
  $("#menu").onclick = () => setNav(!$("#sidebar").classList.contains("open"));
  $("#backdrop").onclick = () => setNav(false);
  $("#theme").onclick = () => {
    const html = document.documentElement;
    const dark = html.dataset.theme ? html.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
    html.dataset.theme = dark ? "light" : "dark";
    try { localStorage.setItem("theme", html.dataset.theme); } catch { /* storage unavailable */ }
  };
}

let renderToken = 0;
async function route() {
  if (!state.user) return;
  const [name = "dashboard", ...params] = location.hash.replace(/^#\/?/, "").split("/");
  const loader = VIEWS[name];
  const view = $("#view");
  if (!view) return;
  $("#sidebar")?.classList.remove("open");
  document.body.classList.remove("nav-open");
  const navRoute = name === "student" ? "students" : name;
  $$(".nav-link").forEach((a) => a.classList.toggle("active", a.dataset.route === navRoute));
  if (!loader) {
    view.innerHTML = `<div class="empty"><h2>Page not found</h2><p><a href="#/dashboard">Back to dashboard</a></p></div>`;
    return;
  }
  const token = ++renderToken;
  // Each navigation renders into its own container, so a slow previous view that
  // finishes late writes into a detached node instead of clobbering this one.
  const host = document.createElement("div");
  host.innerHTML = `<div class="empty">Loading…</div>`;
  view.replaceChildren(host);
  try {
    const mod = await loader();
    if (token !== renderToken) return;
    await mod.default(host, params.map(decodeURIComponent));
    if (token !== renderToken) return;
    const h1 = host.querySelector("h1");
    document.title = `${h1 ? h1.textContent + " · " : ""}${SCHOOL}`;
    view.focus({ preventScroll: true });
  } catch (err) {
    if (token !== renderToken) return;
    view.innerHTML = `<div class="empty"><h2>Could not load this page</h2><p>${esc(err.message)}</p></div>`;
    if (err.status !== 401) handleError(err);
  }
}

async function boot() {
  await refreshMeta();
  renderShell();
  if (!location.hash) location.hash = "#/dashboard";
  else route();
}

setUnauthorizedHandler(() => {
  if (state.user) {
    state.user = null;
    renderLogin("Your session has expired. Please sign in again.");
  }
});
window.addEventListener("hashchange", route);

// Installable app (desktop and phones) + offline page. Needs HTTPS, or localhost.
if ("serviceWorker" in navigator && window.isSecureContext) {
  const root = document.querySelector('meta[name="app-root"]')?.content || "";
  navigator.serviceWorker.register(`${root}/sw.js`, { scope: `${root}/` }).catch(() => {});
}

(async () => {
  try {
    const me = await api.get("/auth/me");
    state.user = me.user;
    await boot();
  } catch {
    renderLogin();
  }
})();

export function navigate(hash) { location.hash = hash; }
export function rerender() { route(); }
export { toast };
