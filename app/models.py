"""Database models.

Money is stored as integer cents everywhere to avoid floating point drift;
the API layer converts to and from decimal strings.
"""
from datetime import datetime, date

from flask_login import UserMixin
from sqlalchemy import UniqueConstraint, CheckConstraint
from sqlalchemy.orm import synonym
from werkzeug.security import generate_password_hash, check_password_hash

from . import db

ROLES = ("admin", "bursar", "teacher", "parent")


def utcnow():
    return datetime.utcnow()


def _default_currency():
    """New money records default to the school's own currency (USD or ZWG)."""
    from .services.currency import base
    return base()


class TimestampMixin:
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)


# --------------------------------------------------------------------------- #
# Users & people
# --------------------------------------------------------------------------- #
class User(UserMixin, TimestampMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False, index=True)
    full_name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120))
    role = db.Column(db.String(16), nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    active = db.Column(db.Boolean, default=True, nullable=False)
    last_login = db.Column(db.DateTime)
    # Optional custom role (e.g. "Accounts clerk"); `role` then holds its base (bursar/teacher).
    role_id = db.Column(db.Integer, db.ForeignKey("role.id"))
    # Per-person adjustments to the role's permissions (comma-separated codes).
    extra_permissions = db.Column(db.Text)
    removed_permissions = db.Column(db.Text)
    # Set when someone else chose this password (a reset): the user must pick their own at next sign-in.
    must_change_password = db.Column(db.Boolean)
    # When the user last used "Forgot password?"; shown to administrators until the password is reset.
    reset_requested_at = db.Column(db.DateTime)

    custom_role = db.relationship("Role")

    __table_args__ = (CheckConstraint(f"role IN {ROLES}", name="ck_user_role"),)

    @property
    def is_active(self):
        return self.active

    def set_password(self, raw):
        self.password_hash = generate_password_hash(raw)

    def check_password(self, raw):
        return check_password_hash(self.password_hash, raw)

    def get_id(self):
        # In multi-school mode the login is bound to its school ("slug:id"), so a session or
        # remember-me cookie from one school can never load a user in another school.
        from .tenancy import current_school
        school = current_school()
        return f"{school.slug}:{self.id}" if school else str(self.id)

    def to_dict(self):
        from .services import permissions as perms
        role = perms.role_of(self) if self.role not in ("admin", "parent") else None
        return {
            "id": self.id, "username": self.username, "full_name": self.full_name,
            "email": self.email, "role": self.role, "active": self.active,
            "role_id": role.id if role else None,
            "role_name": role.name if role else perms.ROLE_LABEL.get(self.role, self.role),
            "extra_permissions": perms.split(self.extra_permissions),
            "removed_permissions": perms.split(self.removed_permissions),
            "permissions": sorted(perms.permissions_for(self)),
            "last_login": self.last_login.isoformat() if self.last_login else None,
            "must_change_password": bool(self.must_change_password),
            "reset_requested_at": self.reset_requested_at.isoformat() if self.reset_requested_at else None,
            "staff_id": self.staff.id if self.staff else None,
            "guardian_id": self.guardian.id if self.guardian else None,
        }


class Role(db.Model):
    """A set of permissions given to users. Bursar and Teacher are built in and editable;
    schools add their own (e.g. "Accounts clerk"). See services/permissions.py."""
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(40), unique=True, nullable=False)
    name = db.Column(db.String(60), unique=True, nullable=False)
    description = db.Column(db.String(200))
    base = db.Column(db.String(16), nullable=False, default="bursar")  # bursar | teacher
    permissions = db.Column(db.Text, nullable=False, default="")
    is_system = db.Column(db.Boolean, default=False, nullable=False)


class Staff(TimestampMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), unique=True)
    staff_no = db.Column(db.String(20), unique=True, nullable=False)
    first_name = db.Column(db.String(60), nullable=False)
    last_name = db.Column(db.String(60), nullable=False)
    gender = db.Column(db.String(10))
    phone = db.Column(db.String(30))
    email = db.Column(db.String(120))
    position = db.Column(db.String(60), nullable=False, default="Teacher")
    department = db.Column(db.String(60))
    hire_date = db.Column(db.Date, default=date.today)
    salary_cents = db.Column(db.Integer, default=0, nullable=False)  # basic monthly salary
    salary_currency = db.Column(db.String(3), default=_default_currency)  # currency the basic salary is paid in
    status = db.Column(db.String(16), default="active", nullable=False)  # active | on_leave | left
    # Payroll / statutory details (ZIMRA, NSSA).
    national_id = db.Column(db.String(30))
    tax_number = db.Column(db.String(30))      # ZIMRA BP number / TIN
    nssa_number = db.Column(db.String(30))
    dob = db.Column(db.Date)                   # elderly persons' tax credit from age 55
    disabled = db.Column(db.Boolean, default=False)  # blind / disabled persons' tax credit
    bank_name = db.Column(db.String(60))
    bank_account = db.Column(db.String(40))

    user = db.relationship("User", backref=db.backref("staff", uselist=False))

    @property
    def name(self):
        return f"{self.first_name} {self.last_name}"


class Guardian(TimestampMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), unique=True)
    name = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(30), nullable=False)
    email = db.Column(db.String(120))
    relationship = db.Column(db.String(30), default="Parent")
    address = db.Column(db.String(200))

    user = db.relationship("User", backref=db.backref("guardian", uselist=False))
    students = db.relationship("Student", back_populates="guardian")


