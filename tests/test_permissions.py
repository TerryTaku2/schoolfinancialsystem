"""Roles and permissions: custom roles, per-person adjustments and escalation guards."""
from datetime import date

import pytest

from app import create_app, db
from app.models import Role, SchoolClass, Student, User
from app.seed import seed_demo
from app.services import permissions

H = {"X-Requested-With": "SchoolMS"}
PW = "Secret123x"


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
        yield app


def login(app, username, password=PW):
    c = app.test_client()
    r = c.post("/api/auth/login", json={"username": username, "password": password}, headers=H)
    assert r.status_code == 200, r.json
    return c


def admin(app):
    return login(app, "admin", "Admin@2026")


def make_role(app, name, perms, base="bursar"):
    r = admin(app).post("/api/roles", json={"name": name, "base": base, "permissions": perms}, headers=H)
    assert r.status_code == 201, r.json
    return r.json


def make_user(app, username, role):
    r = admin(app).post("/api/users", json={"full_name": username.title(), "username": username, "password": PW,
                                            "role": role["base"], "role_id": role["id"]}, headers=H)
    assert r.status_code == 201, r.json
    return r.json


def test_builtin_roles_match_what_each_role_could_do(app):
    bursar = User.query.filter_by(username="bursar").first()
    perms = permissions.permissions_for(bursar)
    assert {"fees.manage", "payments.manage", "payroll.manage", "accounting.view"} <= perms
    assert not perms & {"expenses.approve", "payroll.approve", "payments.approve", "users.manage", "settings.manage"}
    assert permissions.permissions_for(User.query.filter_by(username="admin").first()) == set(permissions.ALL)
    assert permissions.permissions_for(User.query.filter_by(username="parent").first()) == set()
    me = login(app, "bursar", "Bursar@2026").get("/api/auth/me").json["user"]
    assert me["role_name"] == "Bursar" and "fees.manage" in me["permissions"]


def test_custom_role_limits_access(app):
    role = make_role(app, "Cashier", ["payments.manage"])
    assert role["permissions"] == ["payments.manage", "payments.view"]  # manage implies view
    make_user(app, "cashier", role)
    c = login(app, "cashier")
    st = Student.query.filter_by(status="active").first()
    assert c.post("/api/payments", json={"student_id": st.id, "amount": 10, "method": "cash"}, headers=H).status_code == 201
    assert c.get("/api/payments").status_code == 200
    assert c.get("/api/expenses").status_code == 403
    assert c.get("/api/payroll/runs").status_code == 403
    assert c.get("/api/accounting/balance-sheet").status_code == 403
    pid = c.get("/api/payments").json["items"][0]["id"]
    assert c.post(f"/api/payments/{pid}/void", json={"reason": "Mistake made"}, headers=H).status_code == 403
    me = c.get("/api/auth/me").json["user"]
    assert me["role_name"] == "Cashier"


def test_per_person_adjustments(app):
    a = admin(app)
    clerk = User.query.filter_by(username="cashier").first()
    # Give this one cashier the right to approve expenses, and take away payment recording.
    r = a.put(f"/api/users/{clerk.id}", json={"extra_permissions": ["expenses.approve"],
                                               "removed_permissions": ["payments.manage"]}, headers=H)
    assert r.status_code == 200
    assert "expenses.approve" in r.json["permissions"] and "expenses.view" in r.json["permissions"]
    assert "payments.manage" not in r.json["permissions"] and "payments.view" in r.json["permissions"]
    c = login(app, "cashier")
    b = login(app, "bursar", "Bursar@2026")
    eid = b.post("/api/expenses", json={"category": "Supplies", "description": "Markers", "amount": 12,
                                        "expense_date": date.today().isoformat()}, headers=H).json["id"]
    assert c.post(f"/api/expenses/{eid}/status", json={"status": "approved"}, headers=H).status_code == 200
    st = Student.query.filter_by(status="active").first()
    assert c.post("/api/payments", json={"student_id": st.id, "amount": 10, "method": "cash"}, headers=H).status_code == 403
    # Removing "view" of an area removes managing it as well.
    r = a.put(f"/api/users/{clerk.id}", json={"removed_permissions": ["expenses.view"]}, headers=H)
    assert "expenses.approve" not in r.json["permissions"]
    # Nobody approves their own expense, whatever their permissions.
    a.put(f"/api/users/{clerk.id}", json={"extra_permissions": ["expenses.approve", "expenses.manage"], "removed_permissions": []}, headers=H)
    own = c.post("/api/expenses", json={"category": "Supplies", "description": "Pens", "amount": 5,
                                        "expense_date": date.today().isoformat()}, headers=H).json["id"]
    assert c.post(f"/api/expenses/{own}/status", json={"status": "approved"}, headers=H).status_code == 403


