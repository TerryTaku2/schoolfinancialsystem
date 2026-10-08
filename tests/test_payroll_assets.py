"""Payroll (ZIMRA PAYE, NSSA, ZIMDEF) and fixed asset register tests."""
import json
from datetime import date, timedelta

import pytest

from app import create_app, db
from app.models import (FixedAsset, JournalEntry, Payslip, PayrollRun, Staff, StaffPayItem, TaxTable)
from app.seed import seed_demo
from app.services import assets, ledger, payroll

from test_accounting import assert_books_consistent

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


def free_period():
    """A past month with no payroll yet (before the seeded year)."""
    first = PayrollRun.query.filter(PayrollRun.run_no.like("PR-%")).order_by(PayrollRun.period).first().period
    return assets.month_add(first, -3)


def new_staff(salary_cents, **kw):
    s = Staff(staff_no=f"T{Staff.query.count() + 900}", first_name="Pay", last_name=f"Test{Staff.query.count()}",
              position="Teacher", salary_cents=salary_cents, hire_date=date(2015, 1, 1), **kw)
    db.session.add(s)
    db.session.flush()
    return s


def slip_for(staff, period="2019-06", bonus=0):
    """Calculate a standalone payslip without touching the books."""
    run = PayrollRun(run_no=f"X{staff.id}{period}{bonus}", period=period, pay_date=date(2019, 6, 25),
                     tax_table=TaxTable.query.first())
    slip = Payslip(staff=staff, run=run, bonus_cents=bonus)
    db.session.add(run)
    db.session.flush()
    return payroll.calculate(slip, payroll.ZIMRA_USD_2025)


# ---------------------------------------------------------------- PAYE calculation
def test_zimra_bands_match_published_table():
    bands = payroll.ZIMRA_USD_2025["bands"]
    # ZIMRA USD 2025 monthly table: rate x income less the published deduction.
    for income, rate, less in [(250, 20, 20), (800, 25, 35), (1500, 30, 85), (2500, 35, 185), (5000, 40, 335)]:
        assert payroll.band_tax(income * 100, bands) == round((income * rate / 100 - less) * 100)
    assert payroll.band_tax(10000, bands) == 0
    assert payroll.zimra_deduction_column(bands) == [0, 20, 35, 85, 185, 335]


def test_basic_payslip(app):
    s = slip_for(new_staff(150000))
    assert s.nssa_cents == 3150                 # 4.5% of the US$700 ceiling
    assert s.taxable_cents == 146850            # 1500 - 31.50
    assert s.paye_cents == 35555                # 1468.50 x 30% - 85
    assert s.aids_levy_cents == 1067            # 3% of PAYE
    assert s.net_cents == 150000 - 3150 - 35555 - 1067
    assert s.nssa_employer_cents == 3150 and s.zimdef_cents == 1500


def test_credits_pension_cap_and_levy_after_credits(app):
    st = new_staff(200000, dob=date(1950, 1, 1))
    db.session.add_all([StaffPayItem(staff=st, type="pension", name="Pension", amount_cents=60000),
                        StaffPayItem(staff=st, type="medical_aid", name="Medical aid", amount_cents=10000),
                        StaffPayItem(staff=st, type="benefit", name="Housing benefit", amount_cents=20000)])
    db.session.flush()
    s = slip_for(st)
    # Gross 2000 + benefit 200 - NSSA 31.50 - pension capped at 450 = 1718.50 taxable.
    assert s.taxable_cents == 171850
    assert s.tax_before_credits_cents == round((1718.50 * 0.30 - 85) * 100)
    assert s.credits_cents == 7500 + 5000       # elderly credit + 50% of medical aid
    assert s.paye_cents == s.tax_before_credits_cents - 12500
    assert s.aids_levy_cents == round(s.paye_cents * 0.03)
    # The benefit is taxed but not paid out; pension and medical aid come off net pay.
    assert s.gross_cents == 200000
    assert s.net_cents == 200000 - 3150 - 60000 - 10000 - s.paye_cents - s.aids_levy_cents


