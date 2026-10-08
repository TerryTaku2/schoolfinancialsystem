// Shared UI helpers: escaping, formatting, toasts, modals, forms, tables.
import { ApiError } from "./api.js";

export const state = { user: null, meta: null };

export function esc(v) {
  return String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

export function $(sel, root = document) { return root.querySelector(sel); }
export function $$(sel, root = document) { return [...root.querySelectorAll(sel)]; }

// money(12.5) in the school currency; money(12.5, "ZWG") or money(12.5, { currency: "ZWG" }) in another.
export function money(n, opts = {}) {
  if (typeof opts === "string") opts = { currency: opts };
  const cur = opts.currency || state.meta?.currency || "USD";
  const v = Number(n || 0);
  const digits = opts.whole ? 0 : 2;
  if (cur === "ZWG") return `ZiG ${v.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
  try {
    return new Intl.NumberFormat(undefined, { style: "currency", currency: cur, maximumFractionDigits: digits }).format(v);
  } catch { return `${cur} ${v.toFixed(2)}`; }
}

export function compactMoney(n, currency) {
  const v = Number(n || 0), a = Math.abs(v);
  const c = { currency };
  if (a >= 1e6) return money(v / 1e6, c).replace(/\.00/, "") + "M";
  if (a >= 1e4) return money(Math.round(v / 100) / 10, c).replace(/\.00/, "") + "K";
  return money(v, { ...c, whole: true });
}

// ---------------------------------------------------------------- currencies
// The school's currencies: ["USD"] normally, ["USD", "ZWG"] with dual currency on (Settings -> School).
export function currencies() { return state.meta?.profile?.currencies || [state.meta?.currency || "USD"]; }
export function isDual() { return currencies().length > 1; }
export function baseCurrency() { return currencies()[0]; }
// A currency picker for forms; only shown when the school works in both currencies.
export function currencyField(extra = {}) {
  return isDual() ? [{ name: "currency", label: "Currency", type: "select", required: true, options: currencies(), default: baseCurrency(), ...extra }] : [];
}
// Amounts per currency side by side: [{currency, amount}] or {USD: 1, ZWG: 2}.
export function moneyList(items) {
  const rows = Array.isArray(items) ? items : Object.entries(items || {}).map(([currency, amount]) => ({ currency, amount }));
  return rows.length ? rows.map((r) => money(r.amount, r.currency)).join(" · ") : money(0);
}

export function fmtDate(s, opts = { day: "numeric", month: "short", year: "numeric" }) {
  if (!s) return "—";
  const d = new Date(s.length === 10 ? s + "T00:00:00" : s);
  return isNaN(d) ? s : d.toLocaleDateString(undefined, opts);
}

export function fmtDateTime(s) {
  if (!s) return "—";
  const d = new Date(s);
  return d.toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

export function pct(v) { return v === null || v === undefined ? "—" : `${v}%`; }

export function today() { return state.meta?.today || new Date().toISOString().slice(0, 10); }

export function initials(name) { return (name || "?").split(/\s+/).map((p) => p[0]).slice(0, 2).join("").toUpperCase(); }

const BADGE = {
  paid: "good", active: "good", present: "good", approved: "accent", promote: "good",
  partial: "warn", pending: "warn", late: "warn", on_leave: "warn", suspended: "warn",
  unpaid: "info", excused: "info", graduate: "info", graduated: "info",
  overdue: "bad", absent: "bad", rejected: "bad", void: "plain", left: "plain", blocked: "bad",
  transferred: "plain", withdrawn: "plain", draft: "warn", disposed: "plain", written_off: "plain", reversed: "plain",
};
export function badge(status, label) {
  if (!status) return "";
  return `<span class="badge ${BADGE[status] || ""}">${esc(label || String(status).replace(/_/g, " "))}</span>`;
}

// has("fees.manage"): does the signed-in user hold any of these permissions? (Administrators hold all.)
export function has(...perms) {
  return !!state.user && (state.user.role === "admin" || perms.some((p) => (state.user.permissions || []).includes(p)));
}

export function debounce(fn, ms = 300) {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}

// ---------------------------------------------------------------- toasts
export function toast(msg, type = "") {
  const el = document.createElement("div");
  el.className = `toast ${type}`;
  el.textContent = msg;
  $("#toasts").appendChild(el);
  setTimeout(() => el.remove(), type === "error" ? 6000 : 3500);
}

export function handleError(err) {
  console.error(err);
  toast(err instanceof ApiError || err?.message ? err.message : "Something went wrong", "error");
}

// ---------------------------------------------------------------- icons
const P = {
  dashboard: "M3 13h8V3H3zm0 8h8v-6H3zm10 0h8V11h-8zm0-18v6h8V3z",
  students: "M12 3 1 9l11 6 9-4.9V17h2V9zM5 13.2v4L12 21l7-3.8v-4L12 17z",
  guardians: "M16 11c1.7 0 3-1.3 3-3s-1.3-3-3-3-3 1.3-3 3 1.3 3 3 3m-8 0c1.7 0 3-1.3 3-3S9.7 5 8 5 5 6.3 5 8s1.3 3 3 3m0 2c-2.3 0-7 1.2-7 3.5V19h14v-2.5C15 14.2 10.3 13 8 13m8 0c-.3 0-.6 0-1 .1 1.2.8 2 2 2 3.4V19h6v-2.5c0-2.3-4.7-3.5-7-3.5",
  staff: "M12 12c2.2 0 4-1.8 4-4s-1.8-4-4-4-4 1.8-4 4 1.8 4 4 4m0 2c-2.7 0-8 1.3-8 4v2h16v-2c0-2.7-5.3-4-8-4",
  classes: "M4 6H2v14c0 1.1.9 2 2 2h14v-2H4zm16-4H8c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2m-1 9H9V9h10zm-4 4H9v-2h6zm4-8H9V5h10z",
  timetable: "M19 4h-1V2h-2v2H8V2H6v2H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6a2 2 0 0 0-2-2m0 16H5V10h14zM7 12h5v5H7z",
  attendance: "M9 16.2 4.8 12l-1.4 1.4L9 19 21 7l-1.4-1.4z",
  exams: "M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8zm-1 7V3.5L18.5 9zM8 13h8v2H8zm0 4h8v2H8z",
  results: "M5 9.2h3V19H5zM10.6 5h2.8v14h-2.8zm5.6 8H19v6h-2.8z",
  fees: "M11.8 10.9c-2.3-.6-3-1.2-3-2.1 0-1.1 1-1.9 2.7-1.9 1.8 0 2.4.8 2.5 2.1h2.2c-.1-1.7-1.1-3.3-3.2-3.8V3h-3v2.2c-1.9.4-3.5 1.7-3.5 3.6 0 2.3 1.9 3.5 4.7 4.1 2.5.6 3 1.5 3 2.4 0 .7-.5 1.8-2.7 1.8-2.1 0-2.9-.9-3-2.1H6c.1 2.2 1.8 3.5 3.7 3.9V21h3v-2.2c1.9-.4 3.5-1.5 3.5-3.6 0-2.8-2.4-3.8-4.7-4.3",
  invoices: "M19.5 3.5 18 2l-1.5 1.5L15 2l-1.5 1.5L12 2l-1.5 1.5L9 2 7.5 3.5 6 2 4.5 3.5 3 2v20l1.5-1.5L6 22l1.5-1.5L9 22l1.5-1.5L12 22l1.5-1.5L15 22l1.5-1.5L18 22l1.5-1.5L21 22V2zM18 17H6v-2h12zm0-4H6v-2h12zm0-4H6V7h12z",
  payments: "M20 4H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2m0 14H4v-6h16zm0-10H4V6h16z",
  expenses: "M21 18v1c0 1.1-.9 2-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h14c1.1 0 2 .9 2 2v1h-9a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2zm-9-2h10V8H12zm4-2.5c-.8 0-1.5-.7-1.5-1.5s.7-1.5 1.5-1.5 1.5.7 1.5 1.5-.7 1.5-1.5 1.5",
  reports: "M19 3H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2M9 17H7v-7h2zm4 0h-2V7h2zm4 0h-2v-4h2z",
  announcements: "M18 11v2h4v-2zm-2 6.6c1 .7 2.2 1.6 3.2 2.4l.8-1.1c-1-.7-2.2-1.6-3.2-2.4zM20 5.1l-.8-1.1c-1 .7-2.2 1.6-3.2 2.4l.8 1.1c1-.8 2.2-1.7 3.2-2.4M4 9a2 2 0 0 0-2 2v2c0 1.1.9 2 2 2h1v4h2v-4h1l5 3V6L8 9zm11.5 3c0-1.3-.6-2.5-1.5-3.4v6.7c.9-.8 1.5-2 1.5-3.3",
  users: "M12 1 3 5v6c0 5.5 3.8 10.7 9 12 5.2-1.3 9-6.5 9-12V5zm0 10.99h7c-.5 4.1-3.3 7.8-7 8.9V12H5V6.3l7-3.1z",
  audit: "M13 3a9 9 0 0 0-9 9H1l3.9 3.9.1.1L9 12H6c0-3.9 3.1-7 7-7s7 3.1 7 7-3.1 7-7 7c-1.9 0-3.7-.8-4.9-2.1l-1.4 1.4A9 9 0 1 0 13 3m-1 5v5l4.3 2.5.7-1.2-3.5-2.1V8z",
  settings: "M19.1 12.9a7 7 0 0 0 0-1.8l2-1.5-2-3.4-2.3.9a7 7 0 0 0-1.6-.9L14.9 4h-3.8l-.4 2.2c-.6.2-1.1.5-1.6.9l-2.3-.9-2 3.4 2 1.5a7 7 0 0 0 0 1.8l-2 1.5 2 3.4 2.3-.9c.5.4 1 .7 1.6.9l.4 2.2h3.8l.4-2.2c.6-.2 1.1-.5 1.6-.9l2.3.9 2-3.4zM13 15.5a3.5 3.5 0 1 1 0-7 3.5 3.5 0 0 1 0 7",
  plus: "M19 13h-6v6h-2v-6H5v-2h6V5h2v6h6z",
  statements: "M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8zm4 18H6V4h7v5h5zM8 12h8v2H8zm0 4h8v2H8zm0-8h3v2H8z",
  ledger: "M18 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V4a2 2 0 0 0-2-2M6 4h5v8l-2.5-1.5L6 12z",
  print: "M19 8H5c-1.7 0-3 1.3-3 3v6h4v4h12v-4h4v-6c0-1.7-1.3-3-3-3m-3 11H8v-5h8zm3-7c-.6 0-1-.4-1-1s.4-1 1-1 1 .4 1 1-.4 1-1 1m-1-9H6v4h12z",
  download: "M5 20h14v-2H5zM19 9h-4V3H9v6H5l7 7z",
  menu: "M3 18h18v-2H3zm0-5h18v-2H3zm0-7v2h18V6z",
  moon: "M12 3a9 9 0 1 0 9 9c0-.5 0-1-.1-1.4A5.5 5.5 0 0 1 13.4 3.1 9 9 0 0 0 12 3",
  logout: "M10.1 15.6 11.5 17l5-5-5-5-1.4 1.4 2.6 2.6H3v2h9.7zM19 3H5a2 2 0 0 0-2 2v4h2V5h14v14H5v-4H3v4a2 2 0 0 0 2 2h14c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2",
  back: "M20 11H7.8l5.6-5.6L12 4l-8 8 8 8 1.4-1.4L7.8 13H20z",
  payroll: "M20 6h-4V4c0-1.1-.9-2-2-2h-4c-1.1 0-2 .9-2 2v2H4c-1.1 0-2 .9-2 2v11c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V8c0-1.1-.9-2-2-2m-10-2h4v2h-4zm2 13.5c-1.9 0-3.5-1.6-3.5-3.5s1.6-3.5 3.5-3.5 3.5 1.6 3.5 3.5-1.6 3.5-3.5 3.5",
  assets: "M12 2 2 7v2h20V7zM4 11v7h3v-7zm6.5 0v7h3v-7zM17 11v7h3v-7zM2 20v2h20v-2z",
  rates: "M6.99 11 3 15l3.99 4v-3H14v-2H6.99zM21 9l-3.99-4v3H10v2h7.01v3z",
  bank: "M4 10h3v7H4zm6.5 0h3v7h-3zM2 19h20v3H2zm15-9h3v7h-3zm-5-9L2 6v2h20V6z",
};
export function icon(name) {
  return `<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="${P[name] || ""}"/></svg>`;
}

// ---------------------------------------------------------------- modal
export function modal({ title, body = "", wide = false, actions = [], onClose } = {}) {
  const back = document.createElement("div");
  back.className = "modal-back";
  back.innerHTML = `
    <div class="modal ${wide ? "wide" : ""}" role="dialog" aria-modal="true" aria-label="${esc(title)}">
      <div class="modal-head"><h2>${esc(title)}</h2><button class="btn ghost icon" data-close aria-label="Close">✕</button></div>
      <div class="modal-body">${body}</div>
      ${actions.length ? `<div class="modal-foot">${actions.map((a, i) => `<button class="btn ${a.cls || ""}" data-action="${i}" ${a.type === "submit" ? 'type="submit"' : 'type="button"'}>${esc(a.label)}</button>`).join("")}</div>` : ""}
    </div>`;
  const prevFocus = document.activeElement;
  const close = () => {
    back.remove();
    document.removeEventListener("keydown", onKey);
    document.body.classList.remove("printing-modal");
    prevFocus?.focus?.();
    onClose?.();
  };
  const onKey = (e) => { if (e.key === "Escape") close(); };
  document.addEventListener("keydown", onKey);
  back.addEventListener("mousedown", (e) => { if (e.target === back) close(); });
  back.querySelector("[data-close]").onclick = close;
  actions.forEach((a, i) => {
    back.querySelector(`[data-action="${i}"]`).onclick = async (e) => {
      const btn = e.currentTarget;
      if (!a.onClick) return close();
      btn.disabled = true;
      try {
        const keep = await a.onClick({ close, el: back, btn });
        if (keep !== true) close();
      } catch (err) { handleError(err); } finally { btn.disabled = false; }
    };
  });
  document.body.appendChild(back);
  setTimeout(() => (back.querySelector("input,select,textarea") || back.querySelector(".modal-foot .btn:last-child"))?.focus(), 30);
  return { el: back, body: back.querySelector(".modal-body"), close };
}

export function confirmDialog(message, { title = "Please confirm", confirmText = "Confirm", danger = false, reason = false, typed } = {}) {
  return new Promise((resolve) => {
    let done = false;
    const body = `<p style="margin-top:0">${esc(message)}</p>
      ${reason ? `<label class="field"><span>Reason</span><textarea class="input" name="reason" required placeholder="Why is this needed?"></textarea></label>` : ""}
      ${typed ? `<label class="field"><span>Type <b>${esc(typed)}</b> to confirm</span><input class="input" name="typed" autocomplete="off"></label>` : ""}`;
    modal({
      title, body,
      onClose: () => { if (!done) resolve(false); },
      actions: [
        { label: "Cancel" },
        {
          label: confirmText, cls: danger ? "danger solid" : "primary",
          onClick: ({ el }) => {
            const r = el.querySelector('[name="reason"]')?.value.trim();
            if (reason && (!r || r.length < 5)) { toast("Please give a reason (5+ characters)", "error"); return true; }
            if (typed && el.querySelector('[name="typed"]').value !== typed) { toast(`Type ${typed} to confirm`, "error"); return true; }
            done = true;
            resolve(reason ? r : typed ? typed : true);
          },
        },
      ],
    });
  });
}

// ---------------------------------------------------------------- forms
// field: {name, label, type, options:[{value,label}]|[str], required, hint, placeholder, full, min, max, step}
export function fieldHtml(f, value) {
  const v = value ?? f.default ?? "";
  const req = f.required ? "required" : "";
  const attrs = `name="${esc(f.name)}" id="f-${esc(f.name)}" ${req} ${f.min !== undefined ? `min="${f.min}"` : ""} ${f.max !== undefined ? `max="${f.max}"` : ""} ${f.step ? `step="${f.step}"` : ""} ${f.placeholder ? `placeholder="${esc(f.placeholder)}"` : ""} ${f.disabled ? "disabled" : ""}`;
  let control;
  if (f.type === "select") {
    const opts = (f.options || []).map((o) => (typeof o === "object" ? o : { value: o, label: o }));
    control = `<select class="input" ${attrs}>${f.required ? "" : `<option value="">${esc(f.empty || "—")}</option>`}${opts.map((o) => `<option value="${esc(o.value)}" ${String(o.value) === String(v) ? "selected" : ""}>${esc(o.label)}</option>`).join("")}</select>`;
  } else if (f.type === "textarea") {
    control = `<textarea class="input" ${attrs}>${esc(v)}</textarea>`;
  } else if (f.type === "checkbox") {
    return `<label class="check ${f.full ? "full" : ""}"><input type="checkbox" ${attrs} ${v ? "checked" : ""}> ${esc(f.label)}</label>`;
  } else if (f.type === "heading") {
    return `<h3 style="grid-column:1/-1;margin-top:6px">${esc(f.label)}</h3>`;
  } else {
    control = `<input class="input" type="${f.type || "text"}" value="${esc(v)}" ${attrs} ${f.type === "password" ? 'autocomplete="new-password"' : ""}>`;
  }
  return `<label class="field" ${f.full ? 'style="grid-column:1/-1"' : ""}><span>${esc(f.label)}${f.required ? " *" : ""}</span>${control}${f.hint ? `<small class="hint">${esc(f.hint)}</small>` : ""}<small class="err" data-err="${esc(f.name)}"></small></label>`;
}

export function readForm(root, fields) {
  const out = {};
  for (const f of fields) {
    if (f.type === "heading") continue;
    const el = root.querySelector(`[name="${f.name}"]`);
    if (!el) continue;
    out[f.name] = f.type === "checkbox" ? el.checked : el.value.trim();
  }
  return out;
}

export function showFieldErrors(root, err) {
  root.querySelectorAll("[data-err]").forEach((e) => (e.textContent = ""));
  root.querySelectorAll(".invalid").forEach((e) => e.classList.remove("invalid"));
  for (const [name, msg] of Object.entries(err.fields || {})) {
    const slot = root.querySelector(`[data-err="${name}"]`);
    if (slot) slot.textContent = msg;
    root.querySelector(`[name="${name}"]`)?.classList.add("invalid");
  }
}

export function formModal({ title, fields, values = {}, submitText = "Save", wide = false, cols = 2, intro = "", onSubmit, transform }) {
  return new Promise((resolve) => {
    const body = `${intro}<form class="form" novalidate><div class="${cols === 1 ? "form" : cols === 3 ? "cols-3" : "cols"}">${fields.map((f) => fieldHtml(f, values[f.name])).join("")}</div></form>`;
    let result = null;
    const m = modal({
      title, body, wide,
      onClose: () => resolve(result),
      actions: [
        { label: "Cancel" },
        {
          label: submitText, cls: "primary",
          onClick: async ({ el }) => {
            const form = el.querySelector("form");
            let data = readForm(form, fields);
            const missing = fields.filter((f) => f.required && f.type !== "checkbox" && !data[f.name]);
            if (missing.length) {
              showFieldErrors(form, { fields: Object.fromEntries(missing.map((f) => [f.name, "Required"])) });
              toast("Please fill in the required fields", "error");
              return true;
            }
            if (transform) data = transform(data);
            try {
              result = await onSubmit(data);
              return false;
            } catch (err) {
              if (err instanceof ApiError) {
                showFieldErrors(form, err);
                toast(err.message, "error");
                return true;
              }
              throw err;
            }
          },
        },
      ],
    });
    m.el.querySelector("form").addEventListener("submit", (e) => { e.preventDefault(); m.el.querySelector(".modal-foot .primary").click(); });
  });
}

// ---------------------------------------------------------------- tables
// columns: {key, label, render(row) -> html, num, sort:true|fn, csv(row) -> value}
export function table(container, { columns, rows, onRowClick, empty = "Nothing to show yet.", footer, sortKey, sortDir = 1 }) {
  let key = sortKey, dir = sortDir;
  const draw = () => {
    let data = [...rows];
    if (key) {
      const col = columns.find((c) => c.key === key);
      const get = typeof col?.sort === "function" ? col.sort : (r) => r[key];
      data.sort((a, b) => {
        const x = get(a), y = get(b);
        if (x === y) return 0;
        if (x === null || x === undefined) return 1;
        if (y === null || y === undefined) return -1;
        return (typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y), undefined, { numeric: true })) * dir;
      });
    }
    if (!data.length) {
      container.innerHTML = `<div class="empty">${esc(empty)}</div>`;
      return;
    }
    container.innerHTML = `<div class="table-wrap"><table class="table rtable"><thead><tr>${columns.map((c) =>
      `<th class="${c.num ? "num" : ""} ${c.sort !== false ? "sortable" : ""}" data-key="${esc(c.key)}" ${c.sort !== false ? 'tabindex="0"' : ""} aria-sort="${key === c.key ? (dir > 0 ? "ascending" : "descending") : "none"}">${esc(c.label)}${key === c.key ? (dir > 0 ? " ↑" : " ↓") : ""}</th>`).join("")}</tr></thead>
      <tbody>${data.map((r, i) => `<tr data-i="${i}" class="${onRowClick ? "clickable" : ""}">${columns.map((c) =>
        `<td class="${c.num ? "num" : ""} ${c.cls || ""}" data-label="${esc(c.label)}"><span class="cell">${c.render ? c.render(r) : esc(r[c.key] ?? "—")}</span></td>`).join("")}</tr>`).join("")}</tbody>
      ${footer ? `<tfoot><tr>${footer}</tr></tfoot>` : ""}</table></div>`;
    container.querySelectorAll("th.sortable").forEach((th) => {
      const sortBy = () => { const k = th.dataset.key; dir = key === k ? -dir : 1; key = k; draw(); };
      th.onclick = sortBy;
      th.onkeydown = (e) => { if (e.key === "Enter") sortBy(); };
    });
    if (onRowClick) {
      container.querySelectorAll("tbody tr").forEach((tr) => {
        tr.onclick = (e) => { if (!e.target.closest("button,a,input,select")) onRowClick(data[+tr.dataset.i]); };
      });
    }
  };
  draw();
  return { redraw: draw, setRows: (r) => { rows = r; draw(); } };
}

export function downloadCSV(filename, columns, rows) {
  const cell = (v) => {
    const s = String(v ?? "");
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const lines = [columns.map((c) => cell(c.label)).join(",")];
  for (const r of rows) lines.push(columns.map((c) => cell(c.csv ? c.csv(r) : r[c.key])).join(","));
  const blob = new Blob(["﻿" + lines.join("\r\n")], { type: "text/csv;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename.endsWith(".csv") ? filename : filename + ".csv";
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

export function pager(container, { page, perPage, total, onPage }) {
  const pages = Math.max(1, Math.ceil(total / perPage));
  container.innerHTML = total > perPage ? `<div class="pager"><span>${(page - 1) * perPage + 1}–${Math.min(page * perPage, total)} of ${total}</span>
    <span class="row"><button class="btn sm" data-p="${page - 1}" ${page <= 1 ? "disabled" : ""}>Previous</button><button class="btn sm" data-p="${page + 1}" ${page >= pages ? "disabled" : ""}>Next</button></span></div>`
    : total ? `<div class="pager"><span>${total} record${total === 1 ? "" : "s"}</span></div>` : "";
  container.querySelectorAll("[data-p]").forEach((b) => (b.onclick = () => onPage(+b.dataset.p)));
}

export function printModal() {
  document.body.classList.add("printing-modal");
  window.print();
  setTimeout(() => document.body.classList.remove("printing-modal"), 500);
}

export function termOptions() {
  return (state.meta?.terms || []).map((t) => ({ value: t.id, label: t.label }));
}
export function classOptions() {
  return (state.meta?.classes || []).map((c) => ({ value: c.id, label: c.name }));
}
export function selectHtml(name, options, value, { empty, cls = "input" } = {}) {
  return `<select class="${cls}" name="${esc(name)}" aria-label="${esc(name)}">${empty !== undefined ? `<option value="">${esc(empty)}</option>` : ""}${options.map((o) =>
    `<option value="${esc(o.value)}" ${String(o.value) === String(value ?? "") ? "selected" : ""}>${esc(o.label)}</option>`).join("")}</select>`;
}
