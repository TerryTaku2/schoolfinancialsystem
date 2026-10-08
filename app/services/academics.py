"""Grading, ranking, report cards, attendance statistics and promotion."""
from collections import defaultdict
from datetime import date

from flask import current_app

from .. import db
from ..models import Attendance, Exam, GradeBand, Mark, SchoolClass, Student
from ..utils import ApiError, audit


def bands_for(cls):
    """The grading scale for a class: primary, O Level or A Level (see services/structure.py)."""
    from .structure import section_of
    bands = (GradeBand.query.filter_by(section=section_of(cls)).order_by(GradeBand.min_score.desc()).all()
             if cls is not None else [])
    return bands or GradeBand.query.filter_by(section="primary").order_by(GradeBand.min_score.desc()).all()


def grade_for(pct, bands=None):
    bands = bands if bands is not None else bands_for(None)
    for band in bands:
        if pct >= band.min_score:
            return band
    return bands[-1] if bands else None


def rank(values):
    """Competition ranking: {key: score} -> {key: position}; ties share a position (1,1,3)."""
    ordered = sorted(values.items(), key=lambda kv: kv[1], reverse=True)
    positions, prev, pos = {}, None, 0
    for i, (key, score) in enumerate(ordered, start=1):
        if score != prev:
            pos, prev = i, score
        positions[key] = pos
    return positions


def term_results(class_id, term_id):
    """Weighted subject percentages for every active student in a class.

    A subject's term % is the weighted mean of the exams the student sat:
        sum(score/max * weight) / sum(weight)
    so a missed exam does not silently count as zero; it is flagged instead.
    """
    cls = db.session.get(SchoolClass, class_id)
    exams = Exam.query.filter_by(term_id=term_id).order_by(Exam.date, Exam.id).all()
    students = sorted(cls.active_students(), key=lambda s: (s.last_name, s.first_name))
    subjects = [cs.subject for cs in sorted(cls.subjects, key=lambda cs: cs.subject.name)]
    exam_ids = [e.id for e in exams]
    student_ids = [s.id for s in students]
    marks = Mark.query.filter(Mark.exam_id.in_(exam_ids or [-1]),
                              Mark.student_id.in_(student_ids or [-1])).all()
    by_key = {(m.student_id, m.subject_id, m.exam_id): m.score for m in marks}
    # Exams actually administered per subject in this class (anyone has a mark).
    sat = defaultdict(set)
    for m in marks:
        sat[m.subject_id].add(m.exam_id)

    bands = bands_for(cls)
    results = {}
    for st in students:
        subj = {}
        for sub in subjects:
            num = den = 0.0
            missing = []
            breakdown = []
            for ex in exams:
                score = by_key.get((st.id, sub.id, ex.id))
                if score is not None:
                    num += score / ex.max_score * ex.weight
                    den += ex.weight
                    breakdown.append({"exam": ex.name, "score": score, "max": ex.max_score})
                elif ex.id in sat[sub.id]:
                    missing.append(ex.name)
            if den:
                pct = round(num / den * 100, 1)
                band = grade_for(pct, bands)
                subj[sub.id] = {"subject": sub.name, "code": sub.code, "percent": pct,
                                "grade": band.letter if band else "", "points": band.points if band else 0,
                                "remark": band.remark if band else "", "missing": missing,
                                "exams": breakdown}
        avg = round(sum(r["percent"] for r in subj.values()) / len(subj), 1) if subj else None
        results[st.id] = {"student": st, "subjects": subj, "average": avg}

    overall = rank({sid: r["average"] for sid, r in results.items() if r["average"] is not None})
    for sub in subjects:
        sub_rank = rank({sid: r["subjects"][sub.id]["percent"]
                         for sid, r in results.items() if sub.id in r["subjects"]})
        for sid, pos in sub_rank.items():
            results[sid]["subjects"][sub.id]["position"] = pos
            results[sid]["subjects"][sub.id]["out_of"] = len(sub_rank)
    for sid, r in results.items():
        r["position"] = overall.get(sid)
        r["out_of"] = len(overall)
        band = grade_for(r["average"], bands) if r["average"] is not None else None
        r["grade"] = band.letter if band else None
    return {"class": cls, "subjects": subjects, "exams": exams, "results": results}


