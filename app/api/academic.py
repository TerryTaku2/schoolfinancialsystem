"""Academic years, terms, classes, subjects, timetable, attendance, exams and results."""
import re
from datetime import date, timedelta

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required

from .. import db
from ..models import (ATTENDANCE_STATUSES, AcademicYear, Attendance, ClassSubject, Exam, FeeItem,
                      GradeBand, Invoice, Mark, SchoolClass, Staff, Student, Subject, Term,
                      TimetableSlot)
from ..services import academics, structure
from ..services.access import (can_enter_marks, can_take_attendance, ensure_class_access,
                               ensure_student_access, teacher_class_ids, teacher_staff_id)
from ..utils import (ApiError, audit, body, clean_str, current_term, get_or_404, iso, parse_date,
                     parse_float, parse_int, require, permission_required)

from ..services.permissions import has, has_any  # noqa: E402

bp = Blueprint("academic", __name__)

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]


def _overlaps(a_start, a_end, b_start, b_end):
    return a_start <= b_end and b_start <= a_end


# --------------------------------------------------------------------------- #
# Academic years & terms
# --------------------------------------------------------------------------- #
def term_dict(t):
    return {"id": t.id, "year_id": t.year_id, "year": t.year.name, "name": t.name, "label": t.label,
            "start_date": iso(t.start_date), "end_date": iso(t.end_date), "is_current": t.is_current}


@bp.get("/years")
@login_required
def list_years():
    years = AcademicYear.query.order_by(AcademicYear.start_date.desc()).all()
    return jsonify(items=[{"id": y.id, "name": y.name, "start_date": iso(y.start_date),
                           "end_date": iso(y.end_date), "is_current": y.is_current,
                           "terms": [term_dict(t) for t in y.terms]} for y in years])


@bp.post("/years")
@permission_required("academics.manage")
def create_year():
    data = body()
    require(data, "name", "start_date", "end_date")
    start, end = parse_date(data["start_date"], "start_date"), parse_date(data["end_date"], "end_date")
    if end <= start:
        raise ApiError("End date must be after start date", fields={"end_date": "Before start"})
    if (end - start).days > 400:
        raise ApiError("An academic year cannot be longer than 400 days")
    for y in AcademicYear.query:
        if _overlaps(start, end, y.start_date, y.end_date):
            raise ApiError(f"Dates overlap academic year {y.name}")
    if AcademicYear.query.filter_by(name=clean_str(data["name"])).first():
        raise ApiError("A year with this name exists", fields={"name": "Duplicate"})
    y = AcademicYear(name=clean_str(data["name"], 20), start_date=start, end_date=end)
    db.session.add(y)
    db.session.flush()
    audit("create", "year", y.id, y.name)
    db.session.commit()
    return jsonify(id=y.id), 201


@bp.post("/terms")
@permission_required("academics.manage")
def create_term():
    data = body()
    require(data, "year_id", "name", "start_date", "end_date")
    year = get_or_404(AcademicYear, parse_int(data["year_id"], "year_id"), "Academic year")
    start, end = parse_date(data["start_date"], "start_date"), parse_date(data["end_date"], "end_date")
    if end <= start:
        raise ApiError("End date must be after start date", fields={"end_date": "Before start"})
    if start < year.start_date or end > year.end_date:
        raise ApiError(f"Term must fall within {year.name} ({year.start_date} to {year.end_date})")
    for t in year.terms:
        if _overlaps(start, end, t.start_date, t.end_date):
            raise ApiError(f"Dates overlap {t.name}")
        if t.name.lower() == str(data["name"]).strip().lower():
            raise ApiError("This year already has a term with that name", fields={"name": "Duplicate"})
    t = Term(year=year, name=clean_str(data["name"], 30), start_date=start, end_date=end)
    db.session.add(t)
    db.session.flush()
    audit("create", "term", t.id, t.label)
    db.session.commit()
    return jsonify(term_dict(t)), 201


@bp.put("/terms/<int:tid>/current")
@permission_required("academics.manage")
def set_current_term(tid):
    t = get_or_404(Term, tid, "Term")
    Term.query.update({Term.is_current: False})
    AcademicYear.query.update({AcademicYear.is_current: False})
    t.is_current = True
    t.year.is_current = True
    audit("current", "term", t.id, t.label)
    db.session.commit()
    return jsonify(term_dict(t))


