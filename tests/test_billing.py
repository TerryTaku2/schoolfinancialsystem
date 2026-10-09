"""Subscriptions: billing schools per term, in-app notices, EcoCash/cash payments and reminders."""
from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine, inspect, text

import config
from app import create_app, db
from app.models import Student, SubscriptionInvoice, User
from app.tenancy import get_school, use_school

H = {"X-Requested-With": "SchoolMS"}


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("billing")

    class MultiConfig(config.TestConfig):
        MULTI_SCHOOL = True
        PLATFORM_DATABASE_URL = f"sqlite:///{tmp / 'platform.db'}"
        SCHOOLS_DIR = str(tmp / "schools")
        PLATFORM_ADMIN_USERNAME = "ops"
        PLATFORM_ADMIN_PASSWORD = "Platform@2026"

    app = create_app(MultiConfig)

    @app.before_request
    def _fresh_user():
        from flask import g
        g.pop("_login_user", None)

    with app.app_context():
        ops = app.test_client()
        assert ops.post("/platform/api/login", json={"username": "ops", "password": "Platform@2026"}, headers=H).status_code == 200
        ids = {}
        for slug in ("big", "small", "pilot"):
            r = ops.post("/platform/api/schools", headers=H, json={
                "name": f"{slug.title()} School", "slug": slug, "school_type": "primary", "currency": "USD",
                "admin_username": "admin", "admin_password": "AdminPass1"})
            assert r.status_code == 201
            ids[slug] = r.json["id"]
        with use_school(get_school("big")):
            from app.models import SchoolClass
            cls = SchoolClass.query.first()
            for i in range(120):
                db.session.add(Student(admission_no=f"B{i}", first_name="L", last_name=str(i), gender="Male",
                                       dob=date(2015, 1, 1), school_class=cls))
            db.session.add(Student(admission_no="LEFT", first_name="L", last_name="Left", gender="Male",
                                   dob=date(2015, 1, 1), school_class=cls, status="withdrawn"))
            teacher = User(username="teach", full_name="A Teacher", role="teacher")
            teacher.set_password("TeachPass1")
            bursar = User(username="bursar", full_name="The Bursar", role="bursar")
            bursar.set_password("BursarPass1")
            db.session.add_all([teacher, bursar])
            db.session.commit()
        yield app, ops, ids, MultiConfig


def school_client(app, slug, username="admin", password="AdminPass1"):
    c = app.test_client()
    assert c.post(f"/s/{slug}/api/auth/login", json={"username": username, "password": password}, headers=H).status_code == 200
    return c


def test_operator_sets_prices_and_bills_a_term(env):
    app, ops, ids, _ = env
    assert app.test_client().get("/platform/api/billing/invoices").status_code == 401
    r = ops.put("/platform/api/billing/settings", headers=H, json={
        "default_rate_cents": 100, "default_minimum_cents": 5000, "ecocash_number": "0771 234 567",
        "ecocash_name": "T Muromba", "cash_instructions": "at our office in Harare"})
    assert r.status_code == 200 and r.json["settings"]["default_rate_cents"] == 100
    assert ops.put(f"/platform/api/schools/{ids['pilot']}/billing", json={"free": True}, headers=H).status_code == 200
    r = ops.put(f"/platform/api/schools/{ids['small']}/billing", headers=H,
                json={"contact_name": "Mrs Moyo", "contact_phone": "0772 000 111"})
    assert r.json["billing"]["contact_phone"] == "0772 000 111"
    due = (date.today() + timedelta(days=10)).isoformat()
    r = ops.post("/platform/api/billing/run", json={"period": "Term 1 2027", "due_on": due}, headers=H)
    assert r.status_code == 200, r.json
    made = {i["school_slug"]: i for i in r.json["created"]}
    assert made["big"]["learners"] == 120 and made["big"]["amount"] == 120.0   # active learners only
    assert made["small"]["amount"] == 50.0                                    # minimum
    assert [s["reason"] for s in r.json["skipped"]] == ["free (not billed)"]
    again = ops.post("/platform/api/billing/run", json={"period": "Term 1 2027", "due_on": due}, headers=H).json
    assert again["created"] == [] and {s["reason"] for s in again["skipped"]} == {"already billed", "free (not billed)"}
    totals = ops.get("/platform/api/billing/invoices").json["totals"]
    assert totals["outstanding"] == 170.0 and totals["overdue"] == 0


def test_school_sees_the_notice(env):
    app, ops, ids, _ = env
    admin = school_client(app, "big")
    s = admin.get("/s/big/api/subscription").json
    assert s["enabled"] and len(s["notice"]) == 1 and s["notice"][0]["balance"] == 120.0
    assert s["pay"]["ecocash_number"] == "0771 234 567"
    # Bursars see it; teachers don't.
    assert school_client(app, "big", "bursar", "BursarPass1").get("/s/big/api/subscription").json["enabled"]
    assert school_client(app, "big", "teach", "TeachPass1").get("/s/big/api/subscription").json["enabled"] is False
    # Not shown until shortly before the due date.
    ops.post("/platform/api/billing/invoices", headers=H, json={
        "school_id": ids["big"], "period": "Setup and training", "due_on": (date.today() + timedelta(days=60)).isoformat(),
        "amount": 200, "learners": 0})
    s = admin.get("/s/big/api/subscription").json
    assert len(s["invoices"]) == 2 and len(s["notice"]) == 1


