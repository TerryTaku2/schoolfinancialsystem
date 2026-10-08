"""School library: catalogue, copies, lending to students and staff, overdue fines.

- A *book* is a title; each physical *copy* has an accession number (printed on a label and
  used as its barcode), so the desk issues and returns books by scanning or typing that number.
- Students and staff borrow up to a set number of books for a set number of days. Anyone with
  an overdue book can't borrow more until it comes back. Textbooks issued to a whole class for
  the term don't count towards the limit.
- Fines are optional (a rate per day late, in the school's currency). A lost book is charged at
  the copy's replacement cost. A student's fine can be added to their fees invoice for the
  current term (it then shows on their statement and is paid like any fee, booked to Sundry
  Income); otherwise it is marked paid or waived (waiving needs library approval).
"""
from datetime import date, timedelta

from flask import has_request_context
from flask_login import current_user
from sqlalchemy import func

from .. import db
from ..models import Book, BookCopy, Invoice, Loan, Setting, Staff, Student
from ..utils import ApiError, audit, money, next_number
from . import currency as fx

SETTINGS = {  # key: (default, label)
    "student_days": (14, "Loan period for students (days)"),
    "staff_days": (30, "Loan period for staff (days)"),
    "student_max": (3, "Books a student may hold at once"),
    "staff_max": (10, "Books a member of staff may hold at once"),
    "max_renewals": (2, "Times a loan can be renewed"),
    "fine_per_day": (0, "Fine per day late, in cents of the school currency (0 = no fines)"),
}


def settings():
    out = {}
    for key, (default, _) in SETTINGS.items():
        s = db.session.get(Setting, f"library_{key}")
        try:
            out[key] = int(s.value) if s and s.value not in (None, "") else default
        except ValueError:
            out[key] = default
    return out


def save_settings(data):
    for key in SETTINGS:
        if key not in data:
            continue
        try:
            value = int(data[key])
        except (TypeError, ValueError):
            raise ApiError(f"{SETTINGS[key][1]} must be a whole number", fields={key: "Invalid"})
        if value < 0 or (key.endswith("_days") and not 1 <= value <= 365) or value > 1_000_000:
            raise ApiError(f"{SETTINGS[key][1]}: out of range", fields={key: "Out of range"})
        s = db.session.get(Setting, f"library_{key}") or Setting(key=f"library_{key}")
        s.value = str(value)
        db.session.add(s)
    return settings()


# --------------------------------------------------------------------------- #
# Borrowers
# --------------------------------------------------------------------------- #
def _uid():
    return current_user.id if has_request_context() and current_user.is_authenticated else None


def borrower_of(loan):
    return loan.student or loan.staff


def open_loans_q(student_id=None, staff_id=None):
    q = Loan.query.filter(Loan.returned_on.is_(None), Loan.lost.is_(False))
    if student_id:
        q = q.filter(Loan.student_id == student_id)
    if staff_id:
        q = q.filter(Loan.staff_id == staff_id)
    return q


def borrower_dict(student=None, staff=None):
    loans = open_loans_q(student.id if student else None, staff.id if staff else None).all()
    fines = Loan.query.filter(Loan.fine_status == "unpaid",
                              (Loan.student_id == student.id) if student else (Loan.staff_id == staff.id)).all()
    owed = {}
    for f in fines:
        owed[f.fine_currency] = owed.get(f.fine_currency, 0) + f.fine_cents
    if student:
        base = {"type": "student", "id": student.id, "name": student.name, "ref": student.admission_no,
                "detail": student.school_class.name if student.school_class else "No class",
                "active": student.status == "active"}
    else:
        base = {"type": "staff", "id": staff.id, "name": staff.name, "ref": staff.staff_no,
                "detail": staff.position, "active": staff.status != "left"}
    today = date.today()
    return {**base, "on_loan": len(loans), "overdue": sum(1 for l in loans if l.due_on < today),
            "fines": [{"currency": c, "amount": money(v)} for c, v in owed.items()]}


def _limit_and_days(student, staff):
    cfg = settings()
    return (cfg["student_max"], cfg["student_days"]) if student else (cfg["staff_max"], cfg["staff_days"])