@bp.delete("/terms/<int:tid>")
@permission_required("academics.manage")
def delete_term(tid):
    t = get_or_404(Term, tid, "Term")
    if t.is_current:
        raise ApiError("Cannot delete the current term")
    if Exam.query.filter_by(term_id=tid).first() or Invoice.query.filter_by(term_id=tid).first() \
            or FeeItem.query.filter_by(term_id=tid).first():
        raise ApiError("Term has exams, fees or invoices and cannot be deleted", 409)
    audit("delete", "term", tid, t.label)
    db.session.delete(t)
    db.session.commit()
    return jsonify(ok=True)


# --------------------------------------------------------------------------- #
# Classes & subjects
# --------------------------------------------------------------------------- #
def class_dict(c):
    return {"id": c.id, "name": c.name, "level": c.level, "grade_code": c.grade_code,
            "level_label": structure.LONG_LABEL.get(c.grade_code, f"Level {c.level}"),
            "section": structure.section_of(c), "stream": c.stream, "capacity": c.capacity,
            "room": c.room, "class_teacher_id": c.class_teacher_id,
            "class_teacher": c.class_teacher.name if c.class_teacher else None,
            "enrolled": len(c.active_students()),
            "subjects": [{"id": cs.id, "subject_id": cs.subject_id, "subject": cs.subject.name,
                          "code": cs.subject.code, "teacher_id": cs.teacher_id,
                          "teacher": cs.teacher.name if cs.teacher else None}
                         for cs in sorted(c.subjects, key=lambda cs: cs.subject.name)]}


@bp.get("/classes")
@login_required
def list_classes():
    q = SchoolClass.query
    if request.args.get("mine") and not has("academics.view"):
        q = q.filter(SchoolClass.id.in_(teacher_class_ids() or {-1}))
    return jsonify(items=[class_dict(c) for c in q.order_by(SchoolClass.level, SchoolClass.stream)])


def _validate_class_teacher(staff_id, class_id=None):
    if staff_id is None:
        return None
    st = get_or_404(Staff, staff_id, "Staff member")
    if st.status != "active":
        raise ApiError(f"{st.name} is not an active staff member")
    other = SchoolClass.query.filter(SchoolClass.class_teacher_id == st.id, SchoolClass.id != class_id).first()
    if other:
        raise ApiError(f"{st.name} is already class teacher of {other.name}", fields={"class_teacher_id": "Already assigned"})
    return st.id


def _apply_class(c, data):
    require(data, "grade_code", "stream", "capacity")
    code = data["grade_code"]
    allowed = structure.codes_for(structure.school_type())
    if code not in allowed:
        raise ApiError(f"This school offers {structure.LABEL[allowed[0]]} to {structure.LABEL[allowed[-1]]}. "
                       "Change the school type in Settings to add other levels.", fields={"grade_code": "Not offered"})
    c.grade_code, c.level = code, structure.ORDER[code]
    c.stream = clean_str(data["stream"], 10).upper()
    c.name = clean_str(data.get("name"), 40) or structure.class_name(code, c.stream)
    c.capacity = parse_int(data["capacity"], "capacity", minimum=1, maximum=200)
    c.room = clean_str(data.get("room"), 30)
    c.class_teacher_id = _validate_class_teacher(parse_int(data.get("class_teacher_id"), "class_teacher_id",
                                                           required=False), c.id)
    dup = SchoolClass.query.filter(((SchoolClass.name == c.name) |
                                    ((SchoolClass.level == c.level) & (SchoolClass.stream == c.stream))),
                                   SchoolClass.id != c.id).first()
    if dup:
        raise ApiError(f"Class {dup.name} already uses this name or level/stream")


@bp.post("/classes")
@permission_required("academics.manage")
def create_class():
    c = SchoolClass()
    _apply_class(c, body())
    db.session.add(c)
    db.session.flush()
    audit("create", "class", c.id, c.name)
    db.session.commit()
    return jsonify(class_dict(c)), 201


