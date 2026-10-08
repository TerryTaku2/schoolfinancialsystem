"""Shared helpers: errors, validation, money, permissions, numbering and audit."""
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from functools import wraps

from flask import has_request_context, request
from flask_login import current_user, login_required

from . import db


class ApiError(Exception):
    def __init__(self, message, status=400, fields=None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.fields = fields or {}


def body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ApiError("Request body must be a JSON object")
    return data


def require(data, *names):
    missing = {n: "Required" for n in names if data.get(n) in (None, "")}
    if missing:
        raise ApiError("Please fill in all required fields", fields=missing)


def clean_str(value, max_len=None):
    if value is None:
        return None
    value = str(value).strip()
    if max_len and len(value) > max_len:
        raise ApiError(f"Value too long (max {max_len} characters)")
    return value or None


def parse_date(value, field="date", required=True):
    if value in (None, ""):
        if required:
            raise ApiError(f"{field} is required", fields={field: "Required"})
        return None
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except ValueError:
        raise ApiError(f"Invalid {field}; expected YYYY-MM-DD", fields={field: "Invalid date"})


def parse_int(value, field, required=True, minimum=None, maximum=None):
    if value in (None, ""):
        if required:
            raise ApiError(f"{field} is required", fields={field: "Required"})
        return None
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise ApiError(f"{field} must be a whole number", fields={field: "Invalid"})
    if minimum is not None and n < minimum or maximum is not None and n > maximum:
        raise ApiError(f"{field} is out of range", fields={field: "Out of range"})
    return n


def parse_float(value, field, minimum=None, maximum=None):
    try:
        n = float(value)
    except (TypeError, ValueError):
        raise ApiError(f"{field} must be a number", fields={field: "Invalid"})
    if minimum is not None and n < minimum or maximum is not None and n > maximum:
        raise ApiError(f"{field} must be between {minimum} and {maximum}", fields={field: "Out of range"})
    return n


def to_cents(value, field="amount", allow_zero=False):
    try:
        d = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        raise ApiError(f"{field} must be a valid amount", fields={field: "Invalid amount"})
    cents = int(d * 100)
    if cents < 0 or (cents == 0 and not allow_zero):
        raise ApiError(f"{field} must be greater than zero", fields={field: "Must be positive"})
    return cents


def money(cents):
    return round((cents or 0) / 100, 2)


def iso(d):
    return d.isoformat() if d else None


def normalize_phone(raw, country_code="263"):
    """Reduce a phone number to international digits: '+263 77 123 4567' and
    '0771234567' both become '263771234567', matching WhatsApp's sender format."""
    digits = "".join(ch for ch in str(raw or "") if ch.isdigit())
    if digits.startswith("00"):
        digits = digits[2:]
    elif digits.startswith("0"):
        digits = country_code + digits[1:]
    return digits


def get_or_404(model, ident, label=None):
    obj = db.session.get(model, ident)
    if obj is None:
        raise ApiError(f"{label or model.__name__} not found", 404)
    return obj


def permission_required(*perms):
    """Login + permission gate: the user needs at least one of `perms` (see services/permissions.py)."""
    def decorator(fn):
        @wraps(fn)
        @login_required
        def wrapper(*args, **kwargs):
            from .services import permissions
            permissions.require(*perms)
            return fn(*args, **kwargs)
        return wrapper
    return decorator


def next_number(key, prefix, width=5):
    """Allocate the next sequential document number inside the current transaction."""
    from .models import Counter
    counter = db.session.get(Counter, key)
    if counter is None:
        counter = Counter(key=key, value=0)
        db.session.add(counter)
    counter.value += 1
    db.session.flush()
    return f"{prefix}{counter.value:0{width}d}"


def audit(action, entity, entity_id=None, details=None):
    from .models import AuditLog
    db.session.add(AuditLog(
        user_id=current_user.id if has_request_context() and current_user.is_authenticated else None,
        action=action, entity=entity, entity_id=entity_id,
        details=(details or "")[:500],
    ))


def current_term():
    from .models import Term
    return Term.query.filter_by(is_current=True).first()


def paginate_args(default=50, maximum=500):
    page = max(parse_int(request.args.get("page"), "page", required=False) or 1, 1)
    per = parse_int(request.args.get("per_page"), "per_page", required=False) or default
    return page, min(max(per, 1), maximum)