# --------------------------------------------------------------------------- #
# Lending
# --------------------------------------------------------------------------- #
def new_accession_no():
    while True:
        code = next_number("library_copy", "LIB-", 5)
        if not BookCopy.query.filter_by(accession_no=code).first():
            return code


def add_copies(book, count, replacement_cents=0, currency=None, condition="New", acquired_on=None, numbers=None):
    numbers = [n.strip().upper() for n in (numbers or []) if n and n.strip()]
    if numbers:
        clash = BookCopy.query.filter(BookCopy.accession_no.in_(numbers)).first()
        if clash or len(set(numbers)) != len(numbers):
            raise ApiError(f"Accession number {clash.accession_no if clash else 'repeated'} is already in use",
                           fields={"accession_numbers": "Duplicate"})
    made = []
    for i in range(max(count, len(numbers))):
        copy = BookCopy(book=book, accession_no=numbers[i] if i < len(numbers) else new_accession_no(),
                        replacement_cents=replacement_cents, currency=currency or fx.base(), condition=condition,
                        acquired_on=acquired_on or date.today())
        db.session.add(copy)
        made.append(copy)
    db.session.flush()
    return made


def issue(copy, student=None, staff=None, due_on=None, on=None, count_towards_limit=True):
    on = on or date.today()
    if copy.status != "available":
        holder = open_loans_q().filter(Loan.copy_id == copy.id).first()
        who = f" (on loan to {borrower_of(holder).name}, due {holder.due_on.isoformat()})" if holder else ""
        raise ApiError(f"{copy.accession_no} is {copy.status.replace('_', ' ')}{who}", 409)
    if student is not None and student.status != "active":
        raise ApiError(f"{student.name} is not an active student", 409)
    if staff is not None and staff.status == "left":
        raise ApiError(f"{staff.name} has left the school", 409)
    limit, days = _limit_and_days(student, staff)
    current = open_loans_q(student.id if student else None, staff.id if staff else None).all()
    today = date.today()
    late = [l for l in current if l.due_on < today]
    if late:
        raise ApiError(f"{(student or staff).name} has {len(late)} overdue book{'s' if len(late) > 1 else ''} "
                       f"({', '.join(l.copy.book.title for l in late[:3])}). Return {'them' if len(late) > 1 else 'it'} first.", 409)
    counted = [l for l in current if l.copy.book.category != "Textbook"]
    if count_towards_limit and copy.book.category != "Textbook" and len(counted) >= limit:
        raise ApiError(f"{(student or staff).name} already has {len(counted)} books out (limit {limit})", 409)
    due = due_on or on + timedelta(days=days)
    if due < on:
        raise ApiError("The due date can't be before the issue date", fields={"due_on": "Invalid"})
    loan = Loan(copy=copy, student=student, staff=staff, issued_on=on, due_on=due, condition_out=copy.condition,
                issued_by=_uid())
    copy.status = "on_loan"
    db.session.add(loan)
    db.session.flush()
    audit("issue", "loan", loan.id, f"{copy.accession_no} {copy.book.title} to {(student or staff).name}, due {due}")
    return loan


def _open(loan):
    if not loan.is_open:
        raise ApiError("This loan is already closed", 409)


def return_loan(loan, on=None, condition=None):
    _open(loan)
    on = on or date.today()
    if on < loan.issued_on:
        raise ApiError("A book can't be returned before it was issued", fields={"returned_on": "Invalid"})
    loan.returned_on = on
    loan.received_by = _uid()
    if condition:
        loan.condition_in = loan.copy.condition = condition
    loan.copy.status = "available"
    days_late = (on - loan.due_on).days
    rate = settings()["fine_per_day"]
    if days_late > 0 and rate:
        loan.fine_cents, loan.fine_currency, loan.fine_status = days_late * rate, fx.base(), "unpaid"
        loan.fine_note = f"{days_late} day{'s' if days_late > 1 else ''} late"
    db.session.flush()
    audit("return", "loan", loan.id, f"{loan.copy.accession_no} returned" + (f", {loan.fine_note}" if loan.fine_note else ""))
    return loan


