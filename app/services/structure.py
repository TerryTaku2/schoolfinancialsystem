"""Zimbabwe school structure: levels, school types, curriculum defaults, grading and promotion rules.

Levels (in order; `SchoolClass.level` stores the position so classes sort naturally):

    ECD A, ECD B                 early childhood development (primary school)
    Grade 1 - Grade 7            primary; Grade 7 sits the ZIMSEC Grade 7 examinations
    Form 1 - Form 4              secondary, ZIMSEC O Level in Form 4
    Form 5 - Form 6              Lower and Upper Six, ZIMSEC A Level

School types: primary (ECD A - Grade 7), secondary (Form 1 - Form 6) or combined (all).

Promotion at the end of the year (calendar year, three terms):
- Every student moves to the next level offered by the school, same stream where possible.
- Grade 7 leaves a primary school ("completed primary"); in a combined school they go on to
  Form 1 unless marked as leaving.
- Form 4 completes O Level. Advancing to Lower Six depends on O Level results, so Form 4
  students leave by default and only those the school selects continue to Form 5.
- Form 6 completes A Level.
- Any student can be held back to repeat, or marked as leaving.

Curriculum, grading scales and term dates are defaults for a new school; schools edit them.
"""
import re
from datetime import date

from flask import current_app

from .. import db
from ..models import AcademicYear, ClassSubject, GradeBand, SchoolClass, Setting, Subject, Term

# code, label used in class names, section (grading scale), phase
LEVELS = [
    ("ECD_A", "ECD A", "primary", "primary"),
    ("ECD_B", "ECD B", "primary", "primary"),
    *[(f"G{n}", f"Grade {n}", "primary", "primary") for n in range(1, 8)],
    *[(f"F{n}", f"Form {n}", "o_level", "secondary") for n in range(1, 5)],
    ("F5", "Form 5", "a_level", "secondary"),
    ("F6", "Form 6", "a_level", "secondary"),
]
CODES = [lv[0] for lv in LEVELS]
LABEL = {lv[0]: lv[1] for lv in LEVELS}
SECTION = {lv[0]: lv[2] for lv in LEVELS}
PHASE = {lv[0]: lv[3] for lv in LEVELS}
ORDER = {code: i + 1 for i, code in enumerate(CODES)}
LONG_LABEL = {**LABEL, "F5": "Form 5 (Lower Six)", "F6": "Form 6 (Upper Six)"}

SCHOOL_TYPES = {"primary": "Primary school (ECD A to Grade 7)",
                "secondary": "Secondary school (Form 1 to Form 6)",
                "combined": "Combined primary and secondary"}
SECTIONS = {"primary": "Primary (ECD and Grades 1-7)", "o_level": "O Level (Forms 1-4)",
            "a_level": "A Level (Forms 5-6)"}
EXIT_LABEL = {"G7": "Completed primary", "F4": "Completed O Level", "F6": "Completed A Level"}


def codes_for(school_type):
    if school_type == "primary":
        return [c for c in CODES if PHASE[c] == "primary"]
    if school_type == "secondary":
        return [c for c in CODES if PHASE[c] == "secondary"]
    return list(CODES)


def sections_for(school_type):
    return [s for s in SECTIONS if any(SECTION[c] == s for c in codes_for(school_type))]


def class_name(code, stream):
    """Grade 3A, Form 2B; ECD classes read "ECD A", or "ECD A (B)" for further streams."""
    if LABEL[code][-1].isdigit():
        return f"{LABEL[code]}{stream}"
    return LABEL[code] if stream == "A" else f"{LABEL[code]} ({stream})"


def section_of(cls):
    return SECTION.get(cls.grade_code or "", "primary")


# --------------------------------------------------------------------------- #
# School profile (name, currency, type), stored per school
# --------------------------------------------------------------------------- #
def _setting(key):
    s = db.session.get(Setting, key)
    return s.value if s and s.value else None


def _set(key, value):
    s = db.session.get(Setting, key) or Setting(key=key)
    s.value = value
    db.session.add(s)


def school_name():
    from ..tenancy import current_school
    sch = current_school()
    return (sch.name if sch else None) or _setting("school_name") or current_app.config["SCHOOL_NAME"]


def currency():
    from ..tenancy import current_school
    sch = current_school()
    return (sch.currency if sch else None) or current_app.config["CURRENCY"]


DEMO_USERS = {"admin": "Administrator", "bursar": "Bursar", "teacher": "Teacher", "parent": "Parent"}


