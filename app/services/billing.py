"""Subscriptions: billing schools for using the system (multi-school mode, platform database).

- The operator sets a default price per learner per term and a minimum per term; any school can
  have its own price, or be free (a pilot).
- "Bill a term" creates one invoice per active school for that period, from the number of active
  learners at the time.
- Schools pay by EcoCash or cash. The operator records payments; or the school reports an EcoCash
  payment with its transaction ID ("I've paid"), which counts once the operator confirms it.
- School administrators and bursars see a notice in the app from `notice_days` before the due date,
  turning to "overdue" after it. The operator decides when to suspend a school that doesn't pay.
"""
from datetime import date
from urllib.parse import quote

from .. import db
from ..models import PlatformSetting, School, Student, SubscriptionInvoice, SubscriptionPayment
from ..utils import ApiError, money

SETTINGS = {  # key: (default, kind)
    "currency": ("USD", "str"),
    "default_rate_cents": (80, "int"),        # per learner per term
    "default_minimum_cents": (6000, "int"),   # per term
    "notice_days": (14, "int"),               # show the notice this many days before the due date
    "ecocash_number": ("", "str"),
    "ecocash_name": ("", "str"),
    "cash_instructions": ("", "str"),
    "support_phone": ("", "str"),
}


def settings():
    out = {}
    for key, (default, kind) in SETTINGS.items():
        row = db.session.get(PlatformSetting, f"billing_{key}")
        value = row.value if row and row.value is not None else default
        out[key] = int(value) if kind == "int" and str(value).strip().lstrip("-").isdigit() else (default if kind == "int" else value)
    return out


def save_settings(data):
    for key, (default, kind) in SETTINGS.items():
        if key not in data:
            continue
        value = data[key]
        if kind == "int":
            try:
                value = int(value)
            except (TypeError, ValueError):
                raise ApiError(f"{key.replace('_', ' ')} must be a whole number", fields={key: "Invalid"})
            if value < 0 or value > 100_000_000:
                raise ApiError(f"{key.replace('_', ' ')} is out of range", fields={key: "Out of range"})
        else:
            value = str(value or "").strip()[:500]
            if key == "currency" and value not in ("USD", "ZWG", "ZAR"):
                raise ApiError("Billing currency must be USD, ZWG or ZAR", fields={key: "Invalid"})
        row = db.session.get(PlatformSetting, f"billing_{key}") or PlatformSetting(key=f"billing_{key}")
        row.value = str(value)
        db.session.add(row)
    return settings()


def price_for(school, cfg=None):
    cfg = cfg or settings()
    rate = school.billing_rate_cents if school.billing_rate_cents is not None else cfg["default_rate_cents"]
    minimum = school.billing_minimum_cents if school.billing_minimum_cents is not None else cfg["default_minimum_cents"]
    return rate, minimum


def count_learners(school):
    from ..tenancy import use_school
    with use_school(school):
        return Student.query.filter_by(status="active").count()


def create_invoice(school, period, due_on, learners=None, amount_cents=None, notes=None, cfg=None):
    cfg = cfg or settings()
    period = (period or "").strip()[:40]
    if not period:
        raise ApiError("Name the period, e.g. Term 1 2027", fields={"period": "Required"})
    if SubscriptionInvoice.query.filter_by(school_id=school.id, period=period).first():
        raise ApiError(f"{school.name} has already been billed for {period}", 409)
    rate, minimum = price_for(school, cfg)
    if learners is None:
        raise ApiError("Count the school's learners before creating the invoice")  # see bill_period
    if amount_cents is None:
        amount_cents = max(learners * rate, minimum)
    inv = SubscriptionInvoice(school_id=school.id, period=period, due_on=due_on, learners=learners, rate_cents=rate,
                              amount_cents=amount_cents, currency=cfg["currency"], notes=(notes or None))
    db.session.add(inv)
    db.session.flush()
    return inv


def bill_period(period, due_on, school_ids=None):
    """Invoice every active, billable school (or the chosen ones) for a period."""
    cfg = settings()
    q = School.query.filter_by(status="active")
    if school_ids:
        q = q.filter(School.id.in_(school_ids))
    ids = [s.id for s in q.order_by(School.name)]
    # Count learners first: opening a school's database resets the session, which would
    # discard invoices not yet committed.
    learners = {}
    for sid in ids:
        school = db.session.get(School, sid)
        if not school.billing_free:
            try:
                learners[sid] = count_learners(school)
            except Exception:  # an unreachable school database must not stop the others
                learners[sid] = None
    created, skipped = [], []
    for sid in ids:
        school = db.session.get(School, sid)
        if school.billing_free:
            skipped.append({"school": school.name, "reason": "free (not billed)"})
            continue
        if SubscriptionInvoice.query.filter_by(school_id=school.id, period=period.strip()).first():
            skipped.append({"school": school.name, "reason": "already billed"})
            continue
        if learners.get(sid) is None:
            skipped.append({"school": school.name, "reason": "couldn't count learners (database unreachable)"})
            continue
        try:
            inv = create_invoice(school, period, due_on, learners=learners[sid], cfg=cfg)
            created.append(inv)
        except ApiError as err:
            skipped.append({"school": school.name, "reason": err.message})
    return created, skipped


