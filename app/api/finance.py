"""Fee structure, invoices, payments, statements and expenses."""
from datetime import date

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required
from sqlalchemy import false, or_

from .. import db
from ..models import (EXPENSE_STATUSES, Account, PAYMENT_METHODS, Expense, FeeItem, Invoice, InvoiceLine,
                      Payment, SchoolClass, Student, Term)
from ..services import currency as fx
from ..services import finance, ledger, notify
from ..services.access import ensure_student_access, guardian_student_ids
from ..utils import (ApiError, audit, body, clean_str, current_term, get_or_404, iso, money,
                     paginate_args, parse_date, parse_int, require, permission_required, to_cents)

from ..services.permissions import has, has_any  # noqa: E402

bp = Blueprint("finance", __name__)

EXPENSE_CATEGORIES = ("Salaries", "Utilities", "Maintenance", "Supplies", "Transport",
                      "Food", "Events", "IT", "Other")


# --------------------------------------------------------------------------- #
# Fee items
# --------------------------------------------------------------------------- #
def fee_dict(f):
    return {"id": f.id, "term_id": f.term_id, "term": f.term.label, "class_id": f.class_id,
            "class": f.school_class.name if f.school_class else "All classes", "name": f.name,
            "amount": money(f.amount_cents), "currency": fx.of(f), "due_date": iso(f.due_date), "discountable": f.discountable,
            "account_id": f.account_id, "account": f"{f.account.code} {f.account.name}" if f.account else None,
            "invoiced": InvoiceLine.query.filter_by(fee_item_id=f.id).count()}


@bp.get("/fees")
@login_required
def list_fees():
    tid = parse_int(request.args.get("term_id"), "term_id", required=False)
    term = db.session.get(Term, tid) if tid else current_term()
    q = FeeItem.query.filter_by(term_id=term.id) if term else FeeItem.query.filter(false())
    items = [fee_dict(f) for f in q.order_by(FeeItem.class_id.is_(None).desc(), FeeItem.class_id, FeeItem.id)]
    # Total payable per class so admins can sanity-check the structure.
    totals = []
    if term:
        for c in SchoolClass.query.order_by(SchoolClass.level, SchoolClass.stream):
            class_items = finance.fee_items_for(term.id, c.id)
            per = {cur: money(sum(f.amount_cents for f in class_items if fx.of(f) == cur))
                   for cur in fx.enabled() if any(fx.of(f) == cur for f in class_items)}
            totals.append({"class": c.name, "total": per.get(fx.base(), 0), "by_currency": per})
    return jsonify(items=items, totals=totals, term=term.label if term else None)


def _fee_account(data, name):
    """Income account for a fee item; defaults by name (tuition, levy, exam, ICT...)."""
    aid = parse_int(data.get("account_id"), "account_id", required=False)
    if not aid:
        return ledger.fee_account_for(name).id
    a = get_or_404(Account, aid, "Account")
    if a.type != "income" or a.subtype == "contra_income" or not a.active:
        raise ApiError("Fees must be credited to an active income account", fields={"account_id": "Not an income account"})
    return a.id


def _validate_fee(data, term):
    require(data, "name", "amount", "due_date")
    due = parse_date(data["due_date"], "due_date")
    if not term.start_date <= due <= term.end_date:
        raise ApiError("Due date must fall within the term", fields={"due_date": "Outside term"})
    class_id = parse_int(data.get("class_id"), "class_id", required=False)
    if class_id:
        get_or_404(SchoolClass, class_id, "Class")
    return class_id, due


def _check_duplicate_fee(term_id, name, class_id, currency, exclude_id=None):
    """A fee name may be charged once per student per term per currency (all-classes items cover every class)."""
    q = FeeItem.query.filter(FeeItem.term_id == term_id, FeeItem.name == name, FeeItem.currency == currency,
                             FeeItem.id != (exclude_id or -1))
    if class_id:
        q = q.filter(or_(FeeItem.class_id.is_(None), FeeItem.class_id == class_id))
    clash = q.first()
    if clash:
        where = clash.school_class.name if clash.school_class else "all classes"
        raise ApiError(f"'{name}' is already charged in {currency} to {where} this term", fields={"name": "Duplicate"})