@bp.put("/classes/<int:cid>")
@permission_required("academics.manage")
def update_class(cid):
    c = get_or_404(SchoolClass, cid, "Class")
    _apply_class(c, body())
    if c.capacity < len(c.active_students()):
        raise ApiError(f"Capacity cannot be below current enrolment ({len(c.active_students())})",
                       fields={"capacity": "Below enrolment"})
    audit("update", "class", c.id, c.name)
    db.session.commit()
    return jsonify(class_dict(c))


@bp.delete("/classes/<int:cid>")
@permission_required("academics.manage")
def delete_class(cid):
    c = get_or_404(SchoolClass, cid, "Class")
    if c.students or Attendance.query.filter_by(class_id=cid).first():
        raise ApiError("Class has students or attendance history and cannot be deleted", 409)
    audit("delete", "class", cid, c.name)
    db.session.delete(c)
    db.session.commit()
    return jsonify(ok=True)


@bp.post("/classes/<int:cid>/subjects")
@permission_required("academics.manage")
def assign_subject(cid):
    c = get_or_404(SchoolClass, cid, "Class")
    data = body()
    require(data, "subject_id")
    subj = get_or_404(Subject, parse_int(data["subject_id"], "subject_id"), "Subject")
    teacher_id = parse_int(data.get("teacher_id"), "teacher_id", required=False)
    cs = ClassSubject.query.filter_by(class_id=c.id, subject_id=subj.id).first()
    if cs is None:
        cs = ClassSubject(class_id=c.id, subject_id=subj.id)
        db.session.add(cs)
    if teacher_id:
        t = get_or_404(Staff, teacher_id, "Teacher")
        if t.status != "active":
            raise ApiError(f"{t.name} is not active")
        # Re-check existing timetable slots so a teacher change can't create a clash.
        for slot in cs.slots if cs.id else []:
            _check_slot_clash(cs, slot.day, slot.start_time, slot.end_time, slot.room, exclude_id=slot.id,
                              teacher_id=t.id)
    cs.teacher_id = teacher_id
    audit("assign", "class_subject", c.id, f"{subj.code} -> teacher {teacher_id}")
    db.session.commit()
    return jsonify(class_dict(c))


@bp.delete("/class-subjects/<int:csid>")
@permission_required("academics.manage")
def remove_subject(csid):
    cs = get_or_404(ClassSubject, csid, "Class subject")
    student_ids = [s.id for s in cs.school_class.students]
    if Mark.query.filter(Mark.subject_id == cs.subject_id, Mark.student_id.in_(student_ids or [-1])).first():
        raise ApiError("Marks exist for this subject in this class; it cannot be removed", 409)
    audit("unassign", "class_subject", cs.class_id, cs.subject.code)
    db.session.delete(cs)
    db.session.commit()
    return jsonify(ok=True)


@bp.get("/subjects")
@login_required
def list_subjects():
    return jsonify(items=[{"id": s.id, "code": s.code, "name": s.name,
                           "classes": ClassSubject.query.filter_by(subject_id=s.id).count()}
                          for s in Subject.query.order_by(Subject.name)])


@bp.post("/subjects")
@permission_required("academics.manage")
def create_subject():
    data = body()
    require(data, "code", "name")
    code = clean_str(data["code"], 12).upper()
    if Subject.query.filter_by(code=code).first():
        raise ApiError("Subject code already exists", fields={"code": "Duplicate"})
    s = Subject(code=code, name=clean_str(data["name"], 60))
    db.session.add(s)
    db.session.flush()
    audit("create", "subject", s.id, s.code)
    db.session.commit()
    return jsonify(id=s.id), 201


@bp.delete("/subjects/<int:sid>")
@permission_required("academics.manage")
def delete_subject(sid):
    s = get_or_404(Subject, sid, "Subject")
    if ClassSubject.query.filter_by(subject_id=sid).first() or Mark.query.filter_by(subject_id=sid).first():
        raise ApiError("Subject is assigned to classes or has marks", 409)
    db.session.delete(s)
    audit("delete", "subject", sid, s.code)
    db.session.commit()
    return jsonify(ok=True)


