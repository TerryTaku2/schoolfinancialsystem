"""Chart of accounts, journals, general ledger, period lock and financial statements."""
import re
from datetime import date

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required
from sqlalchemy import or_

from .. import db
from ..models import ACCOUNT_TYPES, CASH_FLOW_CLASSES, AcademicYear, Account, JournalEntry
from ..services import currency as fx
from ..services import ledger
from ..utils import (ApiError, money, audit, body, clean_str, get_or_404, paginate_args, parse_date, parse_int,
                     require, permission_required, to_cents)

bp = Blueprint("accounting", __name__)

CODE_PREFIX = {"asset": "1", "liability": "2", "equity": "3", "income": "4", "expense": "5"}
USER_SUBTYPES = (None, "cash", "fixed_asset", "contra_asset", "contra_income")


def _fiscal_year():
    """Default reporting period: the current academic year, up to today."""
    y = AcademicYear.query.filter_by(is_current=True).first()
    today = date.today()
    if not y:
        return date(today.year, 1, 1), today
    return y.start_date, min(y.end_date, today)


def _currency_arg(allow_all=True):
    cur = (request.args.get("currency") or fx.base()).upper()
    if cur == "ALL" and allow_all:
        return cur
    if cur not in fx.enabled():
        raise ApiError("Unknown currency", fields={"currency": "Invalid"})
    return cur


def _period():
    fy_start, fy_end = _fiscal_year()
    start = parse_date(request.args.get("from"), "from", required=False) or fy_start
    end = parse_date(request.args.get("to"), "to", required=False) or fy_end
    if end < start:
        raise ApiError("The end date must be on or after the start date")
    return start, end


# --------------------------------------------------------------------------- #
# Chart of accounts
# --------------------------------------------------------------------------- #
@bp.get("/accounting/accounts")
@permission_required("accounting.view", "fees.manage", "payments.manage", "expenses.manage", "payroll.manage", "assets.manage")
def list_accounts():
    as_at = parse_date(request.args.get("as_at"), "as_at", required=False) or date.today()
    cur = _currency_arg(allow_all=False)
    mv = {c: ledger.movements(end=as_at, currency=c) for c in fx.enabled()}
    items = []
    for a in Account.query.order_by(Account.code):
        held = [a.currency] if a.currency else fx.enabled()
        balances = {c: money(ledger.natural(a, *mv[c].get(a.id, (0, 0)))) for c in held if c in mv}
        d = ledger.account_dict(a, ledger.natural(a, *mv[a.currency or cur].get(a.id, (0, 0)))
                                if (a.currency or cur) in mv else 0)
        d["balances"] = balances
        items.append(d)
    return jsonify(items=items, as_at=as_at.isoformat(), currency=cur)


@bp.post("/accounting/accounts")
@permission_required("accounting.approve")
def create_account():
    data = body()
    require(data, "code", "name", "type")
    code, typ = str(data["code"]).strip(), data["type"]
    if typ not in ACCOUNT_TYPES:
        raise ApiError("Invalid account type", fields={"type": "Invalid"})
    if not re.fullmatch(r"\d{4}", code) or code[0] != CODE_PREFIX[typ]:
        raise ApiError(f"{typ.title()} account codes are 4 digits starting with {CODE_PREFIX[typ]}",
                       fields={"code": "Invalid code"})
    if Account.query.filter_by(code=code).first():
        raise ApiError("Account code already exists", fields={"code": "Duplicate"})
    subtype = data.get("subtype") or None
    if subtype not in USER_SUBTYPES:
        raise ApiError("Invalid account subtype", fields={"subtype": "Invalid"})
    allowed = {"cash": "asset", "fixed_asset": "asset", "contra_asset": "asset", "contra_income": "income"}
    if subtype and allowed[subtype] != typ:
        raise ApiError(f"Subtype {subtype} only applies to {allowed[subtype]} accounts", fields={"subtype": "Mismatch"})
    # A cash, bank or mobile money account holds a single currency.
    held = fx.pick(data.get("currency")) if subtype == "cash" else None
    cf = data.get("cash_flow") or ("investing" if subtype in ("fixed_asset", "contra_asset") else "operating")
    if cf not in CASH_FLOW_CLASSES:
        raise ApiError("Invalid cash flow class", fields={"cash_flow": "Invalid"})
    a = Account(code=code, name=clean_str(data["name"], 80), type=typ, subtype=subtype, cash_flow=cf, currency=held,
                description=clean_str(data.get("description"), 200))
    db.session.add(a)
    db.session.flush()
    audit("create", "account", a.id, f"{a.code} {a.name}")
    db.session.commit()
    return jsonify(ledger.account_dict(a)), 201