def test_teacher_based_role_sees_beyond_own_classes(app):
    deputy = make_role(app, "Deputy head", ["students.view", "attendance.manage", "results.view"], base="teacher")
    make_user(app, "deputy", deputy)
    c = login(app, "deputy")
    assert c.get("/api/students").json["total"] == Student.query.filter_by(status="active").count()
    cls = SchoolClass.query.order_by(SchoolClass.level).first()
    assert c.get(f"/api/results?class_id={cls.id}").status_code == 200
    # A plain teacher still only sees their own classes.
    t = login(app, "teacher", "Teacher@2026")
    assert t.get("/api/students").json["total"] < Student.query.filter_by(status="active").count()
    assert c.get("/api/fees").status_code == 200 and c.get("/api/invoices").status_code == 403


def test_users_cannot_escalate(app):
    manager = make_role(app, "Office manager", ["users.manage", "fees.view"])
    make_user(app, "officemgr", manager)
    c = login(app, "officemgr")
    # Can't hand out permissions they don't hold, or create administrators.
    assert c.post("/api/roles", json={"name": "Super", "base": "bursar", "permissions": ["payroll.approve"]}, headers=H).status_code == 403
    assert c.post("/api/users", json={"full_name": "X", "username": "sneaky", "password": PW, "role": "admin"}, headers=H).status_code == 403
    me = User.query.filter_by(username="officemgr").first()
    assert c.put(f"/api/users/{me.id}", json={"extra_permissions": ["fees.view"]}, headers=H).status_code == 400
    admin_user = User.query.filter_by(username="admin").first()
    assert c.put(f"/api/users/{admin_user.id}", json={"active": False}, headers=H).status_code == 403
    # Within their own permissions they can manage others.
    assert c.post("/api/roles", json={"name": "Fees viewer", "base": "bursar", "permissions": ["fees.view"]}, headers=H).status_code == 201


def test_role_rules(app):
    a = admin(app)
    roles = a.get("/api/permissions").json["roles"]
    builtin = next(r for r in roles if r["key"] == "bursar")
    assert a.delete(f"/api/roles/{builtin['id']}", headers=H).status_code == 409
    cashier = next(r for r in roles if r["name"] == "Cashier")
    assert a.delete(f"/api/roles/{cashier['id']}", headers=H).status_code == 409  # still has a user
    assert a.post("/api/roles", json={"name": "Bad", "base": "bursar", "permissions": ["no.such"]}, headers=H).status_code == 400
    assert a.post("/api/roles", json={"name": "Cashier", "base": "bursar", "permissions": []}, headers=H).status_code == 400
    # Editing a built-in role changes everyone who has it.
    perms = [p for p in builtin["permissions"] if p != "reports.view"]
    assert a.put(f"/api/roles/{builtin['id']}", json={"name": "Bursar", "permissions": perms}, headers=H).status_code == 200
    assert login(app, "bursar", "Bursar@2026").get("/api/reports/fees").status_code == 403
    a.put(f"/api/roles/{builtin['id']}", json={"name": "Bursar", "permissions": builtin["permissions"]}, headers=H)
    assert login(app, "bursar", "Bursar@2026").get("/api/reports/fees").status_code == 200


def test_every_endpoint_has_a_permission_rule(app):
    """No API endpoint is left open by accident: each is public on purpose, needs login, or a permission."""
    public = {"auth.login", "auth.demo_accounts", "auth.demo_login", "static"}
    for rule in app.url_map.iter_rules():
        ep = rule.endpoint
        if not rule.rule.startswith("/api/") or ep.startswith(("integration.", "platform.")) or ep in public:
            continue
        fn = app.view_functions[ep]
        # login_required / permission_required both wrap the view; the bare function would not.
        assert hasattr(fn, "__wrapped__"), f"{rule.rule} has no access rule"
