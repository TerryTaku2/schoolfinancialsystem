"""Double-entry general ledger and financial statements.

Every financial event posts a balanced journal entry automatically:

    Invoice issued      Dr Fees receivable (per student)   Cr Fee income accounts
                        Dr Scholarships & discounts (contra-income) for discount lines
    Extra charge        Dr Fees receivable                 Cr Sundry income
    Payment received    Dr Cash / Bank / Mobile money      Cr Fees receivable
    Expense approved    Dr Expense account                 Cr Accounts payable   (accrual, on expense date)
    Expense paid        Dr Accounts payable                Cr Cash / Bank / Mobile money
    Void (any)          an exact reversing entry dated the day of the void

Currencies: every entry is in one currency (USD or ZWG) and is never converted, so each
currency has a complete, balancing set of books. Statements take a currency; "ALL" shows
both added together in the base currency at the rate on the report date (services/currency.py).

Posted entries are never edited or deleted. Corrections are reversals, so
closed periods stay untouched. Statements are computed from the ledger only,
so they always agree with each other:

    Trial balance         debits = credits
    Balance sheet         assets = liabilities + net assets
    Cash flow             opening cash + net movement = closing cash
"""
from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import func

from decimal import Decimal, ROUND_HALF_UP

from .. import db
from ..models import (Account, Expense, Invoice, JournalEntry, JournalLine, Payment, Setting)
from ..utils import ApiError, audit, money, next_number
from . import currency as fx

# code, name, type, subtype, cash-flow class, description
CHART = [
    ("1000", "Cash on Hand", "asset", "cash", "operating", "Petty cash and cash fee receipts"),
    ("1010", "Bank Account", "asset", "cash", "operating", "Main operating bank account (bank, card and cheque receipts)"),
    ("1020", "Mobile Money Wallet", "asset", "cash", "operating", "Mobile money collections"),
    ("1100", "Student Fees Receivable", "asset", "receivable", "operating", "Amounts invoiced to students and not yet paid"),
    ("1200", "Prepayments", "asset", None, "operating", "Expenses paid in advance"),
    ("1500", "Property, Plant & Equipment", "asset", "fixed_asset", "investing", "Buildings, furniture, vehicles and equipment at cost"),
    ("1590", "Accumulated Depreciation", "asset", "contra_asset", "investing", "Depreciation charged to date on fixed assets"),
    ("2000", "Accounts Payable", "liability", "payable", "operating", "Approved expenses not yet paid"),
    ("2200", "Accrued Expenses", "liability", None, "operating", "Expenses incurred but not yet invoiced"),
    ("2100", "PAYE & AIDS Levy Payable", "liability", "payroll", "operating", "Employees' tax withheld, due to ZIMRA by the 10th of the following month"),
    ("2110", "NSSA Contributions Payable", "liability", "payroll", "operating", "Employee and employer NSSA contributions and WCIF"),
    ("2120", "ZIMDEF Levy Payable", "liability", "payroll", "operating", "Manpower development levy on the wage bill"),
    ("2130", "Pension & Medical Aid Payable", "liability", "payroll", "operating", "Staff contributions withheld for pension funds and medical aid"),
    ("2140", "Other Payroll Deductions Payable", "liability", "payroll", "operating", "Loans, union dues and other staff deductions withheld"),
    ("2150", "Net Salaries Payable", "liability", "payroll", "operating", "Approved payroll not yet paid to staff"),
    ("2500", "Loans Payable", "liability", None, "financing", "Borrowings"),
    ("3000", "Accumulated Fund", "equity", None, "financing", "Opening net assets / capital contributed"),
    ("4000", "Tuition Fees", "income", None, "operating", None),
    ("4100", "Development Levy", "income", None, "operating", None),
    ("4200", "Examination Fees", "income", None, "operating", None),
    ("4300", "ICT & Laboratory Fees", "income", None, "operating", None),
    ("4400", "Other Fee Income", "income", None, "operating", "Default account for fee items without a specific account"),
    ("4800", "Sundry Income", "income", None, "operating", "One-off charges such as fines and lost books"),
    ("4900", "Scholarships & Discounts", "income", "contra_income", "operating", "Fee reductions granted (reduces income)"),
    ("4950", "Donations & Grants", "income", None, "operating", None),
    ("4960", "Gain on Disposal of Assets", "income", None, "investing", "Proceeds above net book value when an asset is sold"),
    ("4970", "Interest Income", "income", None, "operating", "Interest earned on bank balances"),
    ("5000", "Salaries & Wages", "expense", None, "operating", None),
    ("5010", "Employer Payroll Contributions", "expense", None, "operating", "Employer NSSA, WCIF and ZIMDEF levy"),
    ("5100", "Utilities", "expense", None, "operating", None),
    ("5200", "Repairs & Maintenance", "expense", None, "operating", None),
    ("5300", "Teaching Supplies & Stationery", "expense", None, "operating", None),
    ("5400", "Transport", "expense", None, "operating", None),
    ("5500", "Catering", "expense", None, "operating", None),
    ("5600", "Events & Activities", "expense", None, "operating", None),
    ("5700", "IT & Communications", "expense", None, "operating", None),
    ("5800", "General & Administrative", "expense", None, "operating", None),
    ("5810", "Bank Charges", "expense", None, "operating", "Bank and mobile money fees, from bank reconciliations"),
    ("5900", "Depreciation", "expense", None, "operating", "Non-cash charge for wear of fixed assets"),
    ("5950", "Loss on Disposal of Assets", "expense", None, "investing", "Net book value written off above any sale proceeds"),
]
RECEIVABLE, PAYABLE, SUNDRY, DISCOUNTS, DEFAULT_FEE = "1100", "2000", "4800", "4900", "4400"
METHOD_ACCOUNT = {"cash": "1000", "bank": "1010", "card": "1010", "cheque": "1010", "mobile": "1020"}
EXPENSE_ACCOUNT = {"Salaries": "5000", "Utilities": "5100", "Maintenance": "5200", "Supplies": "5300",
                   "Transport": "5400", "Food": "5500", "Events": "5600", "IT": "5700", "Other": "5800"}
