"""Library: catalogue, lending rules, fines (to fees), lost books, class issues and access."""
from datetime import date, timedelta

import pytest

from app import create_app, db
from app.models import BookCopy, Guardian, Invoice, Loan, SchoolClass, Staff, Student, Term, User
from app.seed import seed_demo
from app.services import ledger

H = {"X-Requested-With": "SchoolMS"}


@pytest.fixture(scope="module")
def app():
    app = create_app("config.TestConfig")

    @app.before_request
    def _fresh_user():
        from flask import g
        g.pop("_login_user", None)

    with app.app_context():
        seed_demo(students_per_class=3)
        yield app


def login(app, username="admin", password="Admin@2026"):
    c = app.test_client()
    assert c.post("/api/auth/login", json={"username": username, "password": password}, headers=H).status_code == 200
    return c


def new_student(cls, no):
    s = Student(admission_no=no, first_name="Lib", last_name=no, gender="Female", dob=date(2014, 1, 1), school_class=cls)
    db.session.add(s)
    db.session.commit()
    return s


@pytest.fixture(scope="module")
def book(app):
    admin = login(app)
    r = admin.post("/api/library/books", headers=H, json={
        "title": "Test Novel", "author": "A. Writer", "isbn": "978-0-00-000000-1", "category": "Fiction",
        "copies": 3, "replacement_cost": 12.5})
    assert r.status_code == 201, r.json
    assert len(r.json["copy_list"]) == 3 and all(c["accession_no"].startswith("LIB-") for c in r.json["copy_list"])
    return r.json


def test_catalogue(app, book):
    admin = login(app)
    assert admin.post("/api/library/books", json={"title": "Dup", "isbn": "9780000000001"}, headers=H).status_code == 400
    r = admin.post("/api/library/books", headers=H, json={"title": "Labelled", "copies": 0, "accession_numbers": "old-1\nOLD-2"})
    assert r.status_code == 201 and [c["accession_no"] for c in r.json["copy_list"]] == ["OLD-1", "OLD-2"]
    r = admin.post(f"/api/library/books/{r.json['id']}/copies", json={"accession_numbers": "OLD-2"}, headers=H)
    assert r.status_code == 400  # accession numbers are unique
    found = admin.get("/api/library/copies/lookup?code=old-1").json
    assert found["title"] == "Labelled" and found["loan"] is None
    assert admin.get("/api/library/copies/lookup?code=NOPE").status_code == 404
    assert {b["title"] for b in admin.get("/api/library/books?q=writer").json["items"]} == {"Test Novel"}
    assert admin.get("/api/library/books?q=OLD-2").json["items"][0]["title"] == "Labelled"


