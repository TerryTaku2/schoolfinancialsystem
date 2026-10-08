"""Students, guardians, staff and user accounts."""
from datetime import date

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required
from sqlalchemy import or_

from .. import db
from ..models import (ROLES, Role, Attendance, ClassSubject, Guardian, Invoice, Mark, Payment,
                      SchoolClass, Scholarship, Staff, Student, User)
from ..services import finance, permissions
from ..services.access import ensure_student_access, visible_students_query
from ..services.academics import attendance_stats
from ..utils import (ApiError, audit, body, clean_str, current_term, get_or_404, iso, money,
                     next_number, paginate_args, parse_date, parse_float, parse_int, require, permission_required,
                     to_cents)
from .auth import validate_password

from ..services.permissions import has, has_any  # noqa: E402

bp = Blueprint("people", __name__)

STUDENT_STATUSES = ("active", "suspended", "graduated", "transferred", "withdrawn")
LEAVING = ("graduated", "transferred", "withdrawn")


# --------------------------------------------------------------------------- #
# Students
# --------------------------------------------------------------------------- #
def student_dict(s, with_balance=False):
    d = {
        "id": s.id, "admission_no": s.admission_no, "first_name": s.first_name,
        "last_name": s.last_name, "name": s.name, "gender": s.gender, "dob": iso(s.dob),
        "age": _age(s.dob), "class_id": s.class_id,
        "class": s.school_class.name if s.school_class else None,
        "guardian_id": s.guardian_id, "guardian": s.guardian.name if s.guardian else None,
        "guardian_phone": s.guardian.phone if s.guardian else None,
        "admission_date": iso(s.admission_date), "status": s.status,
        "address": s.address, "medical_notes": s.medical_notes,
    }
    if with_balance:
        summary = finance.account_summary(s)
        d["balance"] = summary["balance"]
        d["balances"] = {x["currency"]: x["balance"] for x in summary["by_currency"]}
    return d


def _age(dob):
    today = date.today()
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


def _check_capacity(class_id, exclude_student_id=None):
    cls = get_or_404(SchoolClass, class_id, "Class")
    enrolled = Student.query.filter_by(class_id=cls.id, status="active")
    if exclude_student_id:
        enrolled = enrolled.filter(Student.id != exclude_student_id)
    if enrolled.count() >= cls.capacity:
        raise ApiError(f"{cls.name} is full ({cls.capacity} students)", fields={"class_id": "Class full"})
    return cls


def _resolve_guardian(data):
    if data.get("guardian_id"):
        return get_or_404(Guardian, parse_int(data["guardian_id"], "guardian_id"), "Guardian")
    g = data.get("guardian") or {}
    if not g.get("name") or not g.get("phone"):
        raise ApiError("A guardian is required (select one or enter name and phone)",
                       fields={"guardian_id": "Required"})
    # Reuse an existing guardian with the same phone instead of duplicating siblings' parents.
    existing = Guardian.query.filter_by(phone=clean_str(g["phone"], 30)).first()
    if existing:
        return existing
    guardian = Guardian(name=clean_str(g["name"], 120), phone=clean_str(g["phone"], 30),
                        email=clean_str(g.get("email"), 120), relationship=clean_str(g.get("relationship"), 30) or "Parent",
                        address=clean_str(g.get("address"), 200))
    db.session.add(guardian)
    return guardian


def _apply_student_fields(s, data):
    s.first_name = clean_str(data["first_name"], 60)
    s.last_name = clean_str(data["last_name"], 60)
    if data["gender"] not in ("Male", "Female"):
        raise ApiError("Gender must be Male or Female", fields={"gender": "Invalid"})
    s.gender = data["gender"]
    dob = parse_date(data["dob"], "dob")
    age = _age(dob)
    if not 3 <= age <= 25:
        raise ApiError(f"Date of birth gives an age of {age}; expected 3-25", fields={"dob": "Implausible age"})
    s.dob = dob
    s.address = clean_str(data.get("address"), 200)
    s.medical_notes = clean_str(data.get("medical_notes"), 300)