FEE_ACCOUNT_RULES = [("tuition", "4000"), ("levy", "4100"), ("development", "4100"),
                     ("exam", "4200"), ("ict", "4300"), ("lab", "4300")]
SYSTEM_SOURCES = ("invoice", "charge", "payment", "expense_accrual", "expense_payment")


# --------------------------------------------------------------------------- #
# Chart of accounts & settings
# --------------------------------------------------------------------------- #
def ensure_chart():
    existing = {a.code for a in Account.query.all()}
    added = False
    for code, name, typ, sub, cf, desc in CHART:
        if code not in existing:
            db.session.add(Account(code=code, name=name, type=typ, subtype=sub, cash_flow=cf,
                                   description=desc, is_system=True, currency=fx.base() if sub == "cash" else None))
            added = True
    if added:
        db.session.commit()


def acct(code):
    a = Account.query.filter_by(code=code).first()
    if a is None:
        raise ApiError(f"Account {code} is missing from the chart of accounts", 500)
    return a


def fee_account_for(name):
    lowered = (name or "").lower()
    for word, code in FEE_ACCOUNT_RULES:
        if word in lowered:
            return acct(code)
    return acct(DEFAULT_FEE)


def cash_accounts(currency=None):
    q = Account.query.filter_by(subtype="cash")
    if currency and currency != "ALL":
        q = q.filter(Account.currency == currency)
    return q.order_by(Account.code).all()


def get_lock_date():
    s = db.session.get(Setting, "books_locked_until")
    return date.fromisoformat(s.value) if s and s.value else None


def set_lock_date(d):
    s = db.session.get(Setting, "books_locked_until") or Setting(key="books_locked_until")
    s.value = d.isoformat() if d else None
    db.session.add(s)


def check_open(d):
    lock = get_lock_date()
    if lock and d <= lock:
        raise ApiError(f"The books are closed up to {lock.isoformat()}. Post this in an open period "
                       f"(after {lock.isoformat()}) or ask an administrator to reopen the period.", 409)


