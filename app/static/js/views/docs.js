// Printable documents: receipt, invoice, statement, report card.
import { api } from "../api.js";
import { badge, docLogo, esc, fmtDate, handleError, icon, modal, money, pct, printModal, state } from "../ui.js";

const school = () => state.meta?.school || document.getElementById("root").dataset.school;

function docModal(title, html) {
  const m = modal({
    title, wide: true,
    body: `<div class="row no-print" style="justify-content:flex-end;margin-bottom:12px"><button class="btn" data-print>${icon("print")} Print</button></div>${html}`,
  });
  m.el.querySelector("[data-print]").onclick = printModal;
  return m;
}

function header(title, sub) {
  return `<div class="doc-head">${docLogo()}<div><h2>${esc(school())}</h2><div class="muted">School Management System</div></div>
    <div style="text-align:right"><h2>${esc(title)}</h2><div class="muted">${sub}</div></div></div>`;
}

export async function openReceipt(id) {
  try {
    const p = await api.get(`/payments/${id}`);
    docModal(`Receipt ${p.receipt_no}`, `<div class="doc">
      ${header("RECEIPT", `${esc(p.receipt_no)}<br>${fmtDate(p.paid_on)}`)}
      ${p.void ? `<p><span class="stamp">VOID</span> <span class="muted">${esc(p.void_reason || "")}</span></p>` : ""}
      <table><tr><th>Received from</th><td>${esc(p.student)} (${esc(p.admission_no)})</td><th>Class</th><td>${esc(p.class || "—")}</td></tr>
      <tr><th>Amount</th><td><b>${money(p.amount, p.currency)}</b></td><th>Method</th><td style="text-transform:capitalize">${esc(p.method)}${p.reference ? ` · ${esc(p.reference)}` : ""}</td></tr></table>
      <h3>Applied to</h3>
      <table><thead><tr><th>Invoice</th><th>Term</th><th style="text-align:right">Amount</th></tr></thead><tbody>
      ${p.allocations.map((a) => `<tr><td>${esc(a.invoice_no)}</td><td>${esc(a.term)}</td><td style="text-align:right">${money(a.amount, p.currency)}</td></tr>`).join("") || `<tr><td colspan="3" class="muted">Held as credit on account</td></tr>`}
      ${p.unallocated > 0 ? `<tr><td colspan="2">Credit carried forward</td><td style="text-align:right">${money(p.unallocated, p.currency)}</td></tr>` : ""}
      </tbody></table>
      ${(() => { const a = (p.account.by_currency || []).find((x) => x.currency === p.currency) || p.account; return `<p><b>${esc(p.currency || "")} account balance after this payment:</b> ${money(a.balance, p.currency)}${a.balance < 0 ? " (in credit)" : ""}</p>`; })()}
      <p class="muted" style="margin-top:28px">Received by: ${esc(p.received_by || "—")} &nbsp;&nbsp; Signature: ____________________</p></div>`);
  } catch (err) { handleError(err); }
}

export async function openInvoice(id) {
  try {
    const i = await api.get(`/invoices/${id}`);
    docModal(`Invoice ${i.invoice_no}`, `<div class="doc">
      ${header("INVOICE", `${esc(i.invoice_no)}<br>Issued ${fmtDate(i.issue_date)}<br>Due ${fmtDate(i.due_date)}`)}
      ${i.status === "void" ? `<p><span class="stamp">VOID</span> <span class="muted">${esc(i.void_reason || "")}</span></p>` : ""}
      <table><tr><th>Student</th><td>${esc(i.student)} (${esc(i.admission_no)})</td><th>Class</th><td>${esc(i.class || "—")}</td></tr>
      <tr><th>Bill to</th><td>${esc(i.guardian || "—")}</td><th>Term</th><td>${esc(i.term)}</td></tr></table>
      <table><thead><tr><th>Description</th><th style="text-align:right">Amount</th></tr></thead><tbody>
      ${i.lines.map((l) => `<tr><td>${esc(l.description)}</td><td style="text-align:right">${money(l.amount, i.currency)}</td></tr>`).join("")}
      <tr><th>Total</th><th style="text-align:right">${money(i.total, i.currency)}</th></tr>
      <tr><td>Paid</td><td style="text-align:right">${money(i.paid, i.currency)}</td></tr>
      <tr><th>Balance due</th><th style="text-align:right">${money(i.balance, i.currency)}</th></tr></tbody></table>
      ${i.payments.length ? `<h3>Payments received</h3><table><thead><tr><th>Receipt</th><th>Date</th><th>Method</th><th style="text-align:right">Applied</th></tr></thead><tbody>
        ${i.payments.map((p) => `<tr><td>${esc(p.receipt_no)}</td><td>${fmtDate(p.date)}</td><td style="text-transform:capitalize">${esc(p.method)}</td><td style="text-align:right">${money(p.amount, i.currency)}</td></tr>`).join("")}</tbody></table>` : ""}
      <p class="muted">Status: ${esc(i.status)}. Please quote the invoice number with every payment.</p></div>`);
  } catch (err) { handleError(err); }
}

