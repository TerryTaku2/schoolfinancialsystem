"""Outbound notifications to the WhatsApp chatbot.

When this app is mounted inside the chatbot, the chatbot supplies
CHATBOT_NOTIFY_CALLBACK and events are handed to it directly. Standalone,
events are POSTed as JSON to CHATBOT_WEBHOOK_URL, signed with an HMAC-SHA256
of the body using CHATBOT_API_KEY (header `X-School-Signature: sha256=<hex>`).
Delivery is best-effort on a background thread: a chatbot outage must never
fail or slow down the school's own work.
"""
import hashlib
import hmac
import json
import logging
import threading
import urllib.request

from flask import current_app

from ..utils import money, normalize_phone
from .structure import currency, school_name

log = logging.getLogger(__name__)


def notify_chatbot(event, payload):
    message = {"event": event, **payload}
    callback = current_app.config.get("CHATBOT_NOTIFY_CALLBACK")
    if callback:
        def deliver():
            try:
                callback(message)
            except Exception as exc:  # noqa: BLE001 - best effort, just log it
                log.warning("Chatbot notification %s failed: %s", event, exc)

        threading.Thread(target=deliver, daemon=True).start()
        return

    url = current_app.config.get("CHATBOT_WEBHOOK_URL")
    key = current_app.config.get("CHATBOT_API_KEY")
    if not url or not key:
        return
    data = json.dumps(message).encode()
    signature = hmac.new(key.encode(), data, hashlib.sha256).hexdigest()
    req = urllib.request.Request(url, data=data, method="POST", headers={
        "Content-Type": "application/json",
        "X-School-Signature": f"sha256={signature}",
    })

    def send():
        try:
            urllib.request.urlopen(req, timeout=10).close()
        except Exception as exc:  # noqa: BLE001 - best effort, just log it
            log.warning("Chatbot notification %s failed: %s", event, exc)

    threading.Thread(target=send, daemon=True).start()


def payment_recorded(payment):
    """Send the guardian a WhatsApp receipt for a payment taken at the school."""
    from .finance import account_summary
    guardian = payment.student.guardian
    if guardian is None or not guardian.phone:
        return
    summary = account_summary(payment.student)
    notify_chatbot("payment_recorded", {
        "phone": normalize_phone(guardian.phone, current_app.config["PHONE_COUNTRY_CODE"]),
        "guardian": guardian.name,
        "student": payment.student.name,
        "admission_no": payment.student.admission_no,
        "receipt_no": payment.receipt_no,
        "amount": money(payment.amount_cents),
        "method": payment.method,
        "paid_on": payment.paid_on.isoformat(),
        "outstanding": summary["outstanding"],
        "credit": summary["credit"],
        "school_name": school_name(),
        "school_code": _school_code(),
        "currency": currency(),
    })


def _school_code():
    from ..tenancy import current_school
    school = current_school()
    return school.slug if school else None