class Student(TimestampMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    admission_no = db.Column(db.String(20), unique=True, nullable=False, index=True)
    first_name = db.Column(db.String(60), nullable=False)
    last_name = db.Column(db.String(60), nullable=False)
    gender = db.Column(db.String(10), nullable=False)
    dob = db.Column(db.Date, nullable=False)
    class_id = db.Column(db.Integer, db.ForeignKey("school_class.id"))
    guardian_id = db.Column(db.Integer, db.ForeignKey("guardian.id"))
    admission_date = db.Column(db.Date, default=date.today, nullable=False)
    # active | suspended | graduated | transferred | withdrawn
    status = db.Column(db.String(16), default="active", nullable=False)
    address = db.Column(db.String(200))
    medical_notes = db.Column(db.String(300))

    school_class = db.relationship("SchoolClass", back_populates="students")
    guardian = db.relationship("Guardian", back_populates="students")

    @property
    def name(self):
        return f"{self.first_name} {self.last_name}"


# --------------------------------------------------------------------------- #
# Academic structure
# --------------------------------------------------------------------------- #
class AcademicYear(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(20), unique=True, nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    is_current = db.Column(db.Boolean, default=False, nullable=False)

    terms = db.relationship("Term", back_populates="year", order_by="Term.start_date",
                            cascade="all, delete-orphan")


class Term(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    year_id = db.Column(db.Integer, db.ForeignKey("academic_year.id"), nullable=False)
    name = db.Column(db.String(30), nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    is_current = db.Column(db.Boolean, default=False, nullable=False)

    year = db.relationship("AcademicYear", back_populates="terms")

    __table_args__ = (UniqueConstraint("year_id", "name"),)

    @property
    def label(self):
        return f"{self.name} {self.year.name}"


class SchoolClass(db.Model):
    __tablename__ = "school_class"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(40), unique=True, nullable=False)
    # Position in the Zimbabwe level catalogue (see services/structure.py), for ordering.
    level = db.Column(db.Integer, nullable=False)
    grade_code = db.Column(db.String(6))  # ECD_A, ECD_B, G1-G7, F1-F6
    stream = db.Column(db.String(10), nullable=False, default="A")
    capacity = db.Column(db.Integer, nullable=False, default=40)
    room = db.Column(db.String(30))
    class_teacher_id = db.Column(db.Integer, db.ForeignKey("staff.id"))

    class_teacher = db.relationship("Staff")
    students = db.relationship("Student", back_populates="school_class")
    subjects = db.relationship("ClassSubject", back_populates="school_class",
                               cascade="all, delete-orphan")

    __table_args__ = (UniqueConstraint("level", "stream"),)

    def active_students(self):
        return [s for s in self.students if s.status == "active"]


class Subject(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(12), unique=True, nullable=False)
    name = db.Column(db.String(60), nullable=False)


class ClassSubject(db.Model):
    """Which subjects a class takes, and who teaches each."""
    id = db.Column(db.Integer, primary_key=True)
    class_id = db.Column(db.Integer, db.ForeignKey("school_class.id"), nullable=False)
    subject_id = db.Column(db.Integer, db.ForeignKey("subject.id"), nullable=False)
    teacher_id = db.Column(db.Integer, db.ForeignKey("staff.id"))

    school_class = db.relationship("SchoolClass", back_populates="subjects")
    subject = db.relationship("Subject")
    teacher = db.relationship("Staff")

    __table_args__ = (UniqueConstraint("class_id", "subject_id"),)


class TimetableSlot(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    class_subject_id = db.Column(db.Integer, db.ForeignKey("class_subject.id"), nullable=False)
    day = db.Column(db.Integer, nullable=False)  # 0 = Monday … 4 = Friday
    start_time = db.Column(db.String(5), nullable=False)  # "HH:MM"
    end_time = db.Column(db.String(5), nullable=False)
    room = db.Column(db.String(30))

    class_subject = db.relationship("ClassSubject",
                                    backref=db.backref("slots", cascade="all, delete-orphan"))

    __table_args__ = (CheckConstraint("day BETWEEN 0 AND 4", name="ck_slot_day"),)


# --------------------------------------------------------------------------- #
# Attendance & assessment
# --------------------------------------------------------------------------- #
ATTENDANCE_STATUSES = ("present", "absent", "late", "excused")


class Attendance(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("student.id"), nullable=False)
    class_id = db.Column(db.Integer, db.ForeignKey("school_class.id"), nullable=False)
    date = db.Column(db.Date, nullable=False, index=True)
    status = db.Column(db.String(10), nullable=False)
    remark = db.Column(db.String(120))
    recorded_by = db.Column(db.Integer, db.ForeignKey("user.id"))

    student = db.relationship("Student")

    __table_args__ = (
        UniqueConstraint("student_id", "date"),
        CheckConstraint(f"status IN {ATTENDANCE_STATUSES}", name="ck_att_status"),
    )


class Exam(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    term_id = db.Column(db.Integer, db.ForeignKey("term.id"), nullable=False)
    name = db.Column(db.String(60), nullable=False)
    weight = db.Column(db.Float, nullable=False)  # % contribution to the term result
    max_score = db.Column(db.Float, nullable=False, default=100)
    date = db.Column(db.Date)
    locked = db.Column(db.Boolean, default=False, nullable=False)

    term = db.relationship("Term")

    __table_args__ = (
        UniqueConstraint("term_id", "name"),
        CheckConstraint("weight > 0 AND weight <= 100", name="ck_exam_weight"),
        CheckConstraint("max_score > 0", name="ck_exam_max"),
    )


class Mark(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    exam_id = db.Column(db.Integer, db.ForeignKey("exam.id"), nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey("student.id"), nullable=False)
    subject_id = db.Column(db.Integer, db.ForeignKey("subject.id"), nullable=False)
    score = db.Column(db.Float, nullable=False)
    entered_by = db.Column(db.Integer, db.ForeignKey("user.id"))
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    exam = db.relationship("Exam")
    student = db.relationship("Student")
    subject = db.relationship("Subject")

    __table_args__ = (UniqueConstraint("exam_id", "student_id", "subject_id"),)


class GradeBand(db.Model):
    """One band of a grading scale. Each section (primary, O Level, A Level) has its own scale."""
    id = db.Column(db.Integer, primary_key=True)
    section = db.Column(db.String(10), nullable=False, default="primary")
    letter = db.Column(db.String(3), nullable=False)
    min_score = db.Column(db.Float, nullable=False)
    points = db.Column(db.Float, nullable=False, default=0)
    remark = db.Column(db.String(30))

    __table_args__ = (UniqueConstraint("section", "letter", name="uq_grade_band_section_letter"),)


# --------------------------------------------------------------------------- #
# Finance
# --------------------------------------------------------------------------- #
class FeeItem(db.Model):
    """A charge for a term; class_id NULL means it applies to every class."""
    id = db.Column(db.Integer, primary_key=True)
    # Currency the amounts are recorded in (USD or ZWG); filled with the school currency on upgrade.
    currency = db.Column(db.String(3), default=_default_currency)
    term_id = db.Column(db.Integer, db.ForeignKey("term.id"), nullable=False)
    class_id = db.Column(db.Integer, db.ForeignKey("school_class.id"))
    name = db.Column(db.String(60), nullable=False)
    amount_cents = db.Column(db.Integer, nullable=False)
    due_date = db.Column(db.Date, nullable=False)
    # Whether scholarships reduce this item (tuition yes, exam fees usually no).
    discountable = db.Column(db.Boolean, default=True, nullable=False)
    # Income account credited when this fee is invoiced.
    account_id = db.Column(db.Integer, db.ForeignKey("account.id"))

    term = db.relationship("Term")
    school_class = db.relationship("SchoolClass")
    account = db.relationship("Account")

    __table_args__ = (CheckConstraint("amount_cents > 0", name="ck_fee_positive"),)


class Scholarship(TimestampMixin, db.Model):
    """Percentage discount applied to tuition-type fees when invoices are generated."""
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("student.id"), nullable=False)
    name = db.Column(db.String(60), nullable=False)
    percent = db.Column(db.Float, nullable=False)
    active = db.Column(db.Boolean, default=True, nullable=False)

    student = db.relationship("Student", backref="scholarships")

    __table_args__ = (CheckConstraint("percent > 0 AND percent <= 100", name="ck_sch_pct"),)


class Invoice(TimestampMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    invoice_no = db.Column(db.String(20), unique=True, nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey("student.id"), nullable=False)
    term_id = db.Column(db.Integer, db.ForeignKey("term.id"), nullable=False)
    issue_date = db.Column(db.Date, default=date.today, nullable=False)
    due_date = db.Column(db.Date, nullable=False)
    void = db.Column(db.Boolean, default=False, nullable=False)
    void_reason = db.Column(db.String(200))
    voided_at = db.Column(db.Date)

    student = db.relationship("Student", backref="invoices")
    term = db.relationship("Term")
    lines = db.relationship("InvoiceLine", back_populates="invoice", cascade="all, delete-orphan")
    allocations = db.relationship("PaymentAllocation", back_populates="invoice")

    currency = db.Column(db.String(3), default=_default_currency)

    # One invoice per student per term per currency keeps billing idempotent.
    __table_args__ = (UniqueConstraint("student_id", "term_id", "currency", name="uq_invoice_student_term_currency"),)

    @property
    def total_cents(self):
        return sum(line.amount_cents for line in self.lines)

    @property
    def paid_cents(self):
        return sum(a.amount_cents for a in self.allocations if not a.payment.void)

    @property
    def balance_cents(self):
        return 0 if self.void else self.total_cents - self.paid_cents

    @property
    def status(self):
        if self.void:
            return "void"
        if self.balance_cents <= 0:
            return "paid"
        if self.paid_cents > 0:
            return "partial" if self.due_date >= date.today() else "overdue"
        return "unpaid" if self.due_date >= date.today() else "overdue"


class InvoiceLine(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    invoice_id = db.Column(db.Integer, db.ForeignKey("invoice.id"), nullable=False)
    fee_item_id = db.Column(db.Integer, db.ForeignKey("fee_item.id"))
    description = db.Column(db.String(120), nullable=False)
    amount_cents = db.Column(db.Integer, nullable=False)  # negative for discounts
    account_id = db.Column(db.Integer, db.ForeignKey("account.id"))

    invoice = db.relationship("Invoice", back_populates="lines")


PAYMENT_METHODS = ("cash", "bank", "mobile", "card", "cheque")


class Payment(TimestampMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    receipt_no = db.Column(db.String(20), unique=True, nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey("student.id"), nullable=False)
    amount_cents = db.Column(db.Integer, nullable=False)
    currency = db.Column(db.String(3), default=_default_currency)
    method = db.Column(db.String(10), nullable=False)
    reference = db.Column(db.String(60))
    paid_on = db.Column(db.Date, nullable=False, default=date.today)
    received_by = db.Column(db.Integer, db.ForeignKey("user.id"))
    void = db.Column(db.Boolean, default=False, nullable=False)
    void_reason = db.Column(db.String(200))
    voided_at = db.Column(db.Date)

    student = db.relationship("Student", backref="payments")
    receiver = db.relationship("User")
    allocations = db.relationship("PaymentAllocation", back_populates="payment",
                                  cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint("amount_cents > 0", name="ck_payment_positive"),
        CheckConstraint(f"method IN {PAYMENT_METHODS}", name="ck_payment_method"),
    )

    @property
    def allocated_cents(self):
        return sum(a.amount_cents for a in self.allocations)

    @property
    def unallocated_cents(self):
        return 0 if self.void else self.amount_cents - self.allocated_cents


class PaymentAllocation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    payment_id = db.Column(db.Integer, db.ForeignKey("payment.id"), nullable=False)
    invoice_id = db.Column(db.Integer, db.ForeignKey("invoice.id"), nullable=False)
    amount_cents = db.Column(db.Integer, nullable=False)

    payment = db.relationship("Payment", back_populates="allocations")
    invoice = db.relationship("Invoice", back_populates="allocations")

    __table_args__ = (CheckConstraint("amount_cents > 0", name="ck_alloc_positive"),)


EXPENSE_STATUSES = ("pending", "approved", "rejected", "paid")


class Expense(TimestampMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    category = db.Column(db.String(40), nullable=False)
    description = db.Column(db.String(200), nullable=False)
    vendor = db.Column(db.String(100))
    amount_cents = db.Column(db.Integer, nullable=False)
    expense_date = db.Column(db.Date, nullable=False, default=date.today)
    status = db.Column(db.String(10), nullable=False, default="pending")
    requested_by = db.Column(db.Integer, db.ForeignKey("user.id"))
    approved_by = db.Column(db.Integer, db.ForeignKey("user.id"))
    decision_note = db.Column(db.String(200))
    approved_at = db.Column(db.Date)
    paid_at = db.Column(db.Date)
    paid_from_account_id = db.Column(db.Integer, db.ForeignKey("account.id"))
    currency = db.Column(db.String(3), default=_default_currency)

    requester = db.relationship("User", foreign_keys=[requested_by])
    approver = db.relationship("User", foreign_keys=[approved_by])

    __table_args__ = (
        CheckConstraint("amount_cents > 0", name="ck_expense_positive"),
        CheckConstraint(f"status IN {EXPENSE_STATUSES}", name="ck_expense_status"),
    )


# --------------------------------------------------------------------------- #
# Communication & audit
# --------------------------------------------------------------------------- #
class Announcement(TimestampMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(120), nullable=False)
    body = db.Column(db.Text, nullable=False)
    audience = db.Column(db.String(10), nullable=False, default="all")  # all | staff | parents
    pinned = db.Column(db.Boolean, default=False, nullable=False)
    created_by = db.Column(db.Integer, db.ForeignKey("user.id"))

    author = db.relationship("User")


class AuditLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    action = db.Column(db.String(40), nullable=False)
    entity = db.Column(db.String(40), nullable=False)
    entity_id = db.Column(db.Integer)
    details = db.Column(db.String(500))
    timestamp = db.Column(db.DateTime, default=utcnow, nullable=False, index=True)

    user = db.relationship("User")


class Counter(db.Model):
    """Sequential document numbers (admission, invoice, receipt) per prefix."""
    key = db.Column(db.String(20), primary_key=True)
    value = db.Column(db.Integer, nullable=False, default=0)


# --------------------------------------------------------------------------- #
# General ledger (double entry)
# --------------------------------------------------------------------------- #
ACCOUNT_TYPES = ("asset", "liability", "equity", "income", "expense")
CASH_FLOW_CLASSES = ("operating", "investing", "financing")


class Account(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(10), unique=True, nullable=False, index=True)
    name = db.Column(db.String(80), nullable=False)
    type = db.Column(db.String(10), nullable=False)
    # cash | receivable | payable | fixed_asset | contra_asset | contra_income | None
    subtype = db.Column(db.String(20))
    # Cash, bank and mobile money accounts hold one currency; other accounts hold both (NULL).
    currency = db.Column(db.String(3))
    # Where movements against this account appear in the cash flow statement.
    cash_flow = db.Column(db.String(10), nullable=False, default="operating")
    is_system = db.Column(db.Boolean, default=False, nullable=False)
    active = db.Column(db.Boolean, default=True, nullable=False)
    description = db.Column(db.String(200))

    __table_args__ = (
        CheckConstraint(f"type IN {ACCOUNT_TYPES}", name="ck_account_type"),
        CheckConstraint(f"cash_flow IN {CASH_FLOW_CLASSES}", name="ck_account_cf"),
    )

    @property
    def normal_debit(self):
        """Assets and expenses grow with debits; contra accounts flip their parent's side."""
        debit = self.type in ("asset", "expense")
        return not debit if self.subtype in ("contra_asset", "contra_income") else debit


class JournalEntry(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    entry_no = db.Column(db.String(20), unique=True, nullable=False)
    date = db.Column(db.Date, nullable=False, index=True)
    description = db.Column(db.String(200), nullable=False)
    reference = db.Column(db.String(60))
    # manual | invoice | charge | payment | expense_accrual | expense_payment | reversal
    source_type = db.Column(db.String(20), nullable=False, default="manual")
    source_id = db.Column(db.Integer)
    reversal_of_id = db.Column(db.Integer, db.ForeignKey("journal_entry.id"))
    # Every entry is in one currency; the books are kept per currency, never converted.
    currency = db.Column(db.String(3), index=True, default=_default_currency)
    created_by = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    lines = db.relationship("JournalLine", back_populates="entry", cascade="all, delete-orphan")
    reversal_of = db.relationship("JournalEntry", remote_side=[id], backref="reversals")
    author = db.relationship("User")

    __table_args__ = (db.Index("ix_journal_source", "source_type", "source_id"),)

    @property
    def total_cents(self):
        return sum(l.debit_cents for l in self.lines)


class JournalLine(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    entry_id = db.Column(db.Integer, db.ForeignKey("journal_entry.id"), nullable=False, index=True)
    account_id = db.Column(db.Integer, db.ForeignKey("account.id"), nullable=False, index=True)
    debit_cents = db.Column(db.Integer, nullable=False, default=0)
    credit_cents = db.Column(db.Integer, nullable=False, default=0)
    memo = db.Column(db.String(120))
    # Receivable sub-ledger: which student a receivables line belongs to.
    student_id = db.Column(db.Integer, db.ForeignKey("student.id"), index=True)
    # Bank reconciliation: the completed statement this cash/bank line was cleared on.
    cleared_statement_id = db.Column(db.Integer, db.ForeignKey("bank_statement.id"), index=True)

    entry = db.relationship("JournalEntry", back_populates="lines")
    account = db.relationship("Account")

    __table_args__ = (
        CheckConstraint("debit_cents >= 0 AND credit_cents >= 0", name="ck_jl_nonneg"),
        CheckConstraint("(debit_cents = 0) <> (credit_cents = 0)", name="ck_jl_one_side"),
    )


class Setting(db.Model):
    key = db.Column(db.String(40), primary_key=True)
    value = db.Column(db.String(200))


class SchoolLogo(db.Model):
    """The school's logo (a single row), shown in the app and on every printed document."""
    id = db.Column(db.Integer, primary_key=True)
    mime = db.Column(db.String(40), nullable=False)
    data = db.Column(db.LargeBinary, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)


# --------------------------------------------------------------------------- #
# Fixed asset register
# --------------------------------------------------------------------------- #
ASSET_CATEGORIES = ("Land", "Buildings", "Furniture & Fittings", "Motor Vehicles", "Computer Equipment",
                    "Office Equipment", "Laboratory & Teaching Equipment", "Sports Equipment", "Other")
DEPRECIATION_METHODS = ("straight_line", "reducing_balance", "none")
ASSET_STATUSES = ("active", "disposed", "written_off")
ASSET_FUNDING = ("purchase", "donation", "existing")


class FixedAsset(TimestampMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    asset_no = db.Column(db.String(20), unique=True, nullable=False)
    name = db.Column(db.String(120), nullable=False)
    category = db.Column(db.String(40), nullable=False)
    serial_no = db.Column(db.String(60))
    location = db.Column(db.String(80))
    custodian_id = db.Column(db.Integer, db.ForeignKey("staff.id"))
    supplier = db.Column(db.String(100))
    condition = db.Column(db.String(20), default="Good")
    notes = db.Column(db.String(300))
    acquisition_date = db.Column(db.Date, nullable=False)
    # purchase (Cr a cash account) | donation (Cr donations income) | existing (already in the books)
    funding = db.Column(db.String(10), nullable=False, default="purchase")
    cost_cents = db.Column(db.Integer, nullable=False)
    currency = db.Column(db.String(3), default=_default_currency)
    residual_cents = db.Column(db.Integer, nullable=False, default=0)
    method = db.Column(db.String(20), nullable=False, default="straight_line")
    useful_life_months = db.Column(db.Integer)  # straight line
    rate_pct = db.Column(db.Float)               # reducing balance, % per year
    # First month depreciated (YYYY-MM), and depreciation brought forward for assets
    # that were already in use when the register was started.
    depreciation_start = db.Column(db.String(7), nullable=False)
    opening_accum_cents = db.Column(db.Integer, nullable=False, default=0)
    depreciated_to = db.Column(db.String(7))     # last month charged (YYYY-MM)
    status = db.Column(db.String(12), nullable=False, default="active")
    disposal_date = db.Column(db.Date)
    disposal_proceeds_cents = db.Column(db.Integer)
    disposal_note = db.Column(db.String(200))
    created_by = db.Column(db.Integer, db.ForeignKey("user.id"))

    custodian = db.relationship("Staff")
    depreciation = db.relationship("AssetDepreciation", back_populates="asset", order_by="AssetDepreciation.id")

    __table_args__ = (
        CheckConstraint("cost_cents > 0", name="ck_asset_cost"),
        CheckConstraint("residual_cents >= 0 AND residual_cents <= cost_cents", name="ck_asset_residual"),
        CheckConstraint(f"method IN {DEPRECIATION_METHODS}", name="ck_asset_method"),
        CheckConstraint(f"status IN {ASSET_STATUSES}", name="ck_asset_status"),
        CheckConstraint(f"funding IN {ASSET_FUNDING}", name="ck_asset_funding"),
    )

    @property
    def accumulated_cents(self):
        return self.opening_accum_cents + sum(d.amount_cents for d in self.depreciation
                                              if not (d.run and d.run.reversed))

    @property
    def nbv_cents(self):
        return self.cost_cents - self.accumulated_cents


class DepreciationRun(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    period = db.Column(db.String(7), nullable=False, index=True)  # YYYY-MM
    entry_id = db.Column(db.Integer, db.ForeignKey("journal_entry.id"))
    created_by = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    reversed = db.Column(db.Boolean, default=False, nullable=False)

    entry = db.relationship("JournalEntry")
    author = db.relationship("User")
    lines = db.relationship("AssetDepreciation", back_populates="run")


class AssetDepreciation(db.Model):
    """Depreciation charged on one asset by one run (several months when catching up).

    run_id is empty for the catch-up charge posted when an asset is disposed of.
    """
    id = db.Column(db.Integer, primary_key=True)
    asset_id = db.Column(db.Integer, db.ForeignKey("fixed_asset.id"), nullable=False, index=True)
    run_id = db.Column(db.Integer, db.ForeignKey("depreciation_run.id"), index=True)
    entry_id = db.Column(db.Integer, db.ForeignKey("journal_entry.id"))
    from_period = db.Column(db.String(7), nullable=False)
    to_period = db.Column(db.String(7), nullable=False)
    amount_cents = db.Column(db.Integer, nullable=False)

    asset = db.relationship("FixedAsset", back_populates="depreciation")
    run = db.relationship("DepreciationRun", back_populates="lines")


# --------------------------------------------------------------------------- #
# Payroll (ZIMRA PAYE, NSSA, ZIMDEF)
# --------------------------------------------------------------------------- #
class TaxTable(TimestampMixin, db.Model):
    """A versioned set of statutory rates. A payroll uses the latest table in force for its month.

    config (JSON): bands [{"upto": monthly amount or null, "rate": %}], aids_levy_pct,
    nssa_rate_pct, nssa_ceiling, zimdef_pct, wcif_pct, pension_cap, elderly_credit,
    disabled_credit, elderly_age, medical_credit_pct, bonus_exempt (per tax year).
    Amounts are in currency units (not cents) so the table reads like ZIMRA's.
    """
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False)
    effective_from = db.Column(db.Date, nullable=False, unique=True)
    config = db.Column(db.Text, nullable=False)
    created_by = db.Column(db.Integer, db.ForeignKey("user.id"))


PAY_ITEM_TYPES = ("allowance", "benefit", "pension", "medical_aid", "deduction")


class StaffPayItem(db.Model):
    """A recurring monthly earning or deduction for a staff member.

    allowance    cash earning (taxable unless marked exempt)
    benefit      non-cash taxable benefit (housing, vehicle): taxed but not paid out
    pension      employee contribution to an approved fund: deductible up to the cap
    medical_aid  employee medical aid contribution: earns the medical tax credit
    deduction    other after-tax deduction (loan, union dues, funeral policy)
    """
    id = db.Column(db.Integer, primary_key=True)
    staff_id = db.Column(db.Integer, db.ForeignKey("staff.id"), nullable=False, index=True)
    type = db.Column(db.String(12), nullable=False)
    name = db.Column(db.String(60), nullable=False)
    amount_cents = db.Column(db.Integer, nullable=False)
    currency = db.Column(db.String(3), default=_default_currency)
    taxable = db.Column(db.Boolean, default=True, nullable=False)
    active = db.Column(db.Boolean, default=True, nullable=False)

    staff = db.relationship("Staff", backref=db.backref("pay_items", cascade="all, delete-orphan"))

    __table_args__ = (
        CheckConstraint(f"type IN {PAY_ITEM_TYPES}", name="ck_payitem_type"),
        CheckConstraint("amount_cents > 0", name="ck_payitem_positive"),
    )


PAYROLL_STATUSES = ("draft", "approved", "paid", "void")


class PayrollRun(TimestampMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    run_no = db.Column(db.String(20), unique=True, nullable=False)
    period = db.Column(db.String(7), nullable=False, index=True)  # YYYY-MM
    pay_date = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(10), nullable=False, default="draft")
    tax_table_id = db.Column(db.Integer, db.ForeignKey("tax_table.id"), nullable=False)
    notes = db.Column(db.String(200))
    created_by = db.Column(db.Integer, db.ForeignKey("user.id"))
    approved_by = db.Column(db.Integer, db.ForeignKey("user.id"))
    approved_at = db.Column(db.Date)
    paid_at = db.Column(db.Date)
    paid_from_account_id = db.Column(db.Integer, db.ForeignKey("account.id"))
    # {"USD": account_id, "ZWG": account_id}: where each currency's net pay was paid from
    paid_accounts = db.Column(db.Text)
    # Rates used to work out PAYE when pay is in more than one currency (ZIMRA apportionment):
    # exchange_rates is {"ZWG": 26.75, "ZAR": 18.4} (units per 1 USD); exchange_rate is the ZWG
    # rate alone, as stored by the two-currency version.
    exchange_rate = db.Column(db.Float)
    exchange_rates = db.Column(db.Text)
    void_reason = db.Column(db.String(200))
    voided_at = db.Column(db.Date)

    tax_table = db.relationship("TaxTable")
    creator = db.relationship("User", foreign_keys=[created_by])
    approver = db.relationship("User", foreign_keys=[approved_by])
    payslips = db.relationship("Payslip", back_populates="run", cascade="all, delete-orphan", order_by="Payslip.id")
    remittances = db.relationship("PayrollRemittance", back_populates="run", order_by="PayrollRemittance.id")

    __table_args__ = (CheckConstraint(f"status IN {PAYROLL_STATUSES}", name="ck_payroll_status"),)


class Payslip(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    run_id = db.Column(db.Integer, db.ForeignKey("payroll_run.id"), nullable=False, index=True)
    staff_id = db.Column(db.Integer, db.ForeignKey("staff.id"), nullable=False, index=True)
    # One-off inputs for this month, editable while the run is a draft.
    overtime_cents = db.Column(db.Integer, nullable=False, default=0)
    bonus_cents = db.Column(db.Integer, nullable=False, default=0)
    extra_deduction_cents = db.Column(db.Integer, nullable=False, default=0)
    extra_deduction_note = db.Column(db.String(60))
    overtime_currency = db.Column(db.String(3), default=_default_currency)
    bonus_currency = db.Column(db.String(3), default=_default_currency)
    extra_deduction_currency = db.Column(db.String(3), default=_default_currency)
    # Calculated figures (cents).
    basic_cents = db.Column(db.Integer, nullable=False, default=0)
    gross_cents = db.Column(db.Integer, nullable=False, default=0)        # cash earnings
    benefits_cents = db.Column(db.Integer, nullable=False, default=0)     # non-cash, taxable
    exempt_cents = db.Column(db.Integer, nullable=False, default=0)       # bonus exemption, exempt allowances
    nssa_cents = db.Column(db.Integer, nullable=False, default=0)         # employee share
    pension_cents = db.Column(db.Integer, nullable=False, default=0)
    taxable_cents = db.Column(db.Integer, nullable=False, default=0)
    tax_before_credits_cents = db.Column(db.Integer, nullable=False, default=0)
    credits_cents = db.Column(db.Integer, nullable=False, default=0)
    paye_cents = db.Column(db.Integer, nullable=False, default=0)         # income tax after credits
    aids_levy_cents = db.Column(db.Integer, nullable=False, default=0)
    medical_aid_cents = db.Column(db.Integer, nullable=False, default=0)
    other_deductions_cents = db.Column(db.Integer, nullable=False, default=0)
    net_cents = db.Column(db.Integer, nullable=False, default=0)
    nssa_employer_cents = db.Column(db.Integer, nullable=False, default=0)
    zimdef_cents = db.Column(db.Integer, nullable=False, default=0)
    wcif_cents = db.Column(db.Integer, nullable=False, default=0)
    detail = db.Column(db.Text)  # JSON: itemised lines for the payslip

    run = db.relationship("PayrollRun", back_populates="payslips")
    staff = db.relationship("Staff")

    __table_args__ = (UniqueConstraint("run_id", "staff_id"),)

    @property
    def total_tax_cents(self):
        return self.paye_cents + self.aids_levy_cents


REMITTANCE_TYPES = ("zimra", "nssa", "zimdef", "pension_medical", "other")


class PayrollRemittance(TimestampMixin, db.Model):
    """Payment of amounts withheld or levied by a payroll to ZIMRA, NSSA, ZIMDEF or third parties."""
    id = db.Column(db.Integer, primary_key=True)
    run_id = db.Column(db.Integer, db.ForeignKey("payroll_run.id"), nullable=False, index=True)
    type = db.Column(db.String(16), nullable=False)
    amount_cents = db.Column(db.Integer, nullable=False)
    currency = db.Column(db.String(3), default=_default_currency)
    paid_on = db.Column(db.Date, nullable=False)
    account_id = db.Column(db.Integer, db.ForeignKey("account.id"), nullable=False)
    reference = db.Column(db.String(60))
    created_by = db.Column(db.Integer, db.ForeignKey("user.id"))

    run = db.relationship("PayrollRun", back_populates="remittances")
    account = db.relationship("Account")

    __table_args__ = (
        CheckConstraint(f"type IN {REMITTANCE_TYPES}", name="ck_remit_type"),
        CheckConstraint("amount_cents > 0", name="ck_remit_positive"),
        UniqueConstraint("run_id", "type", "currency", name="uq_remittance_run_type_currency"),
    )


# --------------------------------------------------------------------------- #
# Platform registry (multi-school mode). These tables live in the platform
# database, never in a school's database.
# --------------------------------------------------------------------------- #
SCHOOL_STATUSES = ("active", "suspended")


class School(db.Model):
    __bind_key__ = "platform"
    id = db.Column(db.Integer, primary_key=True)
    slug = db.Column(db.String(40), unique=True, nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    school_type = db.Column(db.String(10), nullable=False, default="primary")
    currency = db.Column(db.String(5), nullable=False, default="USD")
    database_url = db.Column(db.String(500), nullable=False)
    db_schema = db.Column(db.String(63))  # PostgreSQL schema holding this school's tables
    status = db.Column(db.String(10), nullable=False, default="active")
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    __table_args__ = (CheckConstraint(f"status IN {SCHOOL_STATUSES}", name="ck_school_status"),)


class PlatformAdmin(db.Model):
    """Operators who create and manage schools. Separate from every school's own users."""
    __bind_key__ = "platform"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False)
    full_name = db.Column(db.String(120), nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    active = db.Column(db.Boolean, default=True, nullable=False)

    def set_password(self, raw):
        self.password_hash = generate_password_hash(raw)

    def check_password(self, raw):
        return check_password_hash(self.password_hash, raw)


class PayslipCurrency(db.Model):
    """The part of a payslip paid in one currency.

    Payslip columns hold the totals in the tax table's currency (used for PAYE); these rows
    hold what is actually paid and withheld in each currency, and drive the ledger postings.
    """
    id = db.Column(db.Integer, primary_key=True)
    payslip_id = db.Column(db.Integer, db.ForeignKey("payslip.id"), nullable=False, index=True)
    currency = db.Column(db.String(3), nullable=False)
    gross_cents = db.Column(db.Integer, nullable=False, default=0)
    nssa_cents = db.Column(db.Integer, nullable=False, default=0)
    paye_cents = db.Column(db.Integer, nullable=False, default=0)
    aids_levy_cents = db.Column(db.Integer, nullable=False, default=0)
    pension_cents = db.Column(db.Integer, nullable=False, default=0)
    medical_aid_cents = db.Column(db.Integer, nullable=False, default=0)
    other_deductions_cents = db.Column(db.Integer, nullable=False, default=0)
    net_cents = db.Column(db.Integer, nullable=False, default=0)
    nssa_employer_cents = db.Column(db.Integer, nullable=False, default=0)
    wcif_cents = db.Column(db.Integer, nullable=False, default=0)
    zimdef_cents = db.Column(db.Integer, nullable=False, default=0)

    payslip = db.relationship("Payslip", backref=db.backref("currency_parts", cascade="all, delete-orphan",
                                                            order_by="PayslipCurrency.currency"))

    __table_args__ = (UniqueConstraint("payslip_id", "currency"),)


class ExchangeRate(db.Model):
    """A day's exchange rate entered by the bursar: units of `currency` for 1 USD.

    One row per currency per day (ZWG 26.75, ZAR 18.40, ...); any pair converts through USD.
    Used only for combined totals and for PAYE on pay split across currencies;
    transactions themselves are always recorded in the currency they happened in.
    """
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False, index=True)
    # Rows from the two-currency version have no currency: they were all ZWG (filled in on upgrade).
    currency = db.Column(db.String(3), index=True, default="ZWG")
    # The column keeps its original name so existing rates carry over unchanged.
    per_usd = db.Column("zwg_per_usd", db.Float, nullable=False)
    zwg_per_usd = synonym("per_usd")
    source = db.Column(db.String(60))
    entered_by = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    author = db.relationship("User")

    __table_args__ = (CheckConstraint("zwg_per_usd > 0", name="ck_rate_positive"),
                      UniqueConstraint("date", "currency", name="uq_rate_day_currency"))


# --------------------------------------------------------------------------- #
# Bank reconciliation
# --------------------------------------------------------------------------- #
class BankStatement(TimestampMixin, db.Model):
    """A bank (or mobile money / cash count) statement for one account, reconciled to the books."""
    id = db.Column(db.Integer, primary_key=True)
    account_id = db.Column(db.Integer, db.ForeignKey("account.id"), nullable=False, index=True)
    statement_date = db.Column(db.Date, nullable=False)
    # First reconciliation of an account only: book lines dated before this are taken as already
    # included in the statement's opening balance, and are cleared with it.
    start_date = db.Column(db.Date)
    opening_cents = db.Column(db.Integer, nullable=False, default=0)
    closing_cents = db.Column(db.Integer, nullable=False, default=0)
    reference = db.Column(db.String(60))
    status = db.Column(db.String(10), nullable=False, default="draft")  # draft | completed
    created_by = db.Column(db.Integer, db.ForeignKey("user.id"))
    completed_by = db.Column(db.Integer, db.ForeignKey("user.id"))
    completed_at = db.Column(db.Date)

    account = db.relationship("Account")
    lines = db.relationship("BankStatementLine", back_populates="statement", cascade="all, delete-orphan",
                            order_by="(BankStatementLine.date, BankStatementLine.id)")
    creator = db.relationship("User", foreign_keys=[created_by])
    completer = db.relationship("User", foreign_keys=[completed_by])

    __table_args__ = (CheckConstraint("status IN ('draft', 'completed')", name="ck_bank_statement_status"),)


class BankStatementLine(db.Model):
    """One line of a bank statement: money in (positive) or out (negative), matched to the books."""
    id = db.Column(db.Integer, primary_key=True)
    statement_id = db.Column(db.Integer, db.ForeignKey("bank_statement.id"), nullable=False, index=True)
    date = db.Column(db.Date, nullable=False)
    description = db.Column(db.String(200), nullable=False)
    reference = db.Column(db.String(60))
    amount_cents = db.Column(db.Integer, nullable=False)
    journal_line_id = db.Column(db.Integer, db.ForeignKey("journal_line.id"), index=True)
    # True when the matching entry was posted from the reconciliation (bank charges, interest...).
    posted_here = db.Column(db.Boolean, default=False, nullable=False)

    statement = db.relationship("BankStatement", back_populates="lines")
    journal_line = db.relationship("JournalLine")

    __table_args__ = (CheckConstraint("amount_cents <> 0", name="ck_bank_line_nonzero"),)


# --------------------------------------------------------------------------- #
# Library
# --------------------------------------------------------------------------- #
BOOK_CATEGORIES = ("Textbook", "Fiction", "Non-fiction", "Reference", "Revision guide", "Magazine", "Other")
COPY_STATUSES = ("available", "on_loan", "lost", "withdrawn")
BOOK_CONDITIONS = ("New", "Good", "Fair", "Poor", "Damaged")


class Book(TimestampMixin, db.Model):
    """A title in the library catalogue; the physical books are its copies."""
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False, index=True)
    author = db.Column(db.String(150))
    isbn = db.Column(db.String(20), index=True)
    publisher = db.Column(db.String(120))
    year = db.Column(db.Integer)
    edition = db.Column(db.String(40))
    category = db.Column(db.String(20), nullable=False, default="Other")
    subject_id = db.Column(db.Integer, db.ForeignKey("subject.id"))
    level = db.Column(db.String(10))  # grade/form code the book is meant for (G5, F3...), optional
    shelf = db.Column(db.String(40))
    notes = db.Column(db.String(300))

    subject = db.relationship("Subject")
    copies = db.relationship("BookCopy", back_populates="book", cascade="all, delete-orphan", order_by="BookCopy.id")

    __table_args__ = (CheckConstraint(f"category IN {BOOK_CATEGORIES}", name="ck_book_category"),)


class BookCopy(TimestampMixin, db.Model):
    """One physical book, identified by its accession number (also used as its barcode)."""
    id = db.Column(db.Integer, primary_key=True)
    book_id = db.Column(db.Integer, db.ForeignKey("book.id"), nullable=False, index=True)
    accession_no = db.Column(db.String(30), unique=True, nullable=False, index=True)
    status = db.Column(db.String(10), nullable=False, default="available")
    condition = db.Column(db.String(10), nullable=False, default="Good")
    acquired_on = db.Column(db.Date, default=date.today)
    # What the school charges when the copy is lost (in `currency`); 0 = no charge.
    replacement_cents = db.Column(db.Integer, nullable=False, default=0)
    currency = db.Column(db.String(3), default=_default_currency)
    notes = db.Column(db.String(200))

    book = db.relationship("Book", back_populates="copies")
    loans = db.relationship("Loan", back_populates="copy", order_by="Loan.id.desc()")

    __table_args__ = (CheckConstraint(f"status IN {COPY_STATUSES}", name="ck_copy_status"),
                      CheckConstraint("replacement_cents >= 0", name="ck_copy_replacement"))


class Loan(TimestampMixin, db.Model):
    """A copy lent to a student or a member of staff, and any fine that followed.

    fine_status: none | unpaid (owed) | charged (added to the student's fees invoice) | paid | waived
    """
    id = db.Column(db.Integer, primary_key=True)
    copy_id = db.Column(db.Integer, db.ForeignKey("book_copy.id"), nullable=False, index=True)
    student_id = db.Column(db.Integer, db.ForeignKey("student.id"), index=True)
    staff_id = db.Column(db.Integer, db.ForeignKey("staff.id"), index=True)
    issued_on = db.Column(db.Date, nullable=False, default=date.today)
    due_on = db.Column(db.Date, nullable=False, index=True)
    returned_on = db.Column(db.Date)
    lost = db.Column(db.Boolean, nullable=False, default=False)
    renewals = db.Column(db.Integer, nullable=False, default=0)
    condition_out = db.Column(db.String(10))
    condition_in = db.Column(db.String(10))
    issued_by = db.Column(db.Integer, db.ForeignKey("user.id"))
    received_by = db.Column(db.Integer, db.ForeignKey("user.id"))
    fine_cents = db.Column(db.Integer, nullable=False, default=0)
    fine_currency = db.Column(db.String(3))
    fine_status = db.Column(db.String(10), nullable=False, default="none")
    fine_note = db.Column(db.String(200))
    fine_invoice_id = db.Column(db.Integer, db.ForeignKey("invoice.id"))

    copy = db.relationship("BookCopy", back_populates="loans")
    student = db.relationship("Student")
    staff = db.relationship("Staff")
    issuer = db.relationship("User", foreign_keys=[issued_by])
    receiver = db.relationship("User", foreign_keys=[received_by])
    fine_invoice = db.relationship("Invoice")

    __table_args__ = (
        CheckConstraint("(student_id IS NULL) <> (staff_id IS NULL)", name="ck_loan_one_borrower"),
        CheckConstraint("fine_status IN ('none', 'unpaid', 'charged', 'paid', 'waived')", name="ck_loan_fine_status"),
        CheckConstraint("fine_cents >= 0", name="ck_loan_fine"),
    )

    @property
    def is_open(self):
        return self.returned_on is None and not self.lost