@bp.get("/students")
@login_required
def list_students():
    q = visible_students_query()
    term = request.args.get("q", "").strip()
    if term:
        like = f"%{term}%"
        q = q.filter(or_(Student.first_name.ilike(like), Student.last_name.ilike(like),
                         Student.admission_no.ilike(like)))
    if request.args.get("class_id"):
        q = q.filter(Student.class_id == parse_int(request.args["class_id"], "class_id"))
    status = request.args.get("status", "active")
    if status != "all":
        q = q.filter(Student.status == status)
    page, per = paginate_args()
    total = q.count()
    rows = q.order_by(Student.last_name, Student.first_name).offset((page - 1) * per).limit(per).all()
    show_bal = has("fees.view")
    return jsonify(items=[student_dict(s, show_bal) for s in rows], total=total, page=page, per_page=per)


@bp.get("/students/<int:sid>")
@login_required
def get_student(sid):
    s = get_or_404(Student, sid, "Student")
    ensure_student_access(s)
    d = student_dict(s)
    term = current_term()
    if term:
        d["attendance"] = attendance_stats([s.id], term.start_date, min(term.end_date, date.today()))[s.id]
    recent = (Attendance.query.filter_by(student_id=s.id).order_by(Attendance.date.desc()).limit(10))
    d["recent_attendance"] = [{"date": iso(a.date), "status": a.status, "remark": a.remark} for a in recent]
    if current_user.role == "parent" or has("fees.view"):
        d["account"] = finance.account_summary(s)
        d["invoices"] = [finance.invoice_dict(i) for i in sorted(s.invoices, key=lambda i: i.id, reverse=True)]
        d["payments"] = [finance.payment_dict(p) for p in sorted(s.payments, key=lambda p: p.id, reverse=True)]
        d["scholarships"] = [{"id": x.id, "name": x.name, "percent": x.percent, "active": x.active}
                             for x in s.scholarships]
    if s.guardian:
        g = s.guardian
        d["guardian_detail"] = {"id": g.id, "name": g.name, "phone": g.phone, "email": g.email,
                                "relationship": g.relationship, "address": g.address}
    return jsonify(d)


@bp.post("/students")
@permission_required("students.manage")
def create_student():
    data = body()
    require(data, "first_name", "last_name", "gender", "dob", "class_id")
    cls = _check_capacity(parse_int(data["class_id"], "class_id"))
    s = Student()
    _apply_student_fields(s, data)
    s.class_id = cls.id
    s.guardian = _resolve_guardian(data)
    s.admission_date = parse_date(data.get("admission_date"), "admission_date", required=False) or date.today()
    if s.admission_date > date.today():
        raise ApiError("Admission date cannot be in the future", fields={"admission_date": "Future date"})
    s.admission_no = next_number(f"adm-{s.admission_date.year}", f"ADM{s.admission_date.year}-", 4)
    db.session.add(s)
    db.session.flush()
    audit("create", "student", s.id, f"{s.admission_no} {s.name} -> {cls.name}")
    db.session.commit()
    return jsonify(student_dict(s)), 201


@bp.put("/students/<int:sid>")
@permission_required("students.manage")
def update_student(sid):
    s = get_or_404(Student, sid, "Student")
    data = body()
    require(data, "first_name", "last_name", "gender", "dob")
    _apply_student_fields(s, data)
    status = data.get("status", s.status)
    if status not in STUDENT_STATUSES:
        raise ApiError("Invalid status", fields={"status": "Invalid"})
    new_class = parse_int(data.get("class_id"), "class_id", required=False)
    if status in LEAVING:
        new_class = None
    elif new_class is None:
        raise ApiError("An enrolled student must belong to a class", fields={"class_id": "Required"})
    elif new_class != s.class_id or s.status != "active":
        _check_capacity(new_class, exclude_student_id=s.id)
    if data.get("guardian_id") or data.get("guardian"):
        s.guardian = _resolve_guardian(data)
    changes = []
    if s.status != status:
        changes.append(f"status {s.status}->{status}")
    if s.class_id != new_class:
        changes.append(f"class {s.class_id}->{new_class}")
    s.status, s.class_id = status, new_class
    audit("update", "student", s.id, "; ".join(changes) or "profile")
    db.session.commit()
    return jsonify(student_dict(s))