def is_demo():
    """A demo school: filled with demo data, and its demo accounts sign in without a password.

    Only true when the school was marked as a demo (seed_demo does this, or an administrator
    in Settings), never by default, so a school with real data can't be opened without passwords.
    """
    return _setting("demo_data") == "1"


def set_demo(on):
    _set("demo_data", "1" if on else "0")


def school_type():
    return _setting("school_type") or "primary"


def set_school_type(value):
    if value not in SCHOOL_TYPES:
        from ..utils import ApiError
        raise ApiError("Invalid school type", fields={"school_type": "Invalid"})
    _set("school_type", value)


def profile():
    t = school_type()
    from . import currency as fx
    rates = {c: fx.rate_dict(r) for c, r in fx.latest_rates().items()}
    return {"name": school_name(), "currency": currency(), "currencies": fx.enabled(), "dual_currency": fx.is_multi(),
            "currency_catalog": fx.catalog(), "rate_currencies": fx.rate_currencies(), "demo": is_demo(),
            "rates": rates, "rate": rates.get("ZWG"), "school_type": t,
            "school_type_label": SCHOOL_TYPES[t], "sections": sections_for(t),
            "levels": [{"code": c, "label": LONG_LABEL[c], "order": ORDER[c], "section": SECTION[c]}
                       for c in codes_for(t)]}


# --------------------------------------------------------------------------- #
# Curriculum and grading defaults
# --------------------------------------------------------------------------- #
# Heritage-based curriculum learning areas (primary) and common ZIMSEC subjects (secondary).
PRIMARY_SUBJECTS = [("ENG", "English"), ("MATH", "Mathematics"), ("SHO", "Shona"), ("NDE", "Ndebele"),
                    ("SCT", "Science and Technology"), ("HSS", "Heritage-Social Studies"),
                    ("AGR", "Agriculture"), ("PEA", "Physical Education, Visual and Performing Arts"),
                    ("ICT", "Information and Communication Technology")]
ECD_SUBJECTS = ["ENG", "MATH", "SHO", "PEA"]
GRADE_SUBJECTS = ["ENG", "MATH", "SHO", "SCT", "HSS", "AGR", "PEA"]
SECONDARY_SUBJECTS = [("ENGL", "English Language"), ("MATHS", "Mathematics"), ("CSCI", "Combined Science"),
                      ("HERS", "Heritage Studies"), ("SHON", "Shona"), ("GEOG", "Geography"), ("HIST", "History"),
                      ("POA", "Principles of Accounting"), ("COMM", "Commerce"), ("AGRI", "Agriculture"),
                      ("COMP", "Computer Science"), ("BIO", "Biology"), ("CHEM", "Chemistry"), ("PHYS", "Physics"),
                      ("LIT", "Literature in English"), ("FRS", "Family and Religious Studies")]
O_LEVEL_SUBJECTS = ["ENGL", "MATHS", "CSCI", "HERS", "SHON", "GEOG", "HIST", "POA", "AGRI", "COMP"]

# Minimum %, letter, points, remark. ZIMSEC O Level passes are A, B and C.
GRADE_SCALES = {
    "primary": [("A", 80, 4, "Excellent"), ("B", 70, 3, "Very good"), ("C", 60, 2, "Good"),
                ("D", 50, 1, "Satisfactory"), ("E", 40, 0.5, "Needs support"), ("U", 0, 0, "Unsatisfactory")],
    "o_level": [("A", 75, 1, "Distinction"), ("B", 65, 2, "Merit"), ("C", 50, 3, "Credit (pass)"),
                ("D", 40, 4, "Fail"), ("E", 30, 5, "Fail"), ("U", 0, 9, "Ungraded")],
    "a_level": [("A", 80, 5, "Excellent"), ("B", 70, 4, "Very good"), ("C", 60, 3, "Good"),
                ("D", 50, 2, "Fair"), ("E", 40, 1, "Pass"), ("O", 35, 0, "Subsidiary (O Level) pass"),
                ("F", 0, 0, "Fail")],
}


def ensure_grade_scales(sections):
    have = {s for (s,) in db.session.query(GradeBand.section).distinct()}
    for section in sections:
        if section in have:
            continue
        for letter, mn, pts, remark in GRADE_SCALES[section]:
            db.session.add(GradeBand(section=section, letter=letter, min_score=mn, points=pts, remark=remark))


def ensure_subjects(school_type):
    existing = {s.code for s in Subject.query}
    lists = []
    if school_type in ("primary", "combined"):
        lists.append(PRIMARY_SUBJECTS)
    if school_type in ("secondary", "combined"):
        lists.append(SECONDARY_SUBJECTS)
    for items in lists:
        for code, name in items:
            if code not in existing:
                db.session.add(Subject(code=code, name=name))
    db.session.flush()