def renew(loan, on=None):
    _open(loan)
    on = on or date.today()
    if loan.due_on < on:
        raise ApiError("Overdue books can't be renewed; return the book first", 409)
    cfg = settings()
    if loan.renewals >= cfg["max_renewals"]:
        raise ApiError(f"This loan has already been renewed {loan.renewals} times (limit {cfg['max_renewals']})", 409)
    _, days = _limit_and_days(loan.student, loan.staff)
    loan.due_on = max(on, loan.due_on) + timedelta(days=days)
    loan.renewals += 1
    db.session.flush()
    audit("renew", "loan", loan.id, f"due {loan.due_on}")
    return loan


def mark_lost(loan):
    _open(loan)
    loan.lost = True
    copy = loan.copy
    copy.status = "lost"
    if copy.replacement_cents:
        loan.fine_cents, loan.fine_currency, loan.fine_status = copy.replacement_cents, fx.of(copy), "unpaid"
        loan.fine_note = "Lost book: replacement cost"
    db.session.flush()
    audit("lost", "loan", loan.id, f"{copy.accession_no} {copy.book.title} lost by {borrower_of(loan).name}")
    return loan


def settle_fine(loan, action, note=None):
    if loan.fine_status != "unpaid":
        raise ApiError("There is no unpaid fine on this loan", 409)
    if action == "charge":
        from . import finance
        from ..utils import current_term
        if not loan.student:
            raise ApiError("Only a student's fine can be added to a fees invoice", 409)
        term = current_term()
        inv = term and Invoice.query.filter_by(student_id=loan.student_id, term_id=term.id,
                                               currency=loan.fine_currency, void=False).first()
        if inv is None:
            raise ApiError(f"{loan.student.name} has no {loan.fine_currency} invoice for the current term. "
                           "Generate this term's invoices first, or mark the fine as paid.", 409)
        what = "lost book" if loan.lost else "overdue book"
        finance.add_charge(inv, f"Library fine: {what} ({loan.copy.book.title[:60]})", loan.fine_cents)
        loan.fine_invoice_id, loan.fine_status = inv.id, "charged"
    elif action == "paid":
        loan.fine_status = "paid"
    elif action == "waive":
        from .permissions import has
        if not has("library.approve"):
            raise ApiError("Waiving a fine needs library approval permission", 403)
        if not note or len(note.strip()) < 3:
            raise ApiError("Give a reason for waiving the fine", fields={"note": "Required"})
        loan.fine_status = "waived"
    else:
        raise ApiError("Action must be charge, paid or waive", fields={"action": "Invalid"})
    if note:
        loan.fine_note = f"{loan.fine_note or ''} · {note.strip()}"[:200].lstrip(" ·")
    db.session.flush()
    audit("fine_" + action, "loan", loan.id, f"{loan.fine_currency} {money(loan.fine_cents)}" + (f": {note}" if note else ""))
    return loan


# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #
def book_dict(b, detail=False):
    counts = {s: 0 for s in ("available", "on_loan", "lost", "withdrawn")}
    for c in b.copies:
        counts[c.status] += 1
    d = {"id": b.id, "title": b.title, "author": b.author, "isbn": b.isbn, "publisher": b.publisher, "year": b.year,
         "edition": b.edition, "category": b.category, "subject_id": b.subject_id,
         "subject": b.subject.name if b.subject else None, "level": b.level, "shelf": b.shelf, "notes": b.notes,
         "copies": len(b.copies) - counts["withdrawn"], **counts}
    if detail:
        d["copy_list"] = [copy_dict(c) for c in b.copies]
        loans = (Loan.query.join(BookCopy).filter(BookCopy.book_id == b.id).order_by(Loan.issued_on.desc(), Loan.id.desc())
                 .limit(50).all())
        d["history"] = [loan_dict(l) for l in loans]
    return d


