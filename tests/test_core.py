"""Business-rule tests. Run with:  python -m pytest -q"""
from datetime import date, timedelta

import pytest

from app import create_app, db
from app.models import (Attendance, ClassSubject, Exam, Invoice, Payment, SchoolClass, Staff,
                        Student, Term, User)
from app.seed import seed_demo
from app.services import academics, finance

H = {"X-Requested-With": "SchoolMS"}


@pytest.fixture(scope="module")
def app():
    app = create_app("config.TestConfig")

    # The fixture keeps one app context open, which Flask reuses for test requests;
    # drop Flask-Login's per-context user cache so each client sees its own session.
    @app.before_request
    def _fresh_user():
        from flask import g
        g.pop("_login_user", None)

    with app.app_context():
        seed_demo(students_per_class=3)
        yield app


@pytest.fixture()
def client(app):
    return app.test_client()


def login(client, username, password):
    r = client.post("/api/auth/login", json={"username": username, "password": password}, headers=H)
    assert r.status_code == 200, r.json
    return client


def admin(client):
    return login(client, "admin", "Admin@2026")


def bursar(client):
    return login(client, "bursar", "Bursar@2026")


def fresh_student(cls=None):
    """An active student with no invoices or payments."""
    cls = cls or SchoolClass.query.first()
    s = Student(admission_no=f"T{Student.query.count() + 1000}", first_name="Test", last_name="Pupil",
                gender="Female", dob=date(2015, 1, 1), school_class=cls)
    db.session.add(s)
    db.session.commit()
    return s


# ---------------------------------------------------------------- security
def test_csrf_header_required(client):
    r = client.post("/api/auth/login", json={"username": "admin", "password": "Admin@2026"})
    assert r.status_code == 403


def test_bad_login_and_auth_required(client):
    assert client.get("/api/students").status_code == 401
    r = client.post("/api/auth/login", json={"username": "admin", "password": "nope"}, headers=H)
    assert r.status_code == 401


def test_parent_sees_only_own_children(client, app):
    login(client, "parent", "Parent@2026")
    parent = User.query.filter_by(username="parent").first()
    own = {s.id for s in parent.guardian.students}
    listed = {s["id"] for s in client.get("/api/students?status=all").json["items"]}
    assert listed == own
    other = Student.query.filter(~Student.id.in_(own)).first()
    assert client.get(f"/api/students/{other.id}").status_code == 403
    assert client.post("/api/payments", json={}, headers=H).status_code == 403


def test_teacher_cannot_access_finance(client):
    login(client, "teacher", "Teacher@2026")
    assert client.get("/api/invoices").status_code == 403
    assert client.get("/api/expenses").status_code == 403


# ---------------------------------------------------------------- finance
def test_invoice_generation_is_idempotent(app):
    term = Term.query.filter_by(is_current=True).first()
    before = Invoice.query.filter_by(term_id=term.id).count()
    created, skipped = finance.generate_term_invoices(term)
    assert created == []
    assert Invoice.query.filter_by(term_id=term.id).count() == before


def test_payment_allocates_oldest_first_and_keeps_credit(app):
    bursar_user = User.query.filter_by(username="bursar").first()
    st = fresh_student()
    t1, t2, t3 = Term.query.order_by(Term.start_date).all()
    inv1, inv2 = finance.build_invoice(st, t1), finance.build_invoice(st, t2)
    total = inv1.total_cents + inv2.total_cents
    p = finance.record_payment(st, inv1.total_cents + 1000, "cash", None, date.today(), bursar_user)
    db.session.commit()
    assert inv1.balance_cents == 0 and inv1.status == "paid"
    assert inv2.paid_cents == 1000
    # Overpay the rest: surplus becomes credit, applied automatically to the next invoice.
    finance.record_payment(st, total - inv1.total_cents - 1000 + 5000, "cash", None, date.today(), bursar_user)
    assert finance.credit_cents(st) == 5000
    inv3 = finance.build_invoice(st, t3)
    finance.apply_credit(st)
    db.session.commit()
    assert inv3.paid_cents == 5000
    assert finance.credit_cents(st) == 0
    # Voiding the first payment re-opens the oldest invoice.
    finance.void_payment(p, "Bounced cheque")
    db.session.commit()
    assert inv1.balance_cents > 0
    valid_paid = sum(x.amount_cents for x in st.payments if not x.void)
    billed = inv1.total_cents + inv2.total_cents + inv3.total_cents
    assert finance.account_summary(st)["balance"] == pytest.approx((billed - valid_paid) / 100)