export async function openStatement(studentId) {
  try {
    const s = await api.get(`/students/${studentId}/statement`);
    docModal(`Statement · ${s.student.name}`, `<div class="doc">
      ${header("STATEMENT OF ACCOUNT", `${fmtDate(state.meta.today)}`)}
      <p><b>${esc(s.student.name)}</b> (${esc(s.student.admission_no)}) · ${esc(s.student.class || "")}</p>
      ${(s.sections || [{ currency: "", entries: s.entries, account: s.account }]).map((sec) => `
      ${(s.sections || []).length > 1 ? `<h3>${esc(sec.currency)} account</h3>` : ""}
      <table><thead><tr><th>Date</th><th>Reference</th><th>Description</th><th style="text-align:right">Debit</th><th style="text-align:right">Credit</th><th style="text-align:right">Balance</th></tr></thead><tbody>
      ${sec.entries.map((e) => `<tr style="${e.void ? "text-decoration:line-through;color:#999" : ""}"><td>${fmtDate(e.date)}</td><td>${esc(e.ref)}</td><td>${esc(e.description)}${e.void ? " (void)" : ""}</td>
        <td style="text-align:right">${e.debit ? money(e.debit, sec.currency) : ""}</td><td style="text-align:right">${e.credit ? money(e.credit, sec.currency) : ""}</td><td style="text-align:right">${money(e.balance, sec.currency)}</td></tr>`).join("") || `<tr><td colspan="6">No transactions.</td></tr>`}
      </tbody></table>
      <table><tr><th>Total billed</th><td>${money(sec.account.billed, sec.currency)}</td><th>Total paid</th><td>${money(sec.account.paid, sec.currency)}</td></tr>
      <tr><th>Overdue</th><td>${money(sec.account.overdue, sec.currency)}</td><th>Closing balance</th><td><b>${money(sec.account.balance, sec.currency)}</b></td></tr></table>`).join("")}</div>`);
  } catch (err) { handleError(err); }
}

export function reportCardHtml(r) {
  return `<div class="doc">
    ${header("REPORT CARD", esc(r.term))}
    <table><tr><th>Name</th><td>${esc(r.student.name)}</td><th>Admission no.</th><td>${esc(r.student.admission_no)}</td></tr>
    <tr><th>Class</th><td>${esc(r.class)}</td><th>Class teacher</th><td>${esc(r.class_teacher || "—")}</td></tr></table>
    <table><thead><tr><th>Subject</th><th>Assessments</th><th style="text-align:right">Term %</th><th>Grade</th><th>Remark</th><th style="text-align:right">Position</th></tr></thead><tbody>
    ${r.subjects.map((s) => `<tr><td>${esc(s.subject)}</td><td class="muted" style="font-size:12px">${s.exams.map((e) => `${esc(e.exam)}: ${e.score}/${e.max}`).join("<br>")}${s.missing.length ? `<br><span style="color:#c22f2f">Missed: ${s.missing.map(esc).join(", ")}</span>` : ""}</td>
      <td style="text-align:right"><b>${s.percent}</b></td><td><b>${esc(s.grade)}</b></td><td>${esc(s.remark)}</td><td style="text-align:right">${s.position}/${s.out_of}</td></tr>`).join("") || `<tr><td colspan="6">No marks recorded yet.</td></tr>`}
    </tbody></table>
    <table><tr><th>Overall average</th><td><b>${pct(r.average)}</b> (${esc(r.grade || "—")})</td><th>Class position</th><td><b>${r.position ? `${r.position} of ${r.out_of}` : "—"}</b></td></tr>
    <tr><th>Subjects passed</th><td>${r.passed} of ${r.passed + r.failed}</td><th>Attendance</th><td>${pct(r.attendance.rate)} (${r.attendance.absent} absent, ${r.attendance.late} late, ${r.attendance.excused} excused)</td></tr></table>
    <p><b>Comment:</b> ${esc(r.comment)}</p>
    ${(r.fees.by_currency || [r.fees]).some((x) => x.balance > 0) ? `<p class="muted">Fee balance outstanding: ${(r.fees.by_currency || [r.fees]).filter((x) => x.balance > 0).map((x) => money(x.balance, x.currency)).join(" · ")}</p>` : ""}
    <p class="muted" style="margin-top:28px">Class teacher: ____________________ &nbsp;&nbsp; Principal: ____________________</p></div>`;
}

export async function openReportCard(studentId, termId) {
  try {
    const r = await api.get(`/report-card/${studentId}`, { term_id: termId });
    docModal(`Report card · ${r.student.name}`, reportCardHtml(r));
  } catch (err) { handleError(err); }
}

export { badge };
