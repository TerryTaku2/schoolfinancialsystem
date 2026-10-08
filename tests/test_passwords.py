"""Forgotten passwords: reset requests, temporary passwords and platform-operator resets."""
import pytest

import config
from app import create_app, db
from app.models import PlatformAdmin, User
from app.seed import seed_demo
from app.tenancy import get_school, use_school

H = {"X-Requested-With": "SchoolMS"}


@pytest.fixture(scope="module")
def app():
    app = create_app("config.TestConfig")

    @app.before_request
    def _fresh_user():
        from flask import g
        g.pop("_login_user", None)

    with app.app_context():
        seed_demo(students_per_class=2)
        yield app


def login(app, username, password):
    c = app.test_client()
    r = c.post("/api/auth/login", json={"username": username, "password": password}, headers=H)
    return c, r


def test_forgot_password_tells_administrators(app):
    anon = app.test_client()
    r = anon.post("/api/auth/forgot", json={"username": "Teacher"}, headers=H)
    assert r.status_code == 200 and "administrator" in r.json["message"]
    # Same answer for a username that doesn't exist: nothing is revealed.
    assert anon.post("/api/auth/forgot", json={"username": "nobody"}, headers=H).json == r.json
    assert anon.post("/api/auth/forgot", json={}, headers=H).status_code == 400
    assert User.query.filter_by(username="teacher").one().reset_requested_at is not None
    admin, _ = login(app, "admin", "Admin@2026")
    assert admin.get("/api/meta").json["reset_requests"] == 1
    users = admin.get("/api/users").json["items"]
    assert users[0]["username"] == "teacher" and users[0]["reset_requested_at"]  # listed first
    parent, _ = login(app, "parent", "Parent@2026")
    assert parent.get("/api/meta").json["reset_requests"] == 0  # only for those who can reset


def test_reset_gives_a_temporary_password(app):
    admin, _ = login(app, "admin", "Admin@2026")
    tid = User.query.filter_by(username="teacher").one().id
    assert admin.put(f"/api/users/{tid}", json={"password": "Temp1234"}, headers=H).status_code == 200
    t = db.session.get(User, tid)
    db.session.refresh(t)
    assert t.must_change_password and t.reset_requested_at is None
    assert admin.get("/api/meta").json["reset_requests"] == 0

    teacher, r = login(app, "teacher", "Temp1234")
    assert r.status_code == 200 and r.json["user"]["must_change_password"] is True
    # Nothing else works until the temporary password is replaced.
    r = teacher.get("/api/students")
    assert r.status_code == 403 and r.json["must_change_password"] is True
    assert teacher.get("/api/meta").status_code == 200 and teacher.get("/api/auth/me").status_code == 200
    r = teacher.post("/api/auth/change-password", json={"current_password": "Temp1234", "new_password": "Temp1234"}, headers=H)
    assert r.status_code == 400
    r = teacher.post("/api/auth/change-password", json={"current_password": "Temp1234", "new_password": "MyOwn5678"}, headers=H)
    assert r.status_code == 200
    assert teacher.get("/api/auth/me").json["user"]["must_change_password"] is False
    assert teacher.get("/api/timetable").status_code != 403


def test_changing_your_own_password_is_not_temporary(app):
    admin, _ = login(app, "admin", "Admin@2026")
    aid = User.query.filter_by(username="admin").one().id
    assert admin.put(f"/api/users/{aid}", json={"password": "Admin@2026x"}, headers=H).status_code == 200
    assert admin.get("/api/students").status_code == 200
    assert admin.put(f"/api/users/{aid}", json={"password": "Admin@2026"}, headers=H).status_code == 200


@pytest.fixture(scope="module")
def platform(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("pw-platform")

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
        r = ops.post("/platform/api/schools", headers=H, json={
            "name": "Gamma School", "slug": "gamma", "school_type": "primary", "currency": "USD",
            "admin_username": "head", "admin_password": "Forgotten1"})
        assert r.status_code == 201
        yield app, ops, r.json["id"], MultiConfig


def test_operator_resets_a_school_administrator(platform):
    app, ops, sid, _ = platform
    with use_school(get_school("gamma")):
        db.session.add(User(username="clerk", full_name="Clerk", role="bursar", password_hash="x"))
        db.session.commit()
    admins = ops.get(f"/platform/api/schools/{sid}/admins", headers=H).json["items"]
    assert [a["username"] for a in admins] == ["head"]
    r = ops.post(f"/platform/api/schools/{sid}/reset-password", json={"username": "clerk", "password": "Temp1234"}, headers=H)
    assert r.status_code == 404  # only administrators; staff are reset by the school
    assert app.test_client().post(f"/platform/api/schools/{sid}/reset-password",
                                  json={"username": "head", "password": "Temp1234"}, headers=H).status_code == 401
    r = ops.post(f"/platform/api/schools/{sid}/reset-password", json={"username": "head", "password": "Temp1234"}, headers=H)
    assert r.status_code == 200 and r.json["username"] == "head"
    c = app.test_client()
    r = c.post("/s/gamma/api/auth/login", json={"username": "head", "password": "Temp1234"}, headers=H)
    assert r.status_code == 200 and r.json["user"]["must_change_password"] is True
    assert c.get("/s/gamma/api/students").status_code == 403


def test_forgotten_operator_password_reset_from_environment(platform):
    _, _, _, MultiConfig = platform

    class ResetConfig(MultiConfig):
        PLATFORM_ADMIN_PASSWORD = "Brand-new-2026"
        PLATFORM_ADMIN_RESET = True

    app = create_app(ResetConfig)
    with app.app_context():
        c = app.test_client()
        assert c.post("/platform/api/login", json={"username": "ops", "password": "Platform@2026"}, headers=H).status_code == 401
        assert c.post("/platform/api/login", json={"username": "ops", "password": "Brand-new-2026"}, headers=H).status_code == 200
        assert PlatformAdmin.query.count() == 1