def test_void_invoice_returns_money_as_credit(app):
    bursar_user = User.query.filter_by(username="bursar").first()
    st = fresh_student()
    t1 = Term.query.order_by(Term.start_date).first()
    inv = finance.build_invoice(st, t1)
    finance.record_payment(st, 10000, "cash", None, date.today(), bursar_user)
    finance.void_invoice(inv, "Billed in error")
    db.session.commit()
    assert inv.status == "void" and inv.balance_cents == 0
    assert finance.credit_cents(st) == 10000


def test_scholarship_only_discounts_discountable_fees(app):
    from app.models import Scholarship
    st = fresh_student()
    db.session.add(Scholarship(student=st, name="Merit", percent=50))
    db.session.flush()
    term = Term.query.order_by(Term.start_date).first()
    inv = finance.build_invoice(st, term)
    items = finance.fee_items_for(term.id, st.class_id)
    discountable = sum(i.amount_cents for i in items if i.discountable)
    assert inv.total_cents == sum(i.amount_cents for i in items) - discountable // 2
    db.session.rollback()


def test_payment_api_validation(client):
    bursar(client)
    st = Student.query.filter_by(status="active").first()
    r = client.post("/api/payments", json={"student_id": st.id, "amount": "-5", "method": "cash"}, headers=H)
    assert r.status_code == 400
    r = client.post("/api/payments", json={"student_id": st.id, "amount": "10", "method": "bank"}, headers=H)
    assert r.status_code == 400 and "reference" in r.json["fields"]
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    r = client.post("/api/payments", json={"student_id": st.id, "amount": "10", "method": "cash", "paid_on": tomorrow}, headers=H)
    assert r.status_code == 400
    r = client.post("/api/payments", json={"student_id": st.id, "amount": "10.005", "method": "cash"}, headers=H)
    assert r.status_code == 201 and r.json["amount"] == 10.01
    # Bursars cannot void receipts; only administrators can.
    assert client.post(f"/api/payments/{r.json['id']}/void", json={"reason": "test void"}, headers=H).status_code == 403


def test_expense_cannot_be_self_approved(client):
    admin(client)
    r = client.post("/api/expenses", json={"category": "IT", "description": "Mouse", "amount": 20,
                                           "expense_date": date.today().isoformat()}, headers=H)
    eid = r.json["id"]
    r = client.post(f"/api/expenses/{eid}/status", json={"status": "approved"}, headers=H)
    assert r.status_code == 403
    # Cannot skip straight to paid either.
    assert client.post(f"/api/expenses/{eid}/status", json={"status": "paid"}, headers=H).status_code == 400


# ---------------------------------------------------------------- academics
def test_rank_ties_share_position():
    assert academics.rank({"a": 90, "b": 90, "c": 80}) == {"a": 1, "b": 1, "c": 3}


def test_exam_weights_cannot_exceed_100(client):
    admin(client)
    term = Term.query.filter_by(is_current=True).first()
    r = client.post("/api/exams", json={"term_id": term.id, "name": "Extra", "weight": 10, "max_score": 100}, headers=H)
    assert r.status_code == 400 and "weight" in r.json["fields"]


def test_marks_rules(client):
    login(client, "teacher", "Teacher@2026")
    teacher = User.query.filter_by(username="teacher").first().staff
    exam = Exam.query.join(Term).filter(Term.is_current.is_(True), Exam.date <= date.today()).first()
    mine = ClassSubject.query.filter_by(teacher_id=teacher.id).first()
    other = ClassSubject.query.filter(ClassSubject.teacher_id != teacher.id, ClassSubject.class_id == mine.class_id).first()
    st = db.session.get(SchoolClass, mine.class_id).active_students()[0]
    payload = {"exam_id": exam.id, "class_id": mine.class_id, "subject_id": mine.subject_id,
               "entries": [{"student_id": st.id, "score": exam.max_score + 1}]}
    assert client.post("/api/marks", json=payload, headers=H).status_code == 400
    payload["entries"][0]["score"] = exam.max_score
    assert client.post("/api/marks", json=payload, headers=H).status_code == 200
    payload["subject_id"] = other.subject_id
    assert client.post("/api/marks", json=payload, headers=H).status_code == 403


