"""Dashboards, reports, announcements, audit trail and lookup metadata."""
from collections import defaultdict
from datetime import date, timedelta

from flask import Blueprint, current_app, jsonify, request
from flask_login import current_user, login_required
from sqlalchemy.orm import selectinload

from .. import db
from ..models import (PAYMENT_METHODS, ROLES, Announcement, Attendance, AuditLog, Expense, Invoice,
                      Payment, PaymentAllocation, PayrollRemittance, PayrollRun, SchoolClass, Staff, Student, Term,
                      TimetableSlot, ClassSubject)
from ..services import academics, finance, payroll, structure
from ..services import currency as fx
from ..services.structure import currency, school_name
from ..services.assets import month_end
from ..services.access import teacher_class_ids, teacher_staff_id
from ..utils import (ApiError, audit, body, clean_str, current_term, get_or_404, iso, money,
                     paginate_args, parse_date, parse_int, require, permission_required)

from ..services.permissions import has, has_any  # noqa: E402

bp = Blueprint("dashboard", __name__)


def _month_key(d):
    return f"{d.year}-{d.month:02d}"


def _last_months(n):
    today = date.today().replace(day=1)
    months = []
    y, m = today.year, today.month
    for _ in range(n):
        months.append(f"{y}-{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return list(reversed(months))


def _school_days(n, end=None):
    d = end or date.today()
    days = []
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    return list(reversed(days))


def _attendance_rate(rows):
    counted = [r for r in rows if r.status != "excused"]
    if not counted:
        return None
    return round(sum(1 for r in counted if r.status in ("present", "late")) / len(counted) * 100, 1)


def _announcements():
    q = Announcement.query
    if current_user.role == "parent":
        q = q.filter(Announcement.audience.in_(("all", "parents")))
    else:
        q = q.filter(Announcement.audience.in_(("all", "staff")))
    return [announcement_dict(a) for a in q.order_by(Announcement.pinned.desc(), Announcement.id.desc()).limit(5)]


def announcement_dict(a):
    return {"id": a.id, "title": a.title, "body": a.body, "audience": a.audience, "pinned": a.pinned,
            "author": a.author.full_name if a.author else None, "created_at": a.created_at.isoformat()}


@bp.get("/dashboard")
@login_required
def dashboard():
    role = current_user.role
    term = current_term()
    # Office dashboard for anyone who can see the school's money; teachers see their classes.
    kind = ("office" if has_any("fees.view", "payments.view", "expenses.view", "accounting.view")
            else "parent" if role == "parent" else "teacher")
    data = {"role": "admin" if role == "admin" else {"office": "bursar"}.get(kind, kind),
            "term": term.label if term else None, "announcements": _announcements()}
    if kind == "office":
        data.update(_office_dashboard(term))
    elif kind == "teacher":
        data.update(_teacher_dashboard(term))
    else:
        data.update(_parent_dashboard(term))
    return jsonify(data)


def _office_dashboard(term):
    today = date.today()
    out = {
        "kpis": {
            "students": Student.query.filter_by(status="active").count(),
            "staff": Staff.query.filter_by(status="active").count(),
            "classes": SchoolClass.query.count(),
            "pending_expenses": Expense.query.filter_by(status="pending").count(),
        }
    }
    if term:
        invoices = (Invoice.query.filter_by(term_id=term.id, void=False)
                    .options(selectinload(Invoice.lines),
                             selectinload(Invoice.allocations).selectinload(PaymentAllocation.payment)).all())
        # Fees are billed and collected per currency; never added across currencies.
        per = []
        for cur in fx.enabled():
            mine = [i for i in invoices if fx.of(i) == cur]
            billed = sum(i.total_cents for i in mine)
            paid = sum(i.paid_cents for i in mine)
            if billed or cur == fx.base():
                per.append({"currency": cur, "billed": money(billed), "collected": money(paid),
                            "outstanding": money(billed - paid),
                            "collection_rate": round(paid / billed * 100, 1) if billed else 0})
        base = per[0]
        out["kpis"].update(billed=base["billed"], collected=base["collected"], outstanding=base["outstanding"],
                           collection_rate=base["collection_rate"], fees_by_currency=per,
                           overdue_invoices=sum(1 for i in invoices if i.status == "overdue"))
    today_rows = Attendance.query.filter_by(date=today).all()
    out["kpis"]["attendance_today"] = _attendance_rate(today_rows)

    months = _last_months(6)
    start = date(int(months[0][:4]), int(months[0][5:]), 1)
    coll = defaultdict(int)
    for p in Payment.query.filter(Payment.void.is_(False), Payment.paid_on >= start, Payment.currency == fx.base()):
        coll[_month_key(p.paid_on)] += p.amount_cents
    out["collections"] = [{"month": m, "amount": money(coll[m])} for m in months]
    out["collections_currency"] = fx.base()

    # Spending by category in base-currency terms: other currencies at today's rate (skipped
    # with a note when no rate has been entered).
    exp, skipped = defaultdict(int), set()

    def add(category, cents, cur):
        v = fx.convert(cents, cur, fx.base(), today, required=False)
        if v is None:
            skipped.add(cur)
        else:
            exp[category] += v

    q = Expense.query.filter(Expense.status.in_(("approved", "paid")))
    if term:
        q = q.filter(Expense.expense_date >= term.start_date, Expense.expense_date <= term.end_date)
    for e in q:
        add(e.category, e.amount_cents, fx.of(e))
    # Salaries are paid through payroll: employer cost of approved runs in the term.
    for run in PayrollRun.query.filter(PayrollRun.status.in_(("approved", "paid"))):
        end = month_end(run.period)
        if not term or term.start_date <= end <= term.end_date:
            for cur, t in payroll.currency_totals(run).items():
                add("Salaries", t["employer_cost"], cur)
    out["expenses_by_category"] = sorted(({"category": k, "amount": money(v)} for k, v in exp.items()),
                                         key=lambda r: r["amount"], reverse=True)
    out["expenses_currency"] = fx.base()
    out["expenses_note"] = (f"{', '.join(sorted(skipped))} spending left out: no exchange rate entered"
                            if skipped else None)

    days = _school_days(10)
    rows = Attendance.query.filter(Attendance.date >= days[0], Attendance.date <= days[-1]).all()
    by_day = defaultdict(list)
    for r in rows:
        by_day[r.date].append(r)
    out["attendance_trend"] = [{"date": iso(d), "rate": _attendance_rate(by_day[d])} for d in days]

    out["recent_payments"] = [finance.payment_dict(p) for p in
                              Payment.query.order_by(Payment.id.desc()).limit(6)]
    overdue = defaultdict(int)
    for inv in (Invoice.query.join(Student).filter(Invoice.void.is_(False), Invoice.due_date < today,
                                                   Student.status == "active")
                .options(selectinload(Invoice.lines),
                         selectinload(Invoice.allocations).selectinload(PaymentAllocation.payment))):
        if inv.balance_cents > 0:
            overdue[(inv.student, fx.of(inv))] += inv.balance_cents
    # Rank across currencies by their base-currency value where a rate exists.
    rank = lambda kv: fx.convert(kv[1], kv[0][1], fx.base(), today, required=False) or kv[1]
    top = sorted(overdue.items(), key=rank, reverse=True)[:6]
    out["top_debtors"] = [{"student_id": st.id, "student": st.name, "currency": cur,
                           "class": st.school_class.name if st.school_class else None,
                           "overdue": money(cents)} for (st, cur), cents in top]
    out["unrecorded_classes"] = [c.name for c in SchoolClass.query.order_by(SchoolClass.level)
                                 if c.active_students() and not Attendance.query.filter_by(class_id=c.id, date=today).first()] \
        if today.weekday() < 5 and term and term.start_date <= today <= term.end_date else []
    return out


def _teacher_dashboard(term):
    today = date.today()
    sid = teacher_staff_id()
    classes = SchoolClass.query.filter(SchoolClass.id.in_(teacher_class_ids() or {-1})).order_by(SchoolClass.level).all()
    my = []
    for c in classes:
        my.append({"id": c.id, "name": c.name, "enrolled": len(c.active_students()),
                   "is_class_teacher": c.class_teacher_id == sid,
                   "attendance_recorded": bool(Attendance.query.filter_by(class_id=c.id, date=today).first()),
                   "subjects": [cs.subject.name for cs in c.subjects if cs.teacher_id == sid]})
    lessons = []
    if today.weekday() < 5:
        slots = (TimetableSlot.query.join(ClassSubject).filter(ClassSubject.teacher_id == sid, TimetableSlot.day == today.weekday())
                 .order_by(TimetableSlot.start_time).all())
        lessons = [{"time": f"{s.start_time}-{s.end_time}", "class": s.class_subject.school_class.name,
                    "subject": s.class_subject.subject.name, "room": s.room or s.class_subject.school_class.room}
                   for s in slots]
    at_risk = []
    if term:
        ids = {c.id for c in classes}
        at_risk = [r for r in academics.at_risk_students(term) if r["class_id"] in ids]
    return {"classes": my, "today_lessons": lessons, "at_risk": at_risk[:10]}


def _parent_dashboard(term):
    children = []
    g = current_user.guardian
    for st in (g.students if g else []):
        child = {"id": st.id, "name": st.name, "admission_no": st.admission_no, "status": st.status,
                 "class": st.school_class.name if st.school_class else None,
                 "account": finance.account_summary(st)}
        if term and st.class_id:
            child["attendance"] = academics.attendance_stats([st.id], term.start_date, min(term.end_date, date.today()))[st.id]
            res = academics.term_results(st.class_id, term.id)["results"].get(st.id)
            child["average"] = res["average"] if res else None
            child["position"] = res["position"] if res else None
        children.append(child)
    return {"children": children}


# --------------------------------------------------------------------------- #
# Reports
# --------------------------------------------------------------------------- #
def _term_arg():
    tid = parse_int(request.args.get("term_id"), "term_id", required=False)
    term = db.session.get(Term, tid) if tid else current_term()
    if not term:
        raise ApiError("No term selected")
    return term


@bp.get("/reports/fees")
@permission_required("reports.view")
def report_fees():
    term = _term_arg()
    cur = request.args.get("currency") or fx.base()
    debtors = []
    for inv in Invoice.query.filter_by(term_id=term.id, void=False, currency=cur):
        if inv.balance_cents > 0:
            g = inv.student.guardian
            debtors.append({"student_id": inv.student_id, "student": inv.student.name,
                            "admission_no": inv.student.admission_no,
                            "class": inv.student.school_class.name if inv.student.school_class else "-",
                            "guardian": g.name if g else None, "phone": g.phone if g else None,
                            "invoice_no": inv.invoice_no, "balance": money(inv.balance_cents),
                            "days_overdue": max((date.today() - inv.due_date).days, 0)})
    debtors.sort(key=lambda d: d["balance"], reverse=True)
    # Aging buckets are what a bursar actually chases on.
    aging = {"current": 0, "1-30": 0, "31-60": 0, "61-90": 0, "90+": 0}
    for d in debtors:
        n = d["days_overdue"]
        key = "current" if n == 0 else "1-30" if n <= 30 else "31-60" if n <= 60 else "61-90" if n <= 90 else "90+"
        aging[key] = round(aging[key] + d["balance"], 2)
    methods = defaultdict(int)
    for p in Payment.query.filter(Payment.void.is_(False), Payment.paid_on >= term.start_date,
                                  Payment.paid_on <= term.end_date, Payment.currency == cur):
        methods[p.method] += p.amount_cents
    return jsonify(term=term.label, currency=cur, currencies=fx.enabled(),
                   classes=finance.class_fee_summary(term, cur), debtors=debtors,
                   aging=[{"bucket": k, "amount": v} for k, v in aging.items()],
                   methods=[{"method": k, "amount": money(v)} for k, v in methods.items()])


@bp.get("/reports/cashflow")
@permission_required("reports.view")
def report_cashflow():
    months = _last_months(parse_int(request.args.get("months"), "months", required=False, minimum=1, maximum=24) or 12)
    start = date(int(months[0][:4]), int(months[0][5:]), 1)
    cur = request.args.get("currency") or fx.base()
    income, spend = defaultdict(int), defaultdict(int)
    for p in Payment.query.filter(Payment.void.is_(False), Payment.paid_on >= start, Payment.currency == cur):
        income[_month_key(p.paid_on)] += p.amount_cents
    for e in Expense.query.filter(Expense.status == "paid", Expense.expense_date >= start, Expense.currency == cur):
        spend[_month_key(e.expense_date)] += e.amount_cents
    # Net salaries and statutory remittances leave the bank on their payment dates.
    for run in PayrollRun.query.filter(PayrollRun.status == "paid", PayrollRun.paid_at >= start):
        spend[_month_key(run.paid_at)] += payroll.currency_totals(run).get(cur, {}).get("net", 0)
    for r in PayrollRemittance.query.filter(PayrollRemittance.paid_on >= start, PayrollRemittance.currency == cur):
        spend[_month_key(r.paid_on)] += r.amount_cents
    rows = [{"month": m, "income": money(income[m]), "expenses": money(spend[m]),
             "net": money(income[m] - spend[m])} for m in months]
    return jsonify(rows=rows, currency=cur, currencies=fx.enabled(),
                   totals={"income": round(sum(r["income"] for r in rows), 2),
                                      "expenses": round(sum(r["expenses"] for r in rows), 2),
                                      "net": round(sum(r["net"] for r in rows), 2)})


@bp.get("/reports/academic")
@permission_required("results.view")
def report_academic():
    term = _term_arg()
    pass_mark = current_app.config["PASS_MARK"]
    rows = []
    for c in SchoolClass.query.order_by(SchoolClass.level, SchoolClass.stream):
        res = academics.term_results(c.id, term.id)
        avgs = [r["average"] for r in res["results"].values() if r["average"] is not None]
        att = academics.attendance_stats([s.id for s in c.active_students()], term.start_date,
                                         min(term.end_date, date.today()))
        rates = [a["rate"] for a in att.values() if a["rate"] is not None]
        top = max(res["results"].values(), key=lambda r: r["average"] or -1, default=None)
        rows.append({"class": c.name, "students": len(c.active_students()),
                     "mean": round(sum(avgs) / len(avgs), 1) if avgs else None,
                     "pass_rate": round(sum(1 for a in avgs if a >= pass_mark) / len(avgs) * 100, 1) if avgs else None,
                     "attendance": round(sum(rates) / len(rates), 1) if rates else None,
                     "top_student": top["student"].name if top and top["average"] is not None else None})
    return jsonify(term=term.label, rows=rows, at_risk=academics.at_risk_students(term))


# --------------------------------------------------------------------------- #
# Announcements, audit, metadata
# --------------------------------------------------------------------------- #
@bp.get("/announcements")
@login_required
def list_announcements():
    q = Announcement.query
    if current_user.role == "parent":
        q = q.filter(Announcement.audience.in_(("all", "parents")))
    elif not has("announcements.manage"):
        q = q.filter(Announcement.audience.in_(("all", "staff")))
    return jsonify(items=[announcement_dict(a) for a in q.order_by(Announcement.pinned.desc(), Announcement.id.desc())])


@bp.post("/announcements")
@permission_required("announcements.manage")
def create_announcement():
    data = body()
    require(data, "title", "body")
    if data.get("audience", "all") not in ("all", "staff", "parents"):
        raise ApiError("Invalid audience")
    a = Announcement(title=clean_str(data["title"], 120), body=clean_str(data["body"], 5000),
                     audience=data.get("audience", "all"), pinned=bool(data.get("pinned")),
                     created_by=current_user.id)
    db.session.add(a)
    db.session.flush()
    audit("create", "announcement", a.id, a.title)
    db.session.commit()
    return jsonify(announcement_dict(a)), 201


@bp.delete("/announcements/<int:aid>")
@permission_required("announcements.manage")
def delete_announcement(aid):
    a = get_or_404(Announcement, aid, "Announcement")
    db.session.delete(a)
    audit("delete", "announcement", aid, a.title)
    db.session.commit()
    return jsonify(ok=True)


@bp.get("/audit")
@permission_required("audit.view")
def audit_log():
    q = AuditLog.query
    if request.args.get("entity"):
        q = q.filter_by(entity=request.args["entity"])
    if request.args.get("action"):
        q = q.filter_by(action=request.args["action"])
    start = parse_date(request.args.get("from"), "from", required=False)
    if start:
        q = q.filter(AuditLog.timestamp >= start)
    page, per = paginate_args(100)
    total = q.count()
    rows = q.order_by(AuditLog.id.desc()).offset((page - 1) * per).limit(per).all()
    return jsonify(total=total, page=page, per_page=per, items=[
        {"id": r.id, "timestamp": r.timestamp.isoformat(timespec="seconds"), "user": r.user.username if r.user else "system",
         "action": r.action, "entity": r.entity, "entity_id": r.entity_id, "details": r.details} for r in rows])


@bp.get("/meta")
@login_required
def meta():
    term = current_term()
    terms = Term.query.order_by(Term.start_date.desc()).all()
    return jsonify(
        school=school_name(), currency=currency(), profile=structure.profile(),
        current_term=({"id": term.id, "label": term.label, "start_date": iso(term.start_date),
                       "end_date": iso(term.end_date)} if term else None),
        terms=[{"id": t.id, "label": t.label, "start_date": iso(t.start_date), "end_date": iso(t.end_date)} for t in terms],
        classes=[{"id": c.id, "name": c.name, "level": c.level} for c in
                 SchoolClass.query.order_by(SchoolClass.level, SchoolClass.stream)],
        payment_methods=list(PAYMENT_METHODS), roles=list(ROLES),
        pass_mark=current_app.config["PASS_MARK"],
        attendance_threshold=current_app.config["ATTENDANCE_THRESHOLD"],
        today=date.today().isoformat(),
    )
