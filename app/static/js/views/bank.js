// Bank reconciliation: statements, matching, bank-only items and the reconciliation statement.
import { api } from "../api.js";
import { badge, confirmDialog, esc, fmtDate, formModal, handleError, has, icon, modal, money, printModal, selectHtml, state, table, toast, today } from "../ui.js";

const signedMoney = (v, cur) => `<span style="color:${v < 0 ? "var(--bad)" : "inherit"}">${money(v, cur)}</span>`;

// ---------------------------------------------------------------- CSV import helpers
function parseCSV(text) {
  const rows = [];
  let row = [], cell = "", quoted = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (quoted) {
      if (ch === '"' && text[i + 1] === '"') { cell += '"'; i++; } else if (ch === '"') quoted = false; else cell += ch;
    } else if (ch === '"') quoted = true;
    else if (ch === "," || ch === ";" || ch === "\t") { row.push(cell.trim()); cell = ""; }
    else if (ch === "\n" || ch === "\r") {
      if (ch === "\r" && text[i + 1] === "\n") i++;
      row.push(cell.trim()); cell = "";
      if (row.some((c) => c !== "")) rows.push(row);
      row = [];
    } else cell += ch;
  }
  row.push(cell.trim());
  if (row.some((c) => c !== "")) rows.push(row);
  return rows;
}

const MONTHS = { jan: 1, feb: 2, mar: 3, apr: 4, may: 5, jun: 6, jul: 7, aug: 8, sep: 9, oct: 10, nov: 11, dec: 12 };
function toISODate(s) {
  s = String(s || "").trim();
  let m = s.match(/^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})/);
  if (m) return `${m[1]}-${m[2].padStart(2, "0")}-${m[3].padStart(2, "0")}`;
  m = s.match(/^(\d{1,2})[-/.](\d{1,2})[-/.](\d{2,4})/); // day first, as Zimbabwean banks print it
  if (m) return `${m[3].length === 2 ? "20" + m[3] : m[3]}-${m[2].padStart(2, "0")}-${m[1].padStart(2, "0")}`;
  m = s.match(/^(\d{1,2})[\s-]([A-Za-z]{3})[a-z]*[\s-](\d{2,4})/);
  if (m && MONTHS[m[2].toLowerCase()]) return `${m[3].length === 2 ? "20" + m[3] : m[3]}-${String(MONTHS[m[2].toLowerCase()]).padStart(2, "0")}-${m[1].padStart(2, "0")}`;
  return null;
}
const guess = (headers, words) => headers.findIndex((h) => words.some((w) => h.toLowerCase().includes(w)));

export default async function (el, [statementId]) {
  if (statementId) return workspace(el, +statementId);
  return overview(el);
}