@bp.delete("/students/<int:sid>")
@permission_required("students.manage")
def delete_student(sid):
    s = get_or_404(Student, sid, "Student")
    has_records = (Invoice.query.filter_by(student_id=sid).first() or Payment.query.filter_by(student_id=sid).first()
                   or Mark.query.filter_by(student_id=sid).first() or Attendance.query.filter_by(student_id=sid).first())
    if has_records:
        raise ApiError("Student has academic or financial history. Change status to Withdrawn instead of deleting.", 409)
    Scholarship.query.filter_by(student_id=sid).delete()
    audit("delete", "student", sid, s.admission_no)
    db.session.delete(s)
    db.session.commit()
    return jsonify(ok=True)


@bp.post("/students/<int:sid>/scholarships")
@permission_required("fees.manage")
def add_scholarship(sid):
    s = get_or_404(Student, sid, "Student")
    data = body()
    require(data, "name", "percent")
    pct = parse_float(data["percent"], "percent", 1, 100)
    active_total = sum(x.percent for x in s.scholarships if x.active)
    if active_total + pct > 100:
        raise ApiError(f"Scholarships would total {active_total + pct:g}%; maximum is 100%")
    sch = Scholarship(student=s, name=clean_str(data["name"], 60), percent=pct)
    db.session.add(sch)
    db.session.flush()
    audit("create", "scholarship", sch.id, f"{s.admission_no} {sch.name} {pct}%")
    db.session.commit()
    return jsonify(id=sch.id), 201


@bp.put("/scholarships/<int:xid>")
@permission_required("fees.manage")
def toggle_scholarship(xid):
    sch = get_or_404(Scholarship, xid, "Scholarship")
    sch.active = bool(body().get("active"))
    audit("update", "scholarship", sch.id, f"active={sch.active}")
    db.session.commit()
    return jsonify(ok=True)


# --------------------------------------------------------------------------- #
# Guardians
# --------------------------------------------------------------------------- #
def guardian_dict(g):
    return {"id": g.id, "name": g.name, "phone": g.phone, "email": g.email,
            "relationship": g.relationship, "address": g.address,
            "has_account": g.user_id is not None, "username": g.user.username if g.user else None,
            "children": [{"id": s.id, "name": s.name, "class": s.school_class.name if s.school_class else None,
                          "status": s.status} for s in g.students]}


@bp.get("/guardians")
@permission_required("guardians.view")
def list_guardians():
    q = Guardian.query
    term = request.args.get("q", "").strip()
    if term:
        q = q.filter(or_(Guardian.name.ilike(f"%{term}%"), Guardian.phone.ilike(f"%{term}%")))
    return jsonify(items=[guardian_dict(g) for g in q.order_by(Guardian.name).limit(500)])


@bp.post("/guardians")
@permission_required("guardians.manage")
def create_guardian():
    data = body()
    require(data, "name", "phone")
    if Guardian.query.filter_by(phone=clean_str(data["phone"], 30)).first():
        raise ApiError("A guardian with this phone number already exists", fields={"phone": "Duplicate"})
    g = Guardian(name=clean_str(data["name"], 120), phone=clean_str(data["phone"], 30),
                 email=clean_str(data.get("email"), 120), relationship=clean_str(data.get("relationship"), 30) or "Parent",
                 address=clean_str(data.get("address"), 200))
    db.session.add(g)
    db.session.flush()
    audit("create", "guardian", g.id, g.name)
    db.session.commit()
    return jsonify(guardian_dict(g)), 201


