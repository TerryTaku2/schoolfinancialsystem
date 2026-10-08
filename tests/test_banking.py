"""Bank reconciliation."""
from datetime import date, timedelta

import pytest

from app import create_app, db
from app.models import Account, BankStatement, JournalLine, Role, Setting, User
from app.seed import seed_demo
from app.services import ledger, permissions

H = {"X-Requested-With": "SchoolMS"}
D = date.today()


@pytest.fixture(scope="module")
def app():
    app = create_app("config.TestConfig")

    @app.before_request
    def _fresh_user():
        from flask import g
        g.pop("_login_user", None)
        g.pop("_perms", None)

    with app.app_context():
        seed_demo(students_per_class=3)
        # A fresh bank account with a known history.
        bank = Account(code="1030", name="Test Bank", type="asset", subtype="cash", cash_flow="operating", currency="USD")
        db.session.add(bank)
        db.session.flush()
        a = ledger.acct
        ledger.post(D - timedelta(days=10), "Opening deposit", [{"account": bank, "debit": 100000}, {"account": a("3000"), "credit": 100000}], reference="DEP-1")
        ledger.post(D - timedelta(days=5), "Cheque 001 to supplier", [{"account": a("5800"), "debit": 20000}, {"account": bank, "credit": 20000}], reference="CHQ001")
        ledger.post(D - timedelta(days=3), "Donation banked", [{"account": bank, "debit": 30000}, {"account": a("4950"), "credit": 30000}], reference="DON-9")
        # A deposit recorded by mistake and reversed: never reaches the bank.
        wrong = ledger.post(D - timedelta(days=4), "Wrong deposit", [{"account": bank, "debit": 5000}, {"account": a("4950"), "credit": 5000}])
        ledger.reverse(wrong, D - timedelta(days=4), "Duplicate")
        db.session.commit()
        yield app


def login(app, username="admin", password="Admin@2026"):
    c = app.test_client()
    assert c.post("/api/auth/login", json={"username": username, "password": password}, headers=H).status_code == 200
    return c


def bank():
    return Account.query.filter_by(code="1030").first()


def test_permissions(app):
    assert login(app, "teacher", "Teacher@2026").get("/api/banking/accounts").status_code == 403
    b = login(app, "bursar", "Bursar@2026")
    assert b.get("/api/banking/accounts").status_code == 200  # bursars reconcile...
    perms = permissions.permissions_for(User.query.filter_by(username="bursar").first())
    assert "banking.manage" in perms and "banking.approve" not in perms  # ...an approver completes


def test_reconcile_a_statement(app):
    b = login(app, "bursar", "Bursar@2026")
    admin = login(app)
    r = b.post("/api/banking/statements", headers=H, json={"account_id": bank().id, "statement_date": (D - timedelta(days=1)).isoformat(),
                                                            "opening": 0, "closing": 795, "reference": "Stmt 1"})
    assert r.status_code == 201, r.json
    sid = r.json["id"]
    # Two lines match the books automatically; the bank charge isn't in the books yet.
    r = b.post(f"/api/banking/statements/{sid}/lines", headers=H, json={"lines": [
        {"date": (D - timedelta(days=10)).isoformat(), "description": "Deposit", "reference": "DEP-1", "money_in": "1,000.00"},
        {"date": (D - timedelta(days=4)).isoformat(), "description": "Cheque paid", "reference": "CHQ001", "money_out": "200"},
        {"date": (D - timedelta(days=2)).isoformat(), "description": "Service fee", "amount": "-5.00"}]})
    assert r.status_code == 201 and r.json["matched"] == 2
    s = r.json["statement"]["summary"]
    # The donation is banked in the books but not yet on the statement; the reversed pair is ignored.
    assert s["outstanding_deposits"] == 300 and s["unpresented_payments"] == 0
    assert [o["description"] for o in r.json["statement"]["outstanding"]] == ["Donation banked"]
    assert s["unmatched_lines"] == 1 and not s["can_complete"]
    assert admin.post(f"/api/banking/statements/{sid}/complete", headers=H).status_code == 409
    # Post the fee to Bank Charges from the reconciliation; it is matched at once.
    fee = next(l for l in r.json["statement"]["lines"] if l["description"] == "Service fee")
    r = b.post(f"/api/banking/lines/{fee['id']}/post", headers=H, json={"account_id": ledger.acct("5810").id})
    assert r.status_code == 200
    s = r.json["summary"]
    assert s["book_balance"] == 1095 and s["adjusted_bank"] == 1095 and s["difference"] == 0 and s["can_complete"]
    # Bursars prepare, an approver completes.
    assert b.post(f"/api/banking/statements/{sid}/complete", headers=H).status_code == 403
    assert admin.post(f"/api/banking/statements/{sid}/complete", headers=H).status_code == 200
    cleared = JournalLine.query.filter_by(cleared_statement_id=sid).count()
    assert cleared == 5  # deposit, cheque, fee + the reversed pair
    acct = next(a for a in b.get("/api/banking/accounts").json["items"] if a["code"] == "1030")
    assert acct["reconciled_to"] == (D - timedelta(days=1)).isoformat() and acct["reconciled_balance"] == 795


