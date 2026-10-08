"""Bank reconciliation endpoints (see services/banking.py)."""
from flask import Blueprint, jsonify, request
from flask_login import current_user

from .. import db
from ..models import Account, BankStatement, BankStatementLine, JournalLine
from ..services import banking
from ..utils import (ApiError, audit, body, clean_str, get_or_404, money, parse_date, parse_int, permission_required,
                     require, to_cents)

bp = Blueprint("banking", __name__)


def _signed_cents(value, field):
    """Amounts and balances: positive = money in (or in credit), negative = money out (or overdrawn).
    Accepts "1,000.00", "-5", "(5.00)". Zero is allowed here; lines reject it separately."""
    text = str(value).strip().replace(",", "")
    negative = text.startswith("-") or (text.startswith("(") and text.endswith(")"))
    cents = to_cents(text.strip("-()") or "0", field, allow_zero=True)
    return -cents if negative else cents


@bp.get("/banking/accounts")
@permission_required("banking.view")
def accounts():
    return jsonify(items=[banking.account_status(a) for a in banking.bank_accounts()])


@bp.get("/banking/statements")
@permission_required("banking.view")
def statements():
    q = BankStatement.query
    if request.args.get("account_id"):
        q = q.filter_by(account_id=parse_int(request.args["account_id"], "account_id"))
    rows = q.order_by(BankStatement.statement_date.desc(), BankStatement.id.desc()).limit(200).all()
    return jsonify(items=[banking.statement_dict(s) for s in rows])


@bp.get("/banking/statements/<int:sid>")
@permission_required("banking.view")
def statement(sid):
    return jsonify(banking.statement_dict(get_or_404(BankStatement, sid, "Statement"), detail=True))


@bp.post("/banking/statements")
@permission_required("banking.manage")
def create_statement():
    data = body()
    require(data, "account_id", "statement_date", "closing")
    account = get_or_404(Account, parse_int(data["account_id"], "account_id"), "Account")
    prev = banking.last_completed(account)
    opening = (_signed_cents(data["opening"], "opening") if data.get("opening") not in (None, "")
               else (prev.closing_cents if prev else 0))
    s = banking.create_statement(account, parse_date(data["statement_date"], "statement_date"), opening,
                                 _signed_cents(data["closing"], "closing"), clean_str(data.get("reference"), 60),
                                 current_user.id, parse_date(data.get("start_date"), "start_date", required=False))
    db.session.commit()
    return jsonify(banking.statement_dict(s, detail=True)), 201


@bp.put("/banking/statements/<int:sid>")
@permission_required("banking.manage")
def update_statement(sid):
    s = get_or_404(BankStatement, sid, "Statement")
    banking._draft(s)
    data = body()
    if data.get("statement_date"):
        d = parse_date(data["statement_date"], "statement_date")
        prev = banking.last_completed(s.account)
        if prev and d <= prev.statement_date:
            raise ApiError("The statement must end after the last reconciled date", fields={"statement_date": "Too early"})
        if any(l.date > d for l in s.lines):
            raise ApiError("Some statement lines are dated after that date")
        s.statement_date = d
    if "start_date" in data and not banking.last_completed(s.account):
        s.start_date = parse_date(data.get("start_date"), "start_date", required=False)
    if data.get("opening") not in (None, ""):
        s.opening_cents = _signed_cents(data["opening"], "opening")
    if data.get("closing") not in (None, ""):
        s.closing_cents = _signed_cents(data["closing"], "closing")
    if "reference" in data:
        s.reference = clean_str(data["reference"], 60)
    audit("update", "bank_statement", s.id, f"{money(s.opening_cents)} -> {money(s.closing_cents)}")
    db.session.commit()
    return jsonify(banking.statement_dict(s, detail=True))


@bp.delete("/banking/statements/<int:sid>")
@permission_required("banking.manage")
def delete_statement(sid):
    s = get_or_404(BankStatement, sid, "Statement")
    banking._draft(s)
    if any(l.posted_here for l in s.lines):
        raise ApiError("Entries were posted from this reconciliation. Delete those lines' entries first, or keep "
                       "the reconciliation and complete it.", 409)
    audit("delete", "bank_statement", s.id, f"{s.account.name} {s.statement_date.isoformat()}")
    db.session.delete(s)
    db.session.commit()
    return jsonify(ok=True)