@bp.post("/fees")
@permission_required("fees.manage")
def create_fee():
    data = body()
    require(data, "term_id")
    term = get_or_404(Term, parse_int(data["term_id"], "term_id"), "Term")
    class_id, due = _validate_fee(data, term)
    name = clean_str(data["name"], 60)
    cur = fx.pick(data.get("currency"))
    _check_duplicate_fee(term.id, name, class_id, cur)
    f = FeeItem(term=term, class_id=class_id, name=name, amount_cents=to_cents(data["amount"]), currency=cur,
                due_date=due, discountable=bool(data.get("discountable", True)),
                account_id=_fee_account(data, name))
    db.session.add(f)
    db.session.flush()
    audit("create", "fee", f.id, f"{name} {cur} {money(f.amount_cents)} {term.label}")
    db.session.commit()
    return jsonify(fee_dict(f)), 201


@bp.put("/fees/<int:fid>")
@permission_required("fees.manage")
def update_fee(fid):
    f = get_or_404(FeeItem, fid, "Fee item")
    if InvoiceLine.query.filter_by(fee_item_id=fid).first():
        raise ApiError("This fee has already been invoiced. Add an adjustment to individual invoices instead.", 409)
    data = body()
    class_id, due = _validate_fee(data, f.term)
    f.currency = fx.pick(data.get("currency") or f.currency)
    _check_duplicate_fee(f.term_id, clean_str(data["name"], 60), class_id, f.currency, exclude_id=f.id)
    f.name, f.class_id, f.due_date = clean_str(data["name"], 60), class_id, due
    f.amount_cents = to_cents(data["amount"])
    f.discountable = bool(data.get("discountable", True))
    f.account_id = _fee_account(data, f.name)
    audit("update", "fee", f.id, f.name)
    db.session.commit()
    return jsonify(fee_dict(f))


@bp.delete("/fees/<int:fid>")
@permission_required("fees.manage")
def delete_fee(fid):
    f = get_or_404(FeeItem, fid, "Fee item")
    if InvoiceLine.query.filter_by(fee_item_id=fid).first():
        raise ApiError("This fee has already been invoiced and cannot be deleted", 409)
    db.session.delete(f)
    audit("delete", "fee", fid, f.name)
    db.session.commit()
    return jsonify(ok=True)


# --------------------------------------------------------------------------- #
# Invoices
# --------------------------------------------------------------------------- #
@bp.get("/invoices")
@login_required
def list_invoices():
    q = Invoice.query.join(Student)
    if current_user.role == "parent":
        q = q.filter(Invoice.student_id.in_(guardian_student_ids() or {-1}))
    elif not has("fees.view"):
        raise ApiError("Forbidden", 403)
    if request.args.get("term_id"):
        q = q.filter(Invoice.term_id == parse_int(request.args["term_id"], "term_id"))
    if request.args.get("class_id"):
        q = q.filter(Student.class_id == parse_int(request.args["class_id"], "class_id"))
    if request.args.get("student_id"):
        q = q.filter(Invoice.student_id == parse_int(request.args["student_id"], "student_id"))
    if request.args.get("currency"):
        q = q.filter(Invoice.currency == request.args["currency"])
    if request.args.get("q"):
        like = f"%{request.args['q'].strip()}%"
        q = q.filter(or_(Invoice.invoice_no.ilike(like), Student.first_name.ilike(like),
                         Student.last_name.ilike(like), Student.admission_no.ilike(like)))
    rows = [finance.invoice_dict(i) for i in q.order_by(Invoice.id.desc()).limit(2000)]
    status = request.args.get("status")
    if status:  # status is derived (paid/partial/overdue), so filter after computing
        rows = [r for r in rows if r["status"] == status]
    page, per = paginate_args()
    def sums(rs):
        return {"total": round(sum(r["total"] for r in rs if r["status"] != "void"), 2),
                "paid": round(sum(r["paid"] for r in rs if r["status"] != "void"), 2),
                "balance": round(sum(r["balance"] for r in rs), 2)}
    by_currency = {cur: sums([r for r in rows if r["currency"] == cur])
                   for cur in fx.CURRENCIES if any(r["currency"] == cur for r in rows)}
    totals = by_currency.get(fx.base()) or sums([])
    return jsonify(items=rows[(page - 1) * per: page * per], total=len(rows), page=page,
                   per_page=per, totals=totals, totals_by_currency=by_currency)


