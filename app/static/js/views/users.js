// Users & permissions: accounts, roles (built-in and custom) and per-person permission changes.
import { api } from "../api.js";
import { badge, confirmDialog, esc, fmtDateTime, formModal, handleError, icon, modal, selectHtml, state, table, toast } from "../ui.js";

const LEVELS = ["view", "manage", "approve"];
const LEVEL_LABEL = { view: "View", manage: "Manage", approve: "Approve" };

/** A grid of permission checkboxes, one row per area. `fixed` marks codes that come from the role. */
function matrixHtml(catalog, checked, fixed = new Set()) {
  return `<div class="table-wrap"><table class="table perm-matrix"><thead><tr><th>Area</th>${LEVELS.map((l) => `<th class="num">${LEVEL_LABEL[l]}</th>`).join("")}</tr></thead><tbody>
    ${catalog.map((a) => {
      // Areas hold several permission groups (e.g. Students: students.* and guardians.*).
      const groups = {};
      a.permissions.forEach((p) => { (groups[p.code.split(".")[0]] ||= {})[p.level] = p; });
      return Object.entries(groups).map(([g, levels], i) => `<tr>
        <td>${i === 0 ? `<b>${esc(a.area)}</b><br>` : ""}<span class="muted small">${esc(Object.values(levels).map((p) => p.label).join(" · "))}</span></td>
        ${LEVELS.map((l) => {
          const p = levels[l];
          if (!p) return `<td class="num muted">–</td>`;
          return `<td class="num"><label title="${esc(p.label)}" style="cursor:pointer"><input type="checkbox" data-perm="${p.code}" ${checked.has(p.code) ? "checked" : ""}>${fixed.has(p.code) ? ' <span class="muted small" title="From the role">•</span>' : ""}</label></td>`;
        }).join("")}</tr>`).join("");
    }).join("")}</tbody></table></div>`;
}

/** Keep the grid consistent: manage/approve need view; removing view removes the rest. */
function wireMatrix(root) {
  root.querySelectorAll("[data-perm]").forEach((box) => (box.onchange = () => {
    const [group, level] = box.dataset.perm.split(".");
    const view = root.querySelector(`[data-perm="${group}.view"]`);
    if (level !== "view" && box.checked && view) view.checked = true;
    if (level === "view" && !box.checked) {
      root.querySelectorAll(`[data-perm^="${group}."]`).forEach((b) => (b.checked = false));
    }
  }));
}
const checkedIn = (root) => [...root.querySelectorAll("[data-perm]:checked")].map((b) => b.dataset.perm);

