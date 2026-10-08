"""Billing rules.

* Currencies are kept apart, never converted: fee items, invoices and payments each have a
  currency, a payment only clears invoices in its own currency, and credit and balances are
  per currency. A student can owe USD and ZWG at the same time.
* One invoice per student per term per currency, built from that term's fee items.
* Scholarships discount only `discountable` fee items, capped at 100%.
* Payments are never edited or deleted - only voided with a reason.
* Every payment is allocated to the oldest outstanding invoice first (by due
  date). Any surplus stays on the payment as credit and is applied
  automatically when the next invoice is raised.
* Every invoice, charge, payment and void posts to the general ledger
  (see services/ledger.py) in the same transaction.
"""
from datetime import date

from .. import db
from ..models import (FeeItem, Invoice, InvoiceLine, Payment, PaymentAllocation,
                      SchoolClass, Student)
from ..utils import ApiError, audit, next_number, money
from . import currency as fx
from . import ledger


def outstanding_invoices(student, currency=None):
    invoices = [i for i in student.invoices if not i.void and i.balance_cents > 0
                and (currency is None or fx.of(i) == currency)]
    return sorted(invoices, key=lambda i: (i.due_date, i.id))


def credit_cents(student, currency=None):
    currency = currency or fx.base()
    return sum(p.unallocated_cents for p in student.payments if not p.void and fx.of(p) == currency)


def _summary(student, cur):
    invoices = [i for i in student.invoices if not i.void and fx.of(i) == cur]
    billed = sum(i.total_cents for i in invoices)
    paid = sum(p.amount_cents for p in student.payments if not p.void and fx.of(p) == cur)
    outstanding = sum(i.balance_cents for i in invoices)
    overdue = sum(i.balance_cents for i in invoices if i.balance_cents > 0 and i.due_date < date.today())
    return {
        "currency": cur,
        "billed": money(billed),
        "paid": money(paid),
        "outstanding": money(outstanding),
        "overdue": money(overdue),
        "credit": money(credit_cents(student, cur)),
        # Positive = student owes the school; negative = school holds credit.
        "balance": money(billed - paid),
    }


def account_summary(student):
    """The base-currency summary (top-level keys, as before) plus one per currency in use."""
    used = {fx.of(i) for i in student.invoices} | {fx.of(p) for p in student.payments}
    currencies = [c for c in fx.CURRENCIES if c in used or c == fx.base()]
    by = {c: _summary(student, c) for c in currencies}
    return {**by[fx.base()], "by_currency": [by[c] for c in currencies]}


def apply_credit(student):
    """Allocate unallocated payments to outstanding invoices in the same currency, oldest first."""
    payments = sorted((p for p in student.payments if not p.void and p.unallocated_cents > 0),
                      key=lambda p: (p.paid_on, p.id))
    for payment in payments:
        for invoice in outstanding_invoices(student, fx.of(payment)):
            available = payment.unallocated_cents
            if available <= 0:
                break
            portion = min(available, invoice.balance_cents)
            alloc = PaymentAllocation(payment=payment, invoice=invoice, amount_cents=portion)
            db.session.add(alloc)
            db.session.flush()


def _remove_allocation(alloc):
    # Detach from both in-memory collections so balances recompute immediately;
    # the payment side's delete-orphan cascade removes the row on flush.
    with db.session.no_autoflush:
        payment, invoice = alloc.payment, alloc.invoice
        if alloc in invoice.allocations:
            invoice.allocations.remove(alloc)
        if alloc in payment.allocations:
            payment.allocations.remove(alloc)


def fee_items_for(term_id, class_id, currency=None):
    q = (FeeItem.query.filter(FeeItem.term_id == term_id)
         .filter((FeeItem.class_id.is_(None)) | (FeeItem.class_id == class_id)))
    items = q.order_by(FeeItem.id).all()
    return [i for i in items if currency is None or fx.of(i) == currency]


