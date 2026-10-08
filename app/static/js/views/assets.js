// Fixed asset register: acquisitions, depreciation runs, disposals and ledger reconciliation.
import { api } from "../api.js";
import { badge, baseCurrency, confirmDialog, currencies, currencyField, debounce, downloadCSV, esc, fmtDate, formModal, handleError, has, icon, isDual, modal, money, moneyList, selectHtml, table, toast, today } from "../ui.js";

const METHOD = { straight_line: "Straight line", reducing_balance: "Reducing balance", none: "Not depreciated" };
const FUNDING = { purchase: "Purchased", donation: "Donated", existing: "Existing (already in the books)" };
const monthLabel = (p) => (p ? fmtDate(p + "-01", { month: "short", year: "numeric" }) : "—");

// Show only the form fields that apply to the current choices.
function wireConditional(rules) {
  const form = document.querySelector(".modal-back:last-of-type form");
  if (!form) return;
  const field = (n) => form.querySelector(`[name="${n}"]`)?.closest(".field");
  const apply = () => {
    const v = (n) => form.querySelector(`[name="${n}"]`)?.value;
    for (const [name, when] of Object.entries(rules)) {
      const f = field(name);
      if (f) f.style.display = when(v) ? "" : "none";
    }
  };
  form.addEventListener("change", apply);
  apply();
  return form;
}