# --------------------------------------------------------------------------- #
# Timetable
# --------------------------------------------------------------------------- #
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def slot_dict(s):
    cs = s.class_subject
    return {"id": s.id, "day": s.day, "day_name": DAYS[s.day], "start_time": s.start_time,
            "end_time": s.end_time, "room": s.room or cs.school_class.room,
            "class_subject_id": cs.id, "class_id": cs.class_id, "class": cs.school_class.name,
            "subject": cs.subject.name, "code": cs.subject.code,
            "teacher_id": cs.teacher_id, "teacher": cs.teacher.name if cs.teacher else None}


def _check_slot_clash(cs, day, start, end, room, exclude_id=None, teacher_id=None):
    teacher_id = teacher_id if teacher_id is not None else cs.teacher_id
    room = room or cs.school_class.room
    for other in TimetableSlot.query.filter_by(day=day):
        if other.id == exclude_id or not (start < other.end_time and other.start_time < end):
            continue
        ocs = other.class_subject
        when = f"{DAYS[day]} {other.start_time}-{other.end_time}"
        if ocs.class_id == cs.class_id:
            raise ApiError(f"{cs.school_class.name} already has {ocs.subject.name} on {when}", 409)
        if teacher_id and ocs.teacher_id == teacher_id:
            raise ApiError(f"{ocs.teacher.name} is teaching {ocs.school_class.name} on {when}", 409)
        if room and (other.room or ocs.school_class.room) == room:
            raise ApiError(f"Room {room} is used by {ocs.school_class.name} on {when}", 409)


@bp.get("/timetable")
@login_required
def get_timetable():
    q = TimetableSlot.query.join(ClassSubject)
    if current_user.role == "parent":
        ids = {s.class_id for s in current_user.guardian.students if s.class_id} if current_user.guardian else set()
        q = q.filter(ClassSubject.class_id.in_(ids or {-1}))
    if request.args.get("class_id"):
        q = q.filter(ClassSubject.class_id == parse_int(request.args["class_id"], "class_id"))
    elif request.args.get("teacher_id"):
        q = q.filter(ClassSubject.teacher_id == parse_int(request.args["teacher_id"], "teacher_id"))
    elif current_user.role != "parent" and not has("academics.view"):
        q = q.filter(ClassSubject.teacher_id == teacher_staff_id())
    slots = q.order_by(TimetableSlot.day, TimetableSlot.start_time).all()
    return jsonify(items=[slot_dict(s) for s in slots], days=DAYS)


@bp.post("/timetable")
@permission_required("academics.manage")
def create_slot():
    data = body()
    require(data, "class_subject_id", "day", "start_time", "end_time")
    cs = get_or_404(ClassSubject, parse_int(data["class_subject_id"], "class_subject_id"), "Class subject")
    day = parse_int(data["day"], "day", minimum=0, maximum=4)
    start, end = str(data["start_time"]), str(data["end_time"])
    if not TIME_RE.match(start) or not TIME_RE.match(end):
        raise ApiError("Times must be HH:MM (24h)")
    if end <= start:
        raise ApiError("End time must be after start time", fields={"end_time": "Before start"})
    if start < "07:00" or end > "17:00":
        raise ApiError("Lessons must be within school hours (07:00-17:00)")
    room = clean_str(data.get("room"), 30)
    _check_slot_clash(cs, day, start, end, room)
    slot = TimetableSlot(class_subject=cs, day=day, start_time=start, end_time=end, room=room)
    db.session.add(slot)
    db.session.flush()
    audit("create", "timetable", slot.id, f"{cs.school_class.name} {cs.subject.code} {DAYS[day]} {start}")
    db.session.commit()
    return jsonify(slot_dict(slot)), 201


@bp.delete("/timetable/<int:slot_id>")
@permission_required("academics.manage")
def delete_slot(slot_id):
    slot = get_or_404(TimetableSlot, slot_id, "Slot")
    db.session.delete(slot)
    audit("delete", "timetable", slot_id)
    db.session.commit()
    return jsonify(ok=True)


