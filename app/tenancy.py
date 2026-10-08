"""Multi-school support: one database per school, chosen from the URL on every request.

    /s/<school-code>/...    a school's own app (its pages, API, logins), backed by its own database
    /platform/...           the platform console, where operators create and manage schools
    /                       sign-in by username and password; the school is found from the account

How data is kept apart:
- Each school's tables live in their own database: a SQLite file under instance/schools/, or a
  PostgreSQL schema when SCHOOLS_DATABASE_URL points at PostgreSQL. The list of schools lives in
  a separate platform database. No query can reach another school's rows, because the
  session is only ever connected to the current school's database (see TenantSession).
- The /s/<code> prefix is moved into SCRIPT_NAME before Flask sees the request, so the rest of
  the app is unchanged and the browser's API calls are prefixed automatically.
- Session and remember-me cookies are scoped to the school's path, and login ids carry the
  school code ("greenfield:5"), so being signed in to one school never signs you in to another.

Single-school mode (MULTI_SCHOOL off, the default) behaves exactly as before: one database,
served at "/" (or wherever the app is mounted).
"""
import os
import re
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta
from types import SimpleNamespace

import sqlalchemy as sa
from flask import current_app, g, has_app_context, jsonify, request, session
from flask.sessions import SecureCookieSessionInterface
from flask_login import LoginManager
from flask_login.utils import encode_cookie
from flask_sqlalchemy.session import Session as FsaSession

PREFIX = "/s/"
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$")
RESERVED_SLUGS = {"platform", "static", "api", "admin", "www", "school", "schools", "login"}

_engines = {}
_prepared = set()
_lock = threading.RLock()


# --------------------------------------------------------------------------- #
# Current school
# --------------------------------------------------------------------------- #
def current_school():
    """The school this request (or `use_school` block) works on, or None in single-school mode."""
    return g.get("school") if has_app_context() else None


def _snapshot(row):
    # A plain copy, so it stays usable after the platform session is committed or closed.
    return SimpleNamespace(id=row.id, slug=row.slug, name=row.name, school_type=row.school_type,
                           currency=row.currency, database_url=row.database_url, db_schema=row.db_schema,
                           status=row.status)


class TenantSession(FsaSession):
    """Routes every query for school tables to the current school's database.

    Platform tables (bind key "platform") keep using the platform database.
    """

    def get_bind(self, mapper=None, clause=None, bind=None, **kwargs):
        if bind is not None:
            return bind
        engine = g.get("tenant_engine") if has_app_context() else None
        if engine is not None and not _is_platform(mapper, clause):
            return engine
        return super().get_bind(mapper=mapper, clause=clause, bind=bind, **kwargs)


def _is_platform(mapper, clause):
    table = None
    if mapper is not None:
        try:
            table = sa.inspect(mapper).local_table
        except sa.exc.NoInspectionAvailable:
            table = None
    if table is None and clause is not None:
        if isinstance(clause, sa.Table):
            table = clause
        elif isinstance(clause, sa.sql.dml.UpdateBase) and isinstance(clause.table, sa.Table):
            table = clause.table
    return table is not None and table.metadata.info.get("bind_key") == "platform"


# --------------------------------------------------------------------------- #
# School databases
# --------------------------------------------------------------------------- #
def _sa_url(url):
    return "postgresql://" + url[len("postgres://"):] if url.startswith("postgres://") else url


def new_school_database(slug):
    """(database_url, schema) for a new school."""
    base = current_app.config.get("SCHOOLS_DATABASE_URL") or ""
    if base.startswith(("postgres://", "postgresql://")):
        return base, "school_" + slug.replace("-", "_")
    folder = current_app.config.get("SCHOOLS_DIR") or os.path.join(current_app.instance_path, "schools")
    os.makedirs(folder, exist_ok=True)
    return "sqlite:///" + os.path.join(os.path.abspath(folder), f"{slug}.db"), None


def engine_for(school):
    with _lock:
        engine = _engines.get(school.slug)
        if engine is None:
            kwargs = {"pool_pre_ping": True}
            if school.db_schema:
                kwargs["connect_args"] = {"options": f"-c search_path={school.db_schema}"}
            engine = sa.create_engine(_sa_url(school.database_url), **kwargs)
            _engines[school.slug] = engine
        return engine


def forget_engine(slug):
    with _lock:
        engine = _engines.pop(slug, None)
        _prepared.discard(slug)
    if engine is not None:
        engine.dispose()


def prepare(school):
    """Create or upgrade a school's tables and defaults, once per process."""
    if school.slug in _prepared:
        return
    with _lock:
        if school.slug in _prepared:
            return
        from . import db, upgrade_schema
        engine = g.tenant_engine
        if school.db_schema:
            with engine.begin() as conn:
                conn.execute(sa.text(f'CREATE SCHEMA IF NOT EXISTS "{school.db_schema}"'))
        upgrade_schema(engine)
        from .services import currency, ledger, payroll, structure
        ledger.ensure_chart()
        payroll.ensure_tax_table()
        structure.migrate_legacy()
        currency.migrate()
        from .services import permissions
        permissions.ensure_roles()
        db.session.commit()
        _prepared.add(school.slug)