export default async function (el) {
  let tab = "users";
  el.innerHTML = `
    <div class="page-head"><div><h1>Users & Permissions</h1><p>Who can sign in, their role, and exactly what each person may see and do</p></div>
      <div class="page-actions" id="actions"></div></div>
    <div class="tabs"><button data-tab="users" class="active">Users</button><button data-tab="roles">Roles</button></div>
    <div id="pane"></div>`;
  const pane = el.querySelector("#pane");
  let meta = await api.get("/permissions");

  // ------------------------------------------------------------ users
  async function users() {
    el.querySelector("#actions").innerHTML = `<button class="btn primary" id="add">${icon("plus")} New user</button>`;
    pane.innerHTML = `<div class="notice" style="margin-bottom:16px">Teacher and parent logins are best created from the Staff and Guardians pages so they link to the right record. Administrators always have every permission.</div><div class="card"><div id="tbl"></div></div>`;
    const { items } = await api.get("/users");
    table(pane.querySelector("#tbl"), {
      rows: items,
      columns: [
        { key: "username", label: "Username", render: (u) => `<b>${esc(u.username)}</b><br><span class="muted small">${esc(u.full_name)}</span>` },
        { key: "role_name", label: "Role", render: (u) => `${esc(u.role_name)}${u.staff_id ? ' <span class="muted small">· staff</span>' : u.guardian_id ? ' <span class="muted small">· guardian</span>' : ""}
          ${u.extra_permissions.length || u.removed_permissions.length ? `<br><span class="badge info">${u.extra_permissions.length ? `+${u.extra_permissions.length}` : ""}${u.extra_permissions.length && u.removed_permissions.length ? " / " : ""}${u.removed_permissions.length ? `−${u.removed_permissions.length}` : ""} adjusted</span>` : ""}` },
        { key: "permissions", label: "Can", sort: false, render: (u) => (u.role === "admin" ? '<span class="muted small">Everything</span>' : u.role === "parent" ? '<span class="muted small">Parent portal</span>'
          : `<span class="muted small">${u.permissions.length} permission${u.permissions.length === 1 ? "" : "s"}${u.role === "teacher" ? " + own classes" : ""}</span>`) },
        { key: "last_login", label: "Last login", render: (u) => fmtDateTime(u.last_login) },
        { key: "active", label: "Status", render: (u) => badge(u.active ? "active" : "void", u.active ? "active" : "disabled") },
        { key: "id", label: "", sort: false, cls: "actions", render: (u) => `${u.role !== "admin" && u.role !== "parent" && u.id !== state.user.id ? `<button class="btn sm" data-perms="${u.id}">Permissions</button> ` : ""}<button class="btn sm" data-reset="${u.id}">Reset password</button> ${u.id === state.user.id ? "" : `<button class="btn sm ${u.active ? "danger" : ""}" data-toggle="${u.id}" data-active="${u.active}">${u.active ? "Disable" : "Enable"}</button>`}` },
      ],
    });
    pane.querySelectorAll("[data-perms]").forEach((b) => (b.onclick = () => editUser(items.find((u) => u.id === +b.dataset.perms))));
    pane.querySelectorAll("[data-reset]").forEach((b) => (b.onclick = async () => {
      if (await formModal({ title: "Reset password", cols: 1, fields: [{ name: "password", label: "New password", type: "password", required: true, hint: "8+ characters, letters and numbers" }], onSubmit: (d) => api.put(`/users/${b.dataset.reset}`, d) })) toast("Password reset", "success");
    }));
    pane.querySelectorAll("[data-toggle]").forEach((b) => (b.onclick = async () => {
      const enable = b.dataset.active !== "true";
      if (!enable && !(await confirmDialog("Disable this account? The user will no longer be able to sign in.", { danger: true, confirmText: "Disable" }))) return;
      try { await api.put(`/users/${b.dataset.toggle}`, { active: enable }); users(); } catch (err) { handleError(err); }
    }));
    el.querySelector("#add").onclick = async () => {
      const roleOpts = [{ value: "admin", label: "Administrator (everything)" }, ...meta.roles.map((r) => ({ value: `role:${r.id}`, label: r.name }))];
      const r = await formModal({
        title: "New user account",
        fields: [{ name: "full_name", label: "Full name", required: true }, { name: "email", label: "Email", type: "email" },
          { name: "username", label: "Username", required: true }, { name: "password", label: "Password", type: "password", required: true, hint: "8+ characters, letters and numbers" },
          { name: "role", label: "Role", type: "select", required: true, options: roleOpts, default: `role:${meta.roles.find((x) => x.key === "bursar")?.id}` }],
        transform: (d) => {
          if (d.role.startsWith("role:")) {
            const role = meta.roles.find((x) => x.id === +d.role.slice(5));
            return { ...d, role: role.base, role_id: role.id };
          }
          return d;
        },
        onSubmit: (d) => api.post("/users", d),
      });
      if (r) { toast("User created", "success"); users(); }
    };
  }

  function editUser(u) {
    const roleOf = (id) => meta.roles.find((r) => r.id === +id);
    let role = roleOf(u.role_id) || meta.roles.find((r) => r.key === u.role);
    const m = modal({
      title: `Permissions · ${u.full_name}`, wide: true,
      body: `<div class="cols" style="margin-bottom:12px"><label class="field"><span>Role</span>${selectHtml("role", meta.roles.map((r) => ({ value: r.id, label: `${r.name}${r.is_system ? "" : " (custom)"}` })), role?.id)}</label>
        <div class="muted small" style="align-self:end">Ticks show what this person can do. • marks permissions that come from the role; ticking or unticking others adjusts this person only.${u.role === "teacher" ? " Teachers can always work with the classes and subjects they teach." : ""}</div></div>
        <div id="matrix"></div>`,
      actions: [{ label: "Cancel" }, {
        label: "Save", cls: "primary",
        onClick: async ({ el: mel }) => {
          const chosen = new Set(checkedIn(mel));
          const base = new Set(role.permissions);
          const payload = {
            role_id: role.id,
            extra_permissions: [...chosen].filter((p) => !base.has(p)),
            removed_permissions: [...base].filter((p) => !chosen.has(p)),
          };
          await api.put(`/users/${u.id}`, payload);
          toast("Permissions saved", "success");
          users();
        },
      }],
    });
    const draw = (keepPersonal) => {
      const base = new Set(role.permissions);
      // Start from the role, then apply this person's adjustments.
      const checked = new Set(base);
      if (keepPersonal) {
        u.extra_permissions.forEach((p) => checked.add(p));
        u.removed_permissions.forEach((p) => checked.delete(p));
      }
      m.el.querySelector("#matrix").innerHTML = matrixHtml(meta.catalog, checked, base);
      wireMatrix(m.el.querySelector("#matrix"));
    };
    m.el.querySelector('[name="role"]').onchange = (e) => { role = roleOf(e.target.value); draw(false); };
    draw(true);
  }

  // ------------------------------------------------------------ roles
  async function roles() {
    meta = await api.get("/permissions");
    el.querySelector("#actions").innerHTML = `<button class="btn primary" id="add-role">${icon("plus")} New role</button>`;
    pane.innerHTML = `<div class="notice" style="margin-bottom:16px">Roles bundle permissions so many people can be given the same access. Bursar and Teacher are built in and can be edited; add your own (e.g. "Accounts clerk", "Deputy head"). Changes apply to everyone with the role straight away.</div><div class="card"><div id="tbl"></div></div>`;
    table(pane.querySelector("#tbl"), {
      rows: meta.roles, sortKey: null,
      columns: [
        { key: "name", label: "Role", render: (r) => `<b>${esc(r.name)}</b>${r.is_system ? ' <span class="badge plain">built-in</span>' : ""}${r.description ? `<br><span class="muted small">${esc(r.description)}</span>` : ""}` },
        { key: "base_label", label: "Based on" },
        { key: "permissions", label: "Permissions", sort: false, render: (r) => `<span class="muted small">${r.permissions.length ? r.permissions.length : "None"}${r.base === "teacher" ? " + own classes" : ""}</span>` },
        { key: "users", label: "Users", num: true },
        { key: "id", label: "", sort: false, cls: "actions", render: (r) => `<button class="btn sm" data-edit="${r.id}">Edit</button>${r.is_system ? "" : ` <button class="btn sm danger" data-del="${r.id}">Delete</button>`}` },
      ],
    });
    pane.querySelectorAll("[data-edit]").forEach((b) => (b.onclick = () => editRole(meta.roles.find((r) => r.id === +b.dataset.edit))));
    pane.querySelectorAll("[data-del]").forEach((b) => (b.onclick = async () => {
      if (!(await confirmDialog("Delete this role?", { danger: true, confirmText: "Delete" }))) return;
      try { await api.del(`/roles/${b.dataset.del}`); toast("Role deleted", "success"); roles(); } catch (err) { handleError(err); }
    }));
    el.querySelector("#add-role").onclick = () => editRole(null);
  }

  function editRole(r) {
    const m = modal({
      title: r ? `Edit role · ${r.name}` : "New role", wide: true,
      body: `<form class="form" novalidate><div class="cols">
          <label class="field"><span>Name *</span><input class="input" name="name" value="${esc(r?.name || "")}" placeholder="e.g. Accounts clerk"></label>
          ${r?.is_system ? `<div class="muted small" style="align-self:end">Built-in role: based on ${esc(r.base_label)}</div>` : `<label class="field"><span>Based on *</span>${selectHtml("base", meta.bases, r?.base || "bursar")}<small class="hint">Teacher-based roles also work with the classes they teach</small></label>`}
          <label class="field" style="grid-column:1/-1"><span>Description</span><input class="input" name="description" value="${esc(r?.description || "")}"></label></div>
        <div id="matrix"></div></form>`,
      actions: [{ label: "Cancel" }, {
        label: r ? "Save role" : "Create role", cls: "primary",
        onClick: async ({ el: mel }) => {
          const f = mel.querySelector("form");
          const data = { name: f.name.value, description: f.description.value, base: f.base?.value, permissions: checkedIn(mel) };
          await (r ? api.put(`/roles/${r.id}`, data) : api.post("/roles", data));
          toast(r ? "Role saved" : "Role created", "success");
          roles();
        },
      }],
    });
    m.el.querySelector("#matrix").innerHTML = matrixHtml(meta.catalog, new Set(r?.permissions || []));
    wireMatrix(m.el.querySelector("#matrix"));
  }

  const show = async () => {
    el.querySelectorAll("[data-tab]").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
    pane.innerHTML = `<div class="empty">Loading…</div>`;
    try { await (tab === "users" ? users() : roles()); } catch (err) { handleError(err); }
  };
  el.querySelectorAll("[data-tab]").forEach((b) => (b.onclick = () => { tab = b.dataset.tab; show(); }));
  await show();
}