def build_invoice(student, term, issue_date=None, currency=None):
    currency = currency or fx.base()
    items = fee_items_for(term.id, student.class_id, currency)
    if not items:
        return None
    issue_date = issue_date or date.today()
    ledger.check_open(issue_date)
    invoice = Invoice(
        invoice_no=next_number("invoice", "INV-"), currency=currency,
        student=student, term=term, issue_date=issue_date,
        due_date=max(min(i.due_date for i in items), issue_date),
    )
    db.session.add(invoice)
    for item in items:
        account = item.account or ledger.fee_account_for(item.name)
        invoice.lines.append(InvoiceLine(fee_item_id=item.id, description=item.name,
                                         amount_cents=item.amount_cents, account_id=account.id))
    pct = min(sum(s.percent for s in student.scholarships if s.active), 100.0)
    discountable = sum(i.amount_cents for i in items if i.discountable)
    if pct > 0 and discountable > 0:
        names = ", ".join(s.name for s in student.scholarships if s.active)
        invoice.lines.append(InvoiceLine(description=f"Scholarship ({names}) -{pct:g}%",
                                         amount_cents=-round(discountable * pct / 100),
                                         account_id=ledger.acct(ledger.DISCOUNTS).id))
    db.session.add(invoice)
    db.session.flush()
    ledger.post_invoice(invoice)
    return invoice


def generate_term_invoices(term, class_id=None, issue_date=None):
    """Bill every active student (optionally one class). Safe to run repeatedly."""
    query = Student.query.filter_by(status="active").filter(Student.class_id.isnot(None))
    if class_id:
        query = query.filter_by(class_id=class_id)
    created, skipped = [], []
    for student in query.order_by(Student.admission_no):
        # One invoice per currency that has fee items for the student's class.
        currencies = sorted({fx.of(i) for i in fee_items_for(term.id, student.class_id)})
        if not currencies:
            skipped.append({"student": student.name, "reason": "no fee items for class"})
            continue
        for cur in currencies:
            existing = Invoice.query.filter_by(student_id=student.id, term_id=term.id, currency=cur).first()
            if existing:
                skipped.append({"student": student.name, "reason": f"already invoiced ({existing.invoice_no}, {cur})"})
                continue
            invoice = build_invoice(student, term, issue_date, cur)
            created.append(invoice.invoice_no)
        apply_credit(student)
    if created:
        audit("generate", "invoice", None, f"{len(created)} invoices for {term.label}")
    return created, skipped


def add_charge(invoice, description, amount_cents):
    if invoice.void:
        raise ApiError("Cannot add charges to a void invoice")
    line = InvoiceLine(description=description, amount_cents=amount_cents, account_id=ledger.acct(ledger.SUNDRY).id)
    invoice.lines.append(line)
    db.session.flush()
    ledger.post_charge(invoice, line, date.today())
    apply_credit(invoice.student)
    audit("charge", "invoice", invoice.id, f"{description}: {fx.of(invoice)} {money(amount_cents)}")


def void_invoice(invoice, reason):
    if invoice.void:
        raise ApiError("Invoice is already void")
    # Money already allocated returns to the payments as credit.
    for alloc in list(invoice.allocations):
        _remove_allocation(alloc)
    invoice.void = True
    invoice.void_reason = reason
    invoice.voided_at = date.today()
    db.session.flush()
    # Reverse on the void date so closed periods are never rewritten.
    ledger.reverse_source(("invoice", "charge"), invoice.id, invoice.voided_at, reason)
    apply_credit(invoice.student)
    audit("void", "invoice", invoice.id, reason)


