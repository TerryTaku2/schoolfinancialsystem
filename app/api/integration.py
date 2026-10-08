"""Server-to-server API for the WhatsApp chatbot.

The chatbot identifies a parent only by the WhatsApp number a message came
from, so every call carries that phone and is limited to students whose
guardian has the same number. Requests authenticate with
`Authorization: Bearer <CHATBOT_API_KEY>`; with no key configured the whole
integration is switched off.
"""
import hmac
import secrets
from datetime import date

from flask import Blueprint, current_app, jsonify, request

from .. import db
from ..models import Announcement, Guardian, Payment, Student, User
from ..services import finance
from ..utils import (ApiError, body, clean_str, iso, money, normalize_phone, parse_int,
                     require, to_cents)

bp = Blueprint("integration", __name__)

BOT_USERNAME = "whatsapp-bot"


@bp.before_request
def check_api_key():
    key = current_app.config.get("CHATBOT_API_KEY")
    if not key:
        raise ApiError("Chatbot integration is not configured", 503)
    supplied = request.headers.get("Authorization", "")
    if not hmac.compare_digest(supplied.encode(), f"Bearer {key}".encode()):
        raise ApiError("Invalid API key", 401)


def _phone_arg(value):
    phone = normalize_phone(value, current_app.config["PHONE_COUNTRY_CODE"])
    if len(phone) < 9:
        raise ApiError("A valid phone number is required", fields={"phone": "Invalid"})
    return phone


def _guardians_for(phone):
    cc = current_app.config["PHONE_COUNTRY_CODE"]
    # Guardian phones are free text ("+263 77 ...", "0771..."), so compare normalized forms.
    return [g for g in Guardian.query.order_by(Guardian.id)
            if normalize_phone(g.phone, cc) == phone]


def _owned_student(student_id, phone):
    """The student, but only if `phone` belongs to that student's guardian."""
    student = db.session.get(Student, parse_int(student_id, "student_id"))
    cc = current_app.config["PHONE_COUNTRY_CODE"]
    if (student is None or student.guardian is None
            or normalize_phone(student.guardian.phone, cc) != phone):
        # Same answer for "no such student" and "not yours", so ids can't be probed.
        raise ApiError("Student not found", 404)
    return student


def _bot_user():
    """Service account recorded as the receiver of chatbot payments.

    It is inactive, so it can never log in to the web app.
    """
    user = User.query.filter_by(username=BOT_USERNAME).first()
    if user is None:
        user = User(username=BOT_USERNAME, full_name="WhatsApp Chatbot", role="bursar", active=False)
        user.set_password(secrets.token_urlsafe(32))
        db.session.add(user)
        db.session.flush()
    return user


def _student_brief(s):
    return {
        "id": s.id, "admission_no": s.admission_no, "name": s.name,
        "class": s.school_class.name if s.school_class else None, "status": s.status,
    }


def _school():
    from ..services.structure import currency, school_name
    from ..tenancy import current_school
    school = current_school()
    return {"school_name": school_name(), "school_code": school.slug if school else None, "currency": currency()}


@bp.get("/integration/guardian")
def guardian_lookup():
    phone = _phone_arg(request.args.get("phone"))
    guardians = _guardians_for(phone)
    if not guardians:
        raise ApiError("No guardian is registered with this phone number", 404)
    students = sorted((s for g in guardians for s in g.students), key=lambda s: s.admission_no)
    return jsonify(guardian=guardians[0].name, students=[_student_brief(s) for s in students],
                   **_school())


@bp.get("/integration/students/<int:sid>/account")
def student_account(sid):
    phone = _phone_arg(request.args.get("phone"))
    student = _owned_student(sid, phone)
    invoices = finance.outstanding_invoices(student)
    payments = sorted((p for p in student.payments if not p.void),
                      key=lambda p: (p.paid_on, p.id), reverse=True)[:5]
    return jsonify(
        student=_student_brief(student),
        summary=finance.account_summary(student),
        outstanding_invoices=[{
            "invoice_no": i.invoice_no, "term": i.term.label, "due_date": iso(i.due_date),
            "total": money(i.total_cents), "paid": money(i.paid_cents),
            "balance": money(i.balance_cents), "status": i.status, "currency": i.currency,
        } for i in invoices],
        recent_payments=[{
            "receipt_no": p.receipt_no, "paid_on": iso(p.paid_on),
            "amount": money(p.amount_cents), "method": p.method, "currency": p.currency,
        } for p in payments],
        **_school(),
    )


@bp.post("/integration/payments")
def record_payment():
    """Record a mobile-money payment the chatbot has already confirmed with the gateway.

    Idempotent by reference: the chatbot may retry after a timeout, and a
    repeat returns the original receipt instead of charging twice.
    """
    data = body()
    require(data, "student_id", "phone", "amount", "reference")
    student = _owned_student(data["student_id"], _phone_arg(data["phone"]))
    reference = clean_str(data["reference"], 60)
    amount_cents = to_cents(data["amount"])

    existing = Payment.query.filter_by(reference=reference, method="mobile", void=False).first()
    if existing:
        if existing.student_id != student.id or existing.amount_cents != amount_cents:
            raise ApiError(f"Reference {reference} was already used on receipt {existing.receipt_no}", 409)
        result = finance.payment_dict(existing, detail=True)
        result["duplicate"] = True
        return jsonify(result), 200

    from ..services import currency as fx
    payment = finance.record_payment(student, amount_cents, "mobile", reference,
                                     date.today(), _bot_user(), fx.pick(data.get("currency")))
    db.session.commit()
    result = finance.payment_dict(payment, detail=True)
    result["duplicate"] = False
    return jsonify(result), 201


@bp.get("/integration/announcements")
def announcements():
    items = (Announcement.query.filter(Announcement.audience.in_(("all", "parents")))
             .order_by(Announcement.pinned.desc(), Announcement.created_at.desc())
             .limit(5).all())
    return jsonify(announcements=[{
        "title": a.title, "body": a.body, "pinned": a.pinned, "date": iso(a.created_at.date()),
    } for a in items], **_school())