# --------------------------------------------------------------------------- #
# Posting
# --------------------------------------------------------------------------- #
def post(on_date, description, lines, source_type="manual", source_id=None, reference=None,
         user_id=None, reversal_of=None, force=False, currency=None):
    """Validate and post a balanced journal entry in one currency.

    lines: [{"account": Account, "debit": cents, "credit": cents, "memo": str, "student_id": int}]
    `force` skips the period lock and active-account checks (used when rebuilding history).
    """
    currency = currency or fx.base()
    lines = [l for l in lines if (l.get("debit") or 0) or (l.get("credit") or 0)]
    for l in lines:
        held = l["account"].currency
        if held and held != currency:
            raise ApiError(f"{l['account'].name} holds {held}; this entry is in {currency}. "
                           f"Use the {currency} account instead.")
    if len(lines) < 2:
        raise ApiError("A journal entry needs at least two lines")
    dr = cr = 0
    for l in lines:
        d, c = int(l.get("debit") or 0), int(l.get("credit") or 0)
        if d < 0 or c < 0 or (d and c):
            raise ApiError("Each line must have either a debit or a credit, not both, and no negatives")
        if not force and not l["account"].active:
            raise ApiError(f"Account {l['account'].code} {l['account'].name} is inactive")
        dr, cr = dr + d, cr + c
    if dr != cr:
        raise ApiError(f"Entry does not balance: debits {money(dr)} vs credits {money(cr)}")
    if not force:
        check_open(on_date)
    entry = JournalEntry(entry_no=next_number("journal", "JE-", 6), date=on_date,
                         description=description[:200], reference=reference, source_type=source_type,
                         source_id=source_id, created_by=user_id, reversal_of=reversal_of, currency=currency)
    for l in lines:
        entry.lines.append(JournalLine(account_id=l["account"].id, debit_cents=int(l.get("debit") or 0),
                                       credit_cents=int(l.get("credit") or 0), memo=(l.get("memo") or "")[:120] or None,
                                       student_id=l.get("student_id")))
    db.session.add(entry)
    db.session.flush()
    return entry


def reverse(entry, on_date, reason, user_id=None, force=False):
    if entry.reversal_of_id:
        raise ApiError("A reversing entry cannot itself be reversed")
    if entry.reversals:
        raise ApiError(f"{entry.entry_no} has already been reversed")
    lines = [{"account": l.account, "debit": l.credit_cents, "credit": l.debit_cents,
              "memo": l.memo, "student_id": l.student_id} for l in entry.lines]
    return post(on_date, f"Reversal of {entry.entry_no}: {reason}", lines, source_type=entry.source_type,
                source_id=entry.source_id, reference=entry.reference, user_id=user_id,
                reversal_of=entry, force=force, currency=entry.currency or fx.base())


def reverse_source(source_types, source_id, on_date, reason, force=False):
    entries = (JournalEntry.query.filter(JournalEntry.source_type.in_(source_types),
                                         JournalEntry.source_id == source_id,
                                         JournalEntry.reversal_of_id.is_(None)).all())
    for e in entries:
        if not e.reversals:
            reverse(e, on_date, reason, force=force)


def _uid():
    from flask import has_request_context
    from flask_login import current_user
    return current_user.id if has_request_context() and current_user.is_authenticated else None


def post_invoice(invoice, force=False):
    lines, net = [], 0
    for line in invoice.lines:
        account = line_account(line)
        if line.amount_cents >= 0:
            lines.append({"account": account, "credit": line.amount_cents, "memo": line.description})
        else:
            lines.append({"account": account, "debit": -line.amount_cents, "memo": line.description})
        net += line.amount_cents
    if net > 0:
        lines.insert(0, {"account": acct(RECEIVABLE), "debit": net, "student_id": invoice.student_id,
                         "memo": invoice.student.name})
    if sum(1 for l in lines if l.get("debit") or l.get("credit")) < 2:
        return None  # e.g. a fully-discounted invoice with nothing to post
    return post(invoice.issue_date, f"Invoice {invoice.invoice_no} - {invoice.student.name} ({invoice.term.label})",
                lines, "invoice", invoice.id, invoice.invoice_no, _uid(), force=force, currency=fx.of(invoice))


