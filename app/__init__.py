from flask import Flask, jsonify, render_template, request
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import UniqueConstraint, event, inspect, text
from sqlalchemy.engine import Engine
from werkzeug.exceptions import HTTPException

from .tenancy import (SchoolLoginManager, SchoolPrefixMiddleware, SchoolSessionInterface, TenantSession,
                      resolve_school, user_id_for_school)

db = SQLAlchemy(session_options={"class_": TenantSession})
login_manager = SchoolLoginManager()


@event.listens_for(Engine, "connect")
def _sqlite_pragmas(dbapi_conn, _record):
    # SQLite ignores foreign keys unless asked; we rely on them for integrity.
    if dbapi_conn.__class__.__module__.startswith("sqlite3"):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()


def _add_missing_columns(engine):
    """Tiny forward-only migration: add new nullable columns to existing tables.

    create_all() creates new tables but never alters existing ones, so databases
    created by an earlier version would otherwise miss newly added columns.
    """
    insp = inspect(engine)
    with engine.begin() as conn:
        for table in db.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            existing = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in existing or not col.nullable:
                    continue
                coltype = col.type.compile(engine.dialect)
                conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {coltype}'))


def _rebuild_grade_bands(engine):
    """Grade bands used to be one scale with unique letters; now each section has its own scale.

    The old unique constraint on `letter` can't be altered in place on SQLite, so the small
    table is copied out, recreated and refilled (existing bands become the primary scale).
    """
    insp = inspect(engine)
    if not insp.has_table("grade_band"):
        return
    cols = {c["name"] for c in insp.get_columns("grade_band")}
    letter_unique = any(u["column_names"] == ["letter"] for u in insp.get_unique_constraints("grade_band")) or \
        any(i.get("unique") and i["column_names"] == ["letter"] for i in insp.get_indexes("grade_band"))
    if "section" in cols and not letter_unique:
        return
    from .models import GradeBand
    with engine.begin() as conn:
        rows = conn.execute(text("SELECT letter, min_score, points, remark FROM grade_band")).fetchall()
        conn.execute(text("DROP TABLE grade_band"))
    GradeBand.__table__.create(engine)
    with engine.begin() as conn:
        for letter, mn, pts, remark in rows:
            conn.execute(GradeBand.__table__.insert().values(section="primary", letter=letter, min_score=mn,
                                                             points=pts or 0, remark=remark))


def _widen_unique(engine, table_name, old_cols):
    """Replace an old unique constraint on `old_cols` with the model's current one.

    Used when a rule gains a column (one invoice per student per term -> per term *and
    currency*). PostgreSQL drops the old constraint by name. SQLite can't alter
    constraints, so the table is rebuilt the standard way (new table, copy, drop, rename)
    with foreign keys switched off, which keeps every row and every reference to it.
    """
    insp = inspect(engine)
    if not insp.has_table(table_name):
        return
    old = [u for u in insp.get_unique_constraints(table_name) if sorted(u["column_names"]) == sorted(old_cols)]
    old_idx = [i for i in insp.get_indexes(table_name) if i.get("unique") and sorted(i["column_names"]) == sorted(old_cols)]
    if not old and not old_idx:
        return
    table = db.metadata.tables[table_name]
    _add_missing_columns(engine)  # the new constraint's columns must exist before copying
    if engine.dialect.name != "sqlite":
        with engine.begin() as conn:
            for u in old:
                conn.execute(text(f'ALTER TABLE "{table_name}" DROP CONSTRAINT "{u["name"]}"'))
            for i in old_idx:
                conn.execute(text(f'DROP INDEX IF EXISTS "{i["name"]}"'))
            for c in table.constraints:
                if isinstance(c, UniqueConstraint):
                    cols = ", ".join(f'"{col.name}"' for col in c.columns)
                    conn.execute(text(f'ALTER TABLE "{table_name}" ADD CONSTRAINT "{c.name}" UNIQUE ({cols})'))
        return
    from sqlalchemy.schema import CreateIndex, CreateTable
    existing = [c["name"] for c in insp.get_columns(table_name)]
    cols = ", ".join(f'"{c.name}"' for c in table.columns if c.name in existing)
    ddl = str(CreateTable(table).compile(engine)).replace(f'CREATE TABLE {table_name} ', f'CREATE TABLE "{table_name}__new" ', 1)
    with engine.connect() as conn:
        # PRAGMA foreign_keys is ignored inside a transaction, so end the implicit one first.
        conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
        conn.commit()
        try:
            with conn.begin():
                conn.exec_driver_sql(ddl)
                conn.exec_driver_sql(f'INSERT INTO "{table_name}__new" ({cols}) SELECT {cols} FROM "{table_name}"')
                conn.exec_driver_sql(f'DROP TABLE "{table_name}"')
                conn.exec_driver_sql(f'ALTER TABLE "{table_name}__new" RENAME TO "{table_name}"')
                for index in table.indexes:
                    conn.execute(CreateIndex(index))
        finally:
            conn.exec_driver_sql("PRAGMA foreign_keys=ON")
            conn.commit()