export default async function (el, [tabParam]) {
  const tabs = [["register", "Register"], ["depreciation", "Depreciation"], ["summary", "Summary & reconciliation"]];
  let tab = tabs.some(([k]) => k === tabParam) ? tabParam : "register";
  const [settings, staff] = await Promise.all([api.get("/accounting/settings"), api.get("/staff")]);
  const cashAccounts = settings.cash_accounts.filter((a) => a.active);
  // Cash accounts that can pay for / receive money for something in currency `cur`.
  const accountsIn = (cur) => cashAccounts.filter((a) => !a.currency || a.currency === cur);
  const sumBy = (rows, k) => Object.fromEntries(currencies().map((c) => [c, rows.filter((a) => a.currency === c).reduce((s, a) => s + a[k], 0)]).filter(([c, v]) => v || c === baseCurrency()));
  const staffOptions = staff.items.map((s) => ({ value: s.id, label: `${s.name} (${s.position})` }));
  let meta = null;

  el.innerHTML = `
    <div class="page-head"><div><h1>Asset Register</h1><p>Property, plant and equipment with monthly depreciation, posted to the general ledger</p></div>
      <div class="page-actions">${has("assets.manage") ? `<button class="btn primary" id="add">${icon("plus")} Register asset</button>` : ""}</div></div>
    <div class="tabs">${tabs.map(([k, l]) => `<button data-tab="${k}" class="${k === tab ? "active" : ""}">${l}</button>`).join("")}</div>
    <div id="pane"></div>`;
  const pane = el.querySelector("#pane");

  // ------------------------------------------------------------ register
  async function register() {
    const f = { status: "active", category: "", q: "" };
    pane.innerHTML = `<div class="grid g-4" id="stats" style="margin-bottom:16px"></div><div id="recon"></div>
      <div class="card"><div class="toolbar">
        <input class="input search" type="search" id="q" placeholder="Asset no., name, serial or location" aria-label="Search">
        ${selectHtml("status", [{ value: "active", label: "In use" }, { value: "disposed", label: "Disposed" }, { value: "written_off", label: "Written off" }, { value: "all", label: "All" }], f.status)}
        <span id="cat"></span><span style="flex:1"></span><button class="btn sm" id="x">${icon("download")} CSV</button></div><div id="tbl"></div></div>`;
    const columns = [
      { key: "asset_no", label: "Asset no." },
      { key: "name", label: "Asset", render: (a) => `<b>${esc(a.name)}</b>${a.serial_no ? `<br><span class="muted small">S/N ${esc(a.serial_no)}</span>` : ""}` },
      { key: "category", label: "Category" },
      { key: "location", label: "Location / custodian", render: (a) => `${esc(a.location || "—")}${a.custodian ? `<br><span class="muted small">${esc(a.custodian)}</span>` : ""}`, csv: (a) => [a.location, a.custodian].filter(Boolean).join(" / ") },
      { key: "acquisition_date", label: "Acquired", render: (a) => fmtDate(a.acquisition_date) },
      { key: "cost", label: "Cost", num: true, render: (a) => money(a.cost, a.currency) },
      { key: "accumulated", label: "Acc. depreciation", num: true, render: (a) => money(a.accumulated, a.currency) },
      { key: "nbv", label: "Book value", num: true, render: (a) => `<b>${money(a.nbv, a.currency)}</b>` },
      ...(isDual() ? [{ key: "currency", label: "Currency" }] : []),
      { key: "status", label: "Status", render: (a) => badge(a.status) },
    ];
    let rows = [];
    const load = async () => {
      const r = await api.get("/assets", f);
      meta = r;
      rows = r.items;
      if (!pane.querySelector("#cat").innerHTML) {
        pane.querySelector("#cat").innerHTML = selectHtml("category", r.categories.map((c) => ({ value: c, label: c })), "", { empty: "All categories" });
        pane.querySelector('[name="category"]').onchange = (e) => { f.category = e.target.value; load(); };
      }
      pane.querySelector("#stats").innerHTML = [["Assets", rows.length, false], ["Cost", sumBy(rows, "cost"), true], ["Accumulated depreciation", sumBy(rows, "accumulated"), true], ["Net book value", sumBy(rows, "nbv"), true]]
        .map(([l, v, m]) => `<div class="card stat"><div class="label">${l}</div><div class="value" style="${m && Object.keys(v).length > 1 ? "font-size:18px" : ""}">${m ? moneyList(v) : v}</div></div>`).join("");
      const bad = (r.reconciliation.by_currency || [r.reconciliation]).filter((x) => !x.reconciled);
      pane.querySelector("#recon").innerHTML = !bad.length ? "" : `<div class="notice warn" style="margin-bottom:16px">The register does not agree with the general ledger: ${bad.map((x) => `${esc(x.currency || "")} cost differs by <b>${money(x.cost_difference, x.currency)}</b> and accumulated depreciation by <b>${money(x.accumulated_difference, x.currency)}</b>`).join("; ")}. See <a href="#/assets/summary">Summary &amp; reconciliation</a>.</div>`;
      table(pane.querySelector("#tbl"), { rows, columns, onRowClick: (a) => openAsset(a.id), empty: "No assets match these filters." });
    };
    pane.querySelector("#q").oninput = debounce((e) => { f.q = e.target.value; load(); });
    pane.querySelector('[name="status"]').onchange = (e) => { f.status = e.target.value; load(); };
    pane.querySelector("#x").onclick = () => downloadCSV(`asset-register-${today()}`, columns, rows);
    await load();
  }

  async function openAsset(id) {
    try {
      const a = await api.get(`/assets/${id}`);
      const life = a.method === "straight_line" ? `${a.useful_life_months} months` : a.method === "reducing_balance" ? `${a.rate_pct}% a year` : "—";
      const actions = [{ label: "Close" }];
      if (has("assets.manage")) actions.unshift({ label: "Edit details", onClick: () => editAsset(a) });
      if (a.status === "active" && has("assets.manage")) actions.unshift({ label: "Dispose / write off", cls: "danger", onClick: () => disposeAsset(a) });
      if (has("assets.approve") && a.status === "active" && !a.history.length) actions.unshift({ label: "Cancel registration", cls: "danger", onClick: async () => {
        const reason = await confirmDialog(`Cancel the registration of ${a.asset_no}? Any acquisition posting is reversed today.`, { title: "Cancel registration", confirmText: "Cancel registration", danger: true, reason: true });
        if (!reason) return true;
        await api.post(`/assets/${a.id}/cancel`, { reason });
        toast("Registration cancelled", "success");
        show();
      } });
      const m = modal({
        title: `${a.asset_no} · ${a.name}`, wide: true, actions,
        body: `<div class="grid g-2">
          <div class="table-wrap card"><table class="table"><tbody>
            <tr><th>Category</th><td>${esc(a.category)}</td></tr><tr><th>Serial no.</th><td>${esc(a.serial_no || "—")}</td></tr>
            <tr><th>Location</th><td>${esc(a.location || "—")}</td></tr><tr><th>Custodian</th><td>${esc(a.custodian || "—")}</td></tr>
            <tr><th>Supplier</th><td>${esc(a.supplier || "—")}</td></tr><tr><th>Condition</th><td>${esc(a.condition || "—")}</td></tr>
            <tr><th>Status</th><td>${badge(a.status)}${a.disposal_date ? ` ${fmtDate(a.disposal_date)}` : ""}${a.disposal_note ? `<br><span class="muted small">${esc(a.disposal_note)}</span>` : ""}</td></tr>
          </tbody></table></div>
          <div class="table-wrap card"><table class="table"><tbody>
            <tr><th>Acquired</th><td>${fmtDate(a.acquisition_date)} · ${esc(FUNDING[a.funding])}</td></tr>
            <tr><th>Cost</th><td class="num">${money(a.cost, a.currency)}</td></tr><tr><th>Residual value</th><td class="num">${money(a.residual, a.currency)}</td></tr>
            <tr><th>Method</th><td>${esc(METHOD[a.method])} · ${esc(life)}</td></tr>
            <tr><th>Depreciated</th><td>from ${monthLabel(a.depreciation_start)} to ${monthLabel(a.depreciated_to)}</td></tr>
            <tr><th>Accumulated depreciation</th><td class="num">${money(a.accumulated, a.currency)}${a.opening_accum ? `<br><span class="muted small">incl. ${money(a.opening_accum, a.currency)} brought forward</span>` : ""}</td></tr>
            <tr><th>Net book value</th><td class="num"><b>${money(a.nbv, a.currency)}</b></td></tr>
            ${a.disposal_proceeds !== null ? `<tr><th>Sale proceeds</th><td class="num">${money(a.disposal_proceeds, a.currency)}</td></tr>` : ""}
          </tbody></table></div></div>
          ${a.notes ? `<p class="muted">${esc(a.notes)}</p>` : ""}
          <div class="grid g-2" style="margin-top:16px">
            <div class="card"><div class="card-head"><h3>Depreciation charged</h3></div><div id="hist"></div></div>
            <div class="card"><div class="card-head"><h3>Next 12 months</h3></div><div id="proj"></div></div></div>`,
      });
      table(m.el.querySelector("#hist"), { rows: a.history, sortKey: null, empty: "Nothing charged yet.", columns: [
        { key: "from", label: "Months", sort: false, render: (h) => `${monthLabel(h.from)}${h.to !== h.from ? ` – ${monthLabel(h.to)}` : ""}` },
        { key: "source", label: "Source", sort: false, render: (h) => `${esc(h.source)}${h.reversed ? " " + badge("reversed") : ""}` },
        { key: "amount", label: "Amount", num: true, sort: false, render: (h) => money(h.amount, a.currency) }] });
      table(m.el.querySelector("#proj"), { rows: a.projection, sortKey: null, empty: a.status === "active" ? "Fully depreciated or not depreciated." : "Disposed.", columns: [
        { key: "period", label: "Month", sort: false, render: (p) => monthLabel(p.period) },
        { key: "charge", label: "Charge", num: true, sort: false, render: (p) => money(p.charge, a.currency) },
        { key: "nbv", label: "Book value after", num: true, sort: false, render: (p) => money(p.nbv, a.currency) }] });
    } catch (err) { handleError(err); }
  }

  const descriptive = (cats) => [
    { name: "name", label: "Description", required: true, full: true, placeholder: "e.g. Toyota Hiace school minibus" },
    ...(cats ? [{ name: "category", label: "Category", type: "select", required: true, options: cats, default: "Furniture & Fittings" }] : []),
    { name: "serial_no", label: "Serial / registration no." },
    { name: "location", label: "Location", placeholder: "e.g. Science lab" },
    { name: "custodian_id", label: "Custodian", type: "select", options: staffOptions, empty: "Nobody assigned" },
    { name: "supplier", label: "Supplier / donor" },
    { name: "condition", label: "Condition", type: "select", required: true, options: meta?.conditions || ["New", "Good", "Fair", "Poor", "Unserviceable"], default: "New" },
  ];

  async function addAsset() {
    if (!meta) meta = await api.get("/assets");
    const fields = [
      ...descriptive(meta.categories),
      { type: "heading", label: "Acquisition", name: "_h1" },
      { name: "acquisition_date", label: "Date acquired", type: "date", required: true, default: today(), max: today() },
      { name: "cost", label: "Cost", type: "number", min: 0.01, step: 0.01, required: true },
      ...currencyField({ hint: "The asset is kept in this currency" }),
      { name: "funding", label: "How it was acquired", type: "select", required: true, options: Object.entries(FUNDING).map(([value, label]) => ({ value, label })), default: "purchase" },
      { name: "account_id", label: "Paid from", type: "select", options: accountsIn(baseCurrency()).map((a) => ({ value: a.id, label: a.name })), default: accountsIn(baseCurrency()).find((a) => a.code === "1010")?.id, hint: "Posts Dr PPE / Cr this account; refused if funds are short" },
      { type: "heading", label: "Depreciation", name: "_h2" },
      { name: "method", label: "Method", type: "select", required: true, options: [{ value: "straight_line", label: METHOD.straight_line }, { value: "reducing_balance", label: METHOD.reducing_balance }, { value: "none", label: METHOD.none }], default: "straight_line" },
      { name: "useful_life_months", label: "Useful life (months)", type: "number", min: 1, step: 1, default: 60, hint: "For an existing asset: the remaining life" },
      { name: "rate_pct", label: "Rate (% per year)", type: "number", min: 0.1, max: 100, step: 0.1, default: 25 },
      { name: "residual", label: "Residual value", type: "number", min: 0, step: 0.01, default: 0 },
      { name: "opening_accum", label: "Depreciation brought forward", type: "number", min: 0, step: 0.01, default: 0 },
      { name: "depreciation_start", label: "Depreciate from (month)", type: "month", hint: "Usually the month the register was started" },
      { name: "notes", label: "Notes", type: "textarea", full: true },
    ];
    const done = formModal({
      title: "Register asset", fields, wide: true,
      intro: `<p class="muted small" style="margin-top:0">Depreciation is charged for whole months from the month of acquisition. Cost and depreciation settings can't be changed after posting; cancel the registration and re-enter instead.</p>`,
      transform: (d) => ({ ...d, custodian_id: d.custodian_id || null }),
      onSubmit: (d) => api.post("/assets", d),
    });
    const form = wireConditional({
      account_id: (v) => v("funding") === "purchase",
      opening_accum: (v) => v("funding") === "existing",
      depreciation_start: (v) => v("funding") === "existing",
      useful_life_months: (v) => v("method") === "straight_line" && v("category") !== "Land",
      rate_pct: (v) => v("method") === "reducing_balance" && v("category") !== "Land",
      residual: (v) => v("method") !== "none" && v("category") !== "Land",
      method: (v) => v("category") !== "Land",
    });
    // Only accounts in the asset's currency can pay for it.
    form?.querySelector('[name="currency"]')?.addEventListener("change", (e) => {
      const sel = form.querySelector('[name="account_id"]');
      sel.innerHTML = accountsIn(e.target.value).map((a) => `<option value="${a.id}">${esc(a.name)}</option>`).join("");
    });
    const setLife = () => {
      const life = meta.default_life[form.querySelector('[name="category"]').value];
      if (life) form.querySelector('[name="useful_life_months"]').value = life;
    };
    form?.querySelector('[name="category"]')?.addEventListener("change", setLife);
    if (form) setLife();
    const a = await done;
    if (a) { toast(`${a.asset_no} registered`, "success"); show(); }
  }

  async function editAsset(a) {
    const r = await formModal({ title: `Edit ${a.asset_no}`, fields: [...descriptive(null), { name: "notes", label: "Notes", type: "textarea", full: true }], values: a, wide: true,
      transform: (d) => ({ ...d, custodian_id: d.custodian_id || null }), onSubmit: (d) => api.put(`/assets/${a.id}`, d) });
    if (r) { toast("Saved", "success"); show(); }
  }

  async function disposeAsset(a) {
    const done = formModal({
      title: `Dispose of ${a.asset_no}`, cols: 1,
      intro: `<p class="muted" style="margin-top:0">${esc(a.name)} · book value ${money(a.nbv, a.currency)}. Depreciation up to the end of the previous month is charged first; the difference between proceeds and book value is posted as a gain or loss.</p>`,
      fields: [
        { name: "write_off", label: "Write off (scrapped, lost or stolen: no proceeds)", type: "checkbox" },
        { name: "date", label: "Disposal date", type: "date", required: true, default: today(), max: today(), min: a.acquisition_date },
        { name: "proceeds", label: "Sale proceeds", type: "number", min: 0, step: 0.01, default: 0 },
        { name: "account_id", label: "Proceeds received into", type: "select", options: accountsIn(a.currency).map((x) => ({ value: x.id, label: x.name })), default: accountsIn(a.currency)[0]?.id },
        { name: "note", label: "Details", required: true, placeholder: "Buyer, reason, board minute reference…" },
      ],
      onSubmit: (d) => api.post(`/assets/${a.id}/dispose`, d),
    });
    wireConditional({ proceeds: (v) => !document.querySelector('.modal-back:last-of-type [name="write_off"]')?.checked,
      account_id: (v) => !document.querySelector('.modal-back:last-of-type [name="write_off"]')?.checked && +v("proceeds") > 0 });
    if (await done) { toast("Disposal posted", "success"); show(); }
  }

  // ------------------------------------------------------------ depreciation
  async function depreciation() {
    const r = await api.get("/assets/depreciation");
    const latest = r.items.find((x) => !x.reversed);
    pane.innerHTML = `<div class="grid g-2">
      <div class="card"><div class="card-head"><div><h3>Run depreciation</h3><div class="sub">${r.last_run ? `Last run: ${monthLabel(r.last_run)}` : "No runs yet"} · completed months only</div></div></div>
        <div class="card-body">${r.up_to_date ? `<div class="notice" style="margin-bottom:12px">Depreciation is up to date. ${monthLabel(r.next_period)} can be run once the month has ended.</div>` : ""}
        <form class="row" id="pf" ${r.up_to_date ? 'style="display:none"' : ""}><input class="input" type="month" name="period" value="${r.up_to_date ? r.last_complete : r.next_period}" max="${r.last_complete}" style="width:auto"><button class="btn">Preview</button></form>
        <p class="muted small">Posts Dr Depreciation / Cr Accumulated Depreciation on the last day of the month. Months are run in order; an asset that missed earlier months (for example one registered late) is caught up in the next run.</p>
        <div id="prev"></div></div></div>
      <div class="card"><div class="card-head"><h3>Runs</h3></div><div id="runs"></div></div></div>`;
    table(pane.querySelector("#runs"), { rows: r.items, sortKey: null, empty: "No depreciation runs yet.", columns: [
      { key: "period", label: "Month", render: (x) => monthLabel(x.period) },
      { key: "entry_no", label: "Entry", render: (x) => `${esc(x.entry_no || "")}${x.reversed ? " " + badge("reversed") : ""}` },
      { key: "assets", label: "Assets", num: true },
      { key: "amount", label: "Amount", num: true, render: (x) => (x.amounts ? moneyList(x.amounts) : money(x.amount)) },
      { key: "by", label: "By" },
      { key: "id", label: "", sort: false, cls: "actions", render: (x) => (has("assets.approve") && latest && x.id === latest.id ? `<button class="btn sm danger" data-rev="${x.id}">Reverse</button>` : "") }] });
    pane.querySelector("[data-rev]")?.addEventListener("click", async (e) => {
      const reason = await confirmDialog(`Reverse the ${monthLabel(latest.period)} depreciation run? A reversing journal is posted today and the month can be run again.`, { title: "Reverse depreciation", confirmText: "Reverse", danger: true, reason: true });
      if (!reason) return;
      try { await api.post(`/assets/depreciation/${e.target.dataset.rev}/reverse`, { reason }); toast("Run reversed", "success"); depreciation(); } catch (err) { handleError(err); }
    });
    pane.querySelector("#pf").onsubmit = async (e) => {
      e.preventDefault();
      const period = e.target.period.value;
      try {
        const p = await api.get("/assets/depreciation/preview", { period });
        const box = pane.querySelector("#prev");
        box.innerHTML = `<div id="pt"></div><div class="row" style="justify-content:space-between;margin-top:10px"><b>Total ${moneyList(p.totals || { [baseCurrency()]: p.total })}</b>${p.items.length ? `<button class="btn primary" id="post">Post ${monthLabel(period)} depreciation</button>` : ""}</div>`;
        table(box.querySelector("#pt"), { rows: p.items, empty: "Nothing to depreciate for this month.", columns: [
          { key: "asset_no", label: "Asset", render: (x) => `${esc(x.asset_no)} <span class="muted small">${esc(x.name)}</span>` },
          { key: "months", label: "Months", num: true, render: (x) => (x.months > 1 ? `${x.months} <span class="muted small">(catch-up)</span>` : "1") },
          { key: "amount", label: "Charge", num: true, render: (x) => money(x.amount, x.currency) },
          { key: "nbv_after", label: "Book value after", num: true, render: (x) => money(x.nbv_after, x.currency) }] });
        box.querySelector("#post")?.addEventListener("click", async (ev) => {
          ev.target.disabled = true;
          try { await api.post("/assets/depreciation", { period }); toast("Depreciation posted", "success"); depreciation(); }
          catch (err) { handleError(err); ev.target.disabled = false; }
        });
      } catch (err) { handleError(err); }
    };
  }

  // ------------------------------------------------------------ summary
  async function summary() {
    const r = await api.get("/assets", { status: "active" });
    meta = r;
    const rc = r.reconciliation;
    const recs = rc.by_currency || [rc];
    const row = (label, reg, gl, diff, cur) => `<tr><td>${label}</td><td class="num">${money(reg, cur)}</td><td class="num">${money(gl, cur)}</td><td class="num ${diff ? "check-bad" : ""}">${diff ? money(diff, cur) : "✓"}</td></tr>`;
    pane.innerHTML = `<div class="grid g-2">
      <div class="card"><div class="card-head"><div><h3>By category</h3><div class="sub">Assets in use</div></div></div><div id="cats"></div></div>
      <div class="card"><div class="card-head"><div><h3>Register vs general ledger</h3><div class="sub">${rc.reconciled ? '<span class="check-ok">✓ Reconciled</span>' : '<span class="check-bad">Differences found</span>'}</div></div></div>
        <div class="table-wrap"><table class="table"><thead><tr><th></th><th class="num">Register</th><th class="num">Ledger</th><th class="num">Difference</th></tr></thead><tbody>
          ${recs.map((x) => `${recs.length > 1 ? `<tr class="sec"><td colspan="4"><b>${esc(x.currency)}</b></td></tr>` : ""}
          ${row("Cost (1500)", x.register_cost, x.ledger_cost, x.cost_difference, x.currency)}
          ${row("Accumulated depreciation (1590)", x.register_accumulated, x.ledger_accumulated, x.accumulated_difference, x.currency)}
          ${row("Net book value", x.register_nbv, x.ledger_nbv, Math.round((x.ledger_nbv - x.register_nbv) * 100) / 100, x.currency)}`).join("")}
        </tbody></table></div>
        <div class="card-body muted small">Differences come from manual journals to accounts 1500/1590 that have no matching register entry, or from register entries marked "existing" whose cost was never brought into the books. Register those assets, or correct the journal.</div></div></div>`;
    table(pane.querySelector("#cats"), { rows: r.summary, empty: "No assets registered.", columns: [
      { key: "category", label: "Category" }, { key: "count", label: "Items", num: true },
      { key: "cost", label: "Cost", num: true, render: (c) => money(c.cost, c.currency) },
      { key: "accumulated", label: "Acc. depreciation", num: true, render: (c) => money(c.accumulated, c.currency) },
      { key: "nbv", label: "Book value", num: true, render: (c) => `<b>${money(c.nbv, c.currency)}</b>` }] });
  }

  const views = { register, depreciation, summary };
  const show = async () => {
    el.querySelectorAll("[data-tab]").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
    pane.innerHTML = `<div class="empty">Loading…</div>`;
    try { await views[tab](); } catch (err) { handleError(err); }
  };
  el.querySelectorAll("[data-tab]").forEach((b) => (b.onclick = () => { tab = b.dataset.tab; history.replaceState(null, "", `#/assets/${tab}`); show(); }));
  el.querySelector("#add")?.addEventListener("click", addAsset);
  await show();
}