def line_account(line):
    if line.account_id:
        return db.session.get(Account, line.account_id)
    if line.amount_cents < 0:
        a = acct(DISCOUNTS)
    elif line.fee_item_id:
        from ..models import FeeItem
        item = db.session.get(FeeItem, line.fee_item_id)
        a = item.account if item and item.account else fee_account_for(line.description)
    else:
        a = acct(SUNDRY)
    line.account_id = a.id
    return a


def post_charge(invoice, line, on_date, force=False):
    return post(on_date, f"Charge on {invoice.invoice_no}: {line.description}", [
        {"account": acct(RECEIVABLE), "debit": line.amount_cents, "student_id": invoice.student_id, "memo": invoice.student.name},
        {"account": line_account(line), "credit": line.amount_cents, "memo": line.description},
    ], "charge", invoice.id, invoice.invoice_no, _uid(), force=force, currency=fx.of(invoice))


def post_payment(payment, force=False):
    return post(payment.paid_on, f"Receipt {payment.receipt_no} - {payment.student.name} ({payment.method})", [
        {"account": method_account(payment.method, fx.of(payment)), "debit": payment.amount_cents, "memo": payment.reference},
        {"account": acct(RECEIVABLE), "credit": payment.amount_cents, "student_id": payment.student_id, "memo": payment.student.name},
    ], "payment", payment.id, payment.receipt_no, _uid(), force=force, currency=fx.of(payment))


def method_account(method, currency):
    """Cash, bank or mobile money account for a payment method, in the payment's currency."""
    return fx.cash_account(METHOD_ACCOUNT[method], currency)


def expense_account(expense):
    return acct(EXPENSE_ACCOUNT.get(expense.category, "5800"))


def post_expense_accrual(expense, force=False):
    # Accrual basis: the cost belongs to the period in which it was incurred.
    return post(expense.expense_date, f"Expense approved: {expense.description}", [
        {"account": expense_account(expense), "debit": expense.amount_cents, "memo": expense.vendor},
        {"account": acct(PAYABLE), "credit": expense.amount_cents, "memo": expense.vendor},
    ], "expense_accrual", expense.id, f"EXP-{expense.id}", _uid(), force=force, currency=fx.of(expense))


def post_expense_payment(expense, cash_account, on_date, force=False):
    if cash_account.subtype != "cash":
        raise ApiError("Expenses must be paid from a cash, bank or mobile money account")
    if fx.of(cash_account) != fx.of(expense):
        raise ApiError(f"This expense is in {fx.of(expense)}; pay it from a {fx.of(expense)} account")
    if not force:
        available = balance(cash_account, on_date)
        if available < expense.amount_cents:
            raise ApiError(f"Insufficient funds in {cash_account.name}: balance on {on_date.isoformat()} is "
                           f"{money(available)}, expense is {money(expense.amount_cents)}", 409)
    return post(on_date, f"Expense paid: {expense.description}", [
        {"account": acct(PAYABLE), "debit": expense.amount_cents, "memo": expense.vendor},
        {"account": cash_account, "credit": expense.amount_cents, "memo": expense.vendor},
    ], "expense_payment", expense.id, f"EXP-{expense.id}", _uid(), force=force, currency=fx.of(expense))