def upgrade_schema(engine):
    """Create missing school tables and apply forward-only upgrades to one school database."""
    db.metadata.create_all(engine)
    _rebuild_grade_bands(engine)
    _widen_unique(engine, "invoice", ["student_id", "term_id"])
    _widen_unique(engine, "payroll_remittance", ["run_id", "type"])
    _widen_unique(engine, "exchange_rate", ["date"])  # one rate a day -> one per currency per day
    _add_missing_columns(engine)


def create_app(config_object="config.Config"):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(config_object)
    multi = app.config.get("MULTI_SCHOOL")
    if multi:
        app.config.setdefault("SQLALCHEMY_BINDS", {})
        app.config["SQLALCHEMY_BINDS"] = {**app.config["SQLALCHEMY_BINDS"],
                                          "platform": app.config["PLATFORM_DATABASE_URL"]}

    db.init_app(app)
    login_manager.init_app(app)
    app.session_interface = SchoolSessionInterface()
    app.wsgi_app = SchoolPrefixMiddleware(app.wsgi_app)
    if app.config.get("TRUST_PROXY"):
        # Behind the host's HTTPS proxy: use the real scheme/host/client so URLs and cookies are right.
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    from .models import User

    @login_manager.user_loader
    def load_user(raw_id):
        uid = user_id_for_school(raw_id)
        return db.session.get(User, uid) if uid is not None else None

    @login_manager.unauthorized_handler
    def unauthorized():
        return jsonify(error="Authentication required"), 401

    from .utils import ApiError

    @app.errorhandler(ApiError)
    def handle_api_error(err):
        db.session.rollback()
        payload = {"error": err.message}
        if err.fields:
            payload["fields"] = err.fields
        return jsonify(payload), err.status

    @app.errorhandler(HTTPException)
    def handle_http(err):
        if request.path.startswith(("/api/", "/platform/api/")):
            return jsonify(error=err.description), err.code
        return err

    # Must run first: everything after it relies on the request being tied to its school.
    app.before_request(resolve_school)

    @app.before_request
    def csrf_guard():
        # Browsers will not attach custom headers to cross-site form posts, so
        # requiring one on every state-changing API call blocks CSRF.
        # The chatbot integration is server-to-server and authenticates with a
        # bearer key that browsers never send on their own, so CSRF can't apply.
        if request.path.startswith("/api/integration/"):
            return None
        if request.path.startswith(("/api/", "/platform/api/")) and request.method not in ("GET", "HEAD", "OPTIONS"):
            if request.headers.get("X-Requested-With") != "SchoolMS":
                return jsonify(error="Missing CSRF header"), 403

    @app.after_request
    def security_headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        return resp

    from .api import register_blueprints
    register_blueprints(app)

    from .services.structure import school_name
    from .tenancy import current_school

    @app.get("/manifest.webmanifest")
    def web_manifest():
        """Install details for this school: its own name, icons and address."""
        import json
        from flask import Response
        from .services.structure import school_name as _name
        name = _name()
        icon = lambda f, size, purpose: {"src": f"static/icons/{f}", "sizes": size, "type": "image/png", "purpose": purpose}
        shortcut = lambda label, route: {"name": label, "url": f"./#/{route}",
                                         "icons": [{"src": "static/icons/icon-192.png", "sizes": "192x192"}]}
        data = {
            "name": name, "short_name": name if len(name) <= 15 else name.split()[0][:15],
            "description": f"{name}: students, fees, payroll and accounts",
            "start_url": "./", "scope": "./", "display": "standalone", "orientation": "any",
            "background_color": "#f4f5f7", "theme_color": "#2a78d6", "categories": ["education", "finance", "productivity"],
            "icons": [icon("icon-192.png", "192x192", "any"), icon("icon-512.png", "512x512", "any"),
                      icon("maskable-192.png", "192x192", "maskable"), icon("maskable-512.png", "512x512", "maskable")],
            "shortcuts": [shortcut("Record a payment", "payments"), shortcut("Students", "students"),
                          shortcut("Dashboard", "dashboard")],
        }
        return Response(json.dumps(data), mimetype="application/manifest+json", headers={"Cache-Control": "no-cache"})

    @app.get("/sw.js")
    def service_worker():
        from flask import send_from_directory
        resp = send_from_directory(app.static_folder, "sw.js", mimetype="application/javascript")
        resp.headers["Cache-Control"] = "no-cache"  # browsers must always check for a new version
        return resp

    @app.get("/")
    def index():
        if multi and not current_school():
            return render_template("landing.html", error=None)
        return render_template("index.html", school_name=school_name())

    with app.app_context():
        if multi:
            db.create_all(bind_key="platform")
            from .api.platform import bootstrap_admin
            bootstrap_admin(app)
        else:
            upgrade_schema(db.engine)
            from .services import currency, ledger, payroll, structure
            ledger.ensure_chart()
            payroll.ensure_tax_table()
            structure.migrate_legacy()
            currency.migrate()
            from .services import permissions
            permissions.ensure_roles()
            db.session.commit()

    from .seed import register_cli
    register_cli(app)

    return app
