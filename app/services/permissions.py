"""Permissions: what each user may see and do.

Every action is guarded by a permission "<area>.<level>":

    view      see the area
    manage    create and change things in it (implies view)
    approve   sensitive actions: approvals, voids, period close... (implies view)

A user's permissions come from their role, adjusted per person:

    Administrator   everything, always (can't be reduced, so the school can't lock itself out)
    Parent          the parent portal only (their own children)
    Bursar/Teacher  ready-made roles whose permissions the school can edit
    Custom roles    e.g. "Accounts clerk", "Deputy head": built from a Bursar or Teacher base
    Per person      extra permissions added, or role permissions removed, for one user

Row-level rules still apply on top: teachers see their own classes, parents their own children,
nobody approves their own expense or payroll.
"""
from flask import g, has_request_context
from flask_login import current_user

from .. import db
from ..utils import ApiError

# area, permission, label
CATALOG = [
    ("Students", "students.view", "View all students"),
    ("Students", "students.manage", "Admit and edit students, change their status"),
    ("Students", "guardians.view", "View guardians"),
    ("Students", "guardians.manage", "Add and edit guardians, create parent portal accounts"),
    ("Staff", "staff.view", "View the staff list"),
    ("Staff", "staff.manage", "Add and edit staff"),
    ("Academics", "academics.view", "View every class, timetable and exam"),
    ("Academics", "academics.manage", "Set up classes, subjects, timetable, terms, exams and grading"),
    ("Academics", "attendance.manage", "Take or correct attendance for any class"),
    ("Academics", "marks.manage", "Enter marks for any class and subject"),
    ("Academics", "results.view", "Results, report cards and academic reports for every class"),
    ("Academics", "promotion.approve", "Run the end-of-year promotion"),
    ("Fees", "fees.view", "View the fee structure, invoices and student balances"),
    ("Fees", "fees.manage", "Set fees, generate invoices, add charges, scholarships, void invoices"),
    ("Payments", "payments.view", "View receipts"),
    ("Payments", "payments.manage", "Record payments and the day's exchange rate"),
    ("Payments", "payments.approve", "Void receipts"),
    ("Expenses", "expenses.view", "View expenses"),
    ("Expenses", "expenses.manage", "Request and pay expenses"),
    ("Expenses", "expenses.approve", "Approve or reject expenses (never their own)"),
    ("Payroll", "payroll.view", "View payroll, payslips and salaries"),
    ("Payroll", "payroll.manage", "Prepare payroll, staff pay setup, pay salaries and remittances"),
    ("Payroll", "payroll.approve", "Approve or void payroll (never one they prepared), change salaries, tax tables"),
    ("Assets", "assets.view", "View the asset register"),
    ("Assets", "assets.manage", "Register, edit and dispose of assets, run depreciation"),
    ("Assets", "assets.approve", "Cancel registrations, reverse depreciation runs"),
    ("Accounting", "accounting.view", "General ledger, journals and financial statements"),
    ("Accounting", "accounting.manage", "Post and reverse manual journals"),
    ("Accounting", "accounting.approve", "Chart of accounts, close or reopen periods, rebuild the ledger"),
    ("Banking", "banking.view", "View bank reconciliations"),
    ("Banking", "banking.manage", "Enter bank statements, match transactions, post bank charges and interest"),
    ("Banking", "banking.approve", "Complete or reopen a bank reconciliation"),
    ("Library", "library.view", "Browse the library catalogue, loans and overdue books"),
    ("Library", "library.manage", "Add books, issue and return them, record fines"),
    ("Library", "library.approve", "Waive fines, delete books, change library rules, remove any uploaded file"),
    ("Library", "library.upload", "Upload PDF textbooks, past exam papers and notes to the digital library"),
    ("Reports", "reports.view", "Fee, debtor and cash flow reports"),
    ("School", "announcements.manage", "Post and delete announcements"),
    ("School", "users.manage", "User accounts, roles and permissions"),
    ("School", "audit.view", "Audit log"),
    ("School", "settings.manage", "School type, currencies and demo mode"),
]
ALL = [p for _, p, _ in CATALOG]
LABEL = {p: label for _, p, label in CATALOG}

BASES = {"bursar": "Bursar (office staff)", "teacher": "Teacher (own classes)"}

