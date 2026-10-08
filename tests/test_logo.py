"""School logo: upload, serve, replace and remove."""
import base64

import pytest

from app import create_app
from app.seed import seed_demo

H = {"X-Requested-With": "SchoolMS"}
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")


@pytest.fixture(scope="module")
def app():
    app = create_app("config.TestConfig")

    @app.before_request
    def _fresh_user():
        from flask import g
        g.pop("_login_user", None)
        g.pop("_perms", None)

    with app.app_context():
        seed_demo(students_per_class=1)
        yield app


def login(app, username, password):
    c = app.test_client()
    r = c.post("/api/auth/login", json={"username": username, "password": password}, headers=H)
    assert r.status_code == 200, r.json
    return c


def data_url(raw, mime="image/png"):
    return f"data:{mime};base64,{base64.b64encode(raw).decode()}"


def test_logo_lifecycle(app):
    c = login(app, "admin", "Admin@2026")
    assert c.get("/api/meta", headers=H).json["logo_version"] is None
    assert c.get("/api/school/logo", headers=H).status_code == 404

    r = c.put("/api/school/logo", json={"data": data_url(PNG)}, headers=H)
    assert r.status_code == 200, r.json
    assert c.get("/api/meta", headers=H).json["logo_version"] == r.json["logo_version"]
    img = c.get("/api/school/logo", headers=H)
    assert img.status_code == 200 and img.mimetype == "image/png" and img.data == PNG

    assert c.delete("/api/school/logo", headers=H).status_code == 200
    assert c.get("/api/meta", headers=H).json["logo_version"] is None


def test_logo_rejects_bad_uploads(app):
    c = login(app, "admin", "Admin@2026")
    assert c.put("/api/school/logo", json={"data": data_url(b"x", "text/html")}, headers=H).status_code == 400
    assert c.put("/api/school/logo", json={"data": "data:image/png;base64,***"}, headers=H).status_code == 400
    assert c.put("/api/school/logo", json={"data": data_url(b"\0" * (1024 * 1024 + 1))}, headers=H).status_code == 400


def test_only_settings_managers_can_change_logo(app):
    c = login(app, "teacher", "Teacher@2026")
    assert c.put("/api/school/logo", json={"data": data_url(PNG)}, headers=H).status_code == 403