def copy_dict(c, with_loan=True):
    d = {"id": c.id, "book_id": c.book_id, "accession_no": c.accession_no, "status": c.status, "condition": c.condition,
         "acquired_on": c.acquired_on.isoformat() if c.acquired_on else None, "currency": fx.of(c),
         "replacement": money(c.replacement_cents), "notes": c.notes, "title": c.book.title, "author": c.book.author,
         "category": c.book.category}
    if with_loan:
        cur = open_loans_q().filter(Loan.copy_id == c.id).first()
        d["loan"] = loan_dict(cur) if cur else None
    return d


def loan_dict(l):
    who = borrower_of(l)
    today = date.today()
    status = ("lost" if l.lost else "returned" if l.returned_on else "overdue" if l.due_on < today else "on_loan")
    return {"id": l.id, "copy_id": l.copy_id, "accession_no": l.copy.accession_no, "book_id": l.copy.book_id,
            "title": l.copy.book.title, "author": l.copy.book.author, "category": l.copy.book.category,
            "borrower_type": "student" if l.student_id else "staff", "borrower_id": who.id, "borrower": who.name,
            "borrower_ref": who.admission_no if l.student_id else who.staff_no,
            "class": l.student.school_class.name if l.student and l.student.school_class else None,
            "issued_on": l.issued_on.isoformat(), "due_on": l.due_on.isoformat(),
            "returned_on": l.returned_on.isoformat() if l.returned_on else None, "status": status,
            "days_overdue": max(0, ((l.returned_on or today) - l.due_on).days) if not l.lost else 0,
            "renewals": l.renewals, "condition_out": l.condition_out, "condition_in": l.condition_in,
            "issued_by": l.issuer.full_name if l.issuer else None,
            "fine": money(l.fine_cents), "fine_currency": l.fine_currency, "fine_status": l.fine_status,
            "fine_note": l.fine_note, "fine_invoice": l.fine_invoice.invoice_no if l.fine_invoice else None}


def summary():
    today = date.today()
    open_q = open_loans_q()
    owed = (db.session.query(Loan.fine_currency, func.sum(Loan.fine_cents)).filter(Loan.fine_status == "unpaid")
            .group_by(Loan.fine_currency).all())
    year_ago = today - timedelta(days=365)
    popular = (db.session.query(Book.id, Book.title, Book.author, func.count(Loan.id).label("n"))
               .join(BookCopy, BookCopy.book_id == Book.id).join(Loan, Loan.copy_id == BookCopy.id)
               .filter(Loan.issued_on >= year_ago).group_by(Book.id, Book.title, Book.author)
               .order_by(func.count(Loan.id).desc(), Book.title).limit(5).all())
    return {
        "titles": Book.query.count(),
        "copies": BookCopy.query.filter(BookCopy.status != "withdrawn").count(),
        "available": BookCopy.query.filter_by(status="available").count(),
        "on_loan": open_q.count(),
        "overdue": open_q.filter(Loan.due_on < today).count(),
        "due_today": open_loans_q().filter(Loan.due_on == today).count(),
        "lost": BookCopy.query.filter_by(status="lost").count(),
        "fines_owed": [{"currency": c, "amount": money(v or 0)} for c, v in owed if v],
        "popular": [{"id": i, "title": t, "author": a, "loans": n} for i, t, a, n in popular],
    }


def search_borrowers(q, limit=10):
    from sqlalchemy import or_
    like = f"%{q.strip()}%"
    students = (Student.query.filter(Student.status == "active")
                .filter(or_(Student.first_name.ilike(like), Student.last_name.ilike(like),
                            Student.admission_no.ilike(like),
                            (Student.first_name + " " + Student.last_name).ilike(like)))
                .order_by(Student.last_name, Student.first_name).limit(limit).all())
    staff = (Staff.query.filter(Staff.status != "left")
             .filter(or_(Staff.first_name.ilike(like), Staff.last_name.ilike(like), Staff.staff_no.ilike(like),
                         (Staff.first_name + " " + Staff.last_name).ilike(like)))
             .order_by(Staff.last_name, Staff.first_name).limit(limit).all())
    return [borrower_dict(student=s) for s in students] + [borrower_dict(staff=s) for s in staff]
