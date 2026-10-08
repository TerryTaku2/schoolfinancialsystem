import { api } from "../api.js";
import { esc, fieldHtml, readForm, showFieldErrors, state, toast } from "../ui.js";

const FIELDS = [
  { name: "current_password", label: "Current password", type: "password", required: true },
  { name: "new_password", label: "New password", type: "password", required: true, hint: "At least 8 characters, mixing letters with numbers or symbols" },
  { name: "confirm", label: "Confirm new password", type: "password", required: true },
];

export default async function (el) {
  const u = state.user;
  el.innerHTML = `
    <div class="page-head"><div><h1>My Profile</h1><p>Account details and password</p></div></div>
    <div class="grid g-2">
      <div class="card"><div class="card-head"><h3>Account</h3></div><div class="card-body"><dl class="kv">
        <dt>Name</dt><dd>${esc(u.full_name)}</dd><dt>Username</dt><dd>${esc(u.username)}</dd>
        <dt>Role</dt><dd style="text-transform:capitalize">${esc(u.role)}</dd><dt>Email</dt><dd>${esc(u.email || "—")}</dd></dl></div></div>
      <div class="card"><div class="card-head"><h3>Change password</h3></div><div class="card-body">
        <form class="form" id="pw" novalidate>${FIELDS.map((f) => fieldHtml(f)).join("")}<div><button class="btn primary">Update password</button></div></form></div></div>
    </div>`;
  const form = el.querySelector("#pw");
  form.onsubmit = async (e) => {
    e.preventDefault();
    const d = readForm(form, FIELDS);
    if (d.new_password !== d.confirm) return showFieldErrors(form, { fields: { confirm: "Passwords do not match" } });
    try {
      await api.post("/auth/change-password", { current_password: d.current_password, new_password: d.new_password });
      form.reset();
      showFieldErrors(form, {});
      toast("Password updated", "success");
    } catch (err) { showFieldErrors(form, err); toast(err.message, "error"); }
  };
}