@bp.put("/guardians/<int:gid>")
@permission_required("guardians.manage")
def update_guardian(gid):
    g = get_or_404(Guardian, gid, "Guardian")
    data = body()
    require(data, "name", "phone")
    phone = clean_str(data["phone"], 30)
    if Guardian.query.filter(Guardian.phone == phone, Guardian.id != gid).first():
        raise ApiError("Another guardian has this phone number", fields={"phone": "Duplicate"})
    g.name, g.phone = clean_str(data["name"], 120), phone
    g.email = clean_str(data.get("email"), 120)
    g.relationship = clean_str(data.get("relationship"), 30) or "Parent"
    g.address = clean_str(data.get("address"), 200)
    audit("update", "guardian", g.id)
    db.session.commit()
    return jsonify(guardian_dict(g))


@bp.post("/guardians/<int:gid>/account")
@permission_required("guardians.manage")
def guardian_account(gid):
    g = get_or_404(Guardian, gid, "Guardian")
    if g.user_id:
        raise ApiError("This guardian already has a portal account")
    data = body()
    user = _new_user(data, "parent", g.name, g.email)
    g.user = user
    db.session.commit()
    return jsonify(guardian_dict(g)), 201


# --------------------------------------------------------------------------- #
# Staff
# --------------------------------------------------------------------------- #
def staff_dict(st, full=False):
    d = {"id": st.id, "staff_no": st.staff_no, "first_name": st.first_name, "last_name": st.last_name,
         "name": st.name, "gender": st.gender, "phone": st.phone, "email": st.email,
         "position": st.position, "department": st.department, "hire_date": iso(st.hire_date),
         "status": st.status, "username": st.user.username if st.user else None,
         "role": st.user.role if st.user else None}
    if full:
        d["salary"] = money(st.salary_cents)
        d["class_teacher_of"] = [c.name for c in SchoolClass.query.filter_by(class_teacher_id=st.id)]
        d["teaches"] = [f"{cs.subject.name} ({cs.school_class.name})"
                        for cs in ClassSubject.query.filter_by(teacher_id=st.id)]
    return d


def _apply_staff_fields(st, data):
    st.first_name = clean_str(data["first_name"], 60)
    st.last_name = clean_str(data["last_name"], 60)
    st.gender = clean_str(data.get("gender"), 10)
    st.phone = clean_str(data.get("phone"), 30)
    st.email = clean_str(data.get("email"), 120)
    st.position = clean_str(data["position"], 60)
    st.department = clean_str(data.get("department"), 60)
    st.hire_date = parse_date(data.get("hire_date"), "hire_date", required=False) or date.today()
    st.salary_cents = to_cents(data.get("salary") or 0, "salary", allow_zero=True)


@bp.get("/staff")
@login_required
def list_staff():
    if current_user.role == "parent":
        raise ApiError("Forbidden", 403)
    q = Staff.query
    if request.args.get("status", "active") != "all":
        q = q.filter_by(status=request.args.get("status", "active"))
    # Salaries are payroll information.
    full = has_any("payroll.view", "staff.manage")
    return jsonify(items=[staff_dict(s, full) for s in q.order_by(Staff.last_name)])


@bp.post("/staff")
@permission_required("staff.manage")
def create_staff():
    data = body()
    require(data, "first_name", "last_name", "position")
    st = Staff(staff_no=next_number("staff", "STF", 3))
    _apply_staff_fields(st, data)
    db.session.add(st)
    acct = data.get("account") or {}
    if acct.get("username"):
        role = acct.get("role", "teacher")
        if role == "parent":
            raise ApiError("Staff accounts cannot have the parent role")
        st.user = _new_user(acct, role, st.name, st.email)
    db.session.flush()
    audit("create", "staff", st.id, f"{st.staff_no} {st.name}")
    db.session.commit()
    return jsonify(staff_dict(st, True)), 201


