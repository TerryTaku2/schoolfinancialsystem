"""WhatsApp chatbot integration API tests. Run with:  python -m pytest -q"""
import hashlib
import hmac
import json

import pytest

from app import create_app, db
from app.models import Payment, Student, User
from app.seed import seed_demo
from app.services import notify
from app.utils import normalize_phone

AUTH = {"Authorization": "Bearer test-chatbot-key"}


@pytest.fixture(scope="module")
def app():
    app = create_app("config.TestConfig")
    with app.app_context():
        seed_demo(students_per_class=3)
        yield app


@pytest.fixture()
def client(app):
    return app.test_client()


def family():
    """A guardian with an invoiced student, and that guardian's phone in WhatsApp form."""
    student = next(s for s in Student.query.filter_by(status="active")
                   if s.guardian and s.invoices)
    return student, normalize_phone(student.guardian.phone)


def test_normalize_phone():
    assert normalize_phone("+263 77 123 4567") == "263771234567"
    assert normalize_phone("0771234567") == "263771234567"
    assert normalize_phone("00263771234567") == "263771234567"
    assert normalize_phone("263771234567") == "263771234567"


def test_requires_api_key(client):
    assert client.get("/api/integration/announcements").status_code == 401
    bad = {"Authorization": "Bearer wrong"}
    assert client.get("/api/integration/announcements", headers=bad).status_code == 401
    assert client.get("/api/integration/announcements", headers=AUTH).status_code == 200


def test_disabled_without_key(app, client):
    app.config["CHATBOT_API_KEY"] = ""
    try:
        assert client.get("/api/integration/announcements", headers=AUTH).status_code == 503
    finally:
        app.config["CHATBOT_API_KEY"] = "test-chatbot-key"


def test_guardian_lookup_matches_any_phone_format(client):
    student, phone = family()
    local = "0" + phone[3:]
    for variant in (phone, "+" + phone, local):
        r = client.get("/api/integration/guardian", query_string={"phone": variant}, headers=AUTH)
        assert r.status_code == 200, r.json
        assert student.id in [s["id"] for s in r.json["students"]]
    r = client.get("/api/integration/guardian", query_string={"phone": "263700000001"}, headers=AUTH)
    assert r.status_code == 404


def test_account_only_for_own_children(client):
    student, phone = family()
    r = client.get(f"/api/integration/students/{student.id}/account",
                   query_string={"phone": phone}, headers=AUTH)
    assert r.status_code == 200
    assert r.json["student"]["admission_no"] == student.admission_no
    assert "outstanding" in r.json["summary"]

    other = next(s for s in Student.query if s.guardian and s.guardian_id != student.guardian_id
                 and normalize_phone(s.guardian.phone) != phone)
    r = client.get(f"/api/integration/students/{other.id}/account",
                   query_string={"phone": phone}, headers=AUTH)
    assert r.status_code == 404


def test_payment_is_recorded_once_per_reference(client):
    student, phone = family()
    before = Payment.query.count()
    payload = {"student_id": student.id, "phone": phone, "amount": "25.50",
               "reference": "SCH-TEST-0001"}
    # Server-to-server calls need no CSRF header.
    r = client.post("/api/integration/payments", json=payload, headers=AUTH)
    assert r.status_code == 201, r.json
    assert r.json["amount"] == 25.5 and r.json["method"] == "mobile"
    assert r.json["received_by"] == "WhatsApp Chatbot"

    r2 = client.post("/api/integration/payments", json=payload, headers=AUTH)
    assert r2.status_code == 200 and r2.json["duplicate"] is True
    assert r2.json["receipt_no"] == r.json["receipt_no"]
    assert Payment.query.count() == before + 1

    # Same reference with a different amount is a conflict, not a silent duplicate.
    r3 = client.post("/api/integration/payments", json={**payload, "amount": "30"}, headers=AUTH)
    assert r3.status_code == 409


def test_payment_rejected_for_someone_elses_child(client):
    student, phone = family()
    other = next(s for s in Student.query if s.guardian
                 and normalize_phone(s.guardian.phone) != phone)
    r = client.post("/api/integration/payments", headers=AUTH, json={
        "student_id": other.id, "phone": phone, "amount": "10", "reference": "SCH-TEST-0002"})
    assert r.status_code == 404


def test_bot_account_cannot_log_in(client):
    bot = User.query.filter_by(username="whatsapp-bot").first()
    assert bot is not None and bot.active is False


def test_payment_notification_is_signed(app, monkeypatch):
    sent = []

    class FakeThread:
        def __init__(self, target, daemon):
            self.target = target

        def start(self):
            self.target()

    def fake_urlopen(req, timeout):
        sent.append(req)

        class Resp:
            def close(self):
                pass
        return Resp()

    monkeypatch.setattr(notify.threading, "Thread", FakeThread)
    monkeypatch.setattr(notify.urllib.request, "urlopen", fake_urlopen)
    app.config["CHATBOT_WEBHOOK_URL"] = "http://chatbot.test/webhooks/school"
    try:
        payment = Payment.query.filter_by(void=False).first()
        notify.payment_recorded(payment)
    finally:
        app.config["CHATBOT_WEBHOOK_URL"] = ""

    assert len(sent) == 1
    req = sent[0]
    expected = hmac.new(b"test-chatbot-key", req.data, hashlib.sha256).hexdigest()
    assert req.get_header("X-school-signature") == f"sha256={expected}"
    data = json.loads(req.data)
    assert data["event"] == "payment_recorded"
    assert data["receipt_no"] == payment.receipt_no
    assert data["phone"] == normalize_phone(payment.student.guardian.phone)


def test_payment_notification_uses_callback_when_mounted(app, monkeypatch):
    """Inside the chatbot, events go straight to its callback: no URL needed."""
    received = []

    class FakeThread:
        def __init__(self, target, daemon):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(notify.threading, "Thread", FakeThread)
    monkeypatch.setattr(notify.urllib.request, "urlopen",
                        lambda *a, **k: pytest.fail("must not use HTTP when a callback is set"))
    app.config["CHATBOT_NOTIFY_CALLBACK"] = received.append
    try:
        payment = Payment.query.filter_by(void=False).first()
        notify.payment_recorded(payment)
    finally:
        app.config.pop("CHATBOT_NOTIFY_CALLBACK")

    assert len(received) == 1
    assert received[0]["event"] == "payment_recorded"
    assert received[0]["receipt_no"] == payment.receipt_no
