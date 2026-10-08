import { api } from "../api.js";
import { esc, fmtDateTime, pager, selectHtml, table } from "../ui.js";

const ENTITIES = ["payment", "invoice", "expense", "student", "staff", "user", "attendance", "marks", "exam", "fee", "class", "term", "scholarship", "timetable", "announcement"];

export default async function (el) {
  const f = { entity: "", page: 1 };
  el.innerHTML = `
    <div class="page-head"><div><h1>Audit Log</h1><p>Every change is recorded with who made it and when. Entries cannot be edited.</p></div></div>
    <div class="card"><div class="toolbar">${selectHtml("entity", ENTITIES.map((e) => ({ value: e, label: e[0].toUpperCase() + e.slice(1) })), "", { empty: "All records" })}</div><div id="tbl"></div><div id="pg"></div></div>`;
  const load = async () => {
    const r = await api.get("/audit", { ...f, per_page: 50 });
    table(el.querySelector("#tbl"), {
      rows: r.items, empty: "No entries.",
      columns: [{ key: "timestamp", label: "When (UTC)", render: (x) => fmtDateTime(x.timestamp + "Z") }, { key: "user", label: "User" },
        { key: "action", label: "Action", render: (x) => `<span class="badge plain">${esc(x.action)}</span>` },
        { key: "entity", label: "Record", render: (x) => `${esc(x.entity)}${x.entity_id ? ` #${x.entity_id}` : ""}` },
        { key: "details", label: "Details" }],
    });
    pager(el.querySelector("#pg"), { page: r.page, perPage: r.per_page, total: r.total, onPage: (p) => { f.page = p; load(); } });
  };
  el.querySelector('[name="entity"]').onchange = (e) => { f.entity = e.target.value; f.page = 1; load(); };
  await load();
}
