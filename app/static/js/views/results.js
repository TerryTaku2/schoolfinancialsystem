import { api } from "../api.js";
import { downloadCSV, esc, has, icon, pct, selectHtml, state, table, termOptions, today } from "../ui.js";
import { openReportCard } from "./docs.js";

export default async function (el, [classParam]) {
  const classes = (await api.get("/classes", has("results.view", "academics.view") ? {} : { mine: 1 })).items;
  let classId = classParam || classes[0]?.id || "";
  let termId = state.meta.current_term?.id || "";
  el.innerHTML = `
    <div class="page-head"><div><h1>Results</h1><p>Class broadsheet: weighted term percentage per subject, average and position (ties share a position)</p></div>
      <div class="page-actions">${selectHtml("cls", classes.map((c) => ({ value: c.id, label: c.name })), classId)}${selectHtml("term", termOptions(), termId)}
      <button class="btn" id="export">${icon("download")} Export CSV</button></div></div>
    <div class="card"><div class="card-head"><div><h3 id="title"></h3><div class="sub">Click a student to open their report card</div></div></div><div id="tbl"></div></div>`;
  if (!classes.length) { el.querySelector(".card").innerHTML = `<div class="empty">No classes assigned.</div>`; return; }

  let data = null, columns = [];
  const load = async () => {
    data = await api.get("/results", { class_id: classId, term_id: termId });
    el.querySelector("#title").textContent = `${data.class_name} · ${data.term}`;
    const pass = state.meta.pass_mark;
    const scoreCell = (s) => (s ? `<span style="color:${s.percent < pass ? "var(--bad)" : "inherit"}">${s.percent}</span> <span class="muted small">${esc(s.grade)}</span>` : `<span class="muted">—</span>`);
    columns = [
      { key: "position", label: "Pos", num: true, render: (r) => (r.position ? `<b>${r.position}</b>` : "—") },
      { key: "name", label: "Student", render: (r) => `<b>${esc(r.name)}</b><br><span class="muted small">${esc(r.admission_no)}</span>`, csv: (r) => r.name },
      ...data.subjects.map((s) => ({ key: `s${s.id}`, label: s.code, num: true, sort: (r) => r.scores[s.id]?.percent ?? null, render: (r) => scoreCell(r.scores[s.id]), csv: (r) => r.scores[s.id]?.percent ?? "" })),
      { key: "average", label: "Average", num: true, render: (r) => `<b>${pct(r.average)}</b>` },
      { key: "grade", label: "Grade" },
    ];
    const footer = `<td></td><td>Class mean</td>${data.subjects.map((s) => `<td class="num">${data.subject_means[s.id] ?? "—"}</td>`).join("")}<td class="num">${(() => { const a = data.rows.filter((r) => r.average !== null); return a.length ? (a.reduce((x, r) => x + r.average, 0) / a.length).toFixed(1) + "%" : "—"; })()}</td><td></td>`;
    table(el.querySelector("#tbl"), { rows: data.rows, columns, footer, sortKey: "position", onRowClick: (r) => openReportCard(r.student_id, termId), empty: "No students in this class." });
  };
  el.querySelector('[name="cls"]').onchange = (e) => { classId = e.target.value; load(); };
  el.querySelector('[name="term"]').onchange = (e) => { termId = e.target.value; load(); };
  el.querySelector("#export").onclick = () => data && downloadCSV(`results-${data.class_name}-${today()}`, columns, data.rows);
  await load();
}
