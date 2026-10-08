"""Platform console (multi-school mode): operators create, rename and suspend schools.

Each new school gets its own database, the Zimbabwe structure for its type (classes,
curriculum subjects, grading scales, a three-term calendar for this year), the chart of
accounts, ZIMRA tax tables and its first administrator. Operators never sign in to a
school with their platform login; each school's users are separate.
"""
import os
import threading
import time
from collections import defaultdict, deque

from flask import Blueprint, current_app, jsonify, render_template, request, session

from .. import db
from ..models import PlatformAdmin, School, User
from ..services import structure
from ..tenancy import forget_engine, new_school_database, use_school, valid_slug
from ..utils import ApiError, body, clean_str, require
from .auth import validate_password

bp = Blueprint("platform", __name__)

from ..services.currency import CURRENCIES, catalog as currency_catalog  # noqa: E402
_failed = defaultdict(deque)


def bootstrap_admin(app):
    """Create the first platform operator from PLATFORM_ADMIN_PASSWORD when there is none."""
    if PlatformAdmin.query.first():
        return
    password = app.config.get("PLATFORM_ADMIN_PASSWORD")
    if not password:
        app.logger.warning("No platform operator yet. Set PLATFORM_ADMIN_PASSWORD or run "
                           "'flask --app run create-platform-admin'.")
        return
    admin = PlatformAdmin(username=app.config["PLATFORM_ADMIN_USERNAME"].strip().lower(),
                          full_name="Platform Administrator")
    admin.set_password(password)
    db.session.add(admin)
    db.session.commit()


@bp.before_request
def only_in_multi_school_mode():
    if not current_app.config.get("MULTI_SCHOOL"):
        raise ApiError("Multi-school mode is off, so there is no platform console. Add MULTI_SCHOOL=1 "
                       "to the .env file in the project folder (or set it in the environment) and restart "
                       "the server.", 404)


def _operator():
    aid = session.get("platform_admin_id")
    op = db.session.get(PlatformAdmin, aid) if aid else None
    return op if op and op.active else None


def _require_operator():
    op = _operator()
    if not op:
        raise ApiError("Sign in to the platform console", 401)
    return op


def school_dict(s):
    root = request.script_root
    setup = SETUP.get(s.slug)
    return {"id": s.id, "slug": s.slug, "name": s.name, "school_type": s.school_type,
            "school_type_label": structure.SCHOOL_TYPES.get(s.school_type, s.school_type),
            "currency": s.currency, "status": s.status, "created_at": s.created_at.isoformat(),
            "preparing": bool(setup and setup["state"] == "preparing"),
            "url": f"{root}/s/{s.slug}/", "storage": "PostgreSQL schema " + s.db_schema if s.db_schema else "SQLite"}


@bp.get("/platform/")
@bp.get("/platform")
def console():
    return render_template("platform.html")


@bp.post("/platform/api/login")
def login():
    data = body()
    require(data, "username", "password")
    username = str(data["username"]).strip().lower()
    q = _failed[username]
    now = time.time()
    while q and now - q[0] > current_app.config["LOGIN_WINDOW"]:
        q.popleft()
    if len(q) >= current_app.config["LOGIN_MAX_ATTEMPTS"]:
        raise ApiError("Too many failed attempts. Try again in a few minutes.", 429)
    op = PlatformAdmin.query.filter_by(username=username).first()
    if not op or not op.active or not op.check_password(str(data["password"])):
        q.append(now)
        raise ApiError("Invalid username or password", 401)
    _failed.pop(username, None)
    session.clear()
    session["platform_admin_id"] = op.id
    return jsonify(user={"username": op.username, "full_name": op.full_name})


@bp.post("/platform/api/logout")
def logout():
    session.clear()
    return jsonify(ok=True)


@bp.get("/platform/api/me")
def me():
    op = _require_operator()
    return jsonify(user={"username": op.username, "full_name": op.full_name},
                   school_types=[{"value": k, "label": v} for k, v in structure.SCHOOL_TYPES.items()],
                   currencies=CURRENCIES, currency_catalog=currency_catalog())


@bp.get("/platform/api/schools")
def list_schools():
    _require_operator()
    failed = [dict(v, slug=k) for k, v in SETUP.items() if v["state"] == "failed"]
    for f in failed:
        SETUP.pop(f["slug"], None)  # reported once
    return jsonify(items=[school_dict(s) for s in School.query.order_by(School.name)],
                   failed=[{"slug": f["slug"], "name": f["name"], "error": f["error"]} for f in failed])


# Demo schools being filled in the background: {slug: {"state": "preparing" | "failed", "name", "error"}}.
# Filling a demo takes minutes on a small server, longer than a web request may last, so it runs
# in a thread while the school stays suspended (nobody can open a half-filled school).
SETUP = {}
DEMO_STUDENTS_PER_CLASS = 8