def attendance_stats(student_ids, start, end):
    """{student_id: {present, absent, late, excused, days, rate}}; late counts as attended."""
    rows = Attendance.query.filter(Attendance.student_id.in_(student_ids or [-1]),
                                   Attendance.date >= start, Attendance.date <= end).all()
    stats = defaultdict(lambda: {"present": 0, "absent": 0, "late": 0, "excused": 0})
    for r in rows:
        stats[r.student_id][r.status] += 1
    out = {}
    for sid in student_ids:
        s = stats[sid]
        days = sum(s.values())
        # Excused absences are removed from the denominator rather than penalised.
        counted = days - s["excused"]
        s["days"] = days
        s["rate"] = round((s["present"] + s["late"]) / counted * 100, 1) if counted else None
        out[sid] = s
    return out


def report_card(student, term):
    if not student.class_id:
        raise ApiError("Student is not enrolled in a class")
    data = term_results(student.class_id, term.id)
    r = data["results"].get(student.id)
    if r is None:
        raise ApiError("No results for this student")
    att = attendance_stats([student.id], term.start_date, term.end_date)[student.id]
    from .finance import account_summary
    cls = data["class"]
    pass_mark = current_app.config["PASS_MARK"]
    subjects = list(r["subjects"].values())
    return {
        "student": {"id": student.id, "name": student.name, "admission_no": student.admission_no,
                    "gender": student.gender, "dob": student.dob.isoformat()},
        "class": cls.name,
        "class_teacher": cls.class_teacher.name if cls.class_teacher else None,
        "term": term.label,
        "subjects": subjects,
        "average": r["average"], "grade": r["grade"],
        "position": r["position"], "out_of": r["out_of"],
        "passed": sum(1 for s in subjects if s["percent"] >= pass_mark),
        "failed": sum(1 for s in subjects if s["percent"] < pass_mark),
        "attendance": att,
        "fees": account_summary(student),
        "comment": auto_comment(r["average"], att.get("rate")),
    }


def auto_comment(avg, att_rate):
    if avg is None:
        return "No assessments recorded this term."
    if avg >= 80:
        text = "Excellent performance. Keep it up."
    elif avg >= 65:
        text = "Good work; aim higher next term."
    elif avg >= 50:
        text = "Fair performance. More effort is needed."
    else:
        text = "Below expectations. Requires close support and follow-up."
    threshold = current_app.config["ATTENDANCE_THRESHOLD"]
    if att_rate is not None and att_rate < threshold:
        text += f" Attendance ({att_rate}%) is below the required {threshold:g}%."
    return text


def validate_term_weights(term_id, exclude_id=None, new_weight=0.0):
    total = sum(e.weight for e in Exam.query.filter_by(term_id=term_id) if e.id != exclude_id)
    if total + new_weight > 100.0001:
        raise ApiError(f"Exam weights for this term would total {total + new_weight:g}%; maximum is 100%",
                       fields={"weight": "Exceeds 100% total"})