# --------------------------------------------------------------------------- #
# Attendance
# --------------------------------------------------------------------------- #
def _validate_school_day(d):
    if d > date.today():
        raise ApiError("Attendance cannot be recorded for a future date", fields={"date": "Future date"})
    if d.weekday() >= 5:
        raise ApiError("Attendance is only taken on school days (Mon-Fri)", fields={"date": "Weekend"})
    term = Term.query.filter(Term.start_date <= d, Term.end_date >= d).first()
    if not term:
        raise ApiError("This date is outside every term (school holiday)", fields={"date": "Holiday"})
    return term


@bp.get("/attendance")
@login_required
def attendance_roster():
    cid = parse_int(request.args.get("class_id"), "class_id")
    ensure_class_access(cid)
    d = parse_date(request.args.get("date") or date.today().isoformat(), "date")
    cls = get_or_404(SchoolClass, cid, "Class")
    existing = {a.student_id: a for a in Attendance.query.filter_by(class_id=cid, date=d)}
    roster = [{"student_id": s.id, "name": s.name, "admission_no": s.admission_no,
               "status": existing[s.id].status if s.id in existing else None,
               "remark": existing[s.id].remark if s.id in existing else None}
              for s in sorted(cls.active_students(), key=lambda s: (s.last_name, s.first_name))]
    return jsonify(class_name=cls.name, date=iso(d), roster=roster,
                   recorded=bool(existing), can_edit=can_take_attendance(cid))


@bp.post("/attendance")
@login_required
def save_attendance():
    data = body()
    require(data, "class_id", "date", "records")
    cid = parse_int(data["class_id"], "class_id")
    if not can_take_attendance(cid):
        raise ApiError("Only the class teacher can register attendance for this class", 403)
    d = parse_date(data["date"], "date")
    _validate_school_day(d)
    if date.today() - d > timedelta(days=7) and not has("attendance.manage"):
        raise ApiError("Attendance older than 7 days can only be corrected by someone allowed to manage attendance")
    cls = get_or_404(SchoolClass, cid, "Class")
    valid_ids = {s.id for s in cls.active_students()}
    existing = {a.student_id: a for a in Attendance.query.filter_by(date=d).filter(
        Attendance.student_id.in_(valid_ids or {-1}))}
    saved = 0
    for rec in data["records"]:
        sid = parse_int(rec.get("student_id"), "student_id")
        status = rec.get("status")
        if sid not in valid_ids:
            raise ApiError(f"Student {sid} is not an active member of {cls.name}")
        if status not in ATTENDANCE_STATUSES:
            raise ApiError(f"Invalid attendance status '{status}'")
        row = existing.get(sid) or Attendance(student_id=sid, date=d)
        row.class_id, row.status = cid, status
        row.remark = clean_str(rec.get("remark"), 120)
        row.recorded_by = current_user.id
        db.session.add(row)
        saved += 1
    missing = valid_ids - {parse_int(r.get("student_id"), "student_id") for r in data["records"]}
    if missing:
        raise ApiError(f"Attendance must be recorded for every student ({len(missing)} missing)")
    audit("record", "attendance", cid, f"{cls.name} {d} ({saved} students)")
    db.session.commit()
    return jsonify(saved=saved)


@bp.get("/attendance/summary")
@login_required
def attendance_summary():
    cid = parse_int(request.args.get("class_id"), "class_id")
    ensure_class_access(cid)
    term = current_term()
    start = parse_date(request.args.get("from"), "from", required=False) or (term.start_date if term else date.today())
    end = parse_date(request.args.get("to"), "to", required=False) or date.today()
    cls = get_or_404(SchoolClass, cid, "Class")
    students = sorted(cls.active_students(), key=lambda s: s.last_name)
    stats = academics.attendance_stats([s.id for s in students], start, end)
    return jsonify(items=[{"student_id": s.id, "name": s.name, **stats[s.id]} for s in students],
                   start=iso(start), end=iso(end))


# --------------------------------------------------------------------------- #
# Exams & marks
# --------------------------------------------------------------------------- #
def exam_dict(e):
    return {"id": e.id, "term_id": e.term_id, "term": e.term.label, "name": e.name, "weight": e.weight,
            "max_score": e.max_score, "date": iso(e.date), "locked": e.locked,
            "marks": Mark.query.filter_by(exam_id=e.id).count()}


