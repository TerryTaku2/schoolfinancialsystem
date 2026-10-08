"""Three or more currencies at once: USD, ZWG and ZAR (and more) recorded side by side."""
from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine, inspect, text

from app import create_app, db, upgrade_schema
from app.models import (ExchangeRate, Invoice, JournalEntry, Payslip, PayrollRun, SchoolClass, Staff, StaffPayItem,
                        Student, Term)
from app.seed import seed_demo
from app.services import currency as fx
from app.services import ledger, payroll

H = {"X-Requested-With": "SchoolMS"}
ZWG, ZAR = 26.0, 18.0  # units per 1 USD in these tests


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


def test_school_chooses_its_currencies(app):
    admin = login(app)
    r = admin.put("/api/school", json={"currencies": ["ZAR", "ZWG", "USD"]}, headers=H)
    assert r.status_code == 200, r.json
    assert r.json["currencies"] == ["USD", "ZWG", "ZAR"]  # base first, then catalogue order
    assert r.json["rate_currencies"] == ["ZWG", "ZAR"]
    assert {c["code"] for c in r.json["currency_catalog"]} >= {"USD", "ZWG", "ZAR", "BWP", "GBP", "EUR"}
    # Every currency gets its own cash on hand, bank and mobile money accounts.
    for cur in ("ZWG", "ZAR"):
        codes = sorted(a.code for a in ledger.cash_accounts(cur))
        assert len(codes) == 3 and all(a.endswith("(" + cur + ")") for a in (x.name for x in ledger.cash_accounts(cur)))
    for code in fx.CASH_FAMILIES:
        assert fx.cash_account(code, "ZAR").currency == "ZAR"
        assert fx.cash_account(code, "ZAR").code != fx.cash_account(code, "ZWG").code
    assert admin.put("/api/school", json={"currencies": ["USD", "XYZ"]}, headers=H).status_code == 400
    assert admin.put("/api/school", json={"currencies": "USD"}, headers=H).status_code == 400


def test_rates_per_currency_against_usd(app):
    b = login(app, "bursar", "Bursar@2026")
    old = (date.today() - timedelta(days=200)).isoformat()
    assert b.post("/api/rates", json={"date": old, "rates": {"ZWG": ZWG, "ZAR": ZAR}}, headers=H).status_code == 201
    r = b.post("/api/rates", json={"rates": {"ZWG": ZWG, "ZAR": ZAR}}, headers=H)
    assert r.status_code == 201 and len(r.json["items"]) == 2  # same day, two currencies
    r = b.post("/api/rates", json={"currency": "ZAR", "rate": ZAR, "source": "Bank"}, headers=H)
    assert r.status_code == 201 and r.json["currency"] == "ZAR" and r.json["source"] == "Bank"
    assert ExchangeRate.query.filter_by(date=date.today(), currency="ZAR").count() == 1  # corrected, not duplicated
    assert b.post("/api/rates", json={"rate": 20}, headers=H).status_code == 400            # which currency?
    assert b.post("/api/rates", json={"currency": "USD", "rate": 1}, headers=H).status_code == 400
    assert b.post("/api/rates", json={"currency": "BWP", "rate": 13}, headers=H).status_code == 400  # not enabled
    listing = b.get("/api/rates").json
    assert set(listing["latest"]) == {"ZWG", "ZAR"} and listing["latest"]["ZAR"]["per_usd"] == ZAR
    assert {x["currency"] for x in b.get("/api/rates?currency=ZAR").json["items"]} == {"ZAR"}
    # Any pair converts through USD.
    assert fx.convert(1800, "ZAR", "USD") == 100
    assert fx.convert(1800, "ZAR", "ZWG") == 2600
    assert fx.convert(2600, "ZWG", "ZAR") == 1800


