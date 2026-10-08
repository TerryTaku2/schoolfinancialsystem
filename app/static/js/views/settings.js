import { api } from "../api.js";
import { refreshMeta } from "../app.js";
import { badge, confirmDialog, esc, fmtDate, formModal, handleError, has, icon, logoUrl, selectHtml, table, toast } from "../ui.js";

// "1 USD = 26.75 ZWG · 18.4 ZAR (7 Oct 2026)"
const ratesText = (rates, order = []) => {
  const list = order.map((c) => rates?.[c]).filter(Boolean);
  return list.length ? `Latest rates: 1 USD = ${list.map((r) => `${r.per_usd} ${esc(r.currency)}`).join(" · ")} (${fmtDate(list.map((r) => r.date).sort().pop())}).` : "";
};

export default async function (el) {
  el.innerHTML = `
    <div class="page-head"><div><h1>Settings</h1><p>School type, academic calendar, grading scales and end-of-year promotion</p></div></div>
    <div class="tabs">${[["school", "School", "settings.manage"], ["calendar", "Academic calendar", "academics.manage"], ["grading", "Grading scales", "academics.manage"], ["promotion", "Promotion", "promotion.approve"]]
      .filter(([, , p]) => has(p)).map(([k, l]) => `<button data-tab="${k}">${l}</button>`).join("")}</div>
    <div id="pane"></div>`;
  const pane = el.querySelector("#pane");

  const calendar = async () => {
    const { items } = await api.get("/years");
    pane.innerHTML = `<div class="row" style="justify-content:flex-end;margin-bottom:12px"><button class="btn primary" id="add-year">${icon("plus")} New academic year</button></div>
      <div class="stack">${items.map((y) => `<div class="card"><div class="card-head"><div><h3>${esc(y.name)} ${y.is_current ? badge("active", "current") : ""}</h3><div class="sub">${fmtDate(y.start_date)} – ${fmtDate(y.end_date)}</div></div>
        <button class="btn sm" data-term="${y.id}" data-start="${y.start_date}" data-end="${y.end_date}">${icon("plus")} Add term</button></div>
        <ul class="list">${y.terms.map((t) => `<li><span><b>${esc(t.name)}</b> <span class="muted small">${fmtDate(t.start_date)} – ${fmtDate(t.end_date)}</span></span>
          <span class="row">${t.is_current ? badge("active", "current term") : `<button class="btn sm" data-current="${t.id}">Make current</button><button class="btn sm danger" data-del="${t.id}">Delete</button>`}</span></li>`).join("") || `<li class="muted">No terms yet.</li>`}</ul></div>`).join("") || `<div class="card empty">No academic years configured.</div>`}</div>`;
    pane.querySelector("#add-year").onclick = async () => {
      if (await formModal({ title: "New academic year", fields: [{ name: "name", label: "Name", required: true, placeholder: "e.g. 2027" }, { type: "heading", name: "_", label: "" },
        { name: "start_date", label: "Starts", type: "date", required: true }, { name: "end_date", label: "Ends", type: "date", required: true }], onSubmit: (d) => api.post("/years", d) })) { toast("Year created", "success"); calendar(); }
    };
    pane.querySelectorAll("[data-term]").forEach((b) => (b.onclick = async () => {
      if (await formModal({ title: "Add term", fields: [{ name: "name", label: "Name", required: true, placeholder: "e.g. Term 1", full: true },
        { name: "start_date", label: "Starts", type: "date", required: true, min: b.dataset.start, max: b.dataset.end }, { name: "end_date", label: "Ends", type: "date", required: true, min: b.dataset.start, max: b.dataset.end }],
        onSubmit: (d) => api.post("/terms", { ...d, year_id: b.dataset.term }) })) { toast("Term added", "success"); await refreshMeta(); calendar(); }
    }));
    pane.querySelectorAll("[data-current]").forEach((b) => (b.onclick = async () => {
      if (!(await confirmDialog("Switch the current term? Dashboards, attendance and fees default to the current term.", { confirmText: "Switch" }))) return;
      await api.put(`/terms/${b.dataset.current}/current`).catch(handleError);
      await refreshMeta();
      calendar();
    }));
    pane.querySelectorAll("[data-del]").forEach((b) => (b.onclick = async () => {
      if (!(await confirmDialog("Delete this term?", { danger: true, confirmText: "Delete" }))) return;
      try { await api.del(`/terms/${b.dataset.del}`); await refreshMeta(); calendar(); } catch (err) { handleError(err); }
    }));
  };

  const school = async () => {
    const p = await api.get("/school");
    pane.innerHTML = `<div class="grid g-2"><div class="card"><div class="card-head"><div><h3>${esc(p.name)}</h3><div class="sub">${esc(p.school_type_label)} · ${esc(p.currency)}</div></div></div>
      <div class="card-body"><p style="margin-top:0" class="muted">Levels offered: ${p.levels.map((l) => esc(l.label)).join(", ")}.</p>
      <form class="form" id="type"><label class="field"><span>School type</span>${selectHtml("school_type", p.school_types, p.school_type)}</label>
        <label class="check"><input type="checkbox" name="create_classes" checked> Create one class for each newly offered level</label>
        <div class="row"><button class="btn primary">Save school type</button></div></form>
      <p class="muted small">Changing the type adds the new levels' curriculum subjects and grading scales. Levels can only be removed once they have no classes.</p>
      <h3 style="margin:18px 0 6px">School logo</h3>
      <div class="row" style="gap:16px;align-items:center">
        <div class="logo-preview">${logoUrl() ? `<img src="${esc(logoUrl())}" alt="School logo">` : `<span class="muted small">No logo</span>`}</div>
        <div class="row"><label class="btn">${icon("upload")} ${logoUrl() ? "Replace logo" : "Upload logo"}<input type="file" id="logo-file" accept="image/png,image/jpeg,image/gif,image/webp,image/svg+xml" hidden></label>
          ${logoUrl() ? `<button class="btn ghost danger" id="logo-remove">Remove</button>` : ""}</div>
      </div>
      <p class="muted small" style="margin:6px 0 0">Shown in the menu and at the top of every receipt, invoice, report card, payslip, statement and printed page. PNG, JPG, GIF, WebP or SVG, up to 1 MB; a square or wide image with a transparent background prints best.</p>
      <h3 style="margin:18px 0 6px">Demo school</h3>
      <label class="check"><input type="checkbox" id="demo-login" ${p.demo ? "checked" : ""}> Demo accounts sign in without a password</label>
      <p class="muted small" style="margin:6px 0 0">For training and trying the system out with demo data only. Anyone who opens this school's address can then sign in as the administrator, bursar, teacher or parent demo account.</p>
      <h3 style="margin:18px 0 6px">Currencies</h3>
      <p class="muted small" style="margin:0 0 8px">Tick every currency the school accepts or pays in. ${esc(p.currency)} is the school's own currency and is always on.</p>
      <form id="currencies"><div class="currency-grid">${(p.currency_catalog || []).map((c) => `<label class="check"><input type="checkbox" name="cur" value="${esc(c.code)}" ${p.currencies.includes(c.code) ? "checked" : ""} ${c.code === p.currency ? "disabled" : ""}> <span><b>${esc(c.code)}</b> <span class="muted">${esc(c.label)}</span></span></label>`).join("")}</div>
        <div class="row" style="margin-top:10px"><button class="btn primary">Save currencies</button></div></form>
      <p class="muted small" style="margin:8px 0 0">Each fee, receipt, expense, salary and journal is recorded in the currency it happens in, never converted. Every currency gets its own cash on hand, bank and mobile money accounts, and the bursar records each day's rates against the US dollar under Exchange Rates. A currency can only be removed while nothing has been recorded in it. ${ratesText(p.rates, p.rate_currencies)}</p></div></div>
      <div class="card"><div class="card-head"><h3>Zimbabwe structure</h3></div><div class="card-body small">
        <p style="margin-top:0"><b>Primary:</b> ECD A, ECD B, Grades 1-7. Grade 7 sits ZIMSEC Grade 7 and completes primary school.</p>
        <p><b>Secondary:</b> Forms 1-4 (ZIMSEC O Level in Form 4), then Lower and Upper Six (Forms 5-6, A Level).</p>
        <p><b>Promotion:</b> once a year after Term 3. Form 4 students continue to Lower Six only when selected on their O Level results; everyone else in Form 4 completes school.</p>
        <p class="muted" style="margin-bottom:0">Each section has its own grading scale (primary, O Level, A Level), editable under Grading scales.</p></div></div></div>`;
    pane.querySelector("#logo-file").onchange = async (e) => {
      const file = e.target.files[0];
      if (!file) return;
      if (file.size > 1024 * 1024) return toast("The logo must be 1 MB or smaller", "error");
      const data = await new Promise((resolve, reject) => {
        const r = new FileReader();
        r.onload = () => resolve(r.result);
        r.onerror = () => reject(new Error("The file could not be read"));
        r.readAsDataURL(file);
      });
      try { await api.put("/school/logo", { data }); toast("Logo saved", "success"); await refreshMeta(); school(); }
      catch (err) { handleError(err); }
    };
    pane.querySelector("#logo-remove")?.addEventListener("click", async () => {
      if (!(await confirmDialog("Remove the school logo? Documents will print with the school name only.", { confirmText: "Remove", danger: true }))) return;
      try { await api.del("/school/logo"); toast("Logo removed", "success"); await refreshMeta(); school(); }
      catch (err) { handleError(err); }
    });
    pane.querySelector("#demo-login").onchange = async (e) => {
      const on = e.target.checked;
      if (on && !(await confirmDialog("Allow anyone with this school's address to sign in to the demo accounts (including the administrator) without a password? Only do this for a school with demo data.", { title: "Passwordless demo sign-in", confirmText: "Allow", danger: true }))) { e.target.checked = false; return; }
      try { await api.put("/school", { demo_login: on }); toast(on ? "Demo sign-in without passwords is on" : "Passwords are required again", "success"); }
      catch (err) { e.target.checked = !on; handleError(err); }
    };
    pane.querySelector("#currencies").onsubmit = async (e) => {
      e.preventDefault();
      const chosen = [p.currency, ...[...e.target.querySelectorAll('[name="cur"]:checked')].map((x) => x.value).filter((c) => c !== p.currency)];
      const added = chosen.filter((c) => !p.currencies.includes(c)), removed = p.currencies.filter((c) => !chosen.includes(c));
      if (!added.length && !removed.length) return toast("No change", "");
      const msg = [added.length ? `Add ${added.join(", ")}? Cash on hand, bank and mobile money accounts are created for ${added.length > 1 ? "each" : "it"}, every money form lets you pick ${added.length > 1 ? "them" : "it"}, and the bursar should record ${added.length > 1 ? "their" : "its"} daily rate against USD.` : "",
        removed.length ? `Remove ${removed.join(", ")}? This is only possible if nothing has been recorded in ${removed.length > 1 ? "them" : "it"}.` : ""].filter(Boolean).join(" ");
      if (!(await confirmDialog(msg, { title: "Change currencies", confirmText: "Save", danger: removed.length > 0 }))) return;
      try { const r = await api.put("/school", { currencies: chosen }); toast(`Currencies: ${r.currencies.join(", ")}`, "success"); await refreshMeta(); location.reload(); }
      catch (err) { handleError(err); school(); }
    };
    pane.querySelector("#type").onsubmit = async (e) => {
      e.preventDefault();
      const t = e.target.school_type.value;
      if (t === p.school_type) return toast("No change", "");
      if (!(await confirmDialog(`Change the school type to "${p.school_types.find((x) => x.value === t).label}"?`, { confirmText: "Change" }))) return;
      try { await api.put("/school", { school_type: t, create_classes: e.target.create_classes.checked }); toast("School type saved", "success"); await refreshMeta(); school(); }
      catch (err) { handleError(err); }
    };
  };

  const grading = async (section) => {
    const res = await api.get("/grade-bands", { section });
    let bands = res.items;
    const draw = () => {
      pane.innerHTML = `<div class="card" style="max-width:720px"><div class="card-head"><div><h3>Grade bands</h3><div class="sub">A score gets the highest band whose minimum it meets. One band must start at 0.</div></div>
        ${res.sections.length > 1 ? selectHtml("section", res.sections, res.section) : `<span class="muted small">${esc(res.sections[0]?.label || "")}</span>`}</div>
        <div class="table-wrap"><table class="table"><thead><tr><th>Grade</th><th>Minimum %</th><th>Points</th><th>Remark</th><th></th></tr></thead><tbody>
        ${bands.map((b, i) => `<tr><td><input class="input" data-i="${i}" data-k="letter" value="${esc(b.letter)}" maxlength="3" style="width:70px"></td>
          <td><input class="input" type="number" min="0" max="100" data-i="${i}" data-k="min_score" value="${b.min_score}" style="width:100px"></td>
          <td><input class="input" type="number" min="0" step="0.5" data-i="${i}" data-k="points" value="${b.points}" style="width:90px"></td>
          <td><input class="input" data-i="${i}" data-k="remark" value="${esc(b.remark || "")}"></td>
          <td class="actions"><button class="btn sm ghost danger" data-rm="${i}" aria-label="Remove">✕</button></td></tr>`).join("")}</tbody></table></div>
        <div class="pager"><button class="btn sm" id="add-band">${icon("plus")} Add band</button><button class="btn primary" id="save">Save scale</button></div></div>`;
      pane.querySelectorAll("[data-k]").forEach((i) => (i.oninput = () => (bands[i.dataset.i][i.dataset.k] = i.value)));
      pane.querySelectorAll("[data-rm]").forEach((b) => (b.onclick = () => { bands.splice(+b.dataset.rm, 1); draw(); }));
      pane.querySelector("#add-band").onclick = () => { bands.push({ letter: "", min_score: 0, points: 0, remark: "" }); draw(); };
      pane.querySelector("#save").onclick = async () => {
        try { await api.put("/grade-bands", { section: res.section, bands }); toast("Grading scale saved", "success"); grading(res.section); } catch (err) { handleError(err); }
      };
      pane.querySelector('[name="section"]')?.addEventListener("change", (e) => grading(e.target.value));
    };
    draw();
  };

  const promotion = async () => {
    const choice = { hold_back: new Set(), leave: new Set(), continue: new Set() };
    const body = () => ({ hold_back: [...choice.hold_back], leave: [...choice.leave], continue: [...choice.continue] });
    pane.innerHTML = `<div class="notice warn" style="margin-bottom:16px">Run this once at the end of the year, after Term 3 results are in. Students move to the next level (same stream where possible, respecting capacity). Grade 7 completes primary unless the school offers Form 1; Form 4 completes O Level unless selected for Lower Six; Form 6 completes A Level. Mark students who repeat or leave.</div>
      <div class="card"><div class="card-head"><div><h3>Promotion preview</h3><div class="sub" id="counts"></div></div><button class="btn danger solid" id="run">Run promotion</button></div><div id="t"></div></div>`;
    const LABEL = { promote: "moves up", graduate: "completes", leave: "leaves", repeat: "repeats", blocked: "blocked" };
    const draw = async () => {
      const { items } = await api.post("/promotion/preview", body());
      const counts = items.reduce((a, p) => ((a[p.action] = (a[p.action] || 0) + 1), a), {});
      pane.querySelector("#counts").textContent = `${counts.promote || 0} moving up · ${counts.graduate || 0} completing · ${counts.leave || 0} leaving · ${counts.repeat || 0} repeating · ${counts.blocked || 0} blocked`;
      const box = (kind, p, label) => `<label class="check small"><input type="checkbox" data-kind="${kind}" data-id="${p.student_id}" ${choice[kind].has(p.student_id) ? "checked" : ""}> ${label}</label>`;
      table(pane.querySelector("#t"), {
        rows: items, empty: "No active students.", sortKey: null,
        columns: [{ key: "student", label: "Student" }, { key: "from", label: "Current class" },
          { key: "action", label: "Outcome", render: (p) => `${badge(p.action === "promote" ? "active" : p.action === "blocked" ? "blocked" : p.action === "repeat" ? "pending" : "graduated", LABEL[p.action])}${p.reason ? ` <span class="muted small">${esc(p.reason)}</span>` : ""}` },
          { key: "to", label: "Next year", render: (p) => esc(p.to || "—") },
          { key: "choices", label: "", sort: false, render: (p) => [box("hold_back", p, "Repeat"), p.can_continue ? box("continue", p, "Continue to Form 5") : "", !p.exit ? box("leave", p, "Leaving") : ""].join(" ") }],
      });
      pane.querySelectorAll("[data-kind]").forEach((c) => (c.onchange = () => {
        const set = choice[c.dataset.kind], id = +c.dataset.id;
        c.checked ? set.add(id) : set.delete(id);
        draw().catch(handleError);
      }));
    };
    pane.querySelector("#run").onclick = async () => {
      const ok = await confirmDialog(`Run the end-of-year promotion now? ${choice.hold_back.size} repeating, ${choice.leave.size} leaving, ${choice.continue.size} continuing to Form 5. This cannot be undone automatically.`, { title: "Run promotion", danger: true, confirmText: "Promote", typed: "PROMOTE" });
      if (!ok) return;
      try {
        const r = await api.post("/promotion", { confirm: "PROMOTE", ...body() });
        toast(`Moved up ${r.promoted}, completed ${r.graduated}, left ${r.left}, repeating ${r.held_back}`, "success");
        promotion();
      } catch (err) { handleError(err); }
    };
    await draw();
  };

  const tabs = { school, calendar, grading: () => grading(), promotion };
  el.querySelectorAll("[data-tab]").forEach((b) => (b.onclick = () => {
    el.querySelectorAll("[data-tab]").forEach((x) => x.classList.toggle("active", x === b));
    tabs[b.dataset.tab]().catch(handleError);
  }));
  // Open the first tab this user may use.
  const first = el.querySelector("[data-tab]");
  if (first) { first.classList.add("active"); await tabs[first.dataset.tab](); }
}