def test_school_reports_ecocash_and_operator_confirms(env):
    app, ops, ids, _ = env
    admin = school_client(app, "big")
    inv = admin.get("/s/big/api/subscription").json["notice"][0]
    r = admin.post("/s/big/api/subscription/payments", headers=H,
                   json={"invoice_id": inv["id"], "amount": 120, "reference": "mp241010.1200.a1"})
    assert r.status_code == 201
    assert r.json["notice"][0]["pending"] == 1 and r.json["notice"][0]["status"] == "unpaid"  # not counted yet
    dup = admin.post("/s/big/api/subscription/payments", headers=H,
                     json={"invoice_id": inv["id"], "amount": 120, "reference": "MP241010.1200.A1"})
    assert dup.status_code == 409
    # Another school can't report against this invoice.
    other = school_client(app, "small")
    assert other.post("/s/small/api/subscription/payments", headers=H,
                      json={"invoice_id": inv["id"], "amount": 1, "reference": "X1"}).status_code == 404
    full = ops.get("/platform/api/billing/invoices").json
    assert full["totals"]["pending"] == 1
    pid = next(i for i in full["items"] if i["id"] == inv["id"])["payments"][0]["id"]
    r = ops.post(f"/platform/api/billing/payments/{pid}/confirm", headers=H)
    assert r.status_code == 200 and r.json["status"] == "paid"
    assert ops.post(f"/platform/api/billing/payments/{pid}/reject", headers=H).status_code == 409
    assert admin.get("/s/big/api/subscription").json["notice"] == []


def test_cash_part_payment_overdue_and_reminder(env):
    app, ops, ids, _ = env
    inv = SubscriptionInvoice.query.filter_by(school_id=ids["small"]).one()
    r = ops.post(f"/platform/api/billing/invoices/{inv.id}/payments", json={"amount": 20, "method": "cash"}, headers=H)
    assert r.status_code == 201 and r.json["status"] == "partial" and r.json["balance"] == 30.0
    assert ops.post(f"/platform/api/billing/invoices/{inv.id}/payments", json={"amount": 5, "method": "ecocash"},
                    headers=H).status_code == 400  # EcoCash needs the transaction ID
    inv.due_on = date.today() - timedelta(days=3)
    db.session.commit()
    s = school_client(app, "small").get("/s/small/api/subscription").json
    assert s["notice"][0]["status"] == "overdue"
    rem = ops.get(f"/platform/api/billing/invoices/{inv.id}/reminder").json
    assert "overdue" in rem["text"] and "0771 234 567" in rem["text"] and inv.number in rem["text"]
    assert rem["whatsapp"].startswith("https://wa.me/263772000111?text=")
    # Void: not once money has been received.
    assert ops.post(f"/platform/api/billing/invoices/{inv.id}/void", json={"reason": "x"}, headers=H).status_code == 409
    setup = SubscriptionInvoice.query.filter_by(period="Setup and training").one()
    r = ops.post(f"/platform/api/billing/invoices/{setup.id}/void", json={"reason": "Waived as a goodwill gesture"}, headers=H)
    assert r.json["status"] == "void"


def test_single_school_mode_has_no_subscription():
    app = create_app("config.TestConfig")
    with app.app_context():
        from app.seed import seed_demo
        seed_demo(students_per_class=1)
        c = app.test_client()
        c.post("/api/auth/login", json={"username": "admin", "password": "Admin@2026"}, headers=H)
        assert c.get("/api/subscription").json["enabled"] is False


def test_existing_platform_database_is_upgraded(tmp_path):
    """A platform database from before billing gains the new School columns and tables."""
    url = f"sqlite:///{tmp_path / 'old-platform.db'}"
    eng = create_engine(url)
    with eng.begin() as conn:
        conn.execute(text("""CREATE TABLE school (id INTEGER PRIMARY KEY, slug VARCHAR(40) NOT NULL UNIQUE,
            name VARCHAR(120) NOT NULL, school_type VARCHAR(10) NOT NULL, currency VARCHAR(5) NOT NULL,
            database_url VARCHAR(500) NOT NULL, db_schema VARCHAR(63), status VARCHAR(10) NOT NULL,
            created_at DATETIME NOT NULL)"""))

    class OldConfig(config.TestConfig):
        MULTI_SCHOOL = True
        PLATFORM_DATABASE_URL = url
        SCHOOLS_DIR = str(tmp_path / "schools")

    create_app(OldConfig)
    insp = inspect(create_engine(url))
    assert {"billing_rate_cents", "contact_phone", "billing_free"} <= {c["name"] for c in insp.get_columns("school")}
    assert insp.has_table("subscription_invoice") and insp.has_table("subscription_payment")