def test_rand_fees_and_receipts(app):
    b = login(app, "bursar", "Bursar@2026")
    term = Term.query.filter_by(is_current=True).first()
    cls = SchoolClass.query.order_by(SchoolClass.level).first()
    r = b.post("/api/fees", headers=H, json={"term_id": term.id, "class_id": cls.id, "name": "Sports tour (rand)",
                                             "amount": 900, "currency": "ZAR", "due_date": term.end_date.isoformat()})
    assert r.status_code == 201 and r.json["currency"] == "ZAR"
    st = Student(admission_no="MC-1", first_name="Thandi", last_name="Ncube", gender="Female", dob=date(2016, 3, 1),
                 school_class=cls)
    db.session.add(st)
    db.session.commit()
    assert b.post("/api/invoices/generate", json={"term_id": term.id, "class_id": cls.id}, headers=H).status_code == 200
    invoices = {i.currency: i for i in Invoice.query.filter_by(student_id=st.id, term_id=term.id)}
    assert set(invoices) == {"USD", "ZAR"} and invoices["ZAR"].total_cents == 90000
    r = b.post("/api/payments", json={"student_id": st.id, "amount": 400, "method": "mobile", "currency": "ZAR",
                                      "reference": "ECO-ZAR-1"},
               headers=H)
    assert r.status_code == 201 and r.json["currency"] == "ZAR"
    db.session.expire_all()
    assert Invoice.query.filter_by(student_id=st.id, term_id=term.id, currency="ZAR").one().balance_cents == 50000
    entry = JournalEntry.query.filter_by(source_type="payment", reference=r.json["receipt_no"]).one()
    assert entry.currency == "ZAR"
    assert {l.account.code for l in entry.lines} == {fx.cash_account("1020", "ZAR").code, "1100"}
    stmt = b.get(f"/api/students/{st.id}/statement").json
    assert {s["currency"] for s in stmt["sections"]} == {"USD", "ZAR"}
    # A currency the school doesn't use is refused.
    r = b.post("/api/payments", json={"student_id": st.id, "amount": 10, "method": "cash", "currency": "GBP"}, headers=H)
    assert r.status_code == 400 and "GBP" in r.json["error"]


def test_books_balance_in_every_currency_and_combined(app):
    today = date.today()
    for cur in fx.enabled():
        assert ledger.trial_balance(today, cur)["balanced"], cur
        assert ledger.balance_sheet(today, currency=cur)["balanced"], cur
    combined = ledger.trial_balance(today, "ALL")
    assert combined["balanced"]
    zar_mobile = fx.cash_account("1020", "ZAR")
    row = {r["code"]: r for r in combined["rows"]}
    assert row[zar_mobile.code]["debit"] == pytest.approx(ledger.balance(zar_mobile) / 100 / ZAR, abs=0.01)


def test_payroll_in_dollars_and_rand(app):
    period = payroll.month_add(PayrollRun.query.order_by(PayrollRun.period).first().period, -2)
    st = Staff(staff_no="MC-T1", first_name="Rand", last_name="Pay", position="Teacher", salary_cents=60000,
               salary_currency="USD", hire_date=date(2015, 1, 1))
    db.session.add(st)
    db.session.flush()
    db.session.add(StaffPayItem(staff=st, type="allowance", name="Rand allowance", amount_cents=180000, currency="ZAR"))
    db.session.commit()
    b = login(app, "bursar", "Bursar@2026")
    pay_date = payroll.month_start(period).replace(day=25)
    ExchangeRate.query.filter(ExchangeRate.date <= pay_date).delete()
    db.session.commit()
    r = b.post("/api/payroll/runs", json={"period": period, "pay_date": pay_date.isoformat()}, headers=H)
    assert r.status_code == 409 and "ZAR exchange rate" in r.json["error"]
    db.session.rollback()
    db.session.add(ExchangeRate(date=pay_date - timedelta(days=1), currency="ZAR", per_usd=ZAR))
    db.session.commit()
    r = b.post("/api/payroll/runs", json={"period": period, "pay_date": pay_date.isoformat()}, headers=H)
    assert r.status_code == 201, r.json
    assert r.json["exchange_rates"] == {"ZAR": ZAR}
    slip = Payslip.query.filter_by(run_id=r.json["id"], staff_id=st.id).one()
    # USD 600 + ZAR 1,800 (= USD 100) = USD 700, taxed as in the USD/ZWG case.
    assert slip.gross_cents == 70000 and slip.paye_cents == 13213
    parts = {p.currency: p for p in slip.currency_parts}
    assert parts["ZAR"].gross_cents == 180000
    assert abs(parts["ZAR"].paye_cents - (slip.paye_cents - parts["USD"].paye_cents) * ZAR) <= 0.5
    detail = b.get(f"/api/payroll/payslips/{slip.id}").json
    assert any("1 USD = 18 ZAR" in n for n in detail["notes"])


