import time
from collections import defaultdict, deque

from flask import Blueprint, current_app, jsonify
from flask_login import current_user, login_required, login_user, logout_user

from .. import db
from ..models import User, utcnow
from ..services.structure import currency, school_name
from ..tenancy import current_school
from ..utils import ApiError, audit, body, require

bp = Blueprint("auth", __name__)

_failed = defaultdict(deque)  # (school, username) -> timestamps of recent failures


def _key(username):
    # Usernames repeat across schools ("admin"), so throttle per school.
    school = current_school()
    return (school.slug if school else "", username)


def _throttled(username):
    window = current_app.config["LOGIN_WINDOW"]
    q = _failed[_key(username)]
    now = time.time()
    while q and now - q[0] > window:
        q.popleft()
    return len(q) >= current_app.config["LOGIN_MAX_ATTEMPTS"]


def validate_password(pw):
    if len(pw) < 8 or pw.isalpha() or pw.isdigit():
        raise ApiError("Password must be at least 8 characters and mix letters with numbers or symbols",
                       fields={"password": "Too weak"})


@bp.post("/auth/login")
def login():
    data = body()
    require(data, "username", "password")
    username = str(data["username"]).strip().lower()
    if _throttled(username):
        raise ApiError("Too many failed attempts. Try again in a few minutes.", 429)
    user = User.query.filter_by(username=username).first()
    if not user or not user.check_password(str(data["password"])):
        _failed[_key(username)].append(time.time())
        raise ApiError("Invalid username or password", 401)
    if not user.active:
        raise ApiError("This account has been deactivated", 403)
    _failed.pop(_key(username), None)
    login_user(user, remember=bool(data.get("remember")))
    user.last_login = utcnow()
    audit("login", "user", user.id)
    db.session.commit()
    return jsonify(user=user.to_dict())


@bp.post("/auth/find-school")
def find_school():
    """Multi-school front page: which schools do these credentials sign in to?

    The browser then signs in at the school's own address, so cookies stay scoped to it.
    Only schools where the password matches are listed, so this reveals nothing about
    which usernames exist.
    """
    from flask import request
    from ..models import School
    from ..tenancy import _snapshot, use_school
    if not current_app.config.get("MULTI_SCHOOL") or current_school():
        raise ApiError("Not found", 404)
    data = body()
    require(data, "username", "password")
    username, password = str(data["username"]).strip().lower(), str(data["password"])
    if _throttled(username):
        raise ApiError("Too many failed attempts. Try again in a few minutes.", 429)
    root = request.environ.get("school.root", request.script_root)
    matches = []
    for school in [_snapshot(s) for s in School.query.filter_by(status="active").order_by(School.name)]:
        try:
            with use_school(school):
                user = User.query.filter_by(username=username).first()
                if user and user.active and user.check_password(password):
                    matches.append({"slug": school.slug, "name": school.name, "url": f"{root}/s/{school.slug}/"})
        except Exception:  # one unreachable school database must not block everyone else's sign-in
            current_app.logger.exception("Sign-in lookup failed for school %s", school.slug)
    if not matches:
        _failed[_key(username)].append(time.time())
        raise ApiError("Invalid username or password", 401)
    _failed.pop(_key(username), None)
    return jsonify(schools=matches)


@bp.get("/auth/demo")
def demo_accounts():
    """The one-click demo sign-ins offered on the login page (demo schools only)."""
    from ..services.structure import DEMO_USERS, is_demo
    if not is_demo():
        return jsonify(enabled=False, accounts=[])
    users = {u.username: u for u in User.query.filter(User.username.in_(DEMO_USERS), User.active.is_(True))}
    return jsonify(enabled=True, accounts=[{"username": n, "label": label} for n, label in DEMO_USERS.items() if n in users])


@bp.post("/auth/demo-login")
def demo_login():
    """Sign in to a demo account without a password. Refused unless this is a demo school."""
    from ..services.structure import DEMO_USERS, is_demo
    if not is_demo():
        raise ApiError("Passwordless sign-in is only available in demo schools", 403)
    username = str(body().get("username") or "").strip().lower()
    user = User.query.filter_by(username=username).first() if username in DEMO_USERS else None
    if user is None or not user.active:
        raise ApiError("Unknown demo account", 404)
    login_user(user, remember=False)
    user.last_login = utcnow()
    audit("demo_login", "user", user.id, "signed in without a password (demo school)")
    db.session.commit()
    return jsonify(user=user.to_dict())


@bp.post("/auth/logout")
@login_required
def logout():
    logout_user()
    return jsonify(ok=True)


@bp.get("/auth/me")
@login_required
def me():
    return jsonify(user=current_user.to_dict(),
                   school=school_name(), currency=currency())


@bp.post("/auth/change-password")
@login_required
def change_password():
    data = body()
    require(data, "current_password", "new_password")
    if not current_user.check_password(data["current_password"]):
        raise ApiError("Current password is incorrect", fields={"current_password": "Incorrect"})
    validate_password(data["new_password"])
    current_user.set_password(data["new_password"])
    audit("password", "user", current_user.id)
    db.session.commit()
    return jsonify(ok=True)
