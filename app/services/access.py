"""Row-level access rules: who may see or change which students/classes.

Permissions (services/permissions.py) open whole areas; without them, staff see the classes they
teach and parents see their own children.
"""
from flask_login import current_user

from .. import db
from ..models import ClassSubject, SchoolClass, Student
from ..utils import ApiError
from .permissions import has, has_any


def teacher_staff_id(user=None):
    user = user or current_user
    return user.staff.id if user.staff else None


def teacher_class_ids(user=None):
    """Classes a teacher is class teacher of, or teaches at least one subject in."""
    sid = teacher_staff_id(user)
    if not sid:
        return set()
    ids = {c.id for c in SchoolClass.query.filter_by(class_teacher_id=sid)}
    ids |= {cs.class_id for cs in ClassSubject.query.filter_by(teacher_id=sid)}
    return ids


def guardian_student_ids(user=None):
    user = user or current_user
    return {s.id for s in user.guardian.students} if user.guardian else set()


def sees_all_students():
    return has_any("students.view", "fees.view")


def visible_students_query():
    q = Student.query
    if sees_all_students():
        return q
    if current_user.role == "parent":
        return q.filter(Student.id.in_(guardian_student_ids() or {-1}))
    return q.filter(Student.class_id.in_(teacher_class_ids() or {-1}))


def ensure_student_access(student):
    if sees_all_students():
        return
    if current_user.role == "parent":
        if student.id in guardian_student_ids():
            return
    elif student.class_id in teacher_class_ids():
        return
    raise ApiError("You do not have access to this student", 403)


def ensure_class_access(class_id):
    if has_any("academics.view", "results.view", "attendance.manage", "marks.manage"):
        return
    if current_user.role != "parent" and class_id in teacher_class_ids():
        return
    raise ApiError("You do not have access to this class", 403)


def can_take_attendance(class_id):
    """The class teacher, or anyone allowed to manage attendance for every class."""
    if has("attendance.manage"):
        return True
    if current_user.role == "parent":
        return False
    cls = db.session.get(SchoolClass, class_id)
    return bool(cls and cls.class_teacher_id and cls.class_teacher_id == teacher_staff_id())


def can_enter_marks(class_id, subject_id):
    """The teacher assigned to that subject in that class, or anyone allowed to manage all marks."""
    if has("marks.manage"):
        return True
    if current_user.role == "parent":
        return False
    cs = ClassSubject.query.filter_by(class_id=class_id, subject_id=subject_id).first()
    return bool(cs and cs.teacher_id and cs.teacher_id == teacher_staff_id())
