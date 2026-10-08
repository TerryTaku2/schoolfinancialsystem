import { api } from "../api.js";
import { classOptions, confirmDialog, esc, formModal, handleError, has, icon, selectHtml, state, toast } from "../ui.js";

export default async function (el, [classParam]) {
  const admin = has("academics.manage");
  const role = state.user.role;
  const mode = { by: classParam ? "class" : role === "teacher" ? "teacher" : "class", id: classParam || "" };
  let staff = [];
  if (admin) staff = (await api.get("/staff")).items;
  if (role === "teacher" && !classParam) mode.id = state.user.staff_id;
  if (!mode.id && mode.by === "class" && role !== "parent") mode.id = state.meta.classes[0]?.id || "";

  el.innerHTML = `
    <div class="page-head"><div><h1>Timetable</h1><p>Weekly lesson plan. Clashes (class, teacher or room double-booked) are rejected automatically.</p></div>
      <div class="page-actions" id="pick"></div></div>
    <div class="card"><div class="card-body flush table-wrap" id="grid"></div></div>`;

  const pick = el.querySelector("#pick");
  const drawPicker = (classChoices) => {
    const byOpts = [{ value: "class", label: "By class" }, { value: "teacher", label: "By teacher" }];
    const teacherOpts = (admin ? staff : [{ id: state.user.staff_id, name: "My lessons" }]).filter((s) => s.id).map((s) => ({ value: s.id, label: s.name }));
    pick.innerHTML = `${role !== "parent" ? selectHtml("by", byOpts, mode.by) : ""}
      ${mode.by === "class" ? selectHtml("cls", classChoices, mode.id) : selectHtml("tch", teacherOpts, mode.id)}
      ${admin && mode.by === "class" ? `<button class="btn primary" id="add">${icon("plus")} Add lesson</button>` : ""}`;
    pick.querySelector('[name="by"]')?.addEventListener("change", (e) => {
      mode.by = e.target.value;
      mode.id = mode.by === "teacher" ? (admin ? staff[0]?.id : state.user.staff_id) : state.meta.classes[0]?.id;
      load();
    });
    pick.querySelector('[name="cls"]')?.addEventListener("change", (e) => { mode.id = e.target.value; load(); });
    pick.querySelector('[name="tch"]')?.addEventListener("change", (e) => { mode.id = e.target.value; load(); });
    pick.querySelector("#add")?.addEventListener("click", addLesson);
  };

  const load = async () => {
    const res = await api.get("/timetable", mode.by === "class" ? { class_id: mode.id } : { teacher_id: mode.id });
    let slots = res.items;
    let classChoices = classOptions();
    if (role === "parent") {
      const mine = [...new Map(slots.map((s) => [s.class_id, s.class])).entries()].map(([value, label]) => ({ value, label }));
      classChoices = mine;
      if (!mode.id && mine.length) mode.id = mine[0].value;
      slots = slots.filter((s) => String(s.class_id) === String(mode.id));
    }
    drawPicker(classChoices);
    const times = [...new Set(slots.map((s) => `${s.start_time}-${s.end_time}`))].sort();
    const grid = el.querySelector("#grid");
    if (!times.length) { grid.innerHTML = `<div class="empty">No lessons scheduled${admin && mode.by === "class" ? ". Use “Add lesson” to build the timetable." : "."}</div>`; return; }
    const cell = (day, t) => slots.filter((s) => s.day === day && `${s.start_time}-${s.end_time}` === t).map((s) => `
      <div class="lesson"><strong>${esc(s.code)}</strong><span>${esc(mode.by === "class" ? s.teacher || "No teacher" : s.class)}${s.room ? ` · ${esc(s.room)}` : ""}</span>
        ${admin ? `<button class="x" data-del="${s.id}" aria-label="Remove lesson">×</button>` : ""}</div>`).join("");
    grid.innerHTML = `<div class="tt"><div class="tt-h"></div>${res.days.map((d) => `<div class="tt-h">${d}</div>`).join("")}
      ${times.map((t) => `<div class="tt-t">${t.replace("-", "<br>")}</div>${res.days.map((_, d) => `<div>${cell(d, t)}</div>`).join("")}`).join("")}</div>`;
    grid.querySelectorAll("[data-del]").forEach((b) => (b.onclick = async () => {
      if (!(await confirmDialog("Remove this lesson from the timetable?", { confirmText: "Remove", danger: true }))) return;
      try { await api.del(`/timetable/${b.dataset.del}`); load(); } catch (err) { handleError(err); }
    }));
  };

  async function addLesson() {
    const cls = (await api.get("/classes")).items.find((c) => String(c.id) === String(mode.id));
    if (!cls?.subjects.length) return toast("Assign subjects to this class first", "error");
    const r = await formModal({
      title: `Add lesson · ${cls.name}`,
      fields: [
        { name: "class_subject_id", label: "Subject", type: "select", required: true, options: cls.subjects.map((s) => ({ value: s.id, label: `${s.subject} — ${s.teacher || "no teacher"}` })), full: true },
        { name: "day", label: "Day", type: "select", required: true, options: ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"].map((d, i) => ({ value: i, label: d })) },
        { name: "room", label: "Room", placeholder: cls.room || "Home room" },
        { name: "start_time", label: "Start", type: "time", required: true, default: "08:00" },
        { name: "end_time", label: "End", type: "time", required: true, default: "08:40" },
      ],
      onSubmit: (d) => api.post("/timetable", d),
    });
    if (r) { toast("Lesson added", "success"); load(); }
  }
  await load();
}