@bp.get("/exams")
@login_required
def list_exams():
    q = Exam.query
    if request.args.get("term_id"):
        q = q.filter_by(term_id=parse_int(request.args["term_id"], "term_id"))
    return jsonify(items=[exam_dict(e) for e in q.order_by(Exam.date, Exam.id)])


@bp.post("/exams")
@permission_required("academics.manage")
def create_exam():
    data = body()
    require(data, "term_id", "name", "weight", "max_score")
    term = get_or_404(Term, parse_int(data["term_id"], "term_id"), "Term")
    weight = parse_float(data["weight"], "weight", 1, 100)
    academics.validate_term_weights(term.id, new_weight=weight)
    d = parse_date(data.get("date"), "date", required=False)
    if d and not term.start_date <= d <= term.end_date:
        raise ApiError("Exam date must fall within the term", fields={"date": "Outside term"})
    if Exam.query.filter_by(term_id=term.id, name=clean_str(data["name"], 60)).first():
        raise ApiError("This term already has an exam with that name", fields={"name": "Duplicate"})
    e = Exam(term=term, name=clean_str(data["name"], 60), weight=weight,
             max_score=parse_float(data["max_score"], "max_score", 1, 1000), date=d)
    db.session.add(e)
    db.session.flush()
    audit("create", "exam", e.id, f"{e.name} ({term.label}) {weight}%")
    db.session.commit()
    return jsonify(exam_dict(e)), 201


@bp.put("/exams/<int:eid>")
@permission_required("academics.manage")
def update_exam(eid):
    e = get_or_404(Exam, eid, "Exam")
    data = body()
    if "locked" in data and len(data) == 1:
        e.locked = bool(data["locked"])
        audit("lock" if e.locked else "unlock", "exam", e.id, e.name)
        db.session.commit()
        return jsonify(exam_dict(e))
    require(data, "name", "weight", "max_score")
    weight = parse_float(data["weight"], "weight", 1, 100)
    academics.validate_term_weights(e.term_id, exclude_id=e.id, new_weight=weight)
    new_max = parse_float(data["max_score"], "max_score", 1, 1000)
    top = db.session.query(db.func.max(Mark.score)).filter_by(exam_id=e.id).scalar()
    if top is not None and new_max < top:
        raise ApiError(f"Max score cannot be below the highest recorded mark ({top:g})")
    e.name, e.weight, e.max_score = clean_str(data["name"], 60), weight, new_max
    e.date = parse_date(data.get("date"), "date", required=False)
    audit("update", "exam", e.id, e.name)
    db.session.commit()
    return jsonify(exam_dict(e))


@bp.delete("/exams/<int:eid>")
@permission_required("academics.manage")
def delete_exam(eid):
    e = get_or_404(Exam, eid, "Exam")
    if Mark.query.filter_by(exam_id=eid).first():
        raise ApiError("Marks have been entered for this exam; it cannot be deleted", 409)
    db.session.delete(e)
    audit("delete", "exam", eid, e.name)
    db.session.commit()
    return jsonify(ok=True)


@bp.get("/marks")
@login_required
def marks_sheet():
    exam = get_or_404(Exam, parse_int(request.args.get("exam_id"), "exam_id"), "Exam")
    cid = parse_int(request.args.get("class_id"), "class_id")
    sid = parse_int(request.args.get("subject_id"), "subject_id")
    ensure_class_access(cid)
    cls = get_or_404(SchoolClass, cid, "Class")
    existing = {m.student_id: m.score for m in Mark.query.filter_by(exam_id=exam.id, subject_id=sid)}
    rows = [{"student_id": s.id, "name": s.name, "admission_no": s.admission_no, "score": existing.get(s.id)}
            for s in sorted(cls.active_students(), key=lambda s: (s.last_name, s.first_name))]
    return jsonify(exam=exam_dict(exam), rows=rows,
                   can_edit=can_enter_marks(cid, sid) and not exam.locked)


