import { api } from "../api.js";
import { refreshMeta } from "../app.js";
import { confirmDialog, esc, formModal, handleError, has, icon, modal, state, toast } from "../ui.js";

export default async function (el) {
  const admin = has("academics.manage");
  const all = admin || has("academics.view");
  el.innerHTML = `
    <div class="page-head"><div><h1>Classes & Subjects</h1><p>${admin ? "Streams, capacity, class teachers and subject allocation" : "Classes you teach"}</p></div>
      <div class="page-actions">${admin ? `<button class="btn" id="subjects">Manage subjects</button><button class="btn primary" id="add">${icon("plus")} New class</button>` : ""}</div></div>
    <div class="grid g-3" id="list"></div>`;

  let staff = [], subjects = [], classFields = [];
  const load = async () => {
    const [cls, st, sub] = await Promise.all([
      api.get("/classes", all ? {} : { mine: 1 }),
      admin ? api.get("/staff") : Promise.resolve({ items: [] }),
      api.get("/subjects"),
    ]);
    staff = st.items.filter((s) => s.position === "Teacher" || s.role === "teacher");
    subjects = sub.items;
    el.querySelector("#list").innerHTML = cls.items.map((c) => {
      const fill = Math.round((c.enrolled / c.capacity) * 100);
      return `<div class="card">
        <div class="card-head"><div><h3>${esc(c.name)}</h3><div class="sub">${esc(c.level_label)} · Stream ${esc(c.stream)} · Room ${esc(c.room || "—")}</div></div>
          ${admin ? `<div class="row"><button class="btn sm" data-edit="${c.id}">Edit</button><button class="btn sm ghost danger" data-del="${c.id}" aria-label="Delete">✕</button></div>` : ""}</div>
        <div class="card-body">
          <div class="row" style="justify-content:space-between"><span class="muted">Class teacher</span><b>${esc(c.class_teacher || "Unassigned")}</b></div>
          <div class="row" style="justify-content:space-between;margin-top:6px"><span class="muted">Enrolment</span><span><b>${c.enrolled}</b> / ${c.capacity}</span></div>
          <div class="meter ${fill >= 100 ? "bad" : fill >= 90 ? "warn" : ""}"><span style="width:${Math.min(fill, 100)}%"></span></div>
          <h4 style="margin:16px 0 6px;font-size:13px" class="muted">SUBJECTS</h4>
          <ul class="list" style="margin:0 -18px">${c.subjects.map((s) => `<li style="padding:6px 18px"><span><b>${esc(s.code)}</b> ${esc(s.subject)}</span>
            <span class="row small">${s.teacher ? esc(s.teacher) : `<span class="badge warn">No teacher</span>`}${admin ? ` <button class="btn sm ghost" data-assign="${c.id}" data-subject="${s.subject_id}" data-teacher="${s.teacher_id || ""}">Change</button><button class="btn sm ghost danger" data-unassign="${s.id}" aria-label="Remove">✕</button>` : ""}</span></li>`).join("") || `<li class="muted" style="padding:6px 18px">No subjects assigned.</li>`}</ul>
          ${admin ? `<button class="btn sm" style="margin-top:10px" data-assign="${c.id}">${icon("plus")} Add subject</button>` : ""}
          <div class="row" style="margin-top:12px"><a class="btn sm" href="#/timetable/${c.id}">Timetable</a><a class="btn sm" href="#/results/${c.id}">Results</a>${c.class_teacher_id === state.user.staff_id || admin ? `<a class="btn sm" href="#/attendance/${c.id}">Attendance</a>` : ""}</div>
        </div></div>`;
    }).join("") || `<div class="card empty">No classes yet.</div>`;

    classFields = [
      { name: "grade_code", label: "Level", type: "select", required: true, options: (state.meta.profile?.levels || []).map((l) => ({ value: l.code, label: l.label })) },
      { name: "stream", label: "Stream", required: true, placeholder: "A, B, Blue…" },
      { name: "name", label: "Display name", placeholder: "Defaults to e.g. Grade 3A or Form 2B" },
      { name: "capacity", label: "Capacity", type: "number", min: 1, required: true, default: 35 },
      { name: "room", label: "Home room" },
      { name: "class_teacher_id", label: "Class teacher", type: "select", options: staff.map((s) => ({ value: s.id, label: `${s.name}${s.class_teacher_of?.length ? ` (${s.class_teacher_of.join(", ")})` : ""}` })), hint: "A teacher can be class teacher of one class only" },
    ];
    el.querySelectorAll("[data-edit]").forEach((b) => (b.onclick = async () => {
      const c = cls.items.find((x) => x.id === +b.dataset.edit);
      if (await formModal({ title: `Edit ${c.name}`, fields: classFields, values: c, onSubmit: (d) => api.put(`/classes/${c.id}`, d) })) { toast("Class saved", "success"); await refreshMeta(); load(); }
    }));
    el.querySelectorAll("[data-del]").forEach((b) => (b.onclick = async () => {
      if (!(await confirmDialog("Delete this class? Only empty classes without history can be deleted.", { danger: true, confirmText: "Delete" }))) return;
      try { await api.del(`/classes/${b.dataset.del}`); await refreshMeta(); load(); } catch (err) { handleError(err); }
    }));
    el.querySelectorAll("[data-unassign]").forEach((b) => (b.onclick = async () => {
      if (!(await confirmDialog("Remove this subject from the class? Its timetable slots are removed too.", { danger: true, confirmText: "Remove" }))) return;
      try { await api.del(`/class-subjects/${b.dataset.unassign}`); load(); } catch (err) { handleError(err); }
    }));
    el.querySelectorAll("[data-assign]").forEach((b) => (b.onclick = async () => {
      const r = await formModal({
        title: b.dataset.subject ? "Change subject teacher" : "Add subject to class", cols: 1,
        fields: [
          { name: "subject_id", label: "Subject", type: "select", required: true, options: subjects.map((s) => ({ value: s.id, label: `${s.code} · ${s.name}` })), disabled: !!b.dataset.subject },
          { name: "teacher_id", label: "Teacher", type: "select", options: staff.map((s) => ({ value: s.id, label: `${s.name} (${s.department || s.position})` })) },
        ],
        values: { subject_id: b.dataset.subject, teacher_id: b.dataset.teacher },
        transform: (d) => ({ ...d, subject_id: d.subject_id || b.dataset.subject }),
        onSubmit: (d) => api.post(`/classes/${b.dataset.assign}/subjects`, d),
      });
      if (r) { toast("Saved", "success"); load(); }
    }));
  };
  el.querySelector("#add")?.addEventListener("click", async () => {
    if (await formModal({ title: "New class", fields: classFields, onSubmit: (d) => api.post("/classes", d) })) { toast("Class created", "success"); await refreshMeta(); load(); }
  });
  el.querySelector("#subjects")?.addEventListener("click", () => subjectsModal(load));
  await load();
}

async function subjectsModal(onDone) {
  const draw = async (m) => {
    const { items } = await api.get("/subjects");
    m.body.innerHTML = `<form class="row" id="new-sub" style="margin-bottom:12px"><input class="input" name="code" placeholder="Code" style="width:110px" required><input class="input" name="name" placeholder="Subject name" style="flex:1" required><button class="btn primary">Add</button></form>
      <ul class="list card">${items.map((s) => `<li><span><b>${esc(s.code)}</b> ${esc(s.name)} <span class="muted small">· ${s.classes} class(es)</span></span><button class="btn sm danger" data-del="${s.id}" ${s.classes ? "disabled title='Assigned to classes'" : ""}>Delete</button></li>`).join("")}</ul>`;
    m.body.querySelector("#new-sub").onsubmit = async (e) => {
      e.preventDefault();
      try { await api.post("/subjects", { code: e.target.code.value, name: e.target.name.value }); draw(m); } catch (err) { handleError(err); }
    };
    m.body.querySelectorAll("[data-del]").forEach((b) => (b.onclick = async () => { try { await api.del(`/subjects/${b.dataset.del}`); draw(m); } catch (err) { handleError(err); } }));
  };
  const m = modal({ title: "Subjects", onClose: onDone });
  draw(m);
}
