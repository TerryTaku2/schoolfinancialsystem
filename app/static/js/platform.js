// Platform console: operators create, rename and suspend schools (multi-school mode).
import { ApiError } from "./api.js";
import { $, badge, confirmDialog, esc, fmtDate, formModal, handleError, icon, table, toast } from "./ui.js";

const ROOT = document.querySelector('meta[name="app-root"]')?.content || "";
const root = $("#root");

async function call(method, url, data) {
  const opts = { method, headers: { "X-Requested-With": "SchoolMS" }, credentials: "same-origin" };
  if (data !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(data);
  }
  let res;
  try { res = await fetch(`${ROOT}/platform/api${url}`, opts); } catch { throw new ApiError("Cannot reach the server.", 0); }
  const payload = await res.json().catch(() => null);
  if (!res.ok) throw new ApiError(payload?.error || `Request failed (${res.status})`, res.status, payload?.fields);
  return payload;
}

const slugify = (s) => s.toLowerCase().normalize("NFKD").replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 40);

function renderLogin(message) {
  root.innerHTML = `<div class="login-wrap"><form class="login-card" id="f" novalidate>
    <div class="brand"><div class="logo">SM</div><div><h1>Platform console</h1><div class="muted">Create and manage schools</div></div></div>
    ${message ? `<div class="notice warn" style="margin-bottom:14px">${esc(message)}</div>` : ""}
    <div class="form"><label class="field"><span>Username</span><input class="input" name="username" autocomplete="username" required autofocus></label>
    <label class="field"><span>Password</span><input class="input" name="password" type="password" autocomplete="current-password" required></label>
    <button class="btn primary" type="submit" style="height:40px">Sign in</button></div>
    <p class="muted small" style="margin-top:18px">This is not a school login. School staff and parents sign in at their school's own address.</p></form></div>`;
  $("#f").onsubmit = async (e) => {
    e.preventDefault();
    try { await call("POST", "/login", { username: e.target.username.value, password: e.target.password.value }); boot(); }
    catch (err) { handleError(err); }
  };
}

async function boot() {
  let me;
  try { me = await call("GET", "/me"); } catch (err) { return renderLogin(err.status === 401 ? null : err.message); }
  document.title = "Platform console · School Management";
  root.innerHTML = `<div class="content" style="max-width:1200px;margin:0 auto">
    <div class="page-head"><div><h1>Schools</h1><p>Each school has its own database, users and address. Signed in as ${esc(me.user.full_name)}.</p></div>
      <div class="page-actions"><button class="btn" id="out">Sign out</button><button class="btn primary" id="add">${icon("plus")} New school</button></div></div>
    <div class="card"><div id="tbl"></div></div></div>`;
  $("#out").onclick = async () => { await call("POST", "/logout").catch(() => {}); renderLogin(); };
  $("#add").onclick = () => newSchool(me);
  await load();
}

