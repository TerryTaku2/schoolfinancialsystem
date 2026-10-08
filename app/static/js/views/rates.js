// Daily exchange rates against the US dollar (one per school currency), recorded by the bursar.
import { api } from "../api.js";
import { refreshMeta } from "../app.js";
import { currencyName, esc, fmtDate, formModal, has, icon, selectHtml, table, toast, today } from "../ui.js";

const num = (v, d = 4) => Number(v).toLocaleString(undefined, { maximumFractionDigits: d });

export default async function (el) {
  let filter = "";
  el.innerHTML = `
    <div class="page-head"><div><h1>Exchange Rates</h1><p>Each day's rates against the US dollar: how many units of each currency make 1 USD</p></div>
      <div class="page-actions">${has("payments.manage") ? `<button class="btn primary" id="add">${icon("plus")} Record today's rates</button>` : ""}</div></div>
    <div class="grid g-3" id="top" style="margin-bottom:16px"></div>
    <div class="card"><div class="card-head"><div><h3>Rate history</h3><div class="sub">One rate per currency per day; recording a day again corrects it (the change is kept in the audit log)</div></div>
      <div id="filter"></div></div><div id="tbl"></div></div>`;

  let data;
  const load = async () => {
    data = await api.get("/rates" + (filter ? `?currency=${encodeURIComponent(filter)}` : ""));
    const need = data.rate_currencies;
    const cards = need.map((c) => {
      const l = data.latest[c];
      const stale = l && l.date < today();
      return `<div class="card stat"><div class="label">${esc(c)} · ${esc(currencyName(c))}</div><div class="value">${l ? num(l.per_usd) : "—"}</div>
        <div class="foot">${l ? `${esc(c)} per 1 USD · ${fmtDate(l.date)}${stale ? " · <b>not updated today</b>" : ""}` : "No rate recorded yet"}</div></div>`;
    }).join("");
    el.querySelector("#top").innerHTML = `${cards}
      <div class="card ${need.length % 3 === 2 ? "" : "span-2"}"><div class="card-body small">
        <p style="margin-top:0"><b>What the rates are used for.</b> Every receipt, invoice, expense and salary is recorded in the currency it actually happened in and is never converted. Rates are only used to:</p>
        <ul style="margin:0 0 8px 18px;padding:0"><li>show combined totals in ${esc(data.base)} terms (financial statements, dashboard spending)</li>
        <li>work out PAYE when a salary is paid in more than one currency (ZIMRA taxes the total and splits the tax by currency)</li></ul>
        <p class="muted" style="margin:0">Use the RBZ interbank rate for the day. Any two currencies convert through the US dollar, e.g. ZAR to ZWG. Each figure uses the latest rate on or before its own date.</p>
        ${need.length ? "" : `<div class="notice warn" style="margin-top:10px">This school only works in ${esc(data.base)}, so no rates are needed. An administrator can add currencies in Settings → School.</div>`}</div></div>`;
    el.querySelector("#filter").innerHTML = need.length > 1 ? selectHtml("cur", need.map((c) => ({ value: c, label: c })), filter, { empty: "All currencies" }) : "";
    el.querySelector('#filter [name="cur"]')?.addEventListener("change", (e) => { filter = e.target.value; loading = load(); });
    table(el.querySelector("#tbl"), {
      rows: data.items, sortKey: null, empty: "No rates recorded yet.",
      columns: [{ key: "date", label: "Date", render: (x) => fmtDate(x.date) },
        { key: "currency", label: "Currency" },
        { key: "per_usd", label: "Per 1 USD", num: true, render: (x) => num(x.per_usd) },
        { key: "usd_per", label: "USD per 1 unit", num: true, sort: false, render: (x) => (1 / x.per_usd).toFixed(6) },
        { key: "source", label: "Source" }, { key: "entered_by", label: "Recorded by" }],
    });
  };

  let loading = load();
  el.querySelector("#add")?.addEventListener("click", async () => {
    await loading;  // the button can be clicked before the rates have loaded
    const need = data.rate_currencies;
    if (!need.length) return toast("This school only works in one currency, so no rates are needed", "");
    const r = await formModal({
      title: "Record exchange rates", cols: 1,
      fields: [{ name: "date", label: "Date", type: "date", required: true, default: today(), max: today() },
        ...need.map((c) => ({ name: `rate_${c}`, label: `${c} for 1 USD`, type: "number", min: 0.000001, step: "any", required: need.length === 1,
          default: data.latest[c]?.per_usd, hint: `${currencyName(c)}${data.latest[c] ? ` · last: ${num(data.latest[c].per_usd)} on ${fmtDate(data.latest[c].date)}` : ""}` })),
        { name: "source", label: "Source", default: "RBZ interbank" }],
      onSubmit: (d) => api.post("/rates", { date: d.date, source: d.source,
        rates: Object.fromEntries(need.map((c) => [c, d[`rate_${c}`]]).filter(([, v]) => v !== "" && v !== null && v !== undefined)) }),
    });
    if (r) { toast("Rates saved", "success"); await refreshMeta(); loading = load(); }
  });
  await loading;
}