// ---------------------------------------------------------------- overview
async function overview(el) {
  const [accts, hist] = await Promise.all([api.get("/banking/accounts"), api.get("/banking/statements")]);
  el.innerHTML = `
    <div class="page-head"><div><h1>Bank Reconciliation</h1><p>Prove each bank, mobile money and cash account in the books against its statement</p></div></div>
    <div class="card" style="margin-bottom:16px"><div class="card-head"><div><h3>Accounts</h3><div class="sub">Reconcile at least monthly, when each bank statement arrives</div></div></div><div id="accts"></div></div>
    <div class="card"><div class="card-head"><h3>Reconciliations</h3></div><div id="hist"></div></div>`;
  table(el.querySelector("#accts"), {
    rows: accts.items, sortKey: "code",
    columns: [
      { key: "name", label: "Account", render: (a) => `<b>${esc(a.name)}</b><br><span class="muted small">${esc(a.code)} · ${esc(a.currency)}</span>` },
      { key: "book_balance", label: "Balance in books", num: true, render: (a) => signedMoney(a.book_balance, a.currency) },
      { key: "reconciled_to", label: "Reconciled to", render: (a) => (a.reconciled_to ? `${fmtDate(a.reconciled_to)}${a.days_since > 45 ? ' <span class="badge warn">overdue</span>' : ""}` : '<span class="badge warn">never</span>') },
      { key: "reconciled_balance", label: "Statement balance then", num: true, render: (a) => (a.reconciled_balance === null ? "—" : money(a.reconciled_balance, a.currency)) },
      { key: "uncleared", label: "Uncleared items", num: true },
      { key: "id", label: "", sort: false, cls: "actions", render: (a) => (a.draft_id ? `<a class="btn sm primary" href="#/bank/${a.draft_id}">Continue</a>`
        : has("banking.manage") ? `<button class="btn sm" data-start="${a.id}">Reconcile</button>` : "") },
    ],
  });
  el.querySelectorAll("[data-start]").forEach((b) => (b.onclick = async () => {
    const a = accts.items.find((x) => x.id === +b.dataset.start);
    const s = await formModal({
      title: `Reconcile ${a.name}`, cols: 1,
      intro: `<p class="muted" style="margin-top:0">Copy these from the bank statement. ${a.reconciled_to ? `Last reconciled to ${fmtDate(a.reconciled_to)} with a balance of ${money(a.reconciled_balance, a.currency)}.` : "This is the first reconciliation for this account."}</p>`,
      fields: [...(a.reconciled_to ? [] : [{ name: "start_date", label: "Statement start date", type: "date", required: true, max: today(),
          default: today().slice(0, 8) + "01", hint: "First reconciliation: transactions before this date are taken as already in the opening balance" }]),
        { name: "statement_date", label: "Statement end date", type: "date", required: true, default: today(), max: today(), min: a.reconciled_to || undefined },
        { name: "opening", label: `Opening balance (${a.currency})`, type: "number", step: 0.01, default: a.reconciled_balance ?? 0, hint: "Negative if overdrawn" },
        { name: "closing", label: `Closing balance (${a.currency})`, type: "number", step: 0.01, required: true },
        { name: "reference", label: "Statement number / reference" }],
      onSubmit: (d) => api.post("/banking/statements", { ...d, account_id: a.id }),
    });
    if (s) location.hash = `#/bank/${s.id}`;
  }));
  table(el.querySelector("#hist"), {
    rows: hist.items, sortKey: null, empty: "No reconciliations yet.", onRowClick: (s) => (location.hash = `#/bank/${s.id}`),
    columns: [{ key: "account", label: "Account" }, { key: "statement_date", label: "Statement to", render: (s) => fmtDate(s.statement_date) },
      { key: "reference", label: "Reference" }, { key: "closing", label: "Closing balance", num: true, render: (s) => money(s.closing, s.currency) },
      { key: "lines", label: "Lines", num: true }, { key: "status", label: "Status", render: (s) => badge(s.status === "completed" ? "paid" : "pending", s.status === "completed" ? "reconciled" : "in progress") },
      { key: "completed_by", label: "Completed by", render: (s) => (s.completed_by ? `${esc(s.completed_by)}<br><span class="muted small">${fmtDate(s.completed_at)}</span>` : "—") }],
  });
}