let poll = null;
async function load() {
  const r = await call("GET", "/schools");
  for (const f of r.failed || []) toast(`Setting up ${f.name} failed: ${f.error}`, "error");
  // While a demo school is being filled, check back every few seconds.
  clearTimeout(poll);
  if (r.items.some((s) => s.preparing)) poll = setTimeout(() => load().catch(() => {}), 5000);
  for (const s of r.items) if (load.waiting?.has(s.id) && !s.preparing && s.status === "active") toast(`${s.name} is ready`, "success");
  load.waiting = new Set(r.items.filter((s) => s.preparing).map((s) => s.id));
  table($("#tbl"), {
    rows: r.items, sortKey: "name", empty: "No schools yet. Create the first one.",
    columns: [
      { key: "name", label: "School", render: (s) => `<b>${esc(s.name)}</b><br><a class="small" href="${esc(s.url)}" target="_blank" rel="noopener">${esc(location.origin + s.url)}</a>` },
      { key: "slug", label: "Code" },
      { key: "school_type_label", label: "Type" },
      { key: "currency", label: "Currency" },
      { key: "storage", label: "Database" },
      { key: "created_at", label: "Created", render: (s) => fmtDate(s.created_at) },
      { key: "status", label: "Status", render: (s) => s.preparing ? badge("pending", "Preparing demo data…") : badge(s.status === "active" ? "active" : "blocked", s.status) },
      { key: "id", label: "", sort: false, cls: "actions", render: (s) => s.preparing ? `<span class="muted small">Opens when ready</span>` : `<button class="btn sm" data-rename="${s.id}">Rename</button> <button class="btn sm" data-pw="${s.id}">Reset admin password</button> <button class="btn sm ${s.status === "active" ? "danger" : ""}" data-status="${s.id}">${s.status === "active" ? "Suspend" : "Reactivate"}</button>` },
    ],
  });
  $("#tbl").querySelectorAll("[data-rename]").forEach((b) => (b.onclick = async () => {
    const s = r.items.find((x) => x.id === +b.dataset.rename);
    if (await formModal({ title: `Rename ${s.name}`, cols: 1, values: s, fields: [{ name: "name", label: "School name", required: true }],
      onSubmit: (d) => call("PUT", `/schools/${s.id}`, d) })) { toast("Renamed", "success"); load(); }
  }));
  $("#tbl").querySelectorAll("[data-pw]").forEach((b) => (b.onclick = async () => {
    const s = r.items.find((x) => x.id === +b.dataset.pw);
    let admins;
    try { admins = (await call("GET", `/schools/${s.id}/admins`)).items; } catch (err) { return handleError(err); }
    if (!admins.length) return toast(`${s.name} has no administrator accounts`, "error");
    const res = await formModal({
      title: `Reset admin password · ${s.name}`, cols: 1, submitText: "Reset password",
      intro: `<p style="margin-top:0" class="muted">For a school administrator who forgot their password. Give them the temporary password in person or by phone; they must choose their own when they next sign in. Other staff are reset by the school's own administrators (Users &amp; Permissions).</p>`,
      fields: [{ name: "username", label: "Administrator", type: "select", required: true, default: (admins.find((a) => a.reset_requested) || admins[0]).username,
          options: admins.map((a) => ({ value: a.username, label: `${a.username} · ${a.full_name}${a.reset_requested ? " (asked for a reset)" : ""}${a.active ? "" : " (disabled)"}` })) },
        { name: "password", label: "Temporary password", type: "password", required: true, hint: "8+ characters mixing letters and numbers" }],
      onSubmit: (d) => call("POST", `/schools/${s.id}/reset-password`, d),
    });
    if (res) toast(`Password reset for ${res.username} at ${s.name}`, "success");
  }));
  $("#tbl").querySelectorAll("[data-status]").forEach((b) => (b.onclick = async () => {
    const s = r.items.find((x) => x.id === +b.dataset.status);
    const suspend = s.status === "active";
    if (!(await confirmDialog(suspend ? `Suspend ${s.name}? Nobody can sign in or use the school's address until it is reactivated. Its data is kept.` : `Reactivate ${s.name}?`,
      { title: suspend ? "Suspend school" : "Reactivate school", danger: suspend, confirmText: suspend ? "Suspend" : "Reactivate" }))) return;
    try { await call("PUT", `/schools/${s.id}`, { status: suspend ? "suspended" : "active" }); load(); } catch (err) { handleError(err); }
  }));
}

async function newSchool(me) {
  const done = formModal({
    title: "New school", wide: true,
    intro: `<p class="muted small" style="margin-top:0">Creates a separate database with the Zimbabwe structure for the school type: classes from ECD A to Grade 7 and/or Form 1 to Form 6, curriculum subjects, primary / O Level / A Level grading scales, three terms for this year, the chart of accounts and ZIMRA tax tables. The school can change all of these afterwards.</p>`,
    fields: [
      { name: "name", label: "School name", required: true, full: true, placeholder: "e.g. Chiedza Primary School" },
      { name: "slug", label: "School code (in the address)", required: true, placeholder: "chiedza-primary", hint: "Lowercase letters, numbers and hyphens. Can't be changed later." },
      { name: "school_type", label: "School type", type: "select", required: true, options: me.school_types, default: "primary" },
      { name: "currency", label: "Currency", type: "select", required: true, options: (me.currency_catalog || me.currencies.map((c) => ({ code: c, label: c }))).map((c) => ({ value: c.code, label: c.code === c.label ? c.code : `${c.code} · ${c.label}` })), default: "USD", hint: "Fees, payroll and the books are kept in this currency" },
      { name: "demo", label: "Fill with demo data (for training or trying it out)", type: "checkbox" },
      { type: "heading", name: "_h", label: "First administrator" },
      { name: "admin_full_name", label: "Full name", placeholder: "e.g. the head or bursar" },
      { name: "admin_email", label: "Email", type: "email" },
      { name: "admin_username", label: "Username", required: true, default: "admin" },
      { name: "admin_password", label: "Password", type: "password", required: true, hint: "8+ characters mixing letters and numbers" },
    ],
    onSubmit: (d) => call("POST", "/schools", d),
  });
  const form = document.querySelector(".modal-back:last-of-type form");
  let touched = false;
  form.slug.addEventListener("input", () => (touched = true));
  form.name.addEventListener("input", () => { if (!touched) form.slug.value = slugify(form.name.value); });
  const s = await done;
  if (s) {
    toast(s.preparing ? `${s.name} created. Filling it with demo data; this can take a few minutes.` : `${s.name} created`, "success");
    load();
  }
}

boot().catch(handleError);