@bp.get("/invoices/<int:iid>")
@login_required
def get_invoice(iid):
    inv = get_or_404(Invoice, iid, "Invoice")
    if current_user.role != "parent" and not has("fees.view"):
        raise ApiError("Forbidden", 403)
    ensure_student_access(inv.student)
    return jsonify(finance.invoice_dict(inv, detail=True))


@bp.post("/invoices/generate")
@permission_required("fees.manage")
def generate_invoices():
    data = body()
    require(data, "term_id")
    term = get_or_404(Term, parse_int(data["term_id"], "term_id"), "Term")
    created, skipped = finance.generate_term_invoices(term, parse_int(data.get("class_id"), "class_id", required=False))
    db.session.commit()
    return jsonify(created=len(created), skipped=skipped)


@bp.post("/invoices/<int:iid>/charges")
@permission_required("fees.manage")
def add_charge(iid):
    inv = get_or_404(Invoice, iid, "Invoice")
    data = body()
    require(data, "description", "amount")
    finance.add_charge(inv, clean_str(data["description"], 120), to_cents(data["amount"]))
    db.session.commit()
    return jsonify(finance.invoice_dict(inv, detail=True))


@bp.post("/invoices/<int:iid>/void")
@permission_required("fees.manage")
def void_invoice(iid):
    inv = get_or_404(Invoice, iid, "Invoice")
    reason = clean_str(body().get("reason"), 200)
    if not reason or len(reason) < 5:
        raise ApiError("A reason (5+ characters) is required to void an invoice", fields={"reason": "Required"})
    finance.void_invoice(inv, reason)
    db.session.commit()
    return jsonify(finance.invoice_dict(inv, detail=True))


# --------------------------------------------------------------------------- #
# Payments
# --------------------------------------------------------------------------- #
@bp.get("/payments")
@login_required
def list_payments():
    q = Payment.query.join(Student)
    if current_user.role == "parent":
        q = q.filter(Payment.student_id.in_(guardian_student_ids() or {-1}))
    elif not has("payments.view"):
        raise ApiError("Forbidden", 403)
    if request.args.get("student_id"):
        q = q.filter(Payment.student_id == parse_int(request.args["student_id"], "student_id"))
    if request.args.get("method"):
        q = q.filter(Payment.method == request.args["method"])
    if request.args.get("currency"):
        q = q.filter(Payment.currency == request.args["currency"])
    start = parse_date(request.args.get("from"), "from", required=False)
    end = parse_date(request.args.get("to"), "to", required=False)
    if start:
        q = q.filter(Payment.paid_on >= start)
    if end:
        q = q.filter(Payment.paid_on <= end)
    if request.args.get("q"):
        like = f"%{request.args['q'].strip()}%"
        q = q.filter(or_(Payment.receipt_no.ilike(like), Payment.reference.ilike(like),
                         Student.first_name.ilike(like), Student.last_name.ilike(like),
                         Student.admission_no.ilike(like)))
    page, per = paginate_args()
    total = q.count()
    sums = {cur: money(int(cents or 0)) for cur, cents in
            q.filter(Payment.void.is_(False)).with_entities(Payment.currency, db.func.sum(Payment.amount_cents))
            .group_by(Payment.currency)}
    rows = q.order_by(Payment.paid_on.desc(), Payment.id.desc()).offset((page - 1) * per).limit(per).all()
    return jsonify(items=[finance.payment_dict(p) for p in rows], total=total, page=page,
                   per_page=per, sum=sums.get(fx.base(), 0), sum_by_currency=sums)


