import os
import secrets

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


def _load_dotenv(path=os.path.join(BASE_DIR, ".env")):
    """Read KEY=VALUE lines from .env so settings survive new terminals.

    Real environment variables win over the file. Lines starting with # are comments.
    """
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()

# Hosted (Render sets RENDER=true): HTTPS-only cookies and the platform's proxy headers are trusted.
HOSTED = bool(os.environ.get("RENDER")) or os.environ.get("BEHIND_PROXY", "").lower() in ("1", "true", "yes")


def _db_url(value):
    """Hosts often hand out "postgres://", which SQLAlchemy no longer accepts."""
    if value and value.startswith("postgres://"):
        return "postgresql://" + value[len("postgres://"):]
    return value


DATABASE_URL = _db_url(os.environ.get("DATABASE_URL"))


def _secret_key():
    """Use SECRET_KEY from the environment, otherwise persist a random one in instance/."""
    key = os.environ.get("SECRET_KEY")
    if key:
        return key
    path = os.path.join(BASE_DIR, "instance", "secret.key")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not os.path.exists(path):
        with open(path, "w") as fh:
            fh.write(secrets.token_hex(32))
    with open(path) as fh:
        return fh.read().strip()


class Config:
    SECRET_KEY = _secret_key()
    SQLALCHEMY_DATABASE_URI = DATABASE_URL or "sqlite:///" + os.path.join(BASE_DIR, "instance", "school.db")
    # Hosted databases drop idle connections; check each one before use.
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True, "pool_recycle": 280} if DATABASE_URL else {}
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SESSION_COOKIE_SECURE = HOSTED
    REMEMBER_COOKIE_SECURE = HOSTED
    TRUST_PROXY = HOSTED
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = "Lax"
    JSON_SORT_KEYS = False

    # Multi-school mode: each school is served at /s/<code>/ from its own database, and
    # schools are created in the platform console at /platform/ (see app/tenancy.py).
    MULTI_SCHOOL = os.environ.get("MULTI_SCHOOL", "").lower() in ("1", "true", "yes")
    # The list of schools and the platform operators' logins.
    # Defaults to DATABASE_URL when that is set (one PostgreSQL database for everything when hosted).
    # Forgotten operator password: set PLATFORM_ADMIN_RESET=1 and a new PLATFORM_ADMIN_PASSWORD, restart, then remove it.
    PLATFORM_ADMIN_RESET = os.environ.get("PLATFORM_ADMIN_RESET", "").lower() in ("1", "true", "yes")
    PLATFORM_DATABASE_URL = _db_url(os.environ.get("PLATFORM_DATABASE_URL")) or DATABASE_URL or \
        "sqlite:///" + os.path.join(BASE_DIR, "instance", "platform.db")
    # Where new schools' databases go: a PostgreSQL URL (one schema per school), or empty for
    # one SQLite file per school in SCHOOLS_DIR.
    # When DATABASE_URL is PostgreSQL, schools default to schemas in it: local files would be lost on
    # hosts whose disks are wiped on each deploy (such as Render).
    SCHOOLS_DATABASE_URL = _db_url(os.environ.get("SCHOOLS_DATABASE_URL")) or (
        DATABASE_URL if (DATABASE_URL or "").startswith("postgresql://") else "")
    SCHOOLS_DIR = os.environ.get("SCHOOLS_DIR", os.path.join(BASE_DIR, "instance", "schools"))
    # First platform operator, created on start-up if there is none.
    PLATFORM_ADMIN_USERNAME = os.environ.get("PLATFORM_ADMIN_USERNAME", "platform")
    PLATFORM_ADMIN_PASSWORD = os.environ.get("PLATFORM_ADMIN_PASSWORD", "")

    # Single-school mode only; in multi-school mode each school has its own name and currency.
    SCHOOL_NAME = os.environ.get("SCHOOL_NAME", "Greenfield Academy")
    # Public front page (multi-school mode). Contacts left empty are not shown.
    BRAND_NAME = os.environ.get("BRAND_NAME", "School Management")
    BRAND_TAGLINE = os.environ.get("BRAND_TAGLINE", "")
    CONTACT_WHATSAPP = os.environ.get("CONTACT_WHATSAPP", "")  # e.g. 263771234567
    CONTACT_PHONE = os.environ.get("CONTACT_PHONE", "")
    CONTACT_EMAIL = os.environ.get("CONTACT_EMAIL", "")
    PRICING_NOTE = os.environ.get("PRICING_NOTE", "")
    DEMO_SCHOOL = os.environ.get("DEMO_SCHOOL", "demo")  # code of the demo school linked from the front page
    CURRENCY = os.environ.get("CURRENCY", "USD")
    # Minimum attendance % before a student is flagged as at-risk.
    ATTENDANCE_THRESHOLD = 85.0
    # Pass mark for an individual subject (percentage).
    PASS_MARK = 50.0
    # Failed login attempts allowed per username within LOGIN_WINDOW seconds.
    LOGIN_MAX_ATTEMPTS = 5
    LOGIN_WINDOW = 300

    # WhatsApp chatbot integration (see app/api/integration.py). The chatbot calls
    # /api/integration/* with this key as a Bearer token, and the same key signs the
    # notifications this app posts to CHATBOT_WEBHOOK_URL. Unset = integration off.
    CHATBOT_API_KEY = os.environ.get("CHATBOT_API_KEY", "")
    CHATBOT_WEBHOOK_URL = os.environ.get("CHATBOT_WEBHOOK_URL", "")
    # Country code assumed for guardian phones stored in local format (0771...).
    PHONE_COUNTRY_CODE = os.environ.get("PHONE_COUNTRY_CODE", "263")


class TestConfig(Config):
    TESTING = True
    SESSION_COOKIE_SECURE = REMEMBER_COOKIE_SECURE = TRUST_PROXY = False
    # Tests choose their own mode; a developer's .env must not switch them to multi-school.
    MULTI_SCHOOL = False
    PLATFORM_ADMIN_PASSWORD = ""
    PLATFORM_ADMIN_RESET = False
    # TEST_DATABASE_URL runs the suite against PostgreSQL (as used in production) instead of SQLite.
    SQLALCHEMY_DATABASE_URI = os.environ.get("TEST_DATABASE_URL", "sqlite:///:memory:")
    SECRET_KEY = "test"
    CHATBOT_API_KEY = "test-chatbot-key"
    CHATBOT_WEBHOOK_URL = ""