@contextmanager
def use_school(school):
    """Work on one school's database outside a request (CLI commands, provisioning, tests).

    Commit any pending work before entering: the session is reset on the way in and out.
    """
    from . import db
    ref = school if isinstance(school, SimpleNamespace) else _snapshot(school)
    previous = (g.get("school"), g.get("tenant_engine"))
    db.session.remove()
    g.school, g.tenant_engine = ref, engine_for(ref)
    try:
        prepare(ref)
        yield ref
    finally:
        db.session.remove()
        g.school, g.tenant_engine = previous


def get_school(slug):
    from .models import School
    return School.query.filter_by(slug=(slug or "").strip().lower()).first()


def valid_slug(slug):
    return bool(SLUG_RE.match(slug or "")) and slug not in RESERVED_SLUGS


# --------------------------------------------------------------------------- #
# Request plumbing
# --------------------------------------------------------------------------- #
class SchoolPrefixMiddleware:
    """Moves /s/<code> from PATH_INFO into SCRIPT_NAME and remembers the code."""

    def __init__(self, app):
        self.app = app

    def __call__(self, environ, start_response):
        path = environ.get("PATH_INFO", "")
        if path.startswith(PREFIX):
            slug, sep, rest = path[len(PREFIX):].partition("/")
            if slug:
                if not sep:
                    query = environ.get("QUERY_STRING")
                    location = environ.get("SCRIPT_NAME", "") + path + "/" + (f"?{query}" if query else "")
                    start_response("308 Permanent Redirect", [("Location", location), ("Content-Length", "0")])
                    return [b""]
                environ["school.root"] = environ.get("SCRIPT_NAME", "")
                environ["SCRIPT_NAME"] = environ["school.root"] + PREFIX + slug
                environ["PATH_INFO"] = "/" + rest
                environ["school.slug"] = slug.lower()
        return self.app(environ, start_response)


def _not_found(message):
    if request.path.startswith("/api/"):
        return jsonify(error=message), 404
    from flask import render_template
    return render_template("landing.html", error=message), 404


def resolve_school():
    """before_request: connect this request to its school's database."""
    g.school = g.tenant_engine = None
    slug = request.environ.get("school.slug")
    multi = current_app.config.get("MULTI_SCHOOL")
    if slug is None:
        if multi and not (request.path in ("/", "/sw.js", "/manifest.webmanifest", "/api/auth/find-school") or request.path.startswith(("/platform", "/static/"))):
            return _not_found("Open your school's own address, for example /s/your-school-code/")
        return None
    if not multi:
        return _not_found("Not found")
    row = get_school(slug)
    if row is None:
        return _not_found("That school address doesn't exist. Sign in below and we'll take you to your school.")
    if row.status != "active":
        from .api.platform import SETUP
        if SETUP.get(slug, {}).get("state") == "preparing":
            return _not_found("This school is still being set up. Please try again in a few minutes.")
        return _not_found("This school's account is suspended. Please contact the platform administrator.")
    g.school = _snapshot(row)
    g.tenant_engine = engine_for(g.school)
    prepare(g.school)
    return None


class SchoolSessionInterface(SecureCookieSessionInterface):
    """Scopes the session cookie to the school (or the platform console)."""

    def get_cookie_path(self, app):
        if current_school():
            return request.script_root + "/"
        if app.config.get("MULTI_SCHOOL") and request.path.startswith("/platform"):
            return request.script_root + "/platform"
        return super().get_cookie_path(app)


class SchoolLoginManager(LoginManager):
    """Flask-Login with the remember-me cookie scoped to the school's path."""

    def _cookie_path(self):
        if current_school():
            return request.script_root + "/"
        return current_app.config.get("REMEMBER_COOKIE_PATH", "/")

    def _set_cookie(self, response):
        config = current_app.config
        duration = (timedelta(seconds=session["_remember_seconds"]) if "_remember_seconds" in session
                    else config.get("REMEMBER_COOKIE_DURATION", timedelta(days=365)))
        if isinstance(duration, int):
            duration = timedelta(seconds=duration)
        response.set_cookie(config.get("REMEMBER_COOKIE_NAME", "remember_token"),
                            value=encode_cookie(str(session["_user_id"])),
                            expires=datetime.utcnow() + duration, domain=config.get("REMEMBER_COOKIE_DOMAIN"),
                            path=self._cookie_path(), secure=config.get("REMEMBER_COOKIE_SECURE", False),
                            httponly=config.get("REMEMBER_COOKIE_HTTPONLY", True),
                            samesite=config.get("REMEMBER_COOKIE_SAMESITE"))

    def _clear_cookie(self, response):
        config = current_app.config
        response.delete_cookie(config.get("REMEMBER_COOKIE_NAME", "remember_token"),
                               domain=config.get("REMEMBER_COOKIE_DOMAIN"), path=self._cookie_path())


def user_id_for_school(raw):
    """Turn a login id from the session/cookie into a user id, or None if it belongs elsewhere."""
    school = current_school()
    raw = str(raw)
    if ":" in raw:
        slug, _, ident = raw.rpartition(":")
        if not school or slug != school.slug:
            return None
    else:
        if school:
            return None
        ident = raw
    try:
        return int(ident)
    except ValueError:
        return None


def current_engine():
    """The database engine for the current school (or the single-school database)."""
    from . import db
    return g.get("tenant_engine") or db.engine