def provision_school(name, slug, school_type, currency, admin, demo=False, background=False,
                     students_per_class=None):
    """Create a school, its database and its first administrator. Returns the School row.

    background=True returns at once with the school suspended and fills it in a thread
    (used for demo schools from the console); it becomes active when ready."""
    slug = (slug or "").strip().lower()
    if not valid_slug(slug):
        raise ApiError("School code: 3-40 lowercase letters, numbers or hyphens, not starting or ending "
                       "with a hyphen", fields={"slug": "Invalid"})
    if School.query.filter_by(slug=slug).first():
        raise ApiError("That school code is already taken", fields={"slug": "Taken"})
    if school_type not in structure.SCHOOL_TYPES:
        raise ApiError("Invalid school type", fields={"school_type": "Invalid"})
    if currency not in CURRENCIES:
        raise ApiError(f"Currency must be one of {', '.join(CURRENCIES)}", fields={"currency": "Invalid"})
    username = str(admin.get("username") or "").strip().lower()
    if len(username) < 3 or not username.replace(".", "").replace("_", "").isalnum():
        raise ApiError("Administrator username: 3+ letters/numbers", fields={"admin_username": "Invalid"})
    validate_password(str(admin.get("password") or ""))
    url, schema = new_school_database(slug)
    if url.startswith("sqlite:///") and os.path.exists(url[len("sqlite:///"):]):
        raise ApiError("A database for that school code already exists on the server. Choose another code.", 409)
    school = School(slug=slug, name=name, school_type=school_type, currency=currency,
                    database_url=url, db_schema=schema, status="suspended" if background else "active")
    db.session.add(school)
    db.session.commit()
    school_id = school.id
    admin = {**admin, "username": username}
    if background:
        SETUP[slug] = {"state": "preparing", "name": name, "error": None}
        app = current_app._get_current_object()

        def work():
            with app.app_context():
                try:
                    _fill_school(school_id, school_type, admin, demo, students_per_class)
                    row = db.session.get(School, school_id)
                    row.status = "active"
                    db.session.commit()
                    SETUP.pop(slug, None)
                except Exception as err:  # reported in the console; the half-made school is removed
                    current_app.logger.exception("Setting up school %s failed", slug)
                    SETUP[slug] = {"state": "failed", "name": name, "error": str(err)[:300]}

        threading.Thread(target=work, name=f"setup-{slug}", daemon=True).start()
        return school
    _fill_school(school_id, school_type, admin, demo, students_per_class)
    return db.session.get(School, school_id)


def _fill_school(school_id, school_type, admin, demo, students_per_class=None):
    """Load the structure (or demo data) and the first administrator into a new school's database.
    On failure the school and its database are removed again."""
    school = db.session.get(School, school_id)
    slug, url = school.slug, school.database_url
    try:
        with use_school(school):
            if demo:
                from ..seed import seed_demo
                seed_demo(school_type=school_type,
                          **({"students_per_class": students_per_class} if students_per_class else {}))
            else:
                structure.setup_new_school(school_type)
            username = admin["username"]
            if User.query.filter_by(username=username).first():
                user = User.query.filter_by(username=username).first()
                user.role, user.active = "admin", True
            else:
                user = User(username=username, full_name=admin.get("full_name") or "School Administrator",
                            role="admin", email=admin.get("email"))
                db.session.add(user)
            user.set_password(str(admin["password"]))
            db.session.commit()
    except Exception:
        db.session.rollback()
        forget_engine(slug)
        row = db.session.get(School, school_id)
        if row:
            db.session.delete(row)
            db.session.commit()
        if url.startswith("sqlite:///"):
            try:
                os.remove(url[len("sqlite:///"):])
            except OSError:
                pass
        raise


@bp.post("/platform/api/schools")
def create_school():
    _require_operator()
    data = body()
    require(data, "name", "slug", "school_type", "admin_username", "admin_password")
    school = provision_school(
        clean_str(data["name"], 120), data["slug"], data["school_type"], data.get("currency") or "USD",
        {"username": data["admin_username"], "password": data["admin_password"],
         "full_name": clean_str(data.get("admin_full_name"), 120), "email": clean_str(data.get("admin_email"), 120)},
        demo=bool(data.get("demo")), background=bool(data.get("demo")),
        students_per_class=DEMO_STUDENTS_PER_CLASS if data.get("demo") else None)
    return jsonify(school_dict(school)), 201


@bp.put("/platform/api/schools/<int:sid>")
def update_school(sid):
    _require_operator()
    s = db.session.get(School, sid)
    if not s:
        raise ApiError("School not found", 404)
    data = body()
    if data.get("name"):
        s.name = clean_str(data["name"], 120)
    if "status" in data:
        if data["status"] not in ("active", "suspended"):
            raise ApiError("Invalid status")
        if s.slug in SETUP:
            raise ApiError("This school is still being set up. It becomes active by itself when ready.", 409)
        s.status = data["status"]
    db.session.commit()
    return jsonify(school_dict(s))