def default_subject_codes(code):
    if code in ("ECD_A", "ECD_B"):
        return ECD_SUBJECTS
    if PHASE[code] == "primary":
        return GRADE_SUBJECTS
    if SECTION[code] == "o_level":
        return O_LEVEL_SUBJECTS
    return []  # A Level combinations differ per student; the school allocates them


def create_classes(school_type, streams=("A",), capacity=40):
    subjects = {s.code: s for s in Subject.query}
    made = []
    for code in codes_for(school_type):
        for stream in streams:
            if SchoolClass.query.filter_by(level=ORDER[code], stream=stream).first():
                continue
            c = SchoolClass(name=class_name(code, stream), level=ORDER[code], grade_code=code, stream=stream,
                            capacity=capacity, room=None)
            db.session.add(c)
            db.session.flush()
            for sc in default_subject_codes(code):
                if sc in subjects:
                    db.session.add(ClassSubject(class_id=c.id, subject_id=subjects[sc].id))
            made.append(c)
    return made


def zimbabwe_terms(year):
    """Typical Zimbabwe term dates (three terms in a calendar year). Schools adjust to the
    Ministry's published calendar."""
    return [("Term 1", date(year, 1, 13), date(year, 4, 10)),
            ("Term 2", date(year, 5, 12), date(year, 8, 7)),
            ("Term 3", date(year, 9, 8), date(year, 12, 4))]


def create_calendar(year=None):
    year = year or date.today().year
    if AcademicYear.query.filter_by(name=str(year)).first():
        return None
    y = AcademicYear(name=str(year), start_date=date(year, 1, 1), end_date=date(year, 12, 31),
                     is_current=not AcademicYear.query.filter_by(is_current=True).first())
    db.session.add(y)
    today = date.today()
    terms = [Term(year=y, name=n, start_date=s, end_date=e) for n, s, e in zimbabwe_terms(year)]
    current = next((t for t in terms if t.start_date <= today <= t.end_date), None) \
        or next((t for t in terms if t.start_date > today), terms[-1])
    if y.is_current:
        current.is_current = True
    db.session.add_all(terms)
    return y


def setup_new_school(school_type, streams=("A",)):
    """Defaults for a brand-new school: profile, subjects, grading scales, classes, calendar."""
    set_school_type(school_type)
    ensure_subjects(school_type)
    ensure_grade_scales(sections_for(school_type))
    create_classes(school_type, streams)
    create_calendar()


# --------------------------------------------------------------------------- #
# Upgrading databases from before the Zimbabwe structure
# --------------------------------------------------------------------------- #
# "(?![0-9])" rather than "\b" after the number, so stream letters match: "Grade 4B" is Grade 4.
_NAME_PATTERNS = [(r"\bECD\s*A(?![a-z])", "ECD_A"), (r"\bECD\s*B(?![a-z])", "ECD_B"),
                  (r"\b(?:lower\s*(?:6|six)|form\s*5)", "F5"), (r"\b(?:upper\s*(?:6|six)|form\s*6)", "F6"),
                  (r"\bform\s*([1-4])(?![0-9])", "F"), (r"\bgrade\s*([1-7])(?![0-9])", "G")]


def infer_code(cls):
    name = cls.name or ""
    for pattern, code in _NAME_PATTERNS:
        m = re.search(pattern, name, re.I)
        if m:
            return code + m.group(1) if code in ("F", "G") else code
    return f"G{min(max(cls.level, 1), 7)}"


def migrate_legacy():
    """Give every class a level code and renumber levels into catalogue order. Idempotent."""
    legacy = SchoolClass.query.filter(SchoolClass.grade_code.is_(None)).all()
    if legacy:
        for c in legacy:
            c.grade_code = infer_code(c)
        # Renumber in two steps so the (level, stream) unique constraint never clashes midway.
        all_classes = SchoolClass.query.all()
        for i, c in enumerate(all_classes):
            c.level = -(i + 1)
        db.session.flush()
        for c in all_classes:
            c.level = ORDER[c.grade_code]
        db.session.flush()
    if not _setting("school_type"):
        phases = {PHASE[c.grade_code] for c in SchoolClass.query if c.grade_code}
        _set("school_type", "combined" if len(phases) > 1 else (phases.pop() if phases else "primary"))
    ensure_grade_scales(sections_for(school_type()))


# --------------------------------------------------------------------------- #
# Promotion
# --------------------------------------------------------------------------- #
def next_code(code):
    i = CODES.index(code)
    return CODES[i + 1] if i + 1 < len(CODES) else None