def test_lending_rules(app, book):
    admin = login(app)
    cls = SchoolClass.query.first()
    st = new_student(cls, "LB-1")
    copies = book["copy_list"]
    r = admin.post("/api/library/loans", json={"accession_no": copies[0]["accession_no"].lower(), "student_id": st.id}, headers=H)
    assert r.status_code == 201 and r.json["status"] == "on_loan"
    assert r.json["due_on"] == (date.today() + timedelta(days=14)).isoformat()
    # The same copy can't go out twice.
    other = new_student(cls, "LB-2")
    r = admin.post("/api/library/loans", json={"copy_id": copies[0]["id"], "student_id": other.id}, headers=H)
    assert r.status_code == 409 and "on loan" in r.json["error"]
    # Borrowing limit (3 for students by default).
    admin.put("/api/library/settings", json={"student_max": 1}, headers=H)
    r = admin.post("/api/library/loans", json={"copy_id": copies[1]["id"], "student_id": st.id}, headers=H)
    assert r.status_code == 409 and "limit 1" in r.json["error"]
    admin.put("/api/library/settings", json={"student_max": 3}, headers=H)
    # Anyone with an overdue book is blocked.
    loan = Loan.query.filter_by(student_id=st.id).one()
    loan.due_on = date.today() - timedelta(days=4)
    db.session.commit()
    r = admin.post("/api/library/loans", json={"copy_id": copies[1]["id"], "student_id": st.id}, headers=H)
    assert r.status_code == 409 and "overdue" in r.json["error"]
    assert admin.post(f"/api/library/loans/{loan.id}/renew", headers=H).status_code == 409  # overdue: no renewal
    # Inactive students can't borrow.
    other.status = "withdrawn"
    db.session.commit()
    assert admin.post("/api/library/loans", json={"copy_id": copies[1]["id"], "student_id": other.id}, headers=H).status_code == 409
    # Staff borrow too.
    teacher = Staff.query.first()
    r = admin.post("/api/library/loans", json={"copy_id": copies[1]["id"], "staff_id": teacher.id}, headers=H)
    assert r.status_code == 201 and r.json["borrower_type"] == "staff"
    assert r.json["due_on"] == (date.today() + timedelta(days=30)).isoformat()
    lid = r.json["id"]
    for _ in range(2):
        assert admin.post(f"/api/library/loans/{lid}/renew", headers=H).status_code == 200
    assert admin.post(f"/api/library/loans/{lid}/renew", headers=H).status_code == 409  # max renewals
    assert admin.post(f"/api/library/returns", json={"accession_no": copies[1]["accession_no"]}, headers=H).status_code == 200
    assert admin.post(f"/api/library/returns", json={"accession_no": copies[1]["accession_no"]}, headers=H).status_code == 409


def test_late_return_fine_goes_on_the_fees_invoice(app, book):
    admin = login(app)
    admin.put("/api/library/settings", json={"fine_per_day": 25}, headers=H)  # 25 cents a day
    st = Student.query.filter_by(admission_no="LB-1").one()
    term = Term.query.filter_by(is_current=True).one()
    admin.post("/api/invoices/generate", json={"term_id": term.id, "class_id": st.class_id}, headers=H)
    inv = Invoice.query.filter_by(student_id=st.id, term_id=term.id, currency="USD").one()
    before = inv.total_cents if hasattr(inv, "total_cents") else None
    loan = Loan.query.filter_by(student_id=st.id).one()
    r = admin.post(f"/api/library/loans/{loan.id}/return", json={"condition": "Fair"}, headers=H)
    assert r.status_code == 200 and r.json["fine_status"] == "unpaid" and r.json["fine"] == 1.0  # 4 days x 0.25
    assert db.session.get(BookCopy, loan.copy_id).condition == "Fair"
    r = admin.post(f"/api/library/loans/{loan.id}/fine", json={"action": "charge"}, headers=H)
    assert r.status_code == 200 and r.json["fine_status"] == "charged" and r.json["fine_invoice"] == inv.invoice_no
    db.session.expire_all()
    inv = db.session.get(Invoice, inv.id)
    assert any(l.description.startswith("Library fine") and l.amount_cents == 100 for l in inv.lines)
    if before is not None:
        assert inv.total_cents == before + 100
    assert ledger.trial_balance(date.today(), "USD")["balanced"]
    assert admin.post(f"/api/library/loans/{loan.id}/fine", json={"action": "charge"}, headers=H).status_code == 409