@bp.get("/payments/<int:pid>")
@login_required
def get_payment(pid):
    p = get_or_404(Payment, pid, "Payment")
    if current_user.role != "parent" and not has_any("payments.view", "fees.view"):
        raise ApiError("Forbidden", 403)
    ensure_student_access(p.student)
    return jsonify(finance.payment_dict(p, detail=True))


@bp.post("/payments")
@permission_required("payments.manage")
def create_payment():
    data = body()
    require(data, "student_id", "amount", "method")
    st = get_or_404(Student, parse_int(data["student_id"], "student_id"), "Student")
    method = data["method"]
    if method not in PAYMENT_METHODS:
        raise ApiError("Invalid payment method", fields={"method": "Invalid"})
    reference = clean_str(data.get("reference"), 60)
    if method != "cash" and not reference:
        raise ApiError("A transaction reference is required for non-cash payments", fields={"reference": "Required"})
    paid_on = parse_date(data.get("paid_on"), "paid_on", required=False) or date.today()
    cur = fx.pick(data.get("currency"))
    p = finance.record_payment(st, to_cents(data["amount"]), method, reference, paid_on, current_user, cur)
    db.session.commit()
    notify.payment_recorded(p)
    return jsonify(finance.payment_dict(p, detail=True)), 201


@bp.post("/payments/<int:pid>/void")
@permission_required("payments.approve")
def void_payment(pid):
    # Only administrators may void receipts: separation of duties from the bursar who received it.
    p = get_or_404(Payment, pid, "Payment")
    reason = clean_str(body().get("reason"), 200)
    if not reason or len(reason) < 5:
        raise ApiError("A reason (5+ characters) is required to void a payment", fields={"reason": "Required"})
    finance.void_payment(p, reason)
    db.session.commit()
    return jsonify(finance.payment_dict(p, detail=True))


@bp.get("/students/<int:sid>/statement")
@login_required
def statement(sid):
    """Chronological ledger with a running balance, one per currency (never mixed)."""
    st = get_or_404(Student, sid, "Student")
    if current_user.role != "parent" and not has("fees.view"):
        raise ApiError("Forbidden", 403)
    ensure_student_access(st)
    account = finance.account_summary(st)
    sections = []
    for summary in account["by_currency"]:
        cur = summary["currency"]
        entries = []
        for inv in st.invoices:
            if fx.of(inv) == cur:
                entries.append({"date": inv.issue_date, "ref": inv.invoice_no, "description": f"Invoice - {inv.term.label}",
                                "debit": inv.total_cents, "credit": 0, "void": inv.void})
        for p in st.payments:
            if fx.of(p) == cur:
                entries.append({"date": p.paid_on, "ref": p.receipt_no, "description": f"Payment ({p.method})",
                                "debit": 0, "credit": p.amount_cents, "void": p.void})
        entries.sort(key=lambda e: (e["date"], e["credit"] > 0))
        running, out = 0, []
        for e in entries:
            if not e["void"]:
                running += e["debit"] - e["credit"]
            out.append({**e, "date": iso(e["date"]), "debit": money(e["debit"]), "credit": money(e["credit"]),
                        "balance": money(running), "currency": cur})
        sections.append({"currency": cur, "entries": out, "account": summary})
    base = next(x for x in sections if x["currency"] == fx.base())
    return jsonify(student={"id": st.id, "name": st.name, "admission_no": st.admission_no,
                            "class": st.school_class.name if st.school_class else None},
                   entries=base["entries"], account=account, sections=sections)