def test_low_earner_pays_no_tax(app):
    s = slip_for(new_staff(10000))
    assert s.paye_cents == 0 and s.aids_levy_cents == 0


def test_bonus_exemption_is_annual(app):
    c = login(app, "bursar", "Bursar@2026")
    admin = login(app)
    st = new_staff(100000)
    db.session.commit()
    # Two runs in the same tax year, each with a US$500 bonus: US$700 exempt in total.
    periods = [free_period(), assets.month_add(free_period(), 1)]
    exempt = []
    for p in periods:
        r = c.post("/api/payroll/runs", json={"period": p}, headers=H)
        assert r.status_code == 201, r.json
        slip = next(s for s in r.json["payslips"] if s["staff_id"] == st.id)
        assert c.put(f"/api/payroll/payslips/{slip['id']}", json={"bonus": 500}, headers=H).status_code == 200
        exempt.append(json.loads(db.session.get(Payslip, slip["id"]).detail)["bonus_exempt"])
        assert admin.post(f"/api/payroll/runs/{r.json['id']}/approve", headers=H).status_code == 200
    if periods[0][:4] == periods[1][:4]:
        assert exempt == [50000, 20000]
    assert_books_consistent()


# ---------------------------------------------------------------- payroll workflow
def test_payroll_workflow_and_postings(app):
    b = login(app, "bursar", "Bursar@2026")
    admin = login(app)
    period = assets.month_add(free_period(), 2)
    r = b.post("/api/payroll/runs", json={"period": period}, headers=H)
    assert r.status_code == 201
    rid = r.json["id"]
    assert b.post("/api/payroll/runs", json={"period": period}, headers=H).status_code == 409  # one per month
    # The preparer can't approve; an administrator must.
    assert b.post(f"/api/payroll/runs/{rid}/approve", headers=H).status_code == 403
    r = admin.post(f"/api/payroll/runs/{rid}/approve", headers=H)
    assert r.status_code == 200 and r.json["status"] == "approved"
    run = db.session.get(PayrollRun, rid)
    t = payroll.totals(run)
    entry = JournalEntry.query.filter_by(source_type="payroll", source_id=rid).one()
    assert entry.date == assets.month_end(period)
    by_code = {l.account.code: (l.debit_cents, l.credit_cents) for l in entry.lines}
    assert by_code["5000"] == (t["gross"], 0)
    assert by_code["2100"] == (0, t["paye"] + t["aids_levy"])
    assert by_code["2150"] == (0, t["net"])
    assert sum(d for d, _ in by_code.values()) == sum(c for _, c in by_code.values())
    # Approved runs are frozen.
    slip = run.payslips[0]
    assert b.put(f"/api/payroll/payslips/{slip.id}", json={"bonus": 10}, headers=H).status_code == 409

    bank = ledger.acct("1010")
    ledger.post(date.today(), "Test funding", [{"account": bank, "debit": 10 ** 8}, {"account": ledger.acct("3000"), "credit": 10 ** 8}])
    db.session.commit()
    paid_on = assets.month_end(period).isoformat()
    r = b.post(f"/api/payroll/runs/{rid}/remit", json={"type": "zimra", "account_id": bank.id, "paid_on": paid_on,
                                                        "reference": "ZIMRA-TEST"}, headers=H)
    assert r.status_code == 409  # bank balance on that past date is too low
    r = b.post(f"/api/payroll/runs/{rid}/remit", json={"type": "zimra", "account_id": bank.id,
                                                        "reference": "ZIMRA-TEST"}, headers=H)
    assert r.status_code == 200
    assert ledger.balance(ledger.acct("2100")) >= 0
    assert b.post(f"/api/payroll/runs/{rid}/remit", json={"type": "zimra", "account_id": bank.id,
                                                           "reference": "AGAIN"}, headers=H).status_code == 409
    # Once money has gone out the run can't be voided.
    assert admin.post(f"/api/payroll/runs/{rid}/void", json={"reason": "Mistake here"}, headers=H).status_code == 409
    r = b.post(f"/api/payroll/runs/{rid}/pay", json={"account_id": bank.id}, headers=H)
    assert r.status_code == 200 and r.json["status"] == "paid"
    returns = b.get(f"/api/payroll/returns?year={period[:4]}").json
    row = next(p for p in returns["p2"] if p["period"] == period)
    assert row["total_due"] == pytest.approx((t["paye"] + t["aids_levy"]) / 100) and row["reference"] == "ZIMRA-TEST"
    assert_books_consistent()