# Starting permissions of the built-in roles; reproduce what each role could always do.
DEFAULTS = {
    "bursar": ["students.view", "guardians.view", "staff.view", "fees.view", "fees.manage", "payments.view",
               "payments.manage", "expenses.view", "expenses.manage", "payroll.view", "payroll.manage",
               "assets.view", "assets.manage", "accounting.view", "accounting.manage", "reports.view",
               "banking.view", "banking.manage", "library.view"],
    # Teachers work with their own classes (row-level rules); they can also browse the library.
    "teacher": ["library.view", "library.upload"],
}
ROLE_LABEL = {"admin": "Administrator", "bursar": "Bursar", "teacher": "Teacher", "parent": "Parent"}


def expand(perms):
    """Add the view permission implied by manage/approve; drop unknown codes."""
    out = {p for p in perms if p in LABEL}
    for p in list(out):
        area, level = p.rsplit(".", 1)
        if level in ("manage", "approve") and f"{area}.view" in LABEL:
            out.add(f"{area}.view")
    return out


def split(text):
    return [p for p in (text or "").split(",") if p]


def validate(perms, field="permissions"):
    bad = [p for p in perms if p not in LABEL]
    if bad:
        raise ApiError(f"Unknown permission: {', '.join(bad)}", fields={field: "Invalid"})
    return sorted(set(perms))


# Permissions added to the built-in roles by later versions, applied once to existing schools
# (so a permission an administrator removed on purpose is never put back).
ADDED_DEFAULTS = {2: {"bursar": ["banking.view", "banking.manage"]},
                  3: {"teacher": ["library.view"], "bursar": ["library.view"]},
                  4: {"teacher": ["library.upload"]}}
DEFAULTS_VERSION = max(ADDED_DEFAULTS)


def ensure_roles():
    """The editable built-in roles (Bursar, Teacher) exist as role records, up to date."""
    from ..models import Role, Setting
    created = False
    for key, perms in DEFAULTS.items():
        if not Role.query.filter_by(key=key).first():
            db.session.add(Role(key=key, name=ROLE_LABEL[key], base=key, is_system=True,
                                description=f"Built-in {ROLE_LABEL[key].lower()} role", permissions=",".join(perms)))
            created = True
    db.session.flush()
    ver = db.session.get(Setting, "role_defaults_version") or Setting(key="role_defaults_version", value="1")
    done = int(ver.value or 1) if not created else DEFAULTS_VERSION
    for v in sorted(ADDED_DEFAULTS):
        if v <= done:
            continue
        for key, perms in ADDED_DEFAULTS[v].items():
            role = Role.query.filter_by(key=key).first()
            if role:
                role.permissions = ",".join(sorted(set(split(role.permissions)) | set(perms)))
    ver.value = str(DEFAULTS_VERSION)
    db.session.add(ver)
    db.session.flush()


def role_of(user):
    from ..models import Role
    if user.custom_role is not None:
        return user.custom_role
    return Role.query.filter_by(key=user.role).first()


def permissions_for(user):
    """The full set of permissions a user has right now."""
    if user is None or not getattr(user, "is_authenticated", False):
        return set()
    if user.role == "admin":
        return set(ALL)
    if user.role == "parent":
        return set()
    role = role_of(user)
    base = set(split(role.permissions)) if role else set(DEFAULTS.get(user.role, []))
    perms = expand(base | set(split(user.extra_permissions)))
    removed = set(split(user.removed_permissions))
    # Removing view of an area also removes managing/approving in it.
    removed |= {p for p in perms if f"{p.rsplit('.', 1)[0]}.view" in removed}
    return perms - removed


def current():
    if not has_request_context() or not current_user.is_authenticated:
        return set()
    cached = g.get("_perms")
    if cached is None or g.get("_perms_uid") != current_user.id:
        cached = permissions_for(current_user)
        g._perms, g._perms_uid = cached, current_user.id
    return cached


def has(perm):
    return perm in current()


def has_any(*perms):
    mine = current()
    return any(p in mine for p in perms)


def require(*perms):
    """Raise 403 unless the current user has at least one of `perms`."""
    if not has_any(*perms):
        names = " or ".join(LABEL.get(p, p).lower() for p in perms)
        raise ApiError(f"You do not have permission to {names}", 403)


def catalog():
    areas = {}
    for area, p, label in CATALOG:
        areas.setdefault(area, []).append({"code": p, "label": label, "level": p.rsplit(".", 1)[1]})
    return [{"area": a, "permissions": ps} for a, ps in areas.items()]