# --------------------------------------------------------------------------- #
# Expenses (request -> approve/reject -> pay)
# --------------------------------------------------------------------------- #
def expense_dict(e):
    return {"id": e.id, "category": e.category, "description": e.description, "vendor": e.vendor,
            "amount": money(e.amount_cents), "currency": fx.of(e), "expense_date": iso(e.expense_date), "status": e.status,
            "requested_by": e.requester.full_name if e.requester else None,
            "approved_by": e.approver.full_name if e.approver else None,
            "decision_note": e.decision_note, "can_decide": _can_decide(e),
            "account": f"{ledger.expense_account(e).code} {ledger.expense_account(e).name}",
            "paid_at": iso(e.paid_at),
            "paid_from": e.paid_from_account_id and db.session.get(Account, e.paid_from_account_id).name}


def _can_decide(e):
    # The person who requested an expense can never approve it themselves.
    return has("expenses.approve") and e.requested_by != current_user.id


@bp.get("/expenses")
@permission_required("expenses.view")
def list_expenses():
    q = Expense.query
    if request.args.get("status"):
        q = q.filter_by(status=request.args["status"])
    if request.args.get("category"):
        q = q.filter_by(category=request.args["category"])
    if request.args.get("currency"):
        q = q.filter_by(currency=request.args["currency"])
    rows = q.order_by(Expense.expense_date.desc(), Expense.id.desc()).limit(1000).all()
    return jsonify(items=[expense_dict(e) for e in rows], categories=EXPENSE_CATEGORIES)


@bp.post("/expenses")
@permission_required("expenses.manage")
def create_expense():
    data = body()
    require(data, "category", "description", "amount", "expense_date")
    if data["category"] not in EXPENSE_CATEGORIES:
        raise ApiError("Invalid category", fields={"category": "Invalid"})
    d = parse_date(data["expense_date"], "expense_date")
    if d > date.today():
        raise ApiError("Expense date cannot be in the future", fields={"expense_date": "Future date"})
    e = Expense(category=data["category"], description=clean_str(data["description"], 200),
                vendor=clean_str(data.get("vendor"), 100), amount_cents=to_cents(data["amount"]),
                currency=fx.pick(data.get("currency")), expense_date=d, requested_by=current_user.id)
    db.session.add(e)
    db.session.flush()
    audit("create", "expense", e.id, f"{e.category} {e.currency} {money(e.amount_cents)}")
    db.session.commit()
    return jsonify(expense_dict(e)), 201


TRANSITIONS = {"pending": {"approved", "rejected"}, "approved": {"paid"}, "rejected": set(), "paid": set()}


@bp.post("/expenses/<int:eid>/status")
@permission_required("expenses.manage", "expenses.approve")
def expense_status(eid):
    e = get_or_404(Expense, eid, "Expense")
    data = body()
    new = data.get("status")
    if new not in EXPENSE_STATUSES or new not in TRANSITIONS[e.status]:
        raise ApiError(f"Cannot move an expense from {e.status} to {new}")
    today = date.today()
    if new in ("approved", "rejected"):
        if not _can_decide(e):
            raise ApiError("Expenses must be approved by someone allowed to approve expenses, other than the requester", 403)
        e.approved_by = current_user.id
        e.decision_note = clean_str(data.get("note"), 200)
        if new == "rejected" and not e.decision_note:
            raise ApiError("Give a reason for rejecting", fields={"note": "Required"})
        if new == "approved":
            e.approved_at = today
            ledger.post_expense_accrual(e)
    if new == "paid":
        if not has("expenses.manage"):
            raise ApiError("You do not have permission to pay expenses", 403)
        # Pay from a chosen cash account; the ledger refuses if it lacks funds.
        aid = parse_int(data.get("account_id"), "account_id", required=False)
        account = get_or_404(Account, aid, "Account") if aid else fx.cash_account("1010", fx.of(e))
        paid_on = parse_date(data.get("paid_on"), "paid_on", required=False) or today
        if paid_on > today or paid_on < e.expense_date:
            raise ApiError("Payment date must be between the expense date and today", fields={"paid_on": "Invalid"})
        ledger.post_expense_payment(e, account, paid_on)
        e.paid_at, e.paid_from_account_id = paid_on, account.id
    e.status = new
    audit(new, "expense", e.id, e.decision_note)
    db.session.commit()
    return jsonify(expense_dict(e))