@bp.post("/marks")
@login_required
def save_marks():
    data = body()
    require(data, "exam_id", "class_id", "subject_id", "entries")
    exam = get_or_404(Exam, parse_int(data["exam_id"], "exam_id"), "Exam")
    cid = parse_int(data["class_id"], "class_id")
    sid = parse_int(data["subject_id"], "subject_id")
    if exam.locked:
        raise ApiError("This exam is locked; ask an administrator to unlock it", 409)
    if exam.date and exam.date > date.today():
        raise ApiError("Marks cannot be entered before the exam date")
    if not ClassSubject.query.filter_by(class_id=cid, subject_id=sid).first():
        raise ApiError("This class does not take that subject")
    if not can_enter_marks(cid, sid):
        raise ApiError("Only the assigned subject teacher can enter these marks", 403)
    cls = get_or_404(SchoolClass, cid, "Class")
    valid = {s.id for s in cls.active_students()}
    existing = {m.student_id: m for m in Mark.query.filter_by(exam_id=exam.id, subject_id=sid)}
    errors = {}
    for entry in data["entries"]:
        st_id = parse_int(entry.get("student_id"), "student_id")
        if st_id not in valid:
            raise ApiError(f"Student {st_id} is not in {cls.name}")
        raw = entry.get("score")
        if raw in (None, ""):
            if st_id in existing:
                db.session.delete(existing[st_id])
            continue
        try:
            score = float(raw)
        except (TypeError, ValueError):
            errors[str(st_id)] = "Not a number"
            continue
        if not 0 <= score <= exam.max_score:
            errors[str(st_id)] = f"Must be 0-{exam.max_score:g}"
            continue
        m = existing.get(st_id) or Mark(exam_id=exam.id, student_id=st_id, subject_id=sid)
        m.score, m.entered_by = round(score, 2), current_user.id
        db.session.add(m)
    if errors:
        raise ApiError("Some marks are invalid", fields=errors)
    audit("record", "marks", exam.id, f"{cls.name} subject {sid}")
    db.session.commit()
    return jsonify(ok=True)


@bp.get("/grade-bands")
@login_required
def list_bands():
    sections = structure.sections_for(structure.school_type())
    section = request.args.get("section") or sections[0]
    return jsonify(section=section, sections=[{"value": s, "label": structure.SECTIONS[s]} for s in sections],
                   items=[{"letter": b.letter, "min_score": b.min_score, "points": b.points, "remark": b.remark}
                          for b in GradeBand.query.filter_by(section=section).order_by(GradeBand.min_score.desc())])


@bp.put("/grade-bands")
@permission_required("academics.manage")
def replace_bands():
    data = body()
    section = data.get("section") or "primary"
    if section not in structure.SECTIONS:
        raise ApiError("Invalid section")
    bands = data.get("bands") or []
    letters, mins = set(), set()
    parsed = []
    for b in bands:
        letter = clean_str(b.get("letter"), 3)
        mn = parse_float(b.get("min_score"), "min_score", 0, 100)
        if not letter or letter in letters or mn in mins:
            raise ApiError("Grade letters and minimum scores must be unique")
        letters.add(letter)
        mins.add(mn)
        parsed.append(GradeBand(section=section, letter=letter, min_score=mn, points=parse_float(b.get("points", 0), "points", 0, 100),
                                remark=clean_str(b.get("remark"), 30)))
    if 0 not in mins:
        raise ApiError("One grade band must start at 0 so every score gets a grade")
    GradeBand.query.filter_by(section=section).delete()
    db.session.flush()
    db.session.add_all(parsed)
    audit("update", "grade_bands", None, f"{section}: " + ", ".join(f"{b.letter}>={b.min_score:g}" for b in parsed))
    db.session.commit()
    return jsonify(ok=True)


# --------------------------------------------------------------------------- #
# Results, report cards & promotion
# --------------------------------------------------------------------------- #
def _term_from_args():
    tid = parse_int(request.args.get("term_id"), "term_id", required=False)
    term = db.session.get(Term, tid) if tid else current_term()
    if not term:
        raise ApiError("No term selected and no current term configured")
    return term


