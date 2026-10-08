import { api } from "../api.js";
import { esc, fmtDate, handleError, has, pct, selectHtml, state, table, toast, today } from "../ui.js";

const STATUSES = ["present", "late", "absent", "excused"];

export default async function (el, [classParam]) {
  const classes = (await api.get("/classes", has("attendance.manage") ? {} : { mine: 1 })).items;
  const mine = has("attendance.manage") ? classes : classes.filter((c) => c.class_teacher_id === state.user.staff_id);
  const choices = (mine.length ? mine : classes).map((c) => ({ value: c.id, label: c.name }));
  let classId = classParam || choices[0]?.value || "";
  let date = today();

  el.innerHTML = `
    <div class="page-head"><div><h1>Attendance</h1><p>Daily register, taken by the class teacher. Excused absences don't count against a student's rate.</p></div>
      <div class="page-actions">${selectHtml("cls", choices, classId)}<input class="input" type="date" name="date" value="${date}" max="${today()}" style="width:auto"></div></div>
    <div class="tabs"><button data-tab="register" class="active">Daily register</button><button data-tab="summary">Term summary</button></div>
    <div id="pane"></div>`;
  if (!choices.length) { el.querySelector("#pane").innerHTML = `<div class="card empty">You have no classes assigned.</div>`; return; }

  let tab = "register";
  const pane = el.querySelector("#pane");

  const register = async () => {
    const r = await api.get("/attendance", { class_id: classId, date });
    const marks = Object.fromEntries(r.roster.map((s) => [s.student_id, { status: s.status || "present", remark: s.remark || "" }]));
    const counts = () => STATUSES.map((st) => `${st[0].toUpperCase() + st.slice(1)}: <b>${Object.values(marks).filter((m) => m.status === st).length}</b>`).join(" · ");
    pane.innerHTML = `<div class="card">
      <div class="card-head"><div><h3>${esc(r.class_name)} · ${fmtDate(r.date, { weekday: "long", day: "numeric", month: "long" })}</h3>
        <div class="sub">${r.recorded ? "Already recorded — changes will update the register." : "Not yet recorded. Everyone starts as present; mark the exceptions."}</div></div>
        ${r.can_edit ? `<div class="row"><button class="btn sm" id="all-present">All present</button><button class="btn primary" id="save">Save register</button></div>` : `<span class="badge plain">View only</span>`}</div>
      <div class="toolbar small muted" id="counts">${counts()}</div>
      <div class="table-wrap"><table class="table"><thead><tr><th>#</th><th>Student</th><th>Status</th><th>Remark</th></tr></thead><tbody>
      ${r.roster.map((s, i) => `<tr><td class="muted">${i + 1}</td><td><b>${esc(s.name)}</b><br><span class="muted small">${esc(s.admission_no)}</span></td>
        <td><div class="seg" role="group" aria-label="Status for ${esc(s.name)}">${STATUSES.map((st) => `<button type="button" data-sid="${s.student_id}" data-v="${st}" aria-pressed="${marks[s.student_id].status === st}" ${r.can_edit ? "" : "disabled"}>${st[0].toUpperCase() + st.slice(1)}</button>`).join("")}</div></td>
        <td><input class="input" data-remark="${s.student_id}" value="${esc(marks[s.student_id].remark)}" placeholder="Optional" ${r.can_edit ? "" : "disabled"} maxlength="120"></td></tr>`).join("") || `<tr><td colspan="4" class="empty">No active students in this class.</td></tr>`}
      </tbody></table></div></div>`;
    pane.querySelectorAll(".seg button").forEach((b) => (b.onclick = () => {
      marks[b.dataset.sid].status = b.dataset.v;
      b.parentElement.querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", x === b));
      pane.querySelector("#counts").innerHTML = counts();
    }));
    pane.querySelector("#all-present")?.addEventListener("click", () => {
      Object.values(marks).forEach((m) => (m.status = "present"));
      pane.querySelectorAll(".seg button").forEach((x) => x.setAttribute("aria-pressed", x.dataset.v === "present"));
      pane.querySelector("#counts").innerHTML = counts();
    });
    pane.querySelector("#save")?.addEventListener("click", async (e) => {
      e.target.disabled = true;
      const records = Object.entries(marks).map(([sid, m]) => ({ student_id: +sid, status: m.status, remark: pane.querySelector(`[data-remark="${sid}"]`).value }));
      try { const res = await api.post("/attendance", { class_id: classId, date, records }); toast(`Register saved for ${res.saved} students`, "success"); register(); }
      catch (err) { handleError(err); e.target.disabled = false; }
    });
  };

  const summary = async () => {
    const r = await api.get("/attendance/summary", { class_id: classId });
    pane.innerHTML = `<div class="card"><div class="card-head"><div><h3>Term to date</h3><div class="sub">${fmtDate(r.start)} – ${fmtDate(r.end)} · threshold ${state.meta.attendance_threshold}%</div></div></div><div id="t"></div></div>`;
    table(pane.querySelector("#t"), {
      rows: r.items, sortKey: "rate",
      columns: [{ key: "name", label: "Student", render: (x) => `<a href="#/student/${x.student_id}">${esc(x.name)}</a>` },
        { key: "days", label: "Days", num: true }, { key: "present", label: "Present", num: true }, { key: "late", label: "Late", num: true },
        { key: "absent", label: "Absent", num: true }, { key: "excused", label: "Excused", num: true },
        { key: "rate", label: "Rate", num: true, render: (x) => `<b style="color:${x.rate !== null && x.rate < state.meta.attendance_threshold ? "var(--bad)" : "inherit"}">${pct(x.rate)}</b>` }],
    });
  };

  const show = () => (tab === "register" ? register() : summary()).catch(handleError);
  el.querySelector('[name="cls"]').onchange = (e) => { classId = e.target.value; show(); };
  el.querySelector('[name="date"]').onchange = (e) => { date = e.target.value; tab = "register"; el.querySelectorAll("[data-tab]").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab)); show(); };
  el.querySelectorAll("[data-tab]").forEach((b) => (b.onclick = () => { tab = b.dataset.tab; el.querySelectorAll("[data-tab]").forEach((x) => x.classList.toggle("active", x === b)); show(); }));
  await show();
}
