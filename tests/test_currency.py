"""USD and ZWG recorded side by side (no conversion), daily rates, and split-currency payroll."""
from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine, inspect, text

from app import create_app, db, upgrade_schema
from app.models import (ExchangeRate, FeeItem, Invoice, JournalEntry, Payslip, PayrollRun, SchoolClass, Staff,
                        StaffPayItem, Student, Term, User)
from app.seed import seed_demo
from app.services import currency as fx
from app.services import finance, ledger, payroll

H = {"X-Requested-With": "SchoolMS"}
RATE = 26.0  # ZWG per USD in these tests


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


def test_zwg_needs_dual_currency(app):
    b = login(app, "bursar", "Bursar@2026")
    st = Student.query.filter_by(status="active").first()
    r = b.post("/api/payments", json={"student_id": st.id, "amount": 100, "method": "cash", "currency": "ZWG"}, headers=H)
    assert r.status_code == 400 and "dual currency" in r.json["error"]


def test_turning_on_dual_currency(app):
    admin = login(app)
    r = admin.put("/api/school", json={"dual_currency": True}, headers=H)
    assert r.status_code == 200 and r.json["currencies"] == ["USD", "ZWG"]
    zwg_cash = {a.code: a for a in ledger.cash_accounts("ZWG")}
    assert set(zwg_cash) == {"1001", "1011", "1021"}
    assert ledger.acct("1010").currency == "USD" and ledger.acct("1010").name.endswith("(USD)")
    b = login(app, "bursar", "Bursar@2026")
    assert b.post("/api/rates", json={"zwg_per_usd": RATE, "date": (date.today() - timedelta(days=60)).isoformat()},
                  headers=H).status_code == 201
    assert b.post("/api/rates", json={"zwg_per_usd": RATE}, headers=H).status_code == 201
    future = (date.today() + timedelta(days=1)).isoformat()
    assert b.post("/api/rates", json={"zwg_per_usd": 30, "date": future}, headers=H).status_code == 400


def test_fees_and_payments_stay_in_their_currency(app):
    b = login(app, "bursar", "Bursar@2026")
    term = Term.query.filter_by(is_current=True).first()
    cls = SchoolClass.query.order_by(SchoolClass.level).first()
    r = b.post("/api/fees", headers=H, json={"term_id": term.id, "class_id": cls.id, "name": "Development levy (ZiG)",
                                             "amount": 1300, "currency": "ZWG", "due_date": term.end_date.isoformat()})
    assert r.status_code == 201 and r.json["currency"] == "ZWG"
    st = Student(admission_no="FX-1", first_name="Rudo", last_name="Moyo", gender="Female", dob=date(2016, 1, 1),
                 school_class=cls)
    db.session.add(st)
    db.session.commit()
    r = b.post("/api/invoices/generate", json={"term_id": term.id, "class_id": cls.id}, headers=H)
    assert r.status_code == 200
    invoices = {i.currency: i for i in Invoice.query.filter_by(student_id=st.id, term_id=term.id)}
    assert set(invoices) == {"USD", "ZWG"} and invoices["ZWG"].total_cents == 130000
    # Re-running is idempotent per currency.
    b.post("/api/invoices/generate", json={"term_id": term.id, "class_id": cls.id}, headers=H)
    assert Invoice.query.filter_by(student_id=st.id, term_id=term.id).count() == 2

    usd_before = invoices["USD"].balance_cents
    r = b.post("/api/payments", json={"student_id": st.id, "amount": 500, "method": "mobile", "currency": "ZWG",
                                      "reference": "ECO-ZWG-1"}, headers=H)
    assert r.status_code == 201 and r.json["currency"] == "ZWG"
    db.session.expire_all()
    invoices = {i.currency: i for i in Invoice.query.filter_by(student_id=st.id, term_id=term.id)}
    assert invoices["ZWG"].balance_cents == 80000          # ZWG 1300 - 500, no conversion
    assert invoices["USD"].balance_cents == usd_before     # untouched by a ZWG payment
    entry = JournalEntry.query.filter_by(source_type="payment", reference=r.json["receipt_no"]).one()
    assert entry.currency == "ZWG" and {l.account.code for l in entry.lines} == {"1021", "1100"}
    summary = {s["currency"]: s for s in finance.account_summary(st)["by_currency"]}
    assert summary["ZWG"]["outstanding"] == 800 and summary["USD"]["outstanding"] == usd_before / 100
    stmt = b.get(f"/api/students/{st.id}/statement").json
    assert {s["currency"] for s in stmt["sections"]} == {"USD", "ZWG"}


