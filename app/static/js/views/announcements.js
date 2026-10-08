import { api } from "../api.js";
import { confirmDialog, esc, fmtDate, formModal, handleError, has, icon, toast } from "../ui.js";

export default async function (el) {
  el.innerHTML = `
    <div class="page-head"><div><h1>Announcements</h1><p>Notices for staff, parents or everyone</p></div>
      <div class="page-actions">${has("announcements.manage") ? `<button class="btn primary" id="add">${icon("plus")} New announcement</button>` : ""}</div></div>
    <div class="card" id="list"></div>`;
  const load = async () => {
    const { items } = await api.get("/announcements");
    el.querySelector("#list").innerHTML = items.map((a) => `<div class="announcement">
      <div class="row" style="justify-content:space-between"><div><strong>${a.pinned ? "📌 " : ""}${esc(a.title)}</strong> <span class="badge plain">${a.audience === "all" ? "Everyone" : a.audience === "staff" ? "Staff" : "Parents"}</span></div>
      <span class="row muted small">${esc(a.author || "")} · ${fmtDate(a.created_at)}${has("announcements.manage") ? ` <button class="btn sm ghost danger" data-del="${a.id}">Delete</button>` : ""}</span></div>
      <p>${esc(a.body)}</p></div>`).join("") || `<div class="empty">No announcements.</div>`;
    el.querySelectorAll("[data-del]").forEach((b) => (b.onclick = async () => {
      if (!(await confirmDialog("Delete this announcement?", { danger: true, confirmText: "Delete" }))) return;
      try { await api.del(`/announcements/${b.dataset.del}`); load(); } catch (err) { handleError(err); }
    }));
  };
  el.querySelector("#add")?.addEventListener("click", async () => {
    const r = await formModal({
      title: "New announcement", cols: 1,
      fields: [{ name: "title", label: "Title", required: true }, { name: "body", label: "Message", type: "textarea", required: true },
        { name: "audience", label: "Audience", type: "select", required: true, options: [{ value: "all", label: "Everyone" }, { value: "staff", label: "Staff only" }, { value: "parents", label: "Parents only" }] },
        { name: "pinned", label: "Pin to top", type: "checkbox" }],
      onSubmit: (d) => api.post("/announcements", d),
    });
    if (r) { toast("Announcement published", "success"); load(); }
  });
  await load();
}