@bp.post("/banking/statements/<int:sid>/lines")
@permission_required("banking.manage")
def add_lines(sid):
    """Add statement lines: [{date, description, reference, amount}] (amount negative for money out),
    or separate money_in / money_out columns as banks export them."""
    s = get_or_404(BankStatement, sid, "Statement")
    raw = body().get("lines") or []
    if not raw:
        raise ApiError("No statement lines to add")
    if len(raw) > 2000:
        raise ApiError("Import at most 2,000 lines at a time")
    rows = []
    for i, r in enumerate(raw):
        n = i + 1
        if not str(r.get("description") or "").strip():
            raise ApiError(f"Line {n}: a description is required")
        if r.get("amount") not in (None, ""):
            cents = _signed_cents(r["amount"], f"line {n} amount")
        else:
            money_in = _signed_cents(r["money_in"], f"line {n} money in") if r.get("money_in") not in (None, "", "0") else 0
            money_out = _signed_cents(r["money_out"], f"line {n} money out") if r.get("money_out") not in (None, "", "0") else 0
            cents = abs(money_in) - abs(money_out)
        if not cents:
            raise ApiError(f"Line {n}: the amount can't be zero")
        rows.append({"date": parse_date(r.get("date"), f"line {n} date"), "description": clean_str(r["description"], 200),
                     "reference": clean_str(r.get("reference"), 60), "amount_cents": cents})
    added = banking.add_lines(s, rows)
    matched = banking.auto_match(s)
    audit("import", "bank_statement", s.id, f"{len(added)} lines, {matched} matched automatically")
    db.session.commit()
    return jsonify(added=len(added), matched=matched, statement=banking.statement_dict(s, detail=True)), 201


def _line(lid):
    return get_or_404(BankStatementLine, lid, "Statement line")


@bp.delete("/banking/lines/<int:lid>")
@permission_required("banking.manage")
def delete_line(lid):
    line = _line(lid)
    banking._draft(line.statement)
    if line.posted_here:
        raise ApiError("An entry was posted for this line. Reverse it in the General Ledger first, then unmatch.", 409)
    s = line.statement
    db.session.delete(line)
    db.session.commit()
    return jsonify(banking.statement_dict(s, detail=True))


@bp.post("/banking/statements/<int:sid>/auto-match")
@permission_required("banking.manage")
def auto_match(sid):
    s = get_or_404(BankStatement, sid, "Statement")
    n = banking.auto_match(s)
    db.session.commit()
    return jsonify(matched=n, statement=banking.statement_dict(s, detail=True))


@bp.post("/banking/lines/<int:lid>/match")
@permission_required("banking.manage")
def match(lid):
    line = _line(lid)
    jl = get_or_404(JournalLine, parse_int(body().get("journal_line_id"), "journal_line_id"), "Transaction")
    banking.match(line, jl)
    db.session.commit()
    return jsonify(banking.statement_dict(line.statement, detail=True))


@bp.post("/banking/lines/<int:lid>/unmatch")
@permission_required("banking.manage")
def unmatch(lid):
    line = _line(lid)
    banking.unmatch(line)
    db.session.commit()
    return jsonify(banking.statement_dict(line.statement, detail=True))


@bp.post("/banking/lines/<int:lid>/post")
@permission_required("banking.manage")
def post_adjustment(lid):
    """Post a bank-only item (charges, interest, ...) against another account and match it."""
    line = _line(lid)
    data = body()
    require(data, "account_id")
    counter = get_or_404(Account, parse_int(data["account_id"], "account_id"), "Account")
    entry = banking.post_adjustment(line, counter, clean_str(data.get("description"), 200), current_user.id)
    audit("create", "journal", entry.id, f"{entry.entry_no} from bank reconciliation")
    db.session.commit()
    return jsonify(banking.statement_dict(line.statement, detail=True))


@bp.post("/banking/statements/<int:sid>/complete")
@permission_required("banking.approve")
def complete(sid):
    s = get_or_404(BankStatement, sid, "Statement")
    banking.complete(s, current_user.id)
    db.session.commit()
    return jsonify(banking.statement_dict(s, detail=True))


@bp.post("/banking/statements/<int:sid>/reopen")
@permission_required("banking.approve")
def reopen(sid):
    s = get_or_404(BankStatement, sid, "Statement")
    reason = clean_str(body().get("reason"), 150)
    if not reason or len(reason) < 5:
        raise ApiError("A reason (5+ characters) is required", fields={"reason": "Required"})
    banking.reopen(s, reason)
    db.session.commit()
    return jsonify(banking.statement_dict(s, detail=True))