@bp.get("/results")
@login_required
def class_results():
    cid = parse_int(request.args.get("class_id"), "class_id")
    ensure_class_access(cid)
    term = _term_from_args()
    data = academics.term_results(cid, term.id)
    rows = []
    for sid, r in data["results"].items():
        rows.append({"student_id": sid, "name": r["student"].name, "admission_no": r["student"].admission_no,
                     "scores": {str(k): {"percent": v["percent"], "grade": v["grade"]} for k, v in r["subjects"].items()},
                     "average": r["average"], "grade": r["grade"], "position": r["position"]})
    rows.sort(key=lambda r: (r["position"] is None, r["position"] or 0, r["name"]))
    subject_means = {}
    for sub in data["subjects"]:
        vals = [r["subjects"][sub.id]["percent"] for r in data["results"].values() if sub.id in r["subjects"]]
        subject_means[str(sub.id)] = round(sum(vals) / len(vals), 1) if vals else None
    return jsonify(term=term.label, class_name=data["class"].name,
                   subjects=[{"id": s.id, "code": s.code, "name": s.name} for s in data["subjects"]],
                   rows=rows, subject_means=subject_means)


@bp.get("/report-card/<int:student_id>")
@login_required
def report_card(student_id):
    st = get_or_404(Student, student_id, "Student")
    ensure_student_access(st)
    return jsonify(academics.report_card(st, _term_from_args()))


def _ids(value):
    return {parse_int(v, "student id") for v in (value or [])}


@bp.route("/promotion/preview", methods=["GET", "POST"])
@permission_required("promotion.approve")
def promotion_preview():
    """GET: default plan. POST {continue, leave, hold_back}: plan with the admin's choices."""
    data = body() if request.method == "POST" else {}
    return jsonify(items=academics.promotion_plan(_ids(data.get("continue")), _ids(data.get("leave")),
                                                  _ids(data.get("hold_back"))))


@bp.post("/promotion")
@permission_required("promotion.approve")
def promotion_apply():
    data = body()
    if data.get("confirm") != "PROMOTE":
        raise ApiError("Type PROMOTE to confirm; this moves every active student")
    plan = academics.promotion_plan(_ids(data.get("continue")), _ids(data.get("leave")), _ids(data.get("hold_back")))
    result = academics.apply_promotion(plan)
    db.session.commit()
    return jsonify(result)


# --------------------------------------------------------------------------- #
# School profile
# --------------------------------------------------------------------------- #
@bp.get("/school")
@login_required
def school_profile():
    return jsonify({**structure.profile(),
                    "school_types": [{"value": k, "label": v} for k, v in structure.SCHOOL_TYPES.items()]})


@bp.put("/school")
@permission_required("settings.manage")
def update_school_profile():
    """Change the school type, e.g. a primary school opening Form 1. Adds the new levels' subjects,
    grading scales and (optionally) one class per new level."""
    data = body()
    if "demo_login" in data:
        structure.set_demo(bool(data["demo_login"]))
        audit("update", "school", None, f"demo sign-in without passwords {'on' if data['demo_login'] else 'off'}")
    if "dual_currency" in data:
        from ..services import currency as fx
        if fx.is_dual() and not data["dual_currency"]:
            other = fx.other(fx.base())
            from ..models import JournalEntry
            if JournalEntry.query.filter_by(currency=other).first():
                raise ApiError(f"There are {other} transactions on record, so dual currency can't be switched off.", 409)
        fx.set_dual(bool(data["dual_currency"]))
        audit("update", "school", None, f"dual currency {'on' if data['dual_currency'] else 'off'}")
    old, new = structure.school_type(), data.get("school_type") or structure.school_type()
    structure.set_school_type(new)
    dropped = set(structure.codes_for(old)) - set(structure.codes_for(new))
    in_use = SchoolClass.query.filter(SchoolClass.grade_code.in_(dropped or {"-"})).first()
    if in_use:
        raise ApiError(f"{in_use.name} is at a level the new school type doesn't offer. "
                       "Move its students and delete the class first.", 409)
    structure.ensure_subjects(new)
    structure.ensure_grade_scales(structure.sections_for(new))
    if data.get("create_classes"):
        structure.create_classes(new)
    audit("update", "school", None, f"type {old} -> {new}")
    db.session.commit()
    return jsonify(structure.profile())