def test_currency_with_records_cannot_be_removed(app):
    admin = login(app)
    r = admin.put("/api/school", json={"currencies": ["USD", "ZWG"]}, headers=H)
    assert r.status_code == 409 and "ZAR" in r.json["error"]
    # An unused currency comes and goes freely.
    assert admin.put("/api/school", json={"currencies": ["USD", "ZWG", "ZAR", "BWP"]}, headers=H).json["currencies"] \
        == ["USD", "ZWG", "ZAR", "BWP"]
    assert len(ledger.cash_accounts("BWP")) == 3
    assert admin.put("/api/school", json={"currencies": ["USD", "ZWG", "ZAR"]}, headers=H).status_code == 200
    # The base currency is always kept.
    assert admin.put("/api/school", json={"currencies": ["ZWG", "ZAR"]}, headers=H).json["currencies"][0] == "USD"


def test_every_screen_loads_with_three_currencies(app):
    b = login(app, "bursar", "Bursar@2026")
    term = Term.query.filter_by(is_current=True).first()
    st = Student.query.filter_by(admission_no="MC-1").first()
    run = PayrollRun.query.order_by(PayrollRun.id.desc()).first()
    paths = ["/meta", "/dashboard", f"/fees?term_id={term.id}", "/invoices?currency=ZAR", "/payments?currency=ZAR",
             "/expenses", f"/students/{st.id}/statement", "/rates", "/school", "/accounting/settings",
             f"/reports/fees?term_id={term.id}&currency=ZAR", "/accounting/trial-balance?currency=ALL",
             "/accounting/balance-sheet?currency=ALL", "/accounting/income-statement?currency=ZAR",
             "/accounting/cash-flow?currency=ZAR", f"/payroll/runs/{run.id}", "/banking/accounts"]
    for path in paths:
        r = b.get("/api" + path)
        assert r.status_code in (200, 404) if path == "/bank/accounts" else r.status_code == 200, \
            (path, r.status_code, r.get_data(as_text=True)[:200])


def test_old_one_rate_a_day_table_is_upgraded(tmp_path):
    """A database from the two-currency version: one ZWG rate per day, no currency column."""
    app = create_app("config.TestConfig")
    with app.app_context():
        engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
        db.metadata.create_all(engine)
        with engine.connect() as conn:
            conn.execute(text("DROP TABLE exchange_rate"))
            conn.execute(text("""CREATE TABLE exchange_rate (id INTEGER PRIMARY KEY, date DATE NOT NULL,
                zwg_per_usd FLOAT NOT NULL, source VARCHAR(60), entered_by INTEGER REFERENCES user(id),
                created_at DATETIME NOT NULL, CONSTRAINT ck_rate_positive CHECK (zwg_per_usd > 0))"""))
            conn.execute(text("CREATE UNIQUE INDEX ix_exchange_rate_date ON exchange_rate (date)"))
            conn.execute(text("INSERT INTO exchange_rate VALUES (1, '2026-01-05', 26.5, 'RBZ', NULL, '2026-01-05')"))
            conn.commit()
        upgrade_schema(engine)
        insp = inspect(engine)
        assert "currency" in {c["name"] for c in insp.get_columns("exchange_rate")}
        uniques = [sorted(u["column_names"]) for u in insp.get_unique_constraints("exchange_rate")]
        assert ["currency", "date"] in uniques
        assert not any(i.get("unique") and i["column_names"] == ["date"] for i in insp.get_indexes("exchange_rate"))
        with engine.begin() as conn:
            conn.execute(text("UPDATE exchange_rate SET currency = 'ZWG' WHERE currency IS NULL"))
            conn.execute(text("INSERT INTO exchange_rate (date, currency, zwg_per_usd, created_at) "
                              "VALUES ('2026-01-05', 'ZAR', 18.2, '2026-01-05')"))
            rows = conn.execute(text("SELECT currency, zwg_per_usd FROM exchange_rate ORDER BY id")).fetchall()
        assert [tuple(r) for r in rows] == [("ZWG", 26.5), ("ZAR", 18.2)]
