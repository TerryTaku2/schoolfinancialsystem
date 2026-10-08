"""General ledger and financial statement tests."""
from datetime import date, timedelta

import pytest

from app import create_app, db
from app.models import AcademicYear, Expense, JournalEntry, SchoolClass, Student, Term, User
from app.seed import seed_demo
from app.services import finance, ledger

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


def login(app, username="admin", password="Admin@2026"):
    c = app.test_client()
    assert c.post("/api/auth/login", json={"username": username, "password": password}, headers=H).status_code == 200
    return c


def year():
    y = AcademicYear.query.first()
    return y.start_date, date.today()


def assert_books_consistent():
    start, end = year()
    assert ledger.trial_balance(end)["balanced"]
    assert ledger.balance_sheet(end, start - timedelta(days=1))["balanced"]
    assert ledger.cash_flow(start, end)["reconciled"]
    # Income statement surplus for the year == change in accumulated surplus on the balance sheet.
    inc = ledger.income_statement(start, end)
    bs = ledger.balance_sheet(end, start - timedelta(days=1))
    acc = [r for r in bs["equity"]["rows"] if r["name"].startswith("Accumulated surplus")][0]
    assert inc["surplus"] == pytest.approx(acc["amount"] - acc["prior"])


def test_seeded_books_are_consistent(app):
    assert_books_consistent()


def test_receivable_ledger_matches_invoices(app):
    owed, advance = ledger.receivable_split(date.today())
    outstanding = sum(i.balance_cents for s in Student.query for i in s.invoices if not i.void)
    credit = sum(finance.credit_cents(s) for s in Student.query)
    assert owed == outstanding and advance == credit


def test_payment_and_void_post_and_reverse(app):
    bursar = User.query.filter_by(username="bursar").first()
    st = Student.query.filter_by(status="active").first()
    p = finance.record_payment(st, 12345, "mobile", "TXTEST1", date.today(), bursar)
    db.session.commit()
    e = JournalEntry.query.filter_by(source_type="payment", source_id=p.id).one()
    lines = {l.account.code: l for l in e.lines}
    assert lines["1020"].debit_cents == 12345 and lines["1100"].credit_cents == 12345
    assert lines["1100"].student_id == st.id
    finance.void_payment(p, "Duplicate entry")
    db.session.commit()
    assert len(e.reversals) == 1 and e.reversals[0].date == date.today()
    assert_books_consistent()


def test_overpayment_shows_as_fees_in_advance(app):
    bursar = User.query.filter_by(username="bursar").first()
    st = Student.query.filter_by(status="active").all()[-1]
    before = ledger.receivable_split(date.today())[1]
    owed = sum(i.balance_cents for i in st.invoices if not i.void)
    finance.record_payment(st, owed + 50000, "cash", None, date.today(), bursar)
    db.session.commit()
    assert ledger.receivable_split(date.today())[1] == before + 50000
    bs = ledger.balance_sheet(date.today())
    adv = [r for r in bs["liabilities"]["rows"] if r["name"] == "Fees received in advance"][0]
    assert adv["amount"] >= 500
    assert_books_consistent()


def test_invoice_void_reverses_revenue(app):
    st = Student(admission_no="LEDG1", first_name="Led", last_name="Ger", gender="Male", dob=date(2015, 2, 2),
                 school_class=SchoolClass.query.first())
    db.session.add(st)
    db.session.flush()
    term = Term.query.filter_by(is_current=True).first()
    inv = finance.build_invoice(st, term)
    tuition = ledger.balance(ledger.acct("4000"))
    finance.void_invoice(inv, "Enrolled in error")
    db.session.commit()
    assert ledger.balance(ledger.acct("4000")) < tuition
    assert_books_consistent()


def test_manual_journal_rules(app):
    c = login(app)
    bank, ppe, rec = ledger.acct("1000"), ledger.acct("1500"), ledger.acct("1100")  # bank = cash on hand
    today = date.today().isoformat()
    post = lambda lines, d=today: c.post("/api/accounting/journals", headers=H,
                                         json={"date": d, "description": "Test", "lines": lines})
    assert post([{"account_id": ppe.id, "debit": 100}, {"account_id": bank.id, "credit": 90}]).status_code == 400
    assert post([{"account_id": ppe.id, "debit": 100}, {"account_id": rec.id, "credit": 100}]).status_code == 400
    future = (date.today() + timedelta(days=2)).isoformat()
    assert post([{"account_id": ppe.id, "debit": 100}, {"account_id": bank.id, "credit": 100}], future).status_code == 400
    assert post([{"account_id": ppe.id, "debit": 10 ** 9}, {"account_id": bank.id, "credit": 10 ** 9}]).status_code == 409
    r = post([{"account_id": ppe.id, "debit": 100}, {"account_id": bank.id, "credit": 100}])
    assert r.status_code == 201
    eid = r.json["id"]
    assert c.post(f"/api/accounting/journals/{eid}/reverse", json={"reason": "Wrong account"}, headers=H).status_code == 201
    assert c.post(f"/api/accounting/journals/{eid}/reverse", json={"reason": "Again please"}, headers=H).status_code == 400
    system = JournalEntry.query.filter_by(source_type="payment").first()
    assert c.post(f"/api/accounting/journals/{system.id}/reverse", json={"reason": "Not allowed"}, headers=H).status_code == 400