@bp.put("/accounting/accounts/<int:aid>")
@permission_required("accounting.approve")
def update_account(aid):
    a = get_or_404(Account, aid, "Account")
    data = body()
    if data.get("name"):
        a.name = clean_str(data["name"], 80)
    if "description" in data:
        a.description = clean_str(data["description"], 200)
    if "cash_flow" in data and data["cash_flow"] != a.cash_flow:
        if a.is_system:
            raise ApiError("The cash flow class of a system account cannot be changed")
        if data["cash_flow"] not in CASH_FLOW_CLASSES:
            raise ApiError("Invalid cash flow class")
        a.cash_flow = data["cash_flow"]
    if "active" in data and bool(data["active"]) != a.active:
        if a.is_system:
            raise ApiError("System accounts are used by automatic postings and cannot be deactivated")
        if not data["active"] and any(ledger.balances_by_currency(a).values()):
            raise ApiError("Only accounts with a zero balance (in every currency) can be deactivated. Transfer the balance first.")
        a.active = bool(data["active"])
    audit("update", "account", a.id, f"{a.code} {a.name}")
    db.session.commit()
    return jsonify(ledger.account_dict(a))


# --------------------------------------------------------------------------- #
# Journals
# --------------------------------------------------------------------------- #
@bp.get("/accounting/journals")
@permission_required("accounting.view")
def list_journals():
    start, end = _period()
    q = JournalEntry.query.filter(JournalEntry.date >= start, JournalEntry.date <= end)
    src = request.args.get("source")
    if src:
        q = q.filter(JournalEntry.source_type == src)
    if request.args.get("currency"):
        q = q.filter(JournalEntry.currency == request.args["currency"])
    if request.args.get("q"):
        like = f"%{request.args['q'].strip()}%"
        q = q.filter(or_(JournalEntry.description.ilike(like), JournalEntry.reference.ilike(like),
                         JournalEntry.entry_no.ilike(like)))
    page, per = paginate_args()
    total = q.count()
    rows = q.order_by(JournalEntry.date.desc(), JournalEntry.id.desc()).offset((page - 1) * per).limit(per).all()
    return jsonify(items=[ledger.entry_dict(e) for e in rows], total=total, page=page, per_page=per,
                   start=start.isoformat(), end=end.isoformat())


@bp.get("/accounting/journals/<int:eid>")
@permission_required("accounting.view")
def get_journal(eid):
    return jsonify(ledger.entry_dict(get_or_404(JournalEntry, eid, "Journal entry"), detail=True))


@bp.post("/accounting/journals")
@permission_required("accounting.manage")
def create_journal():
    """Manual journal: opening balances, depreciation, asset purchases, transfers, donations..."""
    data = body()
    require(data, "date", "description", "lines")
    cur = fx.pick(data.get("currency"))
    on = parse_date(data["date"], "date")
    if on > date.today():
        raise ApiError("Journal entries cannot be dated in the future", fields={"date": "Future date"})
    lines = []
    for i, raw in enumerate(data["lines"]):
        a = get_or_404(Account, parse_int(raw.get("account_id"), f"line {i + 1} account"), "Account")
        # Sub-ledger control accounts only move through their source documents.
        if a.currency and a.currency != cur:
            raise ApiError(f"{a.code} {a.name} holds {a.currency}; this journal is in {cur}")
        if a.subtype in ("receivable", "payable", "payroll"):
            raise ApiError(f"{a.code} {a.name} is a control account. Use invoices/payments, expenses or payroll instead.")
        dr = to_cents(raw.get("debit") or 0, "debit", allow_zero=True)
        cr = to_cents(raw.get("credit") or 0, "credit", allow_zero=True)
        lines.append({"account": a, "debit": dr, "credit": cr, "memo": clean_str(raw.get("memo"), 120)})
    if sum(l["debit"] for l in lines) != sum(l["credit"] for l in lines):
        raise ApiError("Debits and credits must be equal")
    if any(l["account"].subtype == "cash" and l["credit"] for l in lines):
        for l in lines:
            if l["account"].subtype == "cash" and l["credit"]:
                available = ledger.balance(l["account"], on)
                if available < l["credit"]:
                    raise ApiError(f"Insufficient funds in {l['account'].name} on {on.isoformat()}", 409)
    entry = ledger.post(on, clean_str(data["description"], 200), lines, "manual", None,
                        clean_str(data.get("reference"), 60), current_user.id, currency=cur)
    audit("create", "journal", entry.id, f"{entry.entry_no} {entry.description}")
    db.session.commit()
    return jsonify(ledger.entry_dict(entry, detail=True)), 201


