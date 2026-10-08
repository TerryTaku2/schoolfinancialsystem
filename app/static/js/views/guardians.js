import { api } from "../api.js";
import { debounce, esc, formModal, has, icon, table, toast } from "../ui.js";

const FIELDS = [
  { name: "name", label: "Full name", required: true },
  { name: "phone", label: "Phone", required: true, hint: "Must be unique — used to match siblings" },
  { name: "relationship", label: "Relationship", type: "select", options: ["Mother", "Father", "Guardian", "Other"], default: "Mother" },
  { name: "email", label: "Email", type: "email" },
  { name: "address", label: "Address", full: true },
];

export default async function (el) {
  let q = "";
  el.innerHTML = `
    <div class="page-head"><div><h1>Guardians</h1><p>Parents and guardians, their children, and parent-portal access</p></div>
      <div class="page-actions">${has("guardians.manage") ? `<button class="btn primary" id="add">${icon("plus")} New guardian</button>` : ""}</div></div>
    <div class="card"><div class="toolbar"><input class="input search" type="search" id="q" placeholder="Search name or phone" aria-label="Search"></div><div id="tbl"></div></div>`;
  const load = async () => {
    const { items } = await api.get("/guardians", { q });
    table(el.querySelector("#tbl"), {
      rows: items, empty: "No guardians found.",
      columns: [
        { key: "name", label: "Name", render: (g) => `<b>${esc(g.name)}</b><br><span class="muted small">${esc(g.relationship || "")}</span>` },
        { key: "phone", label: "Contact", render: (g) => `${esc(g.phone)}${g.email ? `<br><span class="muted small">${esc(g.email)}</span>` : ""}` },
        { key: "children", label: "Children", sort: (g) => g.children.length, render: (g) => g.children.map((c) => `<a href="#/student/${c.id}">${esc(c.name)}</a> <span class="muted small">${esc(c.class || c.status)}</span>`).join("<br>") || "—" },
        { key: "has_account", label: "Portal", render: (g) => (g.has_account ? `<span class="badge good">${esc(g.username)}</span>` : `<span class="muted small">No account</span>`) },
        ...(has("guardians.manage") ? [{ key: "id", label: "", sort: false, cls: "actions", render: (g) => `<button class="btn sm" data-edit="${g.id}">Edit</button>${g.has_account ? "" : ` <button class="btn sm" data-acct="${g.id}">Create login</button>`}` }] : []),
      ],
    });
    el.querySelectorAll("[data-edit]").forEach((b) => (b.onclick = async () => {
      const g = items.find((x) => x.id === +b.dataset.edit);
      if (await formModal({ title: `Edit ${g.name}`, fields: FIELDS, values: g, onSubmit: (d) => api.put(`/guardians/${g.id}`, d) })) { toast("Saved", "success"); load(); }
    }));
    el.querySelectorAll("[data-acct]").forEach((b) => (b.onclick = async () => {
      const g = items.find((x) => x.id === +b.dataset.acct);
      const r = await formModal({
        title: `Parent portal login · ${g.name}`, cols: 1,
        intro: `<p class="muted" style="margin-top:0">The parent will see only their own children's attendance, results, invoices and payments.</p>`,
        fields: [{ name: "username", label: "Username", required: true, default: g.name.toLowerCase().replace(/[^a-z]+/g, ".") },
          { name: "password", label: "Temporary password", type: "password", required: true, hint: "8+ characters, letters and numbers" }],
        onSubmit: (d) => api.post(`/guardians/${g.id}/account`, d),
      });
      if (r) { toast("Portal account created", "success"); load(); }
    }));
  };
  el.querySelector("#q").oninput = debounce((e) => { q = e.target.value; load(); });
  el.querySelector("#add")?.addEventListener("click", async () => {
    if (await formModal({ title: "New guardian", fields: FIELDS, onSubmit: (d) => api.post("/guardians", d) })) { toast("Guardian added", "success"); load(); }
  });
  await load();
}