def test_attendance_rules(client):
    login(client, "teacher", "Teacher@2026")
    teacher = User.query.filter_by(username="teacher").first().staff
    cls = SchoolClass.query.filter_by(class_teacher_id=teacher.id).first()
    other = SchoolClass.query.filter(SchoolClass.id != cls.id).first()
    recs = [{"student_id": s.id, "status": "present"} for s in cls.active_students()]
    future = (date.today() + timedelta(days=3)).isoformat()
    assert client.post("/api/attendance", json={"class_id": cls.id, "date": future, "records": recs}, headers=H).status_code == 400
    # Not the class teacher of another class.
    assert client.post("/api/attendance", json={"class_id": other.id, "date": date.today().isoformat(), "records": []}, headers=H).status_code == 403
    # Every student must be accounted for.
    d = date.today()
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    r = client.post("/api/attendance", json={"class_id": cls.id, "date": d.isoformat(), "records": recs[:-1]}, headers=H)
    assert r.status_code == 400
    r = client.post("/api/attendance", json={"class_id": cls.id, "date": d.isoformat(), "records": recs}, headers=H)
    assert r.status_code == 200
    assert Attendance.query.filter_by(class_id=cls.id, date=d).count() == len(recs)


def test_timetable_clash_detected(client):
    admin(client)
    from app.models import TimetableSlot
    slot = TimetableSlot.query.first()
    cs = slot.class_subject
    other_cs = ClassSubject.query.filter(ClassSubject.class_id == cs.class_id, ClassSubject.id != cs.id).first()
    r = client.post("/api/timetable", json={"class_subject_id": other_cs.id, "day": slot.day,
                                            "start_time": slot.start_time, "end_time": slot.end_time}, headers=H)
    assert r.status_code == 409


def test_class_capacity_enforced(client):
    admin(client)
    cls = SchoolClass.query.first()
    cls.capacity = len(cls.active_students())
    db.session.commit()
    r = client.post("/api/students", json={"first_name": "Over", "last_name": "Flow", "gender": "Male", "dob": "2016-03-03",
                                           "class_id": cls.id, "guardian": {"name": "G", "phone": "+1 555"}}, headers=H)
    assert r.status_code == 400 and "class_id" in r.json["fields"]
    cls.capacity = 30
    db.session.commit()


def test_report_card_and_dashboards(client):
    admin(client)
    st = Student.query.filter_by(status="active").first()
    r = client.get(f"/api/report-card/{st.id}")
    assert r.status_code == 200 and r.json["subjects"]
    for path in ["/api/dashboard", "/api/reports/fees", "/api/reports/cashflow", "/api/reports/academic",
                 "/api/meta", "/api/timetable", "/api/audit", "/api/promotion/preview"]:
        assert client.get(path).status_code == 200, path


def test_staff_with_duties_cannot_leave(client):
    admin(client)
    t = Staff.query.filter(Staff.position == "Teacher").first()
    body = {"first_name": t.first_name, "last_name": t.last_name, "position": t.position, "status": "left"}
    assert client.put(f"/api/staff/{t.id}", json=body, headers=H).status_code == 409


def test_cannot_delete_student_with_history(client):
    admin(client)
    st = Payment.query.first().student
    assert client.delete(f"/api/students/{st.id}", headers=H).status_code == 409


# ---------------------------------------------------------------- demo sign-in
def test_demo_accounts_sign_in_without_password(client):
    from app.services import structure
    assert client.get("/api/auth/demo").json["enabled"]  # the demo seed marks the school as a demo
    r = client.post("/api/auth/demo-login", json={"username": "bursar"}, headers=H)
    assert r.status_code == 200 and r.json["user"]["role"] == "bursar"
    assert client.get("/api/auth/me").status_code == 200
    other = client.application.test_client()
    # Only the four demo accounts, and never without the CSRF header.
    assert other.post("/api/auth/demo-login", json={"username": "whatsapp-bot"}, headers=H).status_code == 404
    assert other.post("/api/auth/demo-login", json={"username": "admin"}).status_code == 403
    # Switched off, passwords are required again.
    structure.set_demo(False)
    db.session.commit()
    try:
        assert other.get("/api/auth/demo").json == {"enabled": False, "accounts": []}
        assert other.post("/api/auth/demo-login", json={"username": "admin"}, headers=H).status_code == 403
    finally:
        structure.set_demo(True)
        db.session.commit()