def record_payment(inv, amount_cents, method, reference=None, paid_on=None, status="confirmed", reported_by=None, note=None):
    if inv.void:
        raise ApiError("This invoice is void", 409)
    if method not in ("ecocash", "cash", "bank"):
        raise ApiError("Payment method must be EcoCash, cash or bank", fields={"method": "Invalid"})
    if method == "ecocash" and not (reference or "").strip():
        raise ApiError("Enter the EcoCash transaction ID", fields={"reference": "Required"})
    if paid_on and paid_on > date.today():
        raise ApiError("The payment date can't be in the future", fields={"paid_on": "Future date"})
    reference = (reference or "").strip().upper()[:60] or None
    if reference and SubscriptionPayment.query.filter(SubscriptionPayment.reference == reference,
                                                      SubscriptionPayment.method == method,
                                                      SubscriptionPayment.status != "rejected").first():
        raise ApiError(f"Payment reference {reference} has already been recorded", 409, fields={"reference": "Duplicate"})
    p = SubscriptionPayment(invoice=inv, amount_cents=amount_cents, method=method, reference=reference,
                            paid_on=paid_on or date.today(), status=status, reported_by=reported_by, note=note)
    db.session.add(p)
    db.session.flush()
    return p


def payment_dict(p):
    return {"id": p.id, "invoice_id": p.invoice_id, "amount": money(p.amount_cents), "method": p.method,
            "reference": p.reference, "paid_on": p.paid_on.isoformat(), "status": p.status,
            "reported_by": p.reported_by, "note": p.note}


def invoice_dict(inv, with_payments=True):
    d = {"id": inv.id, "number": inv.number, "school_id": inv.school_id, "school": inv.school.name,
         "school_slug": inv.school.slug, "period": inv.period, "issued_on": inv.issued_on.isoformat(),
         "due_on": inv.due_on.isoformat(), "learners": inv.learners, "rate": money(inv.rate_cents),
         "amount": money(inv.amount_cents), "paid": money(inv.paid_cents),
         "balance": money(max(0, inv.amount_cents - inv.paid_cents)), "currency": inv.currency,
         "status": inv.status, "notes": inv.notes, "void_reason": inv.void_reason,
         "pending": sum(1 for p in inv.payments if p.status == "pending")}
    if with_payments:
        d["payments"] = [payment_dict(p) for p in inv.payments]
    return d


def pay_instructions(cfg=None):
    cfg = cfg or settings()
    return {"ecocash_number": cfg["ecocash_number"], "ecocash_name": cfg["ecocash_name"],
            "cash_instructions": cfg["cash_instructions"], "support_phone": cfg["support_phone"]}


def reminder_text(inv, cfg=None):
    cfg = cfg or settings()
    bal = money(max(0, inv.amount_cents - inv.paid_cents))
    lines = [f"Hello {inv.school.contact_name or inv.school.name},",
             f"This is a reminder that the school system subscription for {inv.period} ({inv.number}) "
             f"of {inv.currency} {bal:,.2f} is {'overdue since' if inv.status == 'overdue' else 'due on'} "
             f"{inv.due_on.strftime('%d %b %Y')}."]
    if cfg["ecocash_number"]:
        lines.append(f"Pay by EcoCash to {cfg['ecocash_number']}" + (f" ({cfg['ecocash_name']})" if cfg["ecocash_name"] else "")
                     + f", reference {inv.number}.")
    if cfg["cash_instructions"]:
        lines.append(f"Cash: {cfg['cash_instructions']}")
    lines.append("After paying by EcoCash, enter the transaction ID under Settings → Subscription so we can confirm it. Thank you.")
    return "\n".join(lines)


def whatsapp_link(inv, cfg=None):
    digits = "".join(ch for ch in (inv.school.contact_phone or "") if ch.isdigit())
    if digits.startswith("0"):
        digits = "263" + digits[1:]  # local Zimbabwe number
    text = quote(reminder_text(inv, cfg))
    return f"https://wa.me/{digits}?text={text}" if digits else f"https://wa.me/?text={text}"


def school_notice(school):
    """What a school's administrators should see now: open invoices due soon or overdue."""
    cfg = settings()
    today = date.today()
    open_invoices = [inv for inv in SubscriptionInvoice.query.filter_by(school_id=school.id, void=False)
                     .order_by(SubscriptionInvoice.due_on) if inv.status != "paid"]
    show = [inv for inv in open_invoices if (inv.due_on - today).days <= cfg["notice_days"]]
    return {"enabled": True, "invoices": [invoice_dict(i) for i in open_invoices],
            "notice": [invoice_dict(i, with_payments=False) for i in show],
            "pay": pay_instructions(cfg),
            "history": [invoice_dict(i) for i in SubscriptionInvoice.query.filter_by(school_id=school.id)
                        .order_by(SubscriptionInvoice.issued_on.desc(), SubscriptionInvoice.id.desc()).limit(20)]}
