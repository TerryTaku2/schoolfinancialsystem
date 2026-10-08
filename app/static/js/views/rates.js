// Daily USD/ZWG exchange rates, recorded by the bursar.
import { api } from "../api.js";
import { refreshMeta } from "../app.js";
import { esc, fmtDate, formModal, has, icon, isDual, table, toast, today } from "../ui.js";

export default async function (el) {
  el.innerHTML = `
    <div class="page-head"><div><h1>Exchange Rates</h1><p>The day's rate: how many ZWG (ZiG) make 1 US dollar</p></div>
      <div class="page-actions">${has("payments.manage") ? `<button class="btn primary" id="add">${icon("plus")} Record today's rate</button>` : ""}</div></div>
    <div class="grid g-3" id="top" style="margin-bottom:16px"></div>
    <div class="card"><div class="card-head"><div><h3>Rate history</h3><div class="sub">One rate per day; recording a day again corrects it (the change is kept in the audit log)</div></div></div><div id="tbl"></div></div>`;

  const load = async () => {
    const r = await api.get("/rates");
    const l = r.latest;
    const stale = l && l.date < today();
    el.querySelector("#top").innerHTML = `
      <div class="card stat"><div class="label">Latest rate</div><div class="value">${l ? `${Number(l.zwg_per_usd).toLocaleString(undefined, { maximumFractionDigits: 4 })}` : "—"}</div>
        <div class="foot">${l ? `ZWG per 1 USD · ${fmtDate(l.date)}${stale ? " · <b>not updated today</b>" : ""}` : "No rate recorded yet"}</div></div>
      <div class="card span-2"><div class="card-body small">
        <p style="margin-top:0"><b>What the rate is used for.</b> Every receipt, invoice, expense and salary is recorded in the currency it actually happened in and is never converted. The rate is only used to:</p>
        <ul style="margin:0 0 8px 18px;padding:0"><li>show combined totals in ${esc(r.base)} terms (financial statements, dashboard spending)</li>
        <li>work out PAYE when a salary is paid partly in USD and partly in ZWG (ZIMRA taxes the total and splits the tax by currency)</li></ul>
        <p class="muted" style="margin:0">Use the RBZ interbank rate for the day. Each figure uses the latest rate on or before its own date.</p>
        ${isDual() ? "" : `<div class="notice warn" style="margin-top:10px">Dual currency is off, so only ${esc(r.base)} can be recorded. An administrator can turn it on in Settings → School.</div>`}</div></div>`;
    table(el.querySelector("#tbl"), {
      rows: r.items, sortKey: null, empty: "No rates recorded yet.",
      columns: [{ key: "date", label: "Date", render: (x) => fmtDate(x.date) },
        { key: "zwg_per_usd", label: "ZWG per 1 USD", num: true, render: (x) => Number(x.zwg_per_usd).toLocaleString(undefined, { maximumFractionDigits: 4 }) },
        { key: "usd_per_zwg", label: "USD per 1 ZWG", num: true, sort: false, render: (x) => (1 / x.zwg_per_usd).toFixed(6) },
        { key: "source", label: "Source" }, { key: "entered_by", label: "Recorded by" }],
    });
  };
  el.querySelector("#add")?.addEventListener("click", async () => {
    const latest = (await api.get("/rates")).latest;
    const r = await formModal({
      title: "Record exchange rate", cols: 1,
      fields: [{ name: "date", label: "Date", type: "date", required: true, default: today(), max: today() },
        { name: "zwg_per_usd", label: "ZWG for 1 USD", type: "number", min: 0.0001, step: 0.0001, required: true, default: latest?.zwg_per_usd, hint: "e.g. 26.75" },
        { name: "source", label: "Source", default: "RBZ interbank" }],
      onSubmit: (d) => api.post("/rates", d),
    });
    if (r) { toast("Rate saved", "success"); await refreshMeta(); load(); }
  });
  await load();
}