def test_void_approved_payroll_reverses_accrual(app):
    b = login(app, "bursar", "Bursar@2026")
    admin = login(app)
    period = assets.month_add(free_period(), -1)
    rid = b.post("/api/payroll/runs", json={"period": period}, headers=H).json["id"]
    admin.post(f"/api/payroll/runs/{rid}/approve", headers=H)
    net_before = ledger.balance(ledger.acct("2150"))
    assert b.post(f"/api/payroll/runs/{rid}/void", json={"reason": "Wrong month"}, headers=H).status_code == 403
    assert admin.post(f"/api/payroll/runs/{rid}/void", json={"reason": "Wrong month"}, headers=H).status_code == 200
    assert ledger.balance(ledger.acct("2150")) == net_before - payroll.totals(db.session.get(PayrollRun, rid))["net"]
    # The month is free again.
    assert b.post("/api/payroll/runs", json={"period": period}, headers=H).status_code == 201
    assert_books_consistent()


def test_payroll_control_accounts_blocked_in_manual_journals(app):
    c = login(app)
    r = c.post("/api/accounting/journals", headers=H, json={"date": date.today().isoformat(), "description": "x", "lines": [
        {"account_id": ledger.acct("2100").id, "debit": 5}, {"account_id": ledger.acct("1000").id, "credit": 5}]})
    assert r.status_code == 400


def test_tax_table_rules(app):
    admin = login(app)
    b = login(app, "bursar", "Bursar@2026")
    cfg = dict(payroll.ZIMRA_USD_2025, bands=[{"upto": 150, "rate": 0}, {"upto": 100, "rate": 20}, {"upto": None, "rate": 30}])
    future = date(date.today().year + 1, 1, 1).isoformat()
    assert b.post("/api/payroll/tax-tables", json={"name": "x", "effective_from": future, "config": cfg}, headers=H).status_code == 403
    assert admin.post("/api/payroll/tax-tables", json={"name": "x", "effective_from": future, "config": cfg}, headers=H).status_code == 400
    cfg["bands"] = [{"upto": 150, "rate": 0}, {"upto": None, "rate": 25}]
    # Can't take effect before an approved payroll.
    past = date(date.today().year - 1, 6, 1).isoformat()
    assert admin.post("/api/payroll/tax-tables", json={"name": "Back", "effective_from": past, "config": cfg}, headers=H).status_code == 409
    r = admin.post("/api/payroll/tax-tables", json={"name": "Next year", "effective_from": future, "config": cfg}, headers=H)
    assert r.status_code == 201 and r.json["less"] == [0, 37.5]


# ---------------------------------------------------------------- asset register
def test_straight_line_is_exact_and_respects_residual(app):
    a = FixedAsset(asset_no="TMP", name="t", category="Office Equipment", acquisition_date=date(2025, 1, 15),
                   funding="existing", cost_cents=100000, residual_cents=10000, method="straight_line",
                   useful_life_months=7, depreciation_start="2025-01", opening_accum_cents=0)
    charges = assets.monthly_charges(a, "2026-12")
    assert len(charges) == 7 and sum(c for _, c in charges) == 90000
    a.method, a.rate_pct = "reducing_balance", 40
    rb = assets.monthly_charges(a, "2045-12")
    assert sum(c for _, c in rb) <= 90000 and rb[0][1] == round(100000 * 0.40 / 12)
    land = FixedAsset(category="Land", method="none", depreciation_start="2025-01", cost_cents=1, residual_cents=0,
                      opening_accum_cents=0)
    assert assets.monthly_charges(land, "2030-01") == []


