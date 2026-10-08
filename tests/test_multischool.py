"""Multi-school isolation and the Zimbabwe school structure."""
from datetime import date

import pytest

import config
from app import create_app, db
from app.models import GradeBand, SchoolClass, Student
from app.services import academics, structure
from app.tenancy import get_school, use_school

H = {"X-Requested-With": "SchoolMS"}
PW = {"alpha": "AlphaPass1", "beta": "BetaPass22"}


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("platform")

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
        yield app


def operator(app):
    c = app.test_client()
    assert c.post("/platform/api/login", json={"username": "ops", "password": "Platform@2026"}, headers=H).status_code == 200
    return c


def school_login(app, slug, username="admin", password=None):
    c = app.test_client()
    r = c.post(f"/s/{slug}/api/auth/login", json={"username": username, "password": password or PW[slug]}, headers=H)
    return c, r


@pytest.fixture(scope="module")
def schools(app):
    ops = operator(app)
    for slug, kind in (("alpha", "primary"), ("beta", "secondary")):
        r = ops.post("/platform/api/schools", headers=H, json={
            "name": f"{slug.title()} School", "slug": slug, "school_type": kind, "currency": "USD",
            "admin_username": "admin", "admin_password": PW[slug]})
        assert r.status_code == 201, r.json
    return ops


# ---------------------------------------------------------------- platform
def test_platform_requires_operator(app, schools):
    anon = app.test_client()
    assert anon.get("/platform/api/schools").status_code == 401
    assert anon.post("/platform/api/login", json={"username": "ops", "password": "wrong-pass1"}, headers=H).status_code == 401
    # A school's administrator is not a platform operator.
    assert anon.post("/platform/api/login", json={"username": "admin", "password": PW["alpha"]}, headers=H).status_code == 401
    items = schools.get("/platform/api/schools").json["items"]
    assert {s["slug"] for s in items} == {"alpha", "beta"}


def test_school_codes_are_validated(app, schools):
    base = {"name": "X", "school_type": "primary", "admin_username": "admin", "admin_password": "GoodPass1"}
    for bad in ("ab", "Bad Code", "-x-", "platform", "alpha"):
        assert schools.post("/platform/api/schools", json={**base, "slug": bad}, headers=H).status_code in (400, 409)


def test_each_school_has_its_own_users(app, schools):
    _, r = school_login(app, "alpha")
    assert r.status_code == 200
    # Same username, but alpha's password does not open beta.
    _, r = school_login(app, "beta", password=PW["alpha"])
    assert r.status_code == 401
    _, r = school_login(app, "beta")
    assert r.status_code == 200


def test_data_is_isolated(app, schools):
    with use_school(get_school("alpha")):
        cls = SchoolClass.query.filter_by(grade_code="G3").first()
        db.session.add(Student(admission_no="ALPHA-1", first_name="Tariro", last_name="Moyo", gender="Female",
                               dob=date(2017, 3, 1), school_class=cls))
        db.session.commit()
    a, _ = school_login(app, "alpha")
    b, _ = school_login(app, "beta")
    names = [s["admission_no"] for s in a.get("/s/alpha/api/students").json["items"]]
    assert "ALPHA-1" in names
    assert b.get("/s/beta/api/students").json["items"] == []
    a_id = next(s["id"] for s in a.get("/s/alpha/api/students").json["items"] if s["admission_no"] == "ALPHA-1")
    # The same numeric id in beta is a different (non-existent) record.
    assert b.get(f"/s/beta/api/students/{a_id}").status_code == 404


def test_session_does_not_cross_schools(app, schools):
    a, _ = school_login(app, "alpha")
    assert a.get("/s/alpha/api/auth/me").status_code == 200
    assert a.get("/s/beta/api/auth/me").status_code == 401  # cookie is scoped to /s/alpha/
    # Even a stolen alpha session cookie replayed against beta is refused: the login id names alpha.
    cookie = a.get_cookie("session", path="/s/alpha/")
    assert cookie is not None
    b = app.test_client()
    b.set_cookie("session", cookie.value, path="/s/beta/")
    assert b.get("/s/beta/api/auth/me").status_code == 401


def test_unknown_suspended_and_unprefixed(app, schools):
    c = app.test_client()
    assert c.get("/api/students").status_code == 404           # must name a school
    assert c.get("/s/nope/api/meta").status_code == 404
    assert c.get("/").status_code == 200                        # school-code landing page
    assert c.get("/s/alpha").status_code == 308                 # adds the trailing slash
    sid = next(s["id"] for s in schools.get("/platform/api/schools").json["items"] if s["slug"] == "beta")
    assert schools.put(f"/platform/api/schools/{sid}", json={"status": "suspended"}, headers=H).status_code == 200
    _, r = school_login(app, "beta")
    assert r.status_code == 404
    schools.put(f"/platform/api/schools/{sid}", json={"status": "active"}, headers=H)
    _, r = school_login(app, "beta")
    assert r.status_code == 200


def test_school_name_comes_from_registry(app, schools):
    a, _ = school_login(app, "alpha")
    assert a.get("/s/alpha/api/meta").json["school"] == "Alpha School"
    assert b"Alpha School" in a.get("/s/alpha/").data


