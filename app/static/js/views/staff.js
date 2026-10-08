import { api } from "../api.js";
import { badge, downloadCSV, esc, fmtDate, formModal, has, icon, money, selectHtml, table, toast, today } from "../ui.js";

const BASE = [
  { name: "first_name", label: "First name", required: true },
  { name: "last_name", label: "Last name", required: true },
  { name: "gender", label: "Gender", type: "select", options: ["Female", "Male"] },
  { name: "position", label: "Position", required: true, default: "Teacher", hint: "e.g. Teacher, Bursar, Librarian" },
  { name: "department", label: "Department" },
  { name: "phone", label: "Phone" },
  { name: "email", label: "Email", type: "email" },
  { name: "hire_date", label: "Hire date", type: "date", default: today(), max: today() },
  { name: "salary", label: "Monthly salary", type: "number", min: 0, step: 0.01 },
];
const ACCOUNT = [
  { type: "heading", label: "System login (optional)", name: "_h" },
  { name: "username", label: "Username" },
  { name: "password", label: "Password", type: "password", hint: "8+ characters, letters and numbers" },
  { name: "role", label: "Role", type: "select", options: [{ value: "teacher", label: "Teacher" }, { value: "bursar", label: "Bursar" }, { value: "admin", label: "Administrator" }], default: "teacher" },
];
const withAccount = (d) => {
  const out = { ...d };
  if (d.username) out.account = { username: d.username, password: d.password, role: d.role };
  delete out.username; delete out.password; delete out.role;
  return out;
};

export default async function (el) {
  let status = "active";
  el.innerHTML = `
    <div class="page-head"><div><h1>Staff</h1><p>Teaching and support staff, duties and system access</p></div>
      <div class="page-actions"><button class="btn" id="export">${icon("download")} Export CSV</button>${has("staff.manage") ? `<button class="btn primary" id="add">${icon("plus")} Add staff</button>` : ""}</div></div>
    <div class="card"><div class="toolbar">${selectHtml("status", [{ value: "active", label: "Active" }, { value: "on_leave", label: "On leave" }, { value: "left", label: "Left" }, { value: "all", label: "All" }], status)}</div><div id="tbl"></div></div>`;
  const columns = [
    { key: "staff_no", label: "Staff no." },
    { key: "name", label: "Name", render: (s) => `<b>${esc(s.name)}</b><br><span class="muted small">${esc(s.email || "")}</span>`, sort: (s) => s.last_name },
    { key: "position", label: "Position", render: (s) => `${esc(s.position)}<br><span class="muted small">${esc(s.department || "")}</span>` },
    { key: "teaches", label: "Duties", sort: false, render: (s) => [...(s.class_teacher_of || []).map((c) => `<span class="badge accent plain">Class teacher ${esc(c)}</span>`), (s.teaches || []).length ? `<span class="muted small">${s.teaches.length} subject class(es)</span>` : ""].join(" ") || "—", csv: (s) => (s.teaches || []).join("; ") },
    { key: "hire_date", label: "Hired", render: (s) => fmtDate(s.hire_date) },
    { key: "salary", label: "Salary", num: true, render: (s) => money(s.salary) },
    { key: "username", label: "Login", render: (s) => (s.username ? `${esc(s.username)} <span class="muted small">(${esc(s.role)})</span>` : `<span class="muted small">—</span>`) },
    { key: "status", label: "Status", render: (s) => badge(s.status) },
    ...(has("staff.manage") ? [{ key: "id", label: "", sort: false, cls: "actions", csv: () => "", render: (s) => `<button class="btn sm" data-edit="${s.id}">Edit</button>` }] : []),
  ];
  let rows = [];
  const load = async () => {
    rows = (await api.get("/staff", { status })).items;
    table(el.querySelector("#tbl"), { rows, columns, empty: "No staff found." });
    el.querySelectorAll("[data-edit]").forEach((b) => (b.onclick = async () => {
      const s = rows.find((x) => x.id === +b.dataset.edit);
      const fields = [...BASE, { name: "status", label: "Status", type: "select", required: true, options: [{ value: "active", label: "Active" }, { value: "on_leave", label: "On leave" }, { value: "left", label: "Left the school" }], hint: "Leavers lose system access; reassign their duties first" }, ...(s.username ? [] : ACCOUNT)];
      if (await formModal({ title: `Edit ${s.name}`, fields, values: s, wide: true, transform: withAccount, onSubmit: (d) => api.put(`/staff/${s.id}`, d) })) { toast("Saved", "success"); load(); }
    }));
  };
  el.querySelector('[name="status"]').onchange = (e) => { status = e.target.value; load(); };
  el.querySelector("#export").onclick = () => downloadCSV(`staff-${today()}`, columns.filter((c) => c.key !== "id"), rows);
  el.querySelector("#add")?.addEventListener("click", async () => {
    if (await formModal({ title: "Add staff member", fields: [...BASE, ...ACCOUNT], wide: true, transform: withAccount, onSubmit: (d) => api.post("/staff", d) })) { toast("Staff member added", "success"); load(); }
  });
  await load();
}