def promotion_plan(continue_ids=None, leave_ids=None, hold_ids=None):
    """Work out where each active student goes next year (Zimbabwe rules, see services/structure.py).

    - Students move to the next level the school offers, same stream where possible, otherwise
      the least-full class; capacity is respected.
    - With no next level at the school (Grade 7 in a primary school, Form 6) they complete school.
    - Form 4 completes O Level: students leave unless selected (continue_ids) for Form 5.
    - leave_ids leave instead of moving up; hold_ids repeat the year.
    """
    from .structure import EXIT_LABEL, LABEL, codes_for, next_code, school_type
    offered = set(codes_for(school_type()))
    keep, leave, hold = set(continue_ids or []), set(leave_ids or []), set(hold_ids or [])
    by_code = defaultdict(list)
    for c in SchoolClass.query.order_by(SchoolClass.stream):
        by_code[c.grade_code].append(c)
    incoming = defaultdict(int)
    # Students repeating stay in their class and take up its places.
    for st in Student.query.filter(Student.id.in_(hold or {-1}), Student.status == "active"):
        if st.class_id:
            incoming[st.class_id] += 1
    plan = []
    students = (Student.query.filter_by(status="active").filter(Student.class_id.isnot(None))
                .join(SchoolClass).order_by(SchoolClass.level.desc(), Student.last_name).all())
    for st in students:
        cur = st.school_class
        code = cur.grade_code
        nxt = next_code(code) if code else None
        targets = by_code.get(nxt, []) if nxt else []
        entry = {"student_id": st.id, "student": st.name, "from": cur.name, "level": code,
                 "can_continue": code == "F4" and bool(targets), "exit": not targets or code == "F4"}
        if st.id in hold:
            entry.update(action="repeat", to=cur.name)
        elif not targets and nxt in offered and st.id not in leave and code != "F4":
            # The school teaches the next level but has no class for it yet.
            entry.update(action="blocked", to=None, exit=False,
                         reason=f"Create a {LABEL[nxt]} class first (or mark as leaving)")
        elif st.id in leave or not targets or (code == "F4" and st.id not in keep):
            entry.update(action="graduate" if entry["exit"] else "leave", to=None,
                         reason=EXIT_LABEL.get(code, "Leaves the school") if entry["exit"] else "Leaving the school")
        else:
            same = [c for c in targets if c.stream == cur.stream and incoming[c.id] < c.capacity]
            others = sorted((c for c in targets if incoming[c.id] < c.capacity), key=lambda c: incoming[c.id])
            target = same[0] if same else (others[0] if others else None)
            if target:
                incoming[target.id] += 1
                entry.update(action="promote", to=target.name, to_class_id=target.id)
            else:
                entry.update(action="blocked", to=None, reason="No class with free places at the next level")
        plan.append(entry)
    return plan


def apply_promotion(plan):
    if any(p["action"] == "blocked" for p in plan):
        raise ApiError("Some students cannot be placed. Add class capacity, or mark them as repeating or leaving.")
    counts = defaultdict(int)
    for p in plan:
        st = db.session.get(Student, p["student_id"])
        if p["action"] == "promote":
            st.class_id = p["to_class_id"]
        elif p["action"] == "graduate":
            st.status, st.class_id = "graduated", None
        elif p["action"] == "leave":
            st.status, st.class_id = "withdrawn", None
        counts[p["action"]] += 1
    audit("promote", "student", None, f"promoted {counts['promote']}, completed {counts['graduate']}, "
                                      f"left {counts['leave']}, repeating {counts['repeat']}")
    return {"promoted": counts["promote"], "graduated": counts["graduate"], "left": counts["leave"],
            "held_back": counts["repeat"]}


def at_risk_students(term):
    """Students below the attendance threshold or failing on average this term."""
    threshold = current_app.config["ATTENDANCE_THRESHOLD"]
    pass_mark = current_app.config["PASS_MARK"]
    end = min(term.end_date, date.today())
    flagged = []
    for cls in SchoolClass.query.all():
        ids = [s.id for s in cls.active_students()]
        if not ids:
            continue
        att = attendance_stats(ids, term.start_date, end)
        res = term_results(cls.id, term.id)["results"]
        for sid in ids:
            reasons = []
            rate = att[sid]["rate"]
            if rate is not None and rate < threshold:
                reasons.append(f"Attendance {rate}%")
            avg = res[sid]["average"]
            if avg is not None and avg < pass_mark:
                reasons.append(f"Average {avg}%")
            if reasons:
                st = res[sid]["student"]
                flagged.append({"student_id": sid, "student": st.name, "class": cls.name, "class_id": cls.id,
                                "reasons": reasons})
    return flagged