# ---------------------------------------------------------------- Zimbabwe structure
def test_new_schools_get_the_zimbabwe_structure(app, schools):
    with use_school(get_school("alpha")):
        assert [c.grade_code for c in SchoolClass.query.order_by(SchoolClass.level)] == structure.codes_for("primary")
        assert {b.section for b in GradeBand.query} >= {"primary"}
    with use_school(get_school("beta")):
        codes = [c.grade_code for c in SchoolClass.query.order_by(SchoolClass.level)]
        assert codes == ["F1", "F2", "F3", "F4", "F5", "F6"]
        assert {"o_level", "a_level"} <= {b.section for b in GradeBand.query}
        f2 = SchoolClass.query.filter_by(grade_code="F2").first()
        assert [cs.subject.code for cs in f2.subjects] and academics.grade_for(55, academics.bands_for(f2)).letter == "C"
    b, _ = school_login(app, "beta")
    # Beta is a secondary school, so it can't open a Grade 3 class.
    r = b.post("/s/beta/api/classes", json={"grade_code": "G3", "stream": "A", "capacity": 30}, headers=H)
    assert r.status_code == 400
    r = b.post("/s/beta/api/classes", json={"grade_code": "F1", "stream": "B", "capacity": 30}, headers=H)
    assert r.status_code == 201 and r.json["name"] == "Form 1B"


def _student(code, n):
    cls = SchoolClass.query.filter_by(grade_code=code).first()
    st = Student(admission_no=f"P{code}{n}", first_name=f"S{n}", last_name=code, gender="Male",
                 dob=date(2010, 1, 1), school_class=cls)
    db.session.add(st)
    db.session.flush()
    return st.id


def test_secondary_promotion_rules(app, schools):
    with use_school(get_school("beta")):
        f1, f4_stay, f4_go, f6 = _student("F1", 1), _student("F4", 2), _student("F4", 3), _student("F6", 4)
        db.session.commit()
    b, _ = school_login(app, "beta")
    plan = {p["student_id"]: p for p in b.post("/s/beta/api/promotion/preview", json={"continue": [f4_go]}, headers=H).json["items"]}
    assert plan[f1]["action"] == "promote" and plan[f1]["to"].startswith("Form 2")
    assert plan[f4_stay]["action"] == "graduate" and plan[f4_stay]["can_continue"]
    assert plan[f4_go]["action"] == "promote" and plan[f4_go]["to"].startswith("Form 5")
    assert plan[f6]["action"] == "graduate"
    r = b.post("/s/beta/api/promotion", json={"confirm": "PROMOTE", "continue": [f4_go]}, headers=H)
    assert r.status_code == 200
    with use_school(get_school("beta")):
        assert db.session.get(Student, f4_stay).status == "graduated"
        assert db.session.get(Student, f4_go).school_class.grade_code == "F5"


def test_primary_to_combined(app, schools):
    with use_school(get_school("alpha")):
        g7, g5 = _student("G7", 5), _student("G5", 6)
        db.session.commit()
    a, _ = school_login(app, "alpha")
    plan = {p["student_id"]: p for p in a.get("/s/alpha/api/promotion/preview").json["items"]}
    assert plan[g7]["action"] == "graduate" and plan[g7]["reason"] == "Completed primary"
    plan = {p["student_id"]: p for p in a.post("/s/alpha/api/promotion/preview", json={"leave": [g5]}, headers=H).json["items"]}
    assert plan[g5]["action"] == "leave"
    # The school opens a secondary section: Grade 7 now moves on to Form 1.
    r = a.put("/s/alpha/api/school", json={"school_type": "combined", "create_classes": True}, headers=H)
    assert r.status_code == 200 and r.json["school_type"] == "combined"
    plan = {p["student_id"]: p for p in a.get("/s/alpha/api/promotion/preview").json["items"]}
    assert plan[g7]["action"] == "promote" and plan[g7]["to"].startswith("Form 1")
    # Going back to primary is refused while secondary classes exist.
    assert a.put("/s/alpha/api/school", json={"school_type": "primary"}, headers=H).status_code == 409


def test_platform_hidden_in_single_school_mode():
    app = create_app("config.TestConfig")
    assert app.test_client().get("/platform/api/schools").status_code == 404


def test_legacy_class_names_are_mapped():
    class C:
        def __init__(self, name, level=1):
            self.name, self.level = name, level
    assert structure.infer_code(C("Grade 4B")) == "G4"
    assert structure.infer_code(C("Form 3 Blue")) == "F3"
    assert structure.infer_code(C("Lower Six Arts")) == "F5"
    assert structure.infer_code(C("ECD B")) == "ECD_B"
    assert structure.infer_code(C("Room 9", level=2)) == "G2"


def test_real_schools_never_allow_passwordless_sign_in(app, schools):
    c = app.test_client()
    assert c.get("/s/alpha/api/auth/demo").json["enabled"] is False
    assert c.post("/s/alpha/api/auth/demo-login", json={"username": "admin"}, headers=H).status_code == 403