@bp.post("/accounting/journals/<int:eid>/reverse")
@permission_required("accounting.manage")
def reverse_journal(eid):
    e = get_or_404(JournalEntry, eid, "Journal entry")
    if e.source_type != "manual":
        raise ApiError("Automatic entries are corrected by voiding their source document "
                       "(invoice, receipt, expense, payroll, asset or depreciation run)")
    data = body()
    reason = clean_str(data.get("reason"), 150)
    if not reason or len(reason) < 5:
        raise ApiError("A reason (5+ characters) is required", fields={"reason": "Required"})
    on = parse_date(data.get("date"), "date", required=False) or date.today()
    if on > date.today() or on < e.date:
        raise ApiError("Reversal date must be between the original entry date and today")
    r = ledger.reverse(e, on, reason, current_user.id)
    audit("reverse", "journal", e.id, f"{e.entry_no} -> {r.entry_no}: {reason}")
    db.session.commit()
    return jsonify(ledger.entry_dict(r, detail=True)), 201


@bp.get("/accounting/ledger")
@permission_required("accounting.view")
def account_ledger():
    a = get_or_404(Account, parse_int(request.args.get("account_id"), "account_id"), "Account")
    start, end = _period()
    return jsonify(ledger.general_ledger(a, start, end, _currency_arg(allow_all=False)))


# --------------------------------------------------------------------------- #
# Statements
# --------------------------------------------------------------------------- #
@bp.get("/accounting/trial-balance")
@permission_required("accounting.view")
def trial_balance():
    as_at = parse_date(request.args.get("as_at"), "as_at", required=False) or _fiscal_year()[1]
    return jsonify(ledger.trial_balance(as_at, _currency_arg()))


@bp.get("/accounting/income-statement")
@permission_required("accounting.view")
def income_statement():
    start, end = _period()
    return jsonify(ledger.income_statement(start, end, compare=request.args.get("compare", "1") == "1",
                                           currency=_currency_arg()))


@bp.get("/accounting/balance-sheet")
@permission_required("accounting.view")
def balance_sheet():
    as_at = parse_date(request.args.get("as_at"), "as_at", required=False) or _fiscal_year()[1]
    compare = parse_date(request.args.get("compare_to"), "compare_to", required=False)
    if compare and compare >= as_at:
        raise ApiError("The comparative date must be before the reporting date")
    return jsonify(ledger.balance_sheet(as_at, compare, _currency_arg()))


@bp.get("/accounting/cash-flow")
@permission_required("accounting.view")
def cash_flow():
    start, end = _period()
    return jsonify(ledger.cash_flow(start, end, _currency_arg()))


# --------------------------------------------------------------------------- #
# Settings: period lock & rebuild
# --------------------------------------------------------------------------- #
@bp.get("/accounting/settings")
@permission_required("accounting.view", "fees.manage", "payments.manage", "expenses.manage", "payroll.manage", "assets.manage")
def get_settings():
    lock = ledger.get_lock_date()
    start, end = _fiscal_year()
    r = fx.rate_on()
    return jsonify(lock_date=lock.isoformat() if lock else None, fiscal_start=start.isoformat(),
                   fiscal_end=end.isoformat(), base_currency=fx.base(), currencies=fx.enabled(),
                   rate=fx.rate_dict(r) if r else None,
                   cash_accounts=[ledger.account_dict(a) for a in ledger.cash_accounts()])


@bp.put("/accounting/settings")
@permission_required("accounting.approve")
def update_settings():
    data = body()
    lock = parse_date(data.get("lock_date"), "lock_date", required=False)
    if lock and lock >= date.today():
        raise ApiError("You can only close periods that have ended (before today)", fields={"lock_date": "Too late"})
    old = ledger.get_lock_date()
    ledger.set_lock_date(lock)
    audit("lock" if lock and (not old or lock > old) else "reopen", "books", None,
          f"{old.isoformat() if old else 'none'} -> {lock.isoformat() if lock else 'none'}")
    db.session.commit()
    return jsonify(lock_date=lock.isoformat() if lock else None)


@bp.post("/accounting/rebuild")
@permission_required("accounting.approve")
def rebuild():
    if body().get("confirm") != "REBUILD":
        raise ApiError("Type REBUILD to confirm")
    n = ledger.rebuild_ledger()
    db.session.commit()
    return jsonify(postings=n)