# --------------------------------------------------------------------------- #
# Exchange rates (the bursar records each day's rates against the US dollar)
# --------------------------------------------------------------------------- #
@bp.get("/rates")
@login_required
def list_rates():
    from ..models import ExchangeRate
    q = ExchangeRate.query
    cur = (request.args.get("currency") or "").upper()
    if cur:
        q = q.filter(ExchangeRate.currency == cur)
    rows = q.order_by(ExchangeRate.date.desc(), ExchangeRate.currency).limit(240).all()
    latest = fx.latest_rates()
    return jsonify(items=[fx.rate_dict(r) for r in rows],
                   latest={c: fx.rate_dict(r) for c, r in latest.items()},
                   rate_currencies=fx.rate_currencies(), base=fx.base(), currencies=fx.enabled(),
                   anchor=fx.ANCHOR)


@bp.post("/rates")
@permission_required("payments.manage")
def save_rate():
    """Record (or correct) a day's rates: units of each currency for 1 USD.

    Body: {"date", "currency": "ZAR", "rate": 18.4} for one currency, or
    {"date", "rates": {"ZWG": 26.75, "ZAR": 18.4}} for several at once.
    ({"zwg_per_usd": 26.75} from the two-currency version still works.)
    """
    from ..models import ExchangeRate
    from ..utils import parse_float
    data = body()
    on = parse_date(data.get("date"), "date", required=False) or date.today()
    if on > date.today():
        raise ApiError("Rates can't be entered for future dates", fields={"date": "Future date"})
    if isinstance(data.get("rates"), dict):
        given = data["rates"]
    elif "zwg_per_usd" in data and "rate" not in data:
        given = {"ZWG": data["zwg_per_usd"]}
    else:
        need = fx.rate_currencies()
        cur = (data.get("currency") or (need[0] if len(need) == 1 else "")).strip().upper()
        if not cur:
            raise ApiError("Choose the currency this rate is for", fields={"currency": "Required"})
        require(data, "rate")
        given = {cur: data["rate"]}
    given = {str(c).strip().upper(): v for c, v in given.items() if v not in (None, "")}
    if not given:
        raise ApiError("Enter at least one rate", fields={"rate": "Required"})
    saved = []
    for cur, value in given.items():
        if cur == fx.ANCHOR:
            raise ApiError("Rates are quoted per 1 USD, so USD itself doesn't need one", fields={"currency": "Invalid"})
        if cur not in fx.enabled():
            raise ApiError(f"{cur} isn't one of this school's currencies", fields={"currency": "Not enabled"})
        rate = parse_float(value, "rate", minimum=0.000001, maximum=10_000_000)
        r = ExchangeRate.query.filter_by(date=on, currency=cur).first()
        old = r.per_usd if r else None
        if r is None:
            r = ExchangeRate(date=on, currency=cur, per_usd=rate)
            db.session.add(r)
        r.per_usd, r.source, r.entered_by = rate, clean_str(data.get("source"), 60) or "RBZ interbank", current_user.id
        db.session.flush()
        audit("rate", "exchange_rate", r.id,
              f"{on.isoformat()}: 1 USD = {rate:g} {cur}" + (f" (was {old:g})" if old else ""))
        saved.append(r)
    db.session.commit()
    if len(saved) == 1:
        return jsonify(fx.rate_dict(saved[0])), 201
    return jsonify(items=[fx.rate_dict(r) for r in saved]), 201