def record_payment(student, amount_cents, method, reference, paid_on, user, currency=None):
    currency = currency or fx.base()
    if student.status in ("transferred", "withdrawn") and not outstanding_invoices(student, currency):
        raise ApiError("Student has left the school and has no outstanding balance")
    if paid_on > date.today():
        raise ApiError("Payment date cannot be in the future", fields={"paid_on": "Future date"})
    if reference:
        dup = Payment.query.filter_by(reference=reference, method=method, void=False).first()
        if dup and method != "cash":
            raise ApiError(f"Reference {reference} was already used on receipt {dup.receipt_no}")
    payment = Payment(receipt_no=next_number("receipt", "RCT-"), student=student, currency=currency,
                      amount_cents=amount_cents, method=method, reference=reference,
                      paid_on=paid_on, received_by=user.id)
    db.session.add(payment)
    db.session.flush()
    ledger.post_payment(payment)
    apply_credit(student)
    audit("create", "payment", payment.id, f"{payment.receipt_no} {currency} {money(amount_cents)} for {student.admission_no}")
    return payment


def void_payment(payment, reason):
    if payment.void:
        raise ApiError("Payment is already void")
    for alloc in list(payment.allocations):
        _remove_allocation(alloc)
    payment.void = True
    payment.void_reason = reason
    payment.voided_at = date.today()
    db.session.flush()
    ledger.reverse_source(("payment",), payment.id, payment.voided_at, reason)
    # Other credit the student holds may now cover invoices this payment did.
    apply_credit(payment.student)
    audit("void", "payment", payment.id, reason)


def invoice_dict(inv, detail=False):
    d = {
        "id": inv.id, "invoice_no": inv.invoice_no,
        "student_id": inv.student_id, "student": inv.student.name,
        "admission_no": inv.student.admission_no,
        "class": inv.student.school_class.name if inv.student.school_class else None,
        "term_id": inv.term_id, "term": inv.term.label, "currency": fx.of(inv),
        "issue_date": inv.issue_date.isoformat(), "due_date": inv.due_date.isoformat(),
        "total": money(inv.total_cents), "paid": money(inv.paid_cents),
        "balance": money(inv.balance_cents), "status": inv.status,
        "void_reason": inv.void_reason,
    }
    if detail:
        d["lines"] = [{"description": l.description, "amount": money(l.amount_cents)} for l in inv.lines]
        d["payments"] = [{"receipt_no": a.payment.receipt_no, "date": a.payment.paid_on.isoformat(),
                          "amount": money(a.amount_cents), "method": a.payment.method}
                         for a in inv.allocations if not a.payment.void]
        d["guardian"] = inv.student.guardian.name if inv.student.guardian else None
    return d


def payment_dict(p, detail=False):
    d = {
        "id": p.id, "receipt_no": p.receipt_no, "student_id": p.student_id,
        "student": p.student.name, "admission_no": p.student.admission_no,
        "amount": money(p.amount_cents), "currency": fx.of(p), "method": p.method, "reference": p.reference,
        "paid_on": p.paid_on.isoformat(), "void": p.void, "void_reason": p.void_reason,
        "received_by": p.receiver.full_name if p.receiver else None,
        "unallocated": money(p.unallocated_cents),
    }
    if detail:
        d["allocations"] = [{"invoice_no": a.invoice.invoice_no, "term": a.invoice.term.label,
                             "amount": money(a.amount_cents)} for a in p.allocations]
        d["class"] = p.student.school_class.name if p.student.school_class else None
        d["account"] = account_summary(p.student)
    return d


def class_fee_summary(term, currency=None):
    """Per class: billed, collected, outstanding, collection rate for a term, in one currency."""
    currency = currency or fx.base()
    rows = []
    for cls in SchoolClass.query.order_by(SchoolClass.level, SchoolClass.stream):
        invoices = (Invoice.query.join(Student).filter(Invoice.term_id == term.id, Invoice.currency == currency,
                    Invoice.void.is_(False), Student.class_id == cls.id).all())
        billed = sum(i.total_cents for i in invoices)
        paid = sum(i.paid_cents for i in invoices)
        rows.append({
            "class": cls.name, "invoices": len(invoices), "billed": money(billed),
            "collected": money(paid), "outstanding": money(billed - paid),
            "rate": round(paid / billed * 100, 1) if billed else 0,
        })
    return rows