// ---------------------------------------------------------------- workspace
async function workspace(el, id) {
  let s = await api.get(`/banking/statements/${id}`);
  const cur = s.currency;
  const m$ = (v) => money(v, cur);
  const accounts = (await api.get("/accounting/accounts")).items
    .filter((a) => a.active && (a.type === "income" || a.type === "expense") && a.subtype !== "contra_income");

  const draw = () => {
    const sm = s.summary, draft = s.status === "draft", edit = draft && has("banking.manage");
    el.innerHTML = `
      <div class="page-head"><div><h1>${esc(s.account)} · ${fmtDate(s.statement_date)}</h1>
        <p>${draft ? "Reconciliation in progress" : `Reconciled by ${esc(s.completed_by || "")} on ${fmtDate(s.completed_at)}`}${s.reference ? ` · ${esc(s.reference)}` : ""}</p></div>
        <div class="page-actions"><a class="btn" href="#/bank">${icon("back")} All accounts</a><button class="btn" id="print">${icon("print")} Reconciliation statement</button>
          ${edit ? `<button class="btn" id="edit">Edit balances</button>` : ""}
          ${draft && has("banking.approve") ? `<button class="btn primary" id="complete" ${sm.can_complete ? "" : "disabled"}>Complete reconciliation</button>` : ""}
          ${!draft && has("banking.approve") ? `<button class="btn danger" id="reopen">Reopen</button>` : ""}</div></div>
      ${sm.start_date ? `<div class="notice ${sm.books_at_start !== sm.opening ? "warn" : ""}" style="margin-bottom:16px">First reconciliation from ${fmtDate(sm.start_date)}: transactions before then are taken as already in the opening balance. Books at that date: <b>${m$(sm.books_at_start)}</b>, statement opening: <b>${m$(sm.opening)}</b>${sm.books_at_start !== sm.opening ? `. They differ by ${m$(sm.books_at_start - sm.opening)}: something before ${fmtDate(sm.start_date)} hasn't reached the bank (or the books). Choose an earlier start date or post the missing item.` : " ✓"}</div>` : ""}
      ${!sm.opening_continues ? `<div class="notice warn" style="margin-bottom:16px">The opening balance (${m$(sm.opening)}) doesn't follow on from the last reconciled closing balance (${m$(sm.previous_closing)}). Check the statement, or a statement is missing.</div>` : ""}
      <div class="grid g-2" style="margin-bottom:16px">
        <div class="card"><div class="card-head"><div><h3>Reconciliation</h3><div class="sub">at ${fmtDate(s.statement_date)}</div></div>
          ${sm.difference === 0 ? '<span class="check-ok">✓ Balanced</span>' : `<span class="check-bad">Difference ${m$(sm.difference)}</span>`}</div>
          <div class="table-wrap"><table class="table"><tbody>
            <tr><td>Balance per bank statement</td><td class="num">${m$(sm.closing)}</td></tr>
            <tr><td>Add: deposits not yet on the statement</td><td class="num">${m$(sm.outstanding_deposits)}</td></tr>
            <tr><td>Less: payments not yet presented</td><td class="num">(${m$(sm.unpresented_payments)})</td></tr>
            <tr><th>Adjusted bank balance</th><th class="num">${m$(sm.adjusted_bank)}</th></tr>
            <tr><th>Balance per books</th><th class="num">${m$(sm.book_balance)}</th></tr>
            <tr><td>Difference</td><td class="num ${sm.difference ? "check-bad" : ""}">${m$(sm.difference)}</td></tr></tbody></table></div></div>
        <div class="card"><div class="card-head"><h3>Statement checks</h3></div><div class="card-body small">
          <p style="margin-top:0">Opening ${m$(sm.opening)} + lines ${m$(sm.lines_total)} ${sm.lines_balance ? "=" : "≠"} closing ${m$(sm.closing)} ${sm.lines_balance ? '<span class="check-ok">✓</span>' : `<span class="check-bad">short ${m$(sm.lines_difference)}</span>`}</p>
          <p>${sm.unmatched_lines ? `<span class="check-bad">${sm.unmatched_lines} line(s) not matched</span>` : '<span class="check-ok">✓ Every line matched</span>'}</p>
          <p class="muted" style="margin-bottom:0">Match each statement line to the transaction in the books. Bank charges and interest that only appear on the statement can be posted from here. Fee receipts are recorded under Payments first, so the student is credited, then matched.</p>
        </div></div></div>
      <div class="card" style="margin-bottom:16px"><div class="card-head"><div><h3>Statement lines</h3><div class="sub">${s.lines.length} line(s) · money in positive, money out negative</div></div>
        ${edit ? `<div class="row"><button class="btn sm" id="import">${icon("download")} Import CSV</button><button class="btn sm" id="add-lines">${icon("plus")} Add lines</button><button class="btn sm" id="auto">Auto-match</button></div>` : ""}</div><div id="lines"></div></div>
      <div class="card"><div class="card-head"><div><h3>In the books, not on this statement</h3><div class="sub">Deposits not yet credited and payments not yet presented, up to ${fmtDate(s.statement_date)}</div></div></div><div id="outst"></div></div>`;

    table(el.querySelector("#lines"), {
      rows: s.lines, sortKey: null, empty: "No statement lines yet. Import the bank's CSV or add lines by hand.",
      columns: [
        { key: "date", label: "Date", render: (l) => fmtDate(l.date) },
        { key: "description", label: "Statement description", render: (l) => `${esc(l.description)}${l.reference ? `<br><span class="muted small">${esc(l.reference)}</span>` : ""}` },
        { key: "amount", label: "Amount", num: true, render: (l) => signedMoney(l.amount, cur) },
        { key: "match", label: "Matched to", sort: false, render: (l) => (l.match ? `<a href="#/ledger/journals">${esc(l.match.entry_no)}</a> ${fmtDate(l.match.date)}<br><span class="muted small">${esc(l.match.description)}</span>${l.posted_here ? ' <span class="badge info">posted here</span>' : ""}` : '<span class="badge warn">unmatched</span>') },
        { key: "id", label: "", sort: false, cls: "actions", render: (l) => (!edit ? "" : l.match
          ? (l.posted_here ? "" : `<button class="btn sm" data-unmatch="${l.id}">Unmatch</button>`)
          : `<button class="btn sm" data-match="${l.id}">Match</button> <button class="btn sm" data-post="${l.id}">Post</button> <button class="btn sm ghost danger" data-del="${l.id}" aria-label="Delete line">✕</button>`) },
      ],
    });
    table(el.querySelector("#outst"), {
      rows: s.outstanding, sortKey: null, empty: "Nothing outstanding: every book transaction up to this date is on a statement.",
      columns: [{ key: "date", label: "Date", render: (b) => fmtDate(b.date) }, { key: "entry_no", label: "Entry" },
        { key: "description", label: "Description", render: (b) => `${esc(b.description)}${b.reference ? `<br><span class="muted small">${esc(b.reference)}</span>` : ""}` },
        { key: "kind", label: "Type", sort: false, render: (b) => (b.amount > 0 ? "Deposit not yet credited" : "Payment not yet presented") },
        { key: "amount", label: "Amount", num: true, render: (b) => signedMoney(b.amount, cur) }],
    });
    wire();
  };

  const act = async (fn, msg) => {
    try { s = await fn(); if (s.statement) s = s.statement; if (msg) toast(msg, "success"); draw(); } catch (err) { handleError(err); }
  };

  const wire = () => {
    el.querySelector("#print").onclick = () => printStatement(s);
    el.querySelector("#edit")?.addEventListener("click", async () => {
      const r = await formModal({ title: "Statement details", cols: 1, values: s,
        fields: [...(s.start_date ? [{ name: "start_date", label: "Statement start date (first reconciliation)", type: "date", required: true, max: today() }] : []),
          { name: "statement_date", label: "Statement end date", type: "date", required: true, max: today() },
          { name: "opening", label: `Opening balance (${cur})`, type: "number", step: 0.01, required: true },
          { name: "closing", label: `Closing balance (${cur})`, type: "number", step: 0.01, required: true },
          { name: "reference", label: "Reference" }],
        onSubmit: (d) => api.put(`/banking/statements/${s.id}`, d) });
      if (r) { s = r; draw(); }
    });
    el.querySelector("#complete")?.addEventListener("click", async () => {
      if (await confirmDialog(`Complete the reconciliation of ${s.account} to ${fmtDate(s.statement_date)}? The matched transactions are marked as cleared.`, { confirmText: "Complete" }))
        act(() => api.post(`/banking/statements/${s.id}/complete`), "Reconciliation completed");
    });
    el.querySelector("#reopen")?.addEventListener("click", async () => {
      const reason = await confirmDialog("Reopen this reconciliation? Its transactions become uncleared again until it is completed.", { title: "Reopen reconciliation", confirmText: "Reopen", danger: true, reason: true });
      if (reason) act(() => api.post(`/banking/statements/${s.id}/reopen`, { reason }), "Reopened");
    });
    el.querySelector("#auto")?.addEventListener("click", async () => {
      try { const r = await api.post(`/banking/statements/${s.id}/auto-match`); s = r.statement; toast(`${r.matched} line(s) matched`, "success"); draw(); } catch (err) { handleError(err); }
    });
    el.querySelector("#add-lines")?.addEventListener("click", () => addLines());
    el.querySelector("#import")?.addEventListener("click", () => importCSV());
    el.querySelectorAll("[data-unmatch]").forEach((b) => (b.onclick = () => act(() => api.post(`/banking/lines/${b.dataset.unmatch}/unmatch`))));
    el.querySelectorAll("[data-del]").forEach((b) => (b.onclick = () => act(() => api.del(`/banking/lines/${b.dataset.del}`))));
    el.querySelectorAll("[data-match]").forEach((b) => (b.onclick = () => matchLine(s.lines.find((l) => l.id === +b.dataset.match))));
    el.querySelectorAll("[data-post]").forEach((b) => (b.onclick = () => postLine(s.lines.find((l) => l.id === +b.dataset.post))));
  };

  function matchLine(line) {
    // Same amount first; the rest of the outstanding items below.
    const same = s.outstanding.filter((b) => Math.abs(b.amount - line.amount) < 0.005);
    const other = s.outstanding.filter((b) => !same.includes(b));
    const rowHtml = (b) => `<tr class="clickable" data-pick="${b.id}"><td>${fmtDate(b.date)}</td><td>${esc(b.entry_no)}</td><td>${esc(b.description)}${b.reference ? `<br><span class="muted small">${esc(b.reference)}</span>` : ""}</td><td class="num">${signedMoney(b.amount, cur)}</td></tr>`;
    const m = modal({ title: `Match · ${line.description} · ${money(line.amount, cur)}`, wide: true,
      body: `<p class="muted" style="margin-top:0">Pick the transaction in the books that this statement line represents. Amounts must agree. If it isn't in the books yet, record it first (Payments, Expenses or a journal), or use Post for bank charges and interest.</p>
        <div class="table-wrap card"><table class="table"><thead><tr><th>Date</th><th>Entry</th><th>Description</th><th class="num">Amount</th></tr></thead><tbody>
        ${same.length ? same.map(rowHtml).join("") : `<tr><td colspan="4" class="muted">No outstanding transaction of ${money(line.amount, cur)}.</td></tr>`}
        ${other.length ? `<tr><td colspan="4" class="muted small"><b>Other outstanding transactions</b></td></tr>${other.map(rowHtml).join("")}` : ""}</tbody></table></div>`,
      actions: [{ label: "Cancel" }] });
    m.el.querySelectorAll("[data-pick]").forEach((tr) => (tr.onclick = async () => {
      try { s = await api.post(`/banking/lines/${line.id}/match`, { journal_line_id: +tr.dataset.pick }); m.close(); draw(); } catch (err) { handleError(err); }
    }));
  }

  async function postLine(line) {
    const out = line.amount < 0;
    const def = accounts.find((a) => a.code === (out ? "5810" : "4970"));
    const r = await formModal({
      title: `Post ${out ? "bank charge" : "receipt"} · ${money(line.amount, cur)}`, cols: 1,
      intro: `<p class="muted" style="margin-top:0">Records "${esc(line.description)}" in the books on ${fmtDate(line.date)} (${out ? "Cr" : "Dr"} ${esc(s.account)}) and matches it to this line. Use this for items only the bank knew about. Fee receipts belong under Payments.</p>`,
      fields: [{ name: "account_id", label: out ? "Charge to" : "Income account", type: "select", required: true,
        options: accounts.filter((a) => (out ? a.type === "expense" : a.type === "income")).map((a) => ({ value: a.id, label: `${a.code} · ${a.name}` })), default: def?.id },
        { name: "description", label: "Description", default: `Bank statement: ${line.description}` }],
      onSubmit: (d) => api.post(`/banking/lines/${line.id}/post`, d),
    });
    if (r) { s = r; toast("Posted and matched", "success"); draw(); }
  }

  function addLines() {
    let rows = [{ date: s.statement_date }, {}, {}];
    const m = modal({ title: "Add statement lines", wide: true,
      body: `<p class="muted" style="margin-top:0">Type lines as they appear on the statement. Use a minus sign for money out (e.g. -25.00).</p><div id="rows"></div>
        <button type="button" class="btn sm" id="more">${icon("plus")} Another line</button>`,
      actions: [{ label: "Cancel" }, { label: "Add lines", cls: "primary", onClick: async () => {
        collect();
        const lines = rows.filter((r) => r.description || r.amount).map((r) => ({ ...r }));
        if (!lines.length) { toast("Enter at least one line", "error"); return true; }
        const r = await api.post(`/banking/statements/${s.id}/lines`, { lines });
        s = r.statement;
        toast(`${r.added} line(s) added, ${r.matched} matched automatically`, "success");
        draw();
      } }] });
    const box = m.el.querySelector("#rows");
    const collect = () => box.querySelectorAll("[data-row]").forEach((d, i) => {
      rows[i] = Object.fromEntries([...d.querySelectorAll("input")].map((inp) => [inp.name, inp.value.trim()]));
    });
    const render = () => {
      box.innerHTML = rows.map((r) => `<div class="row" data-row style="margin-bottom:6px;flex-wrap:nowrap">
        <input class="input" type="date" name="date" value="${esc(r.date || s.statement_date)}" max="${s.statement_date}" style="width:150px" aria-label="Date">
        <input class="input" name="description" value="${esc(r.description || "")}" placeholder="Description" aria-label="Description">
        <input class="input" name="reference" value="${esc(r.reference || "")}" placeholder="Reference" style="width:140px" aria-label="Reference">
        <input class="input" name="amount" type="number" step="0.01" value="${esc(r.amount || "")}" placeholder="0.00" style="width:120px" aria-label="Amount"></div>`).join("");
    };
    m.el.querySelector("#more").onclick = () => { collect(); rows.push({}); render(); };
    render();
  }

  function importCSV() {
    const m = modal({ title: "Import bank statement (CSV)", wide: true,
      body: `<p class="muted" style="margin-top:0">Export the statement from internet banking (or EcoCash/OneMoney) as CSV, then choose the file. You'll match its columns next.</p>
        <input type="file" accept=".csv,text/csv,text/plain" id="file" class="input"><div id="map" style="margin-top:12px"></div>`,
      actions: [{ label: "Cancel" }] });
    m.el.querySelector("#file").onchange = async (e) => {
      const file = e.target.files[0];
      if (!file) return;
      const data = parseCSV(await file.text());
      if (data.length < 2) { toast("That file has no data rows", "error"); return; }
      const headers = data[0];
      const opts = headers.map((h, i) => ({ value: i, label: h || `Column ${i + 1}` }));
      const pick = (name, label, guessIdx, optional) => `<label class="field"><span>${label}</span>${selectHtml(name, opts, guessIdx >= 0 ? guessIdx : "", optional ? { empty: "—" } : {})}</label>`;
      const amountIdx = guess(headers, ["amount", "value"]);
      const inIdx = guess(headers, ["credit", "money in", "deposit", "paid in"]);
      const outIdx = guess(headers, ["debit", "money out", "withdrawal", "paid out"]);
      m.el.querySelector("#map").innerHTML = `<div class="cols-3">
          ${pick("c_date", "Date", guess(headers, ["date"]))}
          ${pick("c_desc", "Description", guess(headers, ["description", "narrative", "details", "particular", "transaction"]))}
          ${pick("c_ref", "Reference", guess(headers, ["ref", "cheque", "receipt"]), true)}
          ${pick("c_amount", "Amount (− for money out)", inIdx >= 0 && outIdx >= 0 ? -1 : amountIdx, true)}
          ${pick("c_in", "Or: money in column", inIdx, true)}${pick("c_out", "Money out column", outIdx, true)}</div>
        <p class="muted small">${data.length - 1} row(s) found. Rows outside the statement period or without an amount are skipped.</p>
        <div class="row" style="justify-content:flex-end"><button class="btn primary" id="go">Import</button></div>`;
      m.el.querySelector("#go").onclick = async () => {
        const col = (n) => { const v = m.el.querySelector(`[name="${n}"]`).value; return v === "" ? -1 : +v; };
        const [cd, cdesc, cref, camt, cin, cout] = ["c_date", "c_desc", "c_ref", "c_amount", "c_in", "c_out"].map(col);
        const clean = (v) => String(v ?? "").replace(/[^0-9().,-]/g, "").replace(/,(?=\d{3}\b)/g, "");
        const lines = [], bad = [];
        data.slice(1).forEach((r, i) => {
          const d = toISODate(r[cd]);
          const amount = camt >= 0 ? clean(r[camt]) : "";
          const moneyIn = cin >= 0 ? clean(r[cin]) : "", moneyOut = cout >= 0 ? clean(r[cout]) : "";
          if (!d || d > s.statement_date) { if (r[cd]) bad.push(i + 2); return; }
          if (!(+amount || +moneyIn.replace(/[()]/g, "") || +moneyOut.replace(/[()]/g, ""))) return;
          lines.push({ date: d, description: r[cdesc] || "Statement line", reference: cref >= 0 ? r[cref] : "",
            ...(camt >= 0 ? { amount } : { money_in: moneyIn, money_out: moneyOut }) });
        });
        if (!lines.length) { toast("No usable rows: check the column choices and dates", "error"); return; }
        try {
          const r = await api.post(`/banking/statements/${s.id}/lines`, { lines });
          s = r.statement; m.close(); draw();
          toast(`${r.added} line(s) imported, ${r.matched} matched automatically${bad.length ? `; ${bad.length} row(s) skipped (date)` : ""}`, "success");
        } catch (err) { handleError(err); }
      };
    };
  }

  draw();
}

function printStatement(s) {
  const sm = s.summary, cur = s.currency, m$ = (v) => money(v, cur);
  const out = s.outstanding;
  const m = modal({ title: "Bank reconciliation statement", wide: true,
    body: `<div class="row no-print" style="justify-content:flex-end;margin-bottom:12px"><button class="btn" data-print>${icon("print")} Print</button></div>
    <div class="doc"><div class="doc-head"><div><h2>${esc(state.meta.school)}</h2><div class="muted">Bank reconciliation statement</div></div>
      <div style="text-align:right"><h2>${esc(s.account)}</h2><div class="muted">as at ${fmtDate(s.statement_date)}${s.reference ? ` · ${esc(s.reference)}` : ""}</div></div></div>
    <table><tbody>
      <tr><td>Balance per bank statement</td><td style="text-align:right">${m$(sm.closing)}</td></tr>
      <tr><th colspan="2">Add: deposits not yet credited by the bank</th></tr>
      ${out.filter((b) => b.amount > 0).map((b) => `<tr><td>&nbsp;&nbsp;${fmtDate(b.date)} · ${esc(b.entry_no)} · ${esc(b.description)}</td><td style="text-align:right">${m$(b.amount)}</td></tr>`).join("") || `<tr><td class="muted">&nbsp;&nbsp;None</td><td></td></tr>`}
      <tr><th colspan="2">Less: payments not yet presented</th></tr>
      ${out.filter((b) => b.amount < 0).map((b) => `<tr><td>&nbsp;&nbsp;${fmtDate(b.date)} · ${esc(b.entry_no)} · ${esc(b.description)}</td><td style="text-align:right">(${m$(-b.amount)})</td></tr>`).join("") || `<tr><td class="muted">&nbsp;&nbsp;None</td><td></td></tr>`}
      <tr><th>Adjusted bank balance</th><th style="text-align:right">${m$(sm.adjusted_bank)}</th></tr>
      <tr><th>Balance per books (cash book)</th><th style="text-align:right">${m$(sm.book_balance)}</th></tr>
      <tr><th>Difference</th><th style="text-align:right">${m$(sm.difference)}</th></tr></tbody></table>
    <p class="muted">${s.status === "completed" ? `Reconciled by ${esc(s.completed_by)} on ${fmtDate(s.completed_at)}.` : "Draft: not yet completed."}</p>
    <p class="muted" style="margin-top:28px">Prepared by: ____________________ &nbsp;&nbsp; Reviewed by: ____________________</p></div>` });
  m.el.querySelector("[data-print]").onclick = printModal;
}