def test_each_currency_has_balanced_books(app):
    today = date.today()
    for cur in ("USD", "ZWG"):
        assert ledger.trial_balance(today, cur)["balanced"]
        assert ledger.balance_sheet(today, currency=cur)["balanced"]
        year_start = Term.query.order_by(Term.start_date).first().start_date
        assert ledger.cash_flow(year_start, today, cur)["reconciled"]
    zwg_tb = ledger.trial_balance(today, "ZWG")
    assert {r["code"] for r in zwg_tb["rows"]} >= {"1021", "1100"}
    combined = ledger.trial_balance(today, "ALL")
    assert combined["balanced"]
    usd_cash = ledger.balance(ledger.acct("1020"))
    zwg_cash = ledger.balance(ledger.acct("1021"))
    # Combined shows ZWG translated at the day's rate.
    row = {r["code"]: r for r in ledger.trial_balance(today, "ALL")["rows"]}
    assert row["1021"]["debit"] == pytest.approx(zwg_cash / 100 / RATE, abs=0.01)
    assert row["1020"]["debit"] == pytest.approx(usd_cash / 100, abs=0.01)


def test_currency_must_match_cash_account(app):
    admin = login(app)
    b = login(app, "bursar", "Bursar@2026")
    eid = b.post("/api/expenses", json={"category": "Supplies", "description": "Chalk (ZiG)", "amount": 260,
                                        "currency": "ZWG", "expense_date": date.today().isoformat()}, headers=H).json["id"]
    admin.post(f"/api/expenses/{eid}/status", json={"status": "approved"}, headers=H)
    usd_bank = ledger.acct("1010")
    r = b.post(f"/api/expenses/{eid}/status", json={"status": "paid", "account_id": usd_bank.id}, headers=H)
    assert r.status_code == 400 and "ZWG" in r.json["error"]
    zwg_mobile = ledger.acct("1021")
    assert b.post(f"/api/expenses/{eid}/status", json={"status": "paid", "account_id": zwg_mobile.id}, headers=H).status_code == 200
    r = b.post("/api/accounting/journals", headers=H, json={
        "date": date.today().isoformat(), "description": "Wrong", "currency": "ZWG",
        "lines": [{"account_id": ledger.acct("5800").id, "debit": 10}, {"account_id": usd_bank.id, "credit": 10}]})
    assert r.status_code == 400


def test_split_currency_payroll_follows_zimra(app):
    period = payroll.month_add(PayrollRun.query.order_by(PayrollRun.period).first().period, -2)
    st = Staff(staff_no="FX-T1", first_name="Split", last_name="Pay", position="Teacher", salary_cents=60000,
               salary_currency="USD", hire_date=date(2015, 1, 1))
    db.session.add(st)
    db.session.flush()
    db.session.add(StaffPayItem(staff=st, type="allowance", name="ZiG allowance", amount_cents=260000, currency="ZWG"))
    db.session.commit()
    b = login(app, "bursar", "Bursar@2026")
    admin = login(app)
    # Pay for that month needs a rate on or before its pay date.
    pay_date = payroll.month_start(period).replace(day=25)
    r = b.post("/api/payroll/runs", json={"period": period, "pay_date": pay_date.isoformat()}, headers=H)
    assert r.status_code == 409 and "exchange rate" in r.json["error"]
    db.session.rollback()
    db.session.add(ExchangeRate(date=pay_date - timedelta(days=1), zwg_per_usd=RATE))
    db.session.commit()
    r = b.post("/api/payroll/runs", json={"period": period, "pay_date": pay_date.isoformat()}, headers=H)
    assert r.status_code == 201, r.json
    slip = Payslip.query.filter_by(run_id=r.json["id"], staff_id=st.id).one()
    # Tax on the total: USD 600 + ZWG 2,600 (= USD 100) = USD 700; NSSA 31.50; taxable 668.50.
    assert slip.gross_cents == 70000 and slip.taxable_cents == 66850
    assert slip.paye_cents == 13213  # 668.50 x 25% - 35 = 132.125, rounded half up
    parts = {p.currency: p for p in slip.currency_parts}
    assert parts["USD"].gross_cents == 60000 and parts["ZWG"].gross_cents == 260000
    # PAYE withheld 6/7 in USD and 1/7 in ZWG (converted back at the rate).
    usd_share = parts["USD"].paye_cents
    zwg_share_usd = slip.paye_cents - usd_share
    assert abs(usd_share - slip.paye_cents * 6 / 7) <= 0.5
    assert abs(parts["ZWG"].paye_cents - zwg_share_usd * RATE) <= 0.5
    for p in parts.values():
        assert p.net_cents == p.gross_cents - p.nssa_cents - p.paye_cents - p.aids_levy_cents - p.pension_cents \
            - p.medical_aid_cents - p.other_deductions_cents
    rid = r.json["id"]
    assert admin.post(f"/api/payroll/runs/{rid}/approve", headers=H).status_code == 200
    entries = JournalEntry.query.filter_by(source_type="payroll", source_id=rid).all()
    assert {e.currency for e in entries} == {"USD", "ZWG"}
    due = payroll.liabilities(db.session.get(PayrollRun, rid))
    assert due["ZWG"]["zimra"] > 0 and due["USD"]["zimra"] > 0
    # ZIMRA is paid in each currency from an account in that currency.
    ledger.post(date.today(), "Funding", [{"account": ledger.acct("1011"), "debit": 10 ** 9},
                                          {"account": ledger.acct("3000"), "credit": 10 ** 9}], currency="ZWG")
    db.session.commit()
    r = b.post(f"/api/payroll/runs/{rid}/remit", json={"type": "zimra", "currency": "ZWG", "account_id": ledger.acct("1010").id,
                                                       "reference": "Z-1"}, headers=H)
    assert r.status_code == 400
    r = b.post(f"/api/payroll/runs/{rid}/remit", json={"type": "zimra", "currency": "ZWG", "account_id": ledger.acct("1011").id,
                                                       "reference": "Z-1"}, headers=H)
    assert r.status_code == 200
    p2 = {(x["period"], x["currency"]): x for x in b.get(f"/api/payroll/returns?year={period[:4]}").json["p2"]}
    assert p2[(period, "ZWG")]["reference"] == "Z-1"
    for cur in ("USD", "ZWG"):
        assert ledger.trial_balance(date.today(), cur)["balanced"]