def test_lost_book_and_waiving(app, book):
    admin = login(app)
    cls = SchoolClass.query.first()
    st = new_student(cls, "LB-3")
    copy = book["copy_list"][2]
    lid = admin.post("/api/library/loans", json={"copy_id": copy["id"], "student_id": st.id}, headers=H).json["id"]
    r = admin.post(f"/api/library/loans/{lid}/lost", headers=H)
    assert r.status_code == 200 and r.json["status"] == "lost" and r.json["fine"] == 12.5
    assert db.session.get(BookCopy, copy["id"]).status == "lost"
    # Waiving needs library approval: a teacher given "manage" only can't.
    teacher = User.query.filter_by(username="teacher").one()
    admin.put(f"/api/users/{teacher.id}", json={"extra_permissions": ["library.manage"]}, headers=H)
    t = login(app, "teacher", "Teacher@2026")
    r = t.post(f"/api/library/loans/{lid}/fine", json={"action": "waive", "note": "Found in class"}, headers=H)
    assert r.status_code == 403
    assert admin.post(f"/api/library/loans/{lid}/fine", json={"action": "waive"}, headers=H).status_code == 400  # reason
    r = admin.post(f"/api/library/loans/{lid}/fine", json={"action": "waive", "note": "Found in class"}, headers=H)
    assert r.status_code == 200 and r.json["fine_status"] == "waived"
    # The copy turns up: back on the shelf.
    assert admin.put(f"/api/library/copies/{copy['id']}", json={"status": "available"}, headers=H).json["status"] == "available"
    # A book with history can't be deleted.
    assert admin.delete(f"/api/library/books/{book['id']}", headers=H).status_code == 409


def test_issue_textbooks_to_a_class(app):
    admin = login(app)
    cls = SchoolClass.query.order_by(SchoolClass.level).first()
    students = Student.query.filter_by(class_id=cls.id, status="active").count()
    r = admin.post("/api/library/books", json={"title": "Class Reader", "category": "Textbook", "copies": students + 1}, headers=H)
    bid = r.json["id"]
    admin.put("/api/library/settings", json={"student_max": 1}, headers=H)
    r = admin.post("/api/library/issue-class", json={"book_id": bid, "class_id": cls.id}, headers=H)
    assert r.status_code == 200
    # Students with overdue books are skipped; everyone else gets one, limits don't apply to textbooks.
    assert len(r.json["issued"]) + len(r.json["skipped"]) == students
    assert all("overdue" in s["reason"] for s in r.json["skipped"])
    term = Term.query.filter_by(is_current=True).one()
    loan = Loan.query.join(BookCopy).filter(BookCopy.book_id == bid).first()
    assert loan.due_on == term.end_date
    again = admin.post("/api/library/issue-class", json={"book_id": bid, "class_id": cls.id}, headers=H).json
    assert again["issued"] == [] and again["already"] == len(r.json["issued"])
    admin.put("/api/library/settings", json={"student_max": 3}, headers=H)


def test_who_sees_what(app, book):
    teacher = login(app, "teacher", "Teacher@2026")
    assert teacher.get("/api/library/books").status_code == 200  # browse
    bursar = login(app, "bursar", "Bursar@2026")
    assert bursar.post("/api/library/books", json={"title": "X"}, headers=H).status_code == 403
    assert bursar.put("/api/library/settings", json={"student_max": 9}, headers=H).status_code == 403
    # Parents see their own children's books only.
    parent_user = User.query.filter_by(username="parent").one()
    child = parent_user.guardian.students[0]
    stranger = Student.query.filter(Student.guardian_id != parent_user.guardian.id).first()
    p = login(app, "parent", "Parent@2026")
    assert p.get(f"/api/library/students/{child.id}/loans").status_code == 200
    assert p.get(f"/api/library/students/{stranger.id}/loans").status_code == 403
    assert p.get("/api/library/loans").status_code == 403


def test_summary_and_lists(app):
    admin = login(app)
    r = admin.get("/api/library/loans?status=overdue").json
    assert all(l["status"] == "overdue" for l in r["items"])
    s = r["summary"]
    assert s["titles"] >= 3 and s["on_loan"] >= len(r["items"]) and s["overdue"] == len(r["items"])
    for status in ("open", "returned", "fines", "lost", "all"):
        assert admin.get(f"/api/library/loans?status={status}").status_code == 200
    assert admin.get("/api/library/loans?status=bad").status_code == 400
    st = Student.query.filter_by(admission_no="LB-1").one()
    b = admin.get(f"/api/library/borrowers/student/{st.id}").json
    assert b["loans"] and b["name"] == st.name
    assert any(x["type"] == "student" for x in admin.get("/api/library/borrowers?q=LB-").json["items"])