def test_next_statement_and_reopen(app):
    b = login(app, "bursar", "Bursar@2026")
    admin = login(app)
    # The next statement can't overlap the last one, and its opening carries forward.
    assert b.post("/api/banking/statements", headers=H, json={"account_id": bank().id, "statement_date": (D - timedelta(days=2)).isoformat(), "closing": 0}).status_code == 400
    r = b.post("/api/banking/statements", headers=H, json={"account_id": bank().id, "statement_date": D.isoformat(), "closing": 1095})
    assert r.status_code == 201 and r.json["opening"] == 795
    sid = r.json["id"]
    first = BankStatement.query.filter_by(account_id=bank().id, status="completed").first()
    # The earlier statement can't be reopened while a later one is in progress.
    assert admin.post(f"/api/banking/statements/{first.id}/reopen", headers=H, json={"reason": "Check again"}).status_code == 409
    r = b.post(f"/api/banking/statements/{sid}/lines", headers=H, json={"lines": [
        {"date": (D - timedelta(days=1)).isoformat(), "description": "Donation", "amount": "300"}]})
    assert r.json["matched"] == 1 and r.json["statement"]["summary"]["can_complete"]
    line = r.json["statement"]["lines"][0]
    # Manual matching checks amounts.
    b.post(f"/api/banking/lines/{line['id']}/unmatch", headers=H)
    cheque = JournalLine.query.filter_by(account_id=bank().id, credit_cents=20000).first()
    assert b.post(f"/api/banking/lines/{line['id']}/match", headers=H, json={"journal_line_id": cheque.id}).status_code == 400
    donation = JournalLine.query.filter_by(account_id=bank().id, debit_cents=30000).first()
    assert b.post(f"/api/banking/lines/{line['id']}/match", headers=H, json={"journal_line_id": donation.id}).status_code == 200
    assert admin.post(f"/api/banking/statements/{sid}/complete", headers=H).status_code == 200
    # Reopen the latest: its lines become uncleared again.
    assert admin.post(f"/api/banking/statements/{sid}/reopen", headers=H, json={"reason": "Wrong closing"}).status_code == 200
    assert JournalLine.query.filter_by(cleared_statement_id=sid).count() == 0
    assert admin.post(f"/api/banking/statements/{sid}/complete", headers=H).status_code == 200


def test_ledger_rebuild_is_refused_once_reconciling(app):
    admin = login(app)
    r = admin.post("/api/accounting/rebuild", json={"confirm": "REBUILD"}, headers=H)
    assert r.status_code == 409


def test_new_default_permissions_reach_existing_schools_once(app):
    role = Role.query.filter_by(key="bursar").first()
    without = [p for p in permissions.split(role.permissions) if not p.startswith("banking.")]
    role.permissions = ",".join(without)
    db.session.get(Setting, "role_defaults_version").value = "1"
    db.session.commit()
    permissions.ensure_roles()  # an existing school upgrading
    assert {"banking.view", "banking.manage"} <= set(permissions.split(role.permissions))
    # An administrator later removes them on purpose: they stay removed.
    role.permissions = ",".join(without)
    permissions.ensure_roles()
    assert "banking.view" not in permissions.split(role.permissions)
    role.permissions = ",".join(without + ["banking.view", "banking.manage"])
    db.session.commit()


def test_first_reconciliation_brings_history_forward(app):
    """An account with years of history: transactions before the start date are in the opening balance."""
    b = login(app, "bursar", "Bursar@2026")
    admin = login(app)
    mobile = ledger.acct("1020")
    start = D - timedelta(days=6)
    opening = ledger.balance(mobile, start - timedelta(days=1)) / 100
    closing = ledger.balance(mobile, D) / 100
    r = b.post("/api/banking/statements", headers=H, json={"account_id": mobile.id, "start_date": start.isoformat(),
                                                            "statement_date": D.isoformat(), "opening": opening, "closing": closing})
    assert r.status_code == 201
    sid, sm = r.json["id"], r.json["summary"]
    assert sm["books_at_start"] == pytest.approx(opening)
    recent = [o for o in r.json["outstanding"]]
    assert all(o["date"] >= start.isoformat() for o in recent)  # nothing older is listed
    # Statement shows exactly the recent book movements: one line each, auto-matched.
    lines = [{"date": o["date"], "description": o["description"], "reference": o["reference"], "amount": o["amount"]} for o in recent]
    if lines:
        r = b.post(f"/api/banking/statements/{sid}/lines", headers=H, json={"lines": lines})
        assert r.json["matched"] == len(lines)
        sm = r.json["statement"]["summary"]
    assert sm["difference"] == 0 and sm["can_complete"]
    assert admin.post(f"/api/banking/statements/{sid}/complete", headers=H).status_code == 200
    # Everything up to the statement date is now cleared, history included.
    from app.models import JournalEntry
    left = (JournalLine.query.join(JournalEntry).filter(JournalLine.account_id == mobile.id, JournalEntry.date <= D,
                                                       JournalLine.cleared_statement_id.is_(None)).count())
    assert left == 0
    # A second statement can't use a start date.
    r = b.post("/api/banking/statements", headers=H, json={"account_id": mobile.id, "start_date": D.isoformat(),
                                                            "statement_date": D.isoformat(), "closing": closing})
    assert r.status_code == 400