def test_old_unique_rules_are_widened(tmp_path):
    """A database from before dual currency allowed one invoice per student per term."""
    app = create_app("config.TestConfig")
    with app.app_context():
        engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
        db.metadata.create_all(engine)
        with engine.connect() as conn:
            conn.exec_driver_sql("PRAGMA foreign_keys=OFF")  # bare fixture rows without students/terms
            conn.commit()
            conn.execute(text("DROP TABLE invoice"))
            conn.execute(text("""CREATE TABLE invoice (id INTEGER PRIMARY KEY, created_at DATETIME NOT NULL,
                invoice_no VARCHAR(20) NOT NULL UNIQUE, student_id INTEGER NOT NULL REFERENCES student(id),
                term_id INTEGER NOT NULL REFERENCES term(id), issue_date DATE NOT NULL, due_date DATE NOT NULL,
                void BOOLEAN NOT NULL, void_reason VARCHAR(200), voided_at DATE, UNIQUE (student_id, term_id))"""))
            conn.execute(text("INSERT INTO invoice VALUES (1, '2026-01-01', 'INV-1', 1, 1, '2026-01-01', '2026-02-01', 0, NULL, NULL)"))
            conn.commit()
        upgrade_schema(engine)
        insp = inspect(engine)
        uniques = [sorted(u["column_names"]) for u in insp.get_unique_constraints("invoice")]
        assert ["currency", "student_id", "term_id"] in uniques and ["student_id", "term_id"] not in uniques
        with engine.connect() as conn:
            assert conn.execute(text("SELECT invoice_no FROM invoice")).scalar() == "INV-1"
        # Other tables still point at the rebuilt table.
        fks = insp.get_foreign_keys("invoice_line")
        assert any(fk["referred_table"] == "invoice" for fk in fks)


def test_every_finance_screen_loads_in_dual_currency(app):
    """Each screen's data endpoint answers with both currencies in play."""
    b = login(app, "bursar", "Bursar@2026")
    term = Term.query.filter_by(is_current=True).first()
    st = Student.query.filter_by(admission_no="FX-1").first()
    run = PayrollRun.query.order_by(PayrollRun.id.desc()).first()
    paths = ["/meta", "/dashboard", f"/fees?term_id={term.id}", "/invoices", "/invoices?currency=ZWG", "/payments",
             "/payments?currency=ZWG", "/expenses", f"/students/{st.id}", f"/students/{st.id}/statement",
             "/students?status=all", "/rates", "/school", f"/reports/fees?term_id={term.id}&currency=ZWG",
             "/reports/cashflow?currency=ZWG", "/accounting/accounts", "/accounting/journals?currency=ZWG",
             "/accounting/settings", "/accounting/trial-balance?currency=ALL", "/accounting/income-statement?currency=ZWG",
             "/accounting/balance-sheet?currency=ALL", "/accounting/cash-flow?currency=ZWG",
             f"/accounting/ledger?account_id={ledger.acct('1100').id}&currency=ZWG", "/assets", "/assets/depreciation",
             "/payroll/runs", f"/payroll/runs/{run.id}", "/payroll/staff", f"/payroll/returns?year={run.period[:4]}"]
    for path in paths:
        r = b.get("/api" + path)
        assert r.status_code == 200, (path, r.status_code, r.get_data(as_text=True)[:200])
    fees = b.get(f"/api/fees?term_id={term.id}").json
    assert any(f["currency"] == "ZWG" for f in fees["items"])
    assert any("ZWG" in t["by_currency"] for t in fees["totals"])