# --------------------------------------------------------------------------- #
# Balances
# --------------------------------------------------------------------------- #
def _scale(cents, cur, view="ALL"):
    """In the combined view ("ALL"), cents in `cur` expressed in the base currency at the
    translation date. A single-currency view is never converted."""
    if view != "ALL" or cur == fx.base():
        return cents
    f = fx.factor(cur, fx.base(), fx.translate_on())
    return int((Decimal(cents) * f).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def movements(start=None, end=None, currency=None):
    """{account_id: (debits, credits)} for entries dated within [start, end] in one currency.

    currency "ALL" adds the currencies together in the base currency (see currency.translation).
    """
    currency = currency or fx.base()
    q = (db.session.query(JournalLine.account_id, JournalEntry.currency,
                          func.coalesce(func.sum(JournalLine.debit_cents), 0),
                          func.coalesce(func.sum(JournalLine.credit_cents), 0))
         .join(JournalEntry))
    if currency != "ALL":
        q = q.filter(JournalEntry.currency == currency)
    if start:
        q = q.filter(JournalEntry.date >= start)
    if end:
        q = q.filter(JournalEntry.date <= end)
    out = {}
    for aid, cur, d, c in q.group_by(JournalLine.account_id, JournalEntry.currency):
        dr, cr = out.get(aid, (0, 0))
        out[aid] = (dr + _scale(int(d), cur or fx.base(), currency), cr + _scale(int(c), cur or fx.base(), currency))
    return out


def natural(account, dr, cr):
    return dr - cr if account.normal_debit else cr - dr


def balance(account, as_at=None, currency=None):
    """Balance of an account; cash accounts always in their own currency."""
    cur = account.currency or currency or fx.base()
    dr, cr = movements(end=as_at, currency=cur).get(account.id, (0, 0))
    return natural(account, dr, cr)


def balances_by_currency(account, as_at=None):
    return {cur: balance(account, as_at, cur) for cur in fx.enabled() if not account.currency or account.currency == cur}


def _accounts():
    return Account.query.order_by(Account.code).all()


def receivable_split(as_at, currency=None):
    """Gross receivables vs fees received in advance, from the per-student sub-ledger.

    Worked out per currency: a student who owes USD and is in credit in ZWG has both.
    """
    currency = currency or fx.base()
    rec = acct(RECEIVABLE)
    q = (db.session.query(JournalLine.student_id, JournalEntry.currency,
                          func.sum(JournalLine.debit_cents - JournalLine.credit_cents))
         .join(JournalEntry).filter(JournalLine.account_id == rec.id, JournalEntry.date <= as_at))
    if currency != "ALL":
        q = q.filter(JournalEntry.currency == currency)
    owed = advance = 0
    for sid, cur, net in q.group_by(JournalLine.student_id, JournalEntry.currency):
        net = int(net or 0)
        if sid is None or net >= 0:
            owed += _scale(net, cur or fx.base(), currency)
        else:
            advance += _scale(-net, cur or fx.base(), currency)
    return owed, advance


# --------------------------------------------------------------------------- #
# Statements
# --------------------------------------------------------------------------- #
def trial_balance(as_at, currency=None):
    currency = currency or fx.base()
    with fx.translation(as_at):
        return _trial_balance(as_at, currency)


def _tolerance(currency, n):
    # Combined figures are rounded per account when translated, so allow those cents.
    return n if currency == "ALL" else 0


def _trial_balance(as_at, currency):
    mv = movements(end=as_at, currency=currency)
    rows, tdr, tcr = [], 0, 0
    for a in _accounts():
        dr, cr = mv.get(a.id, (0, 0))
        if not dr and not cr:
            continue
        net = dr - cr
        rows.append({"account_id": a.id, "code": a.code, "name": a.name, "type": a.type,
                     "debit": money(max(net, 0)), "credit": money(max(-net, 0))})
        tdr, tcr = tdr + max(net, 0), tcr + max(-net, 0)
    return {"as_at": as_at.isoformat(), "currency": currency, "rows": rows, "total_debit": money(tdr),
            "total_credit": money(tcr), "balanced": abs(tdr - tcr) <= _tolerance(currency, len(rows))}


def _period_income(start, end, currency):
    with fx.translation(end):
        mv = movements(start, end, currency)
    revenue, deductions, expenses = [], [], []
    for a in _accounts():
        dr, cr = mv.get(a.id, (0, 0))
        amt = natural(a, dr, cr)
        if a.type == "income":
            (deductions if a.subtype == "contra_income" else revenue).append((a, amt))
        elif a.type == "expense":
            expenses.append((a, amt))
    return revenue, deductions, expenses


def income_statement(start, end, compare=True, currency=None):
    """Statement of financial performance (income & expenditure) for a period."""
    currency = currency or fx.base()
    days = (end - start).days + 1
    p_end = start - timedelta(days=1)
    p_start = p_end - timedelta(days=days - 1)
    cur = _period_income(start, end, currency)
    prior = _period_income(p_start, p_end, currency) if compare else None

    def section(idx, title):
        rows = []
        for i, (a, amt) in enumerate(cur[idx]):
            pamt = prior[idx][i][1] if prior else None
            if amt or pamt:
                rows.append({"account_id": a.id, "code": a.code, "name": a.name, "amount": money(amt),
                             "prior": money(pamt) if prior else None})
        total = sum(a for _, a in cur[idx])
        ptotal = sum(a for _, a in prior[idx]) if prior else None
        return {"title": title, "rows": rows, "total": money(total), "prior_total": money(ptotal) if prior else None,
                "_t": total, "_p": ptotal}

    rev, ded, exp = section(0, "Revenue"), section(1, "Less: scholarships & discounts"), section(2, "Expenditure")
    net_rev, p_net_rev = rev["_t"] - ded["_t"], (rev["_p"] - ded["_p"]) if prior else None
    surplus = net_rev - exp["_t"]
    p_surplus = (p_net_rev - exp["_p"]) if prior else None
    for s in (rev, ded, exp):
        s.pop("_t"), s.pop("_p")
    return {"start": start.isoformat(), "end": end.isoformat(), "currency": currency,
            "prior_start": p_start.isoformat() if compare else None, "prior_end": p_end.isoformat() if compare else None,
            "revenue": rev, "deductions": ded, "net_revenue": money(net_rev),
            "prior_net_revenue": money(p_net_rev) if prior else None,
            "expenses": exp, "surplus": money(surplus), "prior_surplus": money(p_surplus) if prior else None,
            "margin": round(surplus / net_rev * 100, 1) if net_rev else None}


def _position(as_at, currency):
    with fx.translation(as_at):
        mv = movements(end=as_at, currency=currency)
        owed, advance = receivable_split(as_at, currency)
    bal = {a.id: natural(a, *mv.get(a.id, (0, 0))) for a in _accounts()}
    accts = _accounts()
    cur_assets, nc_assets, liabilities, equity = [], [], [], []
    for a in accts:
        b = bal[a.id]
        if a.type == "asset":
            if a.subtype == "receivable":
                cur_assets.append((a.code, "Student fees receivable", owed))
            elif a.subtype in ("fixed_asset", "contra_asset"):
                nc_assets.append((a.code, a.name, b if a.subtype == "fixed_asset" else -b))
            else:
                cur_assets.append((a.code, a.name, b))
        elif a.type == "liability":
            liabilities.append((a.code, a.name, b))
        elif a.type == "equity":
            equity.append((a.code, a.name, b))
    liabilities.append(("", "Fees received in advance", advance))
    # Net of all income less expenses to date: credits minus debits on every P&L account
    # (this handles contra-income such as scholarships correctly).
    surplus = sum(mv.get(a.id, (0, 0))[1] - mv.get(a.id, (0, 0))[0] for a in accts if a.type in ("income", "expense"))
    equity.append(("", "Accumulated surplus / (deficit)", surplus))
    return {"current_assets": cur_assets, "non_current_assets": nc_assets,
            "liabilities": liabilities, "equity": equity}


def balance_sheet(as_at, compare_to=None, currency=None):
    """Statement of financial position at a date, optionally with a comparative date."""
    currency = currency or fx.base()
    cur = _position(as_at, currency)
    prev = _position(compare_to, currency) if compare_to else None

    def section(key, title):
        rows = []
        for i, (code, name, amt) in enumerate(cur[key]):
            p = prev[key][i][2] if prev else None
            if amt or p:
                rows.append({"code": code, "name": name, "amount": money(amt), "prior": money(p) if prev else None})
        t = sum(r[2] for r in cur[key])
        pt = sum(r[2] for r in prev[key]) if prev else None
        return {"title": title, "rows": rows, "total": money(t), "prior_total": money(pt) if prev else None, "_t": t, "_p": pt}

    ca, nca = section("current_assets", "Current assets"), section("non_current_assets", "Non-current assets")
    li, eq = section("liabilities", "Liabilities"), section("equity", "Net assets / equity")
    ta, tle = ca["_t"] + nca["_t"], li["_t"] + eq["_t"]
    pta = (ca["_p"] + nca["_p"]) if prev else None
    ptle = (li["_p"] + eq["_p"]) if prev else None
    out = {"as_at": as_at.isoformat(), "currency": currency, "compare_to": compare_to.isoformat() if compare_to else None,
           "current_assets": ca, "non_current_assets": nca, "liabilities": li, "equity": eq,
           "total_assets": money(ta), "total_liabilities_equity": money(tle),
           "prior_total_assets": money(pta) if prev else None,
           "prior_total_liabilities_equity": money(ptle) if prev else None,
           "balanced": abs(ta - tle) <= _tolerance(currency, 40) and (not prev or abs(pta - ptle) <= _tolerance(currency, 40)),
           "current_ratio": round(ca["_t"] / li["_t"], 2) if li["_t"] else None}
    for s in (ca, nca, li, eq):
        s.pop("_t"), s.pop("_p")
    return out


def cash_flow(start, end, currency=None):
    currency = currency or fx.base()
    with fx.translation(end):
        return _cash_flow(start, end, currency)


def _cash_flow(start, end, currency):
    """Cash flow statement, direct method.

    Each entry touching a cash account is classified by its counterpart lines: the
    cash effect of a counterpart line is (credit - debit) on that line, and these
    sum exactly to the entry's net cash movement because entries balance.
    Transfers between cash accounts net to zero and drop out.
    """
    cash = cash_accounts(currency)
    cash_ids = {a.id for a in cash}
    q = JournalEntry.query.filter(JournalEntry.date >= start, JournalEntry.date <= end)
    if currency != "ALL":
        q = q.filter(JournalEntry.currency == currency)
    entries = q.join(JournalLine).filter(JournalLine.account_id.in_(cash_ids or {-1})).distinct().all()
    buckets = {"operating": defaultdict(int), "investing": defaultdict(int), "financing": defaultdict(int)}
    for e in entries:
        for l in e.lines:
            if l.account_id in cash_ids:
                continue
            a = l.account
            effect = _scale(l.credit_cents - l.debit_cents, e.currency or fx.base(), currency)
            if a.subtype == "receivable":
                label = "Fees received from students"
            elif a.subtype in ("payable", "payroll") or a.type == "expense":
                label = "Payments to suppliers and employees"
            else:
                label = a.name
            buckets[a.cash_flow][label] += effect

    def section(key, title):
        rows = [{"name": k, "amount": money(v)} for k, v in sorted(buckets[key].items(), key=lambda kv: -kv[1]) if v]
        return {"title": title, "rows": rows, "total": money(sum(buckets[key].values())), "_t": sum(buckets[key].values())}

    op = section("operating", "Cash flows from operating activities")
    inv = section("investing", "Cash flows from investing activities")
    fin = section("financing", "Cash flows from financing activities")
    net = op["_t"] + inv["_t"] + fin["_t"]
    opening = sum(_scale(balance(a, start - timedelta(days=1)), fx.of(a), currency) for a in cash)
    closing = sum(_scale(balance(a, end), fx.of(a), currency) for a in cash)
    for s in (op, inv, fin):
        s.pop("_t")
    return {"start": start.isoformat(), "end": end.isoformat(), "currency": currency,
            "operating": op, "investing": inv, "financing": fin,
            "net_change": money(net), "opening_cash": money(opening), "closing_cash": money(closing),
            "by_account": [{"code": a.code, "name": a.name, "currency": fx.of(a),
                            "opening": money(balance(a, start - timedelta(days=1))),
                            "closing": money(balance(a, end))} for a in cash],
            "reconciled": abs(opening + net - closing) <= _tolerance(currency, len(entries) + len(cash))}


def general_ledger(account, start, end, currency=None):
    currency = account.currency or currency or fx.base()
    opening = balance(account, start - timedelta(days=1), currency)
    lines = (JournalLine.query.join(JournalEntry).filter(JournalLine.account_id == account.id,
                                                         JournalEntry.currency == currency,
                                                         JournalEntry.date >= start, JournalEntry.date <= end)
             .order_by(JournalEntry.date, JournalEntry.id).all())
    running, rows = opening, []
    for l in lines:
        running += natural(account, l.debit_cents, l.credit_cents)
        e = l.entry
        rows.append({"date": e.date.isoformat(), "entry_id": e.id, "entry_no": e.entry_no, "reference": e.reference,
                     "description": e.description, "memo": l.memo, "debit": money(l.debit_cents),
                     "credit": money(l.credit_cents), "balance": money(running)})
    return {"account": account_dict(account), "currency": currency, "start": start.isoformat(), "end": end.isoformat(),
            "opening": money(opening), "closing": money(running), "rows": rows,
            "total_debit": money(sum(l.debit_cents for l in lines)), "total_credit": money(sum(l.credit_cents for l in lines))}


def account_dict(a, bal=None):
    d = {"id": a.id, "code": a.code, "name": a.name, "type": a.type, "subtype": a.subtype, "cash_flow": a.cash_flow,
         "currency": a.currency,
         "is_system": a.is_system, "active": a.active, "description": a.description,
         "normal_balance": "debit" if a.normal_debit else "credit"}
    if bal is not None:
        d["balance"] = money(bal)
    return d


def entry_dict(e, detail=False):
    d = {"id": e.id, "entry_no": e.entry_no, "date": e.date.isoformat(), "description": e.description,
         "currency": e.currency or fx.base(),
         "reference": e.reference, "source_type": e.source_type, "source_id": e.source_id,
         "amount": money(e.total_cents), "reversal_of": e.reversal_of.entry_no if e.reversal_of else None,
         "reversed_by": e.reversals[0].entry_no if e.reversals else None,
         "author": e.author.full_name if e.author else "System"}
    if detail:
        d["lines"] = [{"code": l.account.code, "account": l.account.name, "debit": money(l.debit_cents),
                       "credit": money(l.credit_cents), "memo": l.memo} for l in e.lines]
    return d


# --------------------------------------------------------------------------- #
# Rebuild from source documents (upgrades & repairs)
# --------------------------------------------------------------------------- #
def rebuild_ledger():
    """Delete all system-generated entries and repost them from invoices, payments and expenses.

    Manual journals are kept. Safe to run any time; used when upgrading a
    database that pre-dates the ledger.
    """
    ensure_chart()
    from ..models import BankStatementLine, FeeItem
    if BankStatementLine.query.filter(BankStatementLine.journal_line_id.isnot(None)).first():
        raise ApiError("Bank reconciliations are matched to the current postings. Rebuilding would undo them, "
                       "so the ledger can't be rebuilt once reconciliation has started.", 409)
    for item in FeeItem.query.filter(FeeItem.account_id.is_(None)):
        item.account_id = fee_account_for(item.name).id
    for e in JournalEntry.query.filter(JournalEntry.source_type.in_(SYSTEM_SOURCES)).all():
        db.session.delete(e)
    db.session.flush()

    events = []  # (date, order, callable); order puts postings before same-day reversals
    for inv in Invoice.query.all():
        events.append((inv.issue_date, 0, inv.id, lambda inv=inv: post_invoice(inv, force=True)))
        if inv.void:
            when = inv.voided_at or inv.issue_date
            events.append((when, 1, inv.id, lambda inv=inv, w=when: reverse_source(("invoice", "charge"), inv.id, w,
                                                                                    inv.void_reason or "void", force=True)))
    for p in Payment.query.all():
        events.append((p.paid_on, 0, p.id, lambda p=p: post_payment(p, force=True)))
        if p.void:
            when = p.voided_at or p.paid_on
            events.append((when, 1, p.id, lambda p=p, w=when: reverse_source(("payment",), p.id, w,
                                                                              p.void_reason or "void", force=True)))
    for x in Expense.query.filter(Expense.status.in_(("approved", "paid"))).all():
        events.append((x.expense_date, 0, x.id, lambda x=x: post_expense_accrual(x, force=True)))
        if x.status == "paid":
            when = x.paid_at or x.expense_date
            account = db.session.get(Account, x.paid_from_account_id) if x.paid_from_account_id else acct("1010")
            events.append((when, 0, x.id, lambda x=x, w=when, a=account: post_expense_payment(x, a, w, force=True)))
    for _, _, _, fn in sorted(events, key=lambda ev: (ev[0], ev[1], ev[2])):
        fn()
    audit("rebuild", "ledger", None, f"{len(events)} postings regenerated")
    return len(events)