@bp.put("/staff/<int:sid>")
@permission_required("staff.manage")
def update_staff(sid):
    st = get_or_404(Staff, sid, "Staff member")
    data = body()
    require(data, "first_name", "last_name", "position")
    _apply_staff_fields(st, data)
    status = data.get("status", st.status)
    if status not in ("active", "on_leave", "left"):
        raise ApiError("Invalid status", fields={"status": "Invalid"})
    if status == "left" and st.status != "left":
        duties = staff_dict(st, True)
        if duties["class_teacher_of"] or duties["teaches"]:
            raise ApiError("Reassign this person's classes and subjects before marking them as left: "
                           + ", ".join(duties["class_teacher_of"] + duties["teaches"]), 409)
        if st.user:
            st.user.active = False  # leavers lose system access
    st.status = status
    acct = data.get("account") or {}
    if acct.get("username") and not st.user:
        st.user = _new_user(acct, acct.get("role", "teacher"), st.name, st.email)
    audit("update", "staff", st.id)
    db.session.commit()
    return jsonify(staff_dict(st, True))


# --------------------------------------------------------------------------- #
# Users
# --------------------------------------------------------------------------- #
def _new_user(data, role, full_name, email=None):
    require(data, "username", "password")
    if role not in ROLES:
        raise ApiError("Invalid role", fields={"role": "Invalid"})
    username = str(data["username"]).strip().lower()
    if not username.replace(".", "").replace("_", "").isalnum() or len(username) < 3:
        raise ApiError("Username must be 3+ letters/numbers (dots and underscores allowed)",
                       fields={"username": "Invalid"})
    if User.query.filter_by(username=username).first():
        raise ApiError("Username is already taken", fields={"username": "Taken"})
    validate_password(data["password"])
    if role == "admin" and current_user.role != "admin":
        raise ApiError("Only an administrator can create administrator accounts", 403)
    user = User(username=username, full_name=full_name, email=email, role=role)
    user.set_password(data["password"])
    db.session.add(user)
    db.session.flush()
    if data.get("role_id") and role not in ("admin", "parent"):
        _assign_role(user, data["role_id"])
    audit("create", "user", user.id, f"{username} ({role})")
    return user


# --------------------------------------------------------------------------- #
# Roles and permissions
# --------------------------------------------------------------------------- #
def _grantable(perms):
    """A non-administrator may only hand out permissions they hold themselves."""
    if current_user.role == "admin":
        return
    beyond = set(perms) - permissions.current()
    if beyond:
        raise ApiError("You can only grant permissions you have yourself: " +
                       ", ".join(permissions.LABEL[p] for p in sorted(beyond)), 403)


def _assign_role(user, role_id):
    role = get_or_404(Role, parse_int(role_id, "role_id"), "Role")
    _grantable(permissions.expand(permissions.split(role.permissions)))
    user.custom_role = None if role.is_system else role
    user.role = role.base


def role_dict(r):
    return {"id": r.id, "key": r.key, "name": r.name, "description": r.description, "base": r.base,
            "base_label": permissions.BASES.get(r.base, r.base), "is_system": r.is_system,
            "permissions": sorted(permissions.expand(permissions.split(r.permissions))),
            "users": User.query.filter((User.role_id == r.id) | ((User.role_id.is_(None)) & (User.role == r.key))).count()
            if r.is_system else User.query.filter_by(role_id=r.id).count()}


@bp.get("/permissions")
@permission_required("users.manage")
def list_permissions():
    roles = Role.query.order_by(Role.is_system.desc(), Role.name).all()
    return jsonify(catalog=permissions.catalog(), roles=[role_dict(r) for r in roles],
                   bases=[{"value": k, "label": v} for k, v in permissions.BASES.items()])


def _apply_role(r, data):
    require(data, "name")
    name = clean_str(data["name"], 60)
    clash = Role.query.filter(Role.name == name, Role.id != (r.id or -1)).first()
    if clash:
        raise ApiError("A role with that name already exists", fields={"name": "Taken"})
    r.name = name
    r.description = clean_str(data.get("description"), 200)
    if not r.is_system:
        if data.get("base") not in permissions.BASES:
            raise ApiError("Choose what the role is based on", fields={"base": "Required"})
        r.base = data["base"]
    perms = permissions.validate(data.get("permissions") or [])
    _grantable(permissions.expand(perms))
    r.permissions = ",".join(perms)


