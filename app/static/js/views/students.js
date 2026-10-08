import { api } from "../api.js";
import { navigate } from "../app.js";
import { badge, classOptions, debounce, downloadCSV, esc, formModal, has, icon, isDual, money, moneyList, pager, selectHtml, state, table, toast, today } from "../ui.js";

export const STUDENT_FIELDS = (isNew) => [
  { name: "first_name", label: "First name", required: true },
  { name: "last_name", label: "Last name", required: true },
  { name: "gender", label: "Gender", type: "select", required: true, options: ["Female", "Male"] },
  { name: "dob", label: "Date of birth", type: "date", required: true, max: today() },
  { name: "class_id", label: "Class", type: "select", required: isNew, options: classOptions(), empty: "— none (leaving) —" },
  ...(isNew ? [{ name: "admission_date", label: "Admission date", type: "date", default: today(), max: today() }] : [
    { name: "status", label: "Status", type: "select", required: true, options: [
      { value: "active", label: "Active" }, { value: "suspended", label: "Suspended" }, { value: "graduated", label: "Graduated" },
      { value: "transferred", label: "Transferred" }, { value: "withdrawn", label: "Withdrawn" }],
      hint: "Graduated/transferred/withdrawn students are removed from their class" }]),
  { name: "address", label: "Home address", full: true },
  { name: "medical_notes", label: "Medical notes / allergies", type: "textarea", full: true },
  ...(isNew ? [
    { type: "heading", label: "Guardian", name: "_g" },
    { name: "g_name", label: "Guardian name", required: true },
    { name: "g_phone", label: "Phone", required: true, hint: "An existing guardian with this phone is reused (siblings)" },
    { name: "g_relationship", label: "Relationship", type: "select", options: ["Mother", "Father", "Guardian", "Other"], default: "Mother" },
    { name: "g_email", label: "Email", type: "email" },
  ] : []),
];

export function studentPayload(d) {
  const out = { ...d };
  if (d.g_name || d.g_phone) {
    out.guardian = { name: d.g_name, phone: d.g_phone, relationship: d.g_relationship, email: d.g_email };
  }
  for (const k of ["g_name", "g_phone", "g_relationship", "g_email"]) delete out[k];
  return out;
}

export default async function (el) {
  const office = has("fees.view");
  const isParent = state.user.role === "parent";
  const f = { q: "", class_id: "", status: "active", page: 1 };
  el.innerHTML = `
    <div class="page-head"><div><h1>${isParent ? "My Children" : "Students"}</h1><p>${isParent ? "Children linked to your account" : state.user.role === "teacher" ? "Students in the classes you teach" : "Enrolment register"}</p></div>
      <div class="page-actions">${!isParent ? `<button class="btn" id="export">${icon("download")} Export CSV</button>` : ""}${has("students.manage") ? `<button class="btn primary" id="add">${icon("plus")} Admit student</button>` : ""}</div></div>
    <div class="card">
      ${isParent ? "" : `<div class="toolbar">
        <input class="input search" type="search" placeholder="Search name or admission no." id="q" aria-label="Search">
        ${selectHtml("class_id", classOptions(), "", { empty: "All classes" })}
        ${selectHtml("status", [{ value: "active", label: "Active" }, { value: "suspended", label: "Suspended" }, { value: "graduated", label: "Graduated" }, { value: "transferred", label: "Transferred" }, { value: "withdrawn", label: "Withdrawn" }, { value: "all", label: "All statuses" }], "active")}
      </div>`}
      <div id="tbl"></div><div id="pg"></div>
    </div>`;

  const columns = [
    { key: "admission_no", label: "Adm. No." },
    { key: "name", label: "Name", render: (r) => `<a href="#/student/${r.id}"><b>${esc(r.name)}</b></a>`, sort: (r) => r.last_name },
    { key: "class", label: "Class" },
    { key: "gender", label: "Gender" },
    { key: "age", label: "Age", num: true },
    { key: "guardian", label: "Guardian", render: (r) => `${esc(r.guardian || "—")}<br><span class="muted small">${esc(r.guardian_phone || "")}</span>`, csv: (r) => `${r.guardian || ""} ${r.guardian_phone || ""}` },
    ...(office ? [{ key: "balance", label: "Balance", num: true, render: (r) => `<span style="color:${Object.values(r.balances || { x: r.balance }).some((v) => v > 0) ? "var(--bad)" : "inherit"}">${isDual() && r.balances ? moneyList(r.balances) : money(r.balance)}</span>`, csv: (r) => (r.balances ? Object.entries(r.balances).map(([c, v]) => `${c} ${v}`).join("; ") : r.balance) }] : []),
    { key: "status", label: "Status", render: (r) => badge(r.status) },
  ];
  let rows = [];
  const load = async () => {
    const res = await api.get("/students", { ...f, per_page: 50, status: isParent ? "all" : f.status });
    rows = res.items;
    table(el.querySelector("#tbl"), { columns, rows, onRowClick: (r) => navigate(`#/student/${r.id}`), empty: "No students match your filters." });
    pager(el.querySelector("#pg"), { page: res.page, perPage: res.per_page, total: res.total, onPage: (p) => { f.page = p; load(); } });
  };
  if (!isParent) {
    el.querySelector("#q").oninput = debounce((e) => { f.q = e.target.value; f.page = 1; load(); });
    el.querySelector('[name="class_id"]').onchange = (e) => { f.class_id = e.target.value; f.page = 1; load(); };
    el.querySelector('[name="status"]').onchange = (e) => { f.status = e.target.value; f.page = 1; load(); };
    el.querySelector("#export").onclick = async () => {
      const all = await api.get("/students", { ...f, page: 1, per_page: 500 });
      downloadCSV(`students-${today()}`, columns, all.items);
    };
  }
  el.querySelector("#add")?.addEventListener("click", async () => {
    const s = await formModal({
      title: "Admit new student", fields: STUDENT_FIELDS(true), wide: true, submitText: "Admit student",
      onSubmit: (d) => api.post("/students", studentPayload(d)),
    });
    if (s) { toast(`${s.name} admitted as ${s.admission_no}`, "success"); navigate(`#/student/${s.id}`); }
  });
  await load();
}
