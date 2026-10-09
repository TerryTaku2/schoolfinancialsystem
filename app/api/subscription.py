"""A school's own view of its subscription: what is due, how to pay, and "I've paid" (EcoCash)."""
from flask import Blueprint, current_app, jsonify
from flask_login import current_user, login_required

from .. import db
from ..models import School, SubscriptionInvoice
from ..services import permissions
from ..tenancy import current_school
from ..utils import ApiError, body, clean_str, parse_date, parse_int, require, to_cents

bp = Blueprint("subscription", __name__)


def _billing_people():
    """Administrators and whoever handles money see the subscription."""
    return current_user.role == "admin" or permissions.has_any("settings.manage", "fees.manage", "payments.manage")


def _school_row():
    school = current_school()
    return School.query.filter_by(slug=school.slug).first() if school else None


@bp.get("/subscription")
@login_required
def my_subscription():
    if not current_app.config.get("MULTI_SCHOOL") or not _billing_people():
        return jsonify(enabled=False, invoices=[], notice=[], history=[])
    from ..services import billing
    row = _school_row()
    if row is None:
        return jsonify(enabled=False, invoices=[], notice=[], history=[])
    return jsonify(billing.school_notice(row))


@bp.post("/subscription/payments")
@login_required
def report_payment():
    """The school paid by EcoCash: record the transaction ID for the operator to confirm."""
    from ..services import billing
    if not current_app.config.get("MULTI_SCHOOL") or not _billing_people():
        raise ApiError("Only the school's administrators and bursars can report subscription payments", 403)
    data = body()
    require(data, "invoice_id", "amount", "reference")
    row = _school_row()
    inv = db.session.get(SubscriptionInvoice, parse_int(data["invoice_id"], "invoice_id"))
    if inv is None or row is None or inv.school_id != row.id:
        raise ApiError("Invoice not found", 404)
    if inv.status == "paid":
        raise ApiError("This subscription is already paid", 409)
    method = data.get("method") or "ecocash"
    billing.record_payment(inv, to_cents(data["amount"]), method, data.get("reference"),
                           parse_date(data.get("paid_on"), "paid_on", required=False), status="pending",
                           reported_by=f"{current_user.full_name} ({current_user.username})",
                           note=clean_str(data.get("note"), 200))
    db.session.commit()
    return jsonify(billing.school_notice(row)), 201