@bp.post("/roles")
@permission_required("users.manage")
def create_role():
    data = body()
    r = Role(key="custom-" + next_number("role", "", 4), is_system=False)
    _apply_role(r, data)
    db.session.add(r)
    db.session.flush()
    audit("create", "role", r.id, f"{r.name}: {r.permissions}")
    db.session.commit()
    return jsonify(role_dict(r)), 201


@bp.put("/roles/<int:rid>")
@permission_required("users.manage")
def update_role(rid):
    r = get_or_404(Role, rid, "Role")
    _apply_role(r, body())
    audit("update", "role", r.id, f"{r.name}: {r.permissions}")
    db.session.commit()
    return jsonify(role_dict(r))


@bp.delete("/roles/<int:rid>")
@permission_required("users.manage")
def delete_role(rid):
    r = get_or_404(Role, rid, "Role")
    if r.is_system:
        raise ApiError("Built-in roles can't be deleted; edit their permissions instead", 409)
    if User.query.filter_by(role_id=r.id).first():
        raise ApiError("Move this role's users to another role before deleting it", 409)
    db.session.delete(r)
    audit("delete", "role", rid, r.name)
    db.session.commit()
    return jsonify(ok=True)


@bp.get("/users")
@permission_required("users.manage")
def list_users():
    return jsonify(items=[u.to_dict() for u in User.query.order_by(User.role, User.username)])


@bp.post("/users")
@permission_required("users.manage")
def create_user():
    data = body()
    require(data, "full_name", "role")
    user = _new_user(data, data["role"], clean_str(data["full_name"], 120), clean_str(data.get("email"), 120))
    db.session.commit()
    return jsonify(user.to_dict()), 201


@bp.put("/users/<int:uid>")
@permission_required("users.manage")
def update_user(uid):
    user = get_or_404(User, uid, "User")
    data = body()
    # Administrator accounts (including their passwords) are managed by administrators only.
    if user.role == "admin" and current_user.role != "admin":
        raise ApiError("Only an administrator can change an administrator's account", 403)
    if "active" in data:
        active = bool(data["active"])
        if not active and user.id == current_user.id:
            raise ApiError("You cannot deactivate your own account")
        if not active and user.role == "admin" and User.query.filter_by(role="admin", active=True).count() <= 1:
            raise ApiError("At least one active administrator is required")
        user.active = active
    if "role" in data and data["role"] != user.role:
        if data["role"] not in ROLES:
            raise ApiError("Invalid role")
        if user.id == current_user.id:
            raise ApiError("You cannot change your own role")
        if user.guardian and data["role"] != "parent" or user.staff and data["role"] == "parent":
            raise ApiError("Role does not match the linked staff/guardian record")
        if "admin" in (data["role"], user.role) and current_user.role != "admin":
            raise ApiError("Only an administrator can grant or remove administrator access", 403)
        user.role = data["role"]
        user.custom_role = None
    if "role_id" in data and data["role_id"]:
        if user.role in ("admin", "parent"):
            raise ApiError("Administrators and parents don't use custom roles")
        if user.id == current_user.id:
            raise ApiError("You cannot change your own role")
        _assign_role(user, data["role_id"])
    for key in ("extra_permissions", "removed_permissions"):
        if key in data:
            if user.id == current_user.id:
                raise ApiError("You cannot change your own permissions")
            if user.role in ("admin", "parent"):
                raise ApiError("Administrators always have every permission; parents use the parent portal")
            perms = permissions.validate(data[key] or [], key)
            if key == "extra_permissions":
                _grantable(permissions.expand(perms))
            setattr(user, key, ",".join(perms) or None)
    if data.get("full_name"):
        user.full_name = clean_str(data["full_name"], 120)
    if "email" in data:
        user.email = clean_str(data["email"], 120)
    if data.get("password"):
        validate_password(data["password"])
        user.set_password(data["password"])
    audit("update", "user", user.id, ", ".join(k for k in data if k != "password") +
          (" password reset" if data.get("password") else ""))
    db.session.commit()
    return jsonify(user.to_dict())