def test_period_lock(app):
    c = login(app)
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    assert c.put("/api/accounting/settings", json={"lock_date": tomorrow}, headers=H).status_code == 400
    lock = date.today() - timedelta(days=30)
    assert c.put("/api/accounting/settings", json={"lock_date": lock.isoformat()}, headers=H).status_code == 200
    b = login(app, "bursar", "Bursar@2026")
    st = Student.query.filter_by(status="active").first()
    r = b.post("/api/payments", json={"student_id": st.id, "amount": 10, "method": "cash",
                                      "paid_on": (lock - timedelta(days=1)).isoformat()}, headers=H)
    assert r.status_code == 409
    r = b.post("/api/payments", json={"student_id": st.id, "amount": 10, "method": "cash"}, headers=H)
    assert r.status_code == 201
    # Bursars cannot reopen periods.
    assert b.put("/api/accounting/settings", json={"lock_date": None}, headers=H).status_code == 403
    assert c.put("/api/accounting/settings", json={"lock_date": None}, headers=H).status_code == 200


def test_expense_accrual_and_payment(app):
    admin = login(app)
    b = login(app, "bursar", "Bursar@2026")
    today = date.today().isoformat()
    eid = b.post("/api/expenses", json={"category": "Supplies", "description": "Chalk", "amount": 40,
                                        "expense_date": today}, headers=H).json["id"]
    payable = ledger.balance(ledger.acct("2000"))
    assert admin.post(f"/api/expenses/{eid}/status", json={"status": "approved"}, headers=H).status_code == 200
    assert ledger.balance(ledger.acct("2000")) == payable + 4000
    huge = b.post("/api/expenses", json={"category": "IT", "description": "Server", "amount": 10 ** 7,
                                         "expense_date": today}, headers=H).json["id"]
    admin.post(f"/api/expenses/{huge}/status", json={"status": "approved"}, headers=H)
    cash = ledger.acct("1000")
    r = b.post(f"/api/expenses/{huge}/status", json={"status": "paid", "account_id": cash.id}, headers=H)
    assert r.status_code == 409  # insufficient funds
    r = b.post(f"/api/expenses/{eid}/status", json={"status": "paid", "account_id": cash.id}, headers=H)
    assert r.status_code == 200 and db.session.get(Expense, eid).paid_from_account_id == cash.id
    assert ledger.balance(ledger.acct("2000")) == payable + 10 ** 9  # only the unpaid server remains accrued
    assert_books_consistent()


def test_account_code_rules(app):
    c = login(app)
    assert c.post("/api/accounting/accounts", json={"code": "4999", "name": "Bad", "type": "expense"}, headers=H).status_code == 400
    assert c.post("/api/accounting/accounts", json={"code": "1030", "name": "Petty Cash 2", "type": "asset", "subtype": "cash"}, headers=H).status_code == 201
    sys_acct = ledger.acct("1100")
    assert c.put(f"/api/accounting/accounts/{sys_acct.id}", json={"active": False}, headers=H).status_code == 400


def test_statement_endpoints(app):
    c = login(app, "bursar", "Bursar@2026")
    for path in ["/api/accounting/accounts", "/api/accounting/journals", "/api/accounting/trial-balance",
                 "/api/accounting/income-statement", "/api/accounting/balance-sheet", "/api/accounting/cash-flow",
                 f"/api/accounting/ledger?account_id={ledger.acct('1010').id}", "/api/accounting/settings"]:
        assert c.get(path).status_code == 200, path
    t = login(app, "teacher", "Teacher@2026")
    assert t.get("/api/accounting/income-statement").status_code == 403


def test_rebuild_reproduces_balances(app):
    end = date.today()
    before = ledger.trial_balance(end)["rows"]
    ledger.rebuild_ledger()
    db.session.commit()
    after = ledger.trial_balance(end)["rows"]
    strip = lambda rows: {(r["code"], r["debit"], r["credit"]) for r in rows}
    assert strip(before) == strip(after)
    assert_books_consistent()