def test_asset_purchase_and_funds_check(app):
    b = login(app, "bursar", "Bursar@2026")
    cash = ledger.acct("1000")
    base = {"name": "Photocopier", "category": "Office Equipment", "acquisition_date": date.today().isoformat(),
            "cost": 10 ** 7, "funding": "purchase", "account_id": cash.id, "useful_life_months": 60}
    assert b.post("/api/assets", json=base, headers=H).status_code == 409  # insufficient funds
    ppe = ledger.balance(ledger.acct("1500"))
    r = b.post("/api/assets", json={**base, "cost": 50}, headers=H)
    assert r.status_code == 201 and r.json["asset_no"].startswith("FA-")
    assert ledger.balance(ledger.acct("1500")) == ppe + 5000
    assert assets.reconciliation()["reconciled"]
    # Teachers can't see the register.
    assert login(app, "teacher", "Teacher@2026").get("/api/assets").status_code == 403


def test_depreciation_runs_in_order_and_reverse(app):
    admin = login(app)
    b = login(app, "bursar", "Bursar@2026")
    runs = b.get("/api/assets/depreciation").json
    last = runs["last_run"]
    assert b.post("/api/assets/depreciation", json={"period": last}, headers=H).status_code == 409
    current = assets.period_of(date.today())
    assert b.post("/api/assets/depreciation", json={"period": current}, headers=H).status_code == 400
    latest = runs["items"][0]
    accum = ledger.balance(ledger.acct("1590"))
    assert b.post(f"/api/assets/depreciation/{latest['id']}/reverse", json={"reason": "Re-run test"}, headers=H).status_code == 403
    assert admin.post(f"/api/assets/depreciation/{latest['id']}/reverse", json={"reason": "Re-run test"}, headers=H).status_code == 200
    assert ledger.balance(ledger.acct("1590")) == accum - round(latest["amount"] * 100)
    preview = b.get(f"/api/assets/depreciation/preview?period={last}").json
    assert preview["total"] == pytest.approx(latest["amount"])
    assert b.post("/api/assets/depreciation", json={"period": last}, headers=H).status_code == 201
    assert ledger.balance(ledger.acct("1590")) == accum
    assert assets.reconciliation()["reconciled"]
    assert_books_consistent()


def test_disposal_with_catch_up_and_gain(app):
    b = login(app, "bursar", "Bursar@2026")
    start = assets.month_add(assets.period_of(date.today()), -4)
    r = b.post("/api/assets", headers=H, json={
        "name": "Old minibus", "category": "Motor Vehicles", "acquisition_date": "2020-03-01", "cost": 12000,
        "funding": "existing", "opening_accum": 6000, "depreciation_start": start, "useful_life_months": 24})
    assert r.status_code == 201
    aid = r.json["id"]
    # Register the minibus in the ledger as well (it pre-dates the books).
    ledger.post(date.today(), "Minibus brought into the books", [
        {"account": ledger.acct("1500"), "debit": 1_200_000}, {"account": ledger.acct("1590"), "credit": 600_000},
        {"account": ledger.acct("3000"), "credit": 600_000}])
    db.session.commit()
    bank = ledger.acct("1010")
    r = b.post(f"/api/assets/{aid}/dispose", json={"date": date.today().isoformat(), "proceeds": 7000,
                                                    "account_id": bank.id, "note": "Sold to a dealer"}, headers=H)
    assert r.status_code == 200, r.json
    a = db.session.get(FixedAsset, aid)
    # Four catch-up months of (12000 - 6000) / 24 = 250 each, then sold above book value.
    assert a.accumulated_cents == 600000 + 4 * 25000 and a.status == "disposed"
    gain = ledger.acct("4960")
    assert ledger.balance(gain) == 700000 - (1200000 - 700000)
    assert b.post(f"/api/assets/{aid}/dispose", json={"date": date.today().isoformat(), "note": "x"}, headers=H).status_code == 400
    assert assets.reconciliation()["reconciled"]
    assert_books_consistent()
