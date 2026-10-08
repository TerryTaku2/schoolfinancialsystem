"""Demo data and CLI commands.

    flask --app run seed            # wipe & load demo data
    flask --app run create-admin    # create an administrator interactively
"""
import random
from datetime import date, timedelta

import click

from . import db
from .models import (AcademicYear, Announcement, Attendance, ClassSubject, Exam, Expense, FeeItem, FixedAsset,
                     GradeBand, Guardian, Mark, SchoolClass, Scholarship, Staff, StaffPayItem, Student, Subject,
                     Setting, Term, TimetableSlot, User)
from .services import assets, finance, ledger, payroll, structure
from .tenancy import current_engine
from .utils import ApiError, next_number

DEMO_ACCOUNTS = {
    "admin": ("Admin@2026", "admin", "Grace Ndlovu"),
    "bursar": ("Bursar@2026", "bursar", "Peter Okafor"),
    "teacher": ("Teacher@2026", "teacher", None),  # linked to the first teacher
    "parent": ("Parent@2026", "parent", None),     # linked to a guardian with 2 children
}

FIRST_M = ["Tendai", "James", "Kwame", "Liam", "Tafadzwa", "Daniel", "Musa", "Ethan", "Farai", "Noah",
           "Kudzai", "Samuel", "Tinashe", "Lucas", "Brian", "Arjun", "Mateo", "Ryan", "Takunda", "Oliver"]
FIRST_F = ["Rudo", "Amara", "Chipo", "Emma", "Nyasha", "Sophia", "Zanele", "Mia", "Tariro", "Grace",
           "Ama", "Olivia", "Ruvimbo", "Isabella", "Fatima", "Priya", "Lerato", "Ava", "Nokuthula", "Chloe"]
LAST = ["Moyo", "Smith", "Dube", "Mensah", "Ncube", "Brown", "Phiri", "Banda", "Okoro", "Sibanda",
        "Johnson", "Mutasa", "Chikwanha", "Patel", "Garcia", "Nkosi", "Mahlangu", "Williams", "Zulu", "Kamau"]
DEPARTMENTS = ["Languages", "Mathematics", "Sciences", "Humanities", "Practical Subjects", "Arts & Sport"]
PERIODS = [("08:00", "08:40"), ("08:40", "09:20"), ("09:20", "10:00"), ("10:20", "11:00"),
           ("11:00", "11:40"), ("11:40", "12:20"), ("13:00", "13:40")]


def _monday(d):
    return d - timedelta(days=d.weekday())


def _school_days(start, end):
    d = start
    while d <= end:
        if d.weekday() < 5:
            yield d
        d += timedelta(days=1)


# Demo classes per school type: (level code, streams).
DEMO_CLASSES = {
    "primary": [(c, "A") for c in structure.codes_for("primary")] + [("G1", "B"), ("G7", "B")],
    "secondary": [(f"F{n}", s) for n in range(1, 5) for s in "AB"] + [("F5", "A"), ("F6", "A")],
    "combined": [(c, "A") for c in structure.CODES],
}


def _typical_age(code):
    if code.startswith("ECD"):
        return 4 if code == "ECD_A" else 5
    return 5 + int(code[1:]) if code.startswith("G") else 12 + int(code[1:])


def seed_demo(students_per_class=14, rng_seed=42, school_type="primary"):
    """Erase the current school's data and load a demo school (primary, secondary or combined)."""
    rng = random.Random(rng_seed)
    engine = current_engine()
    db.session.remove()
    db.metadata.drop_all(engine)
    db.metadata.create_all(engine)
    ledger.ensure_chart()
    from .services import permissions
    permissions.ensure_roles()
    structure.set_school_type(school_type)
    db.session.add(Setting(key="demo_data", value="1"))  # demo accounts sign in without passwords
    payroll.ensure_tax_table()
    today = date.today()

    # ---- calendar: three terms with the current one containing today ----------
    t3s = _monday(today - timedelta(days=35))
    t3e = t3s + timedelta(days=88)
    t2s = _monday(t3s - timedelta(days=120))
    t2e = t2s + timedelta(days=88)
    t1s = _monday(t2s - timedelta(days=120))
    t1e = t1s + timedelta(days=88)
    yname = str(t1s.year) if t1s.year == t3e.year else f"{t1s.year}/{t3e.year}"
    year = AcademicYear(name=yname, start_date=t1s, end_date=t3e, is_current=True)
    terms = [Term(year=year, name=f"Term {i + 1}", start_date=s, end_date=e, is_current=(i == 2))
             for i, (s, e) in enumerate([(t1s, t1e), (t2s, t2e), (t3s, t3e)])]
    db.session.add(year)

    structure.ensure_grade_scales(structure.sections_for(school_type))

    # ---- users & staff -------------------------------------------------------
    users = {}
    for uname, (pw, role, full) in DEMO_ACCOUNTS.items():
        if full:
            u = User(username=uname, full_name=full, role=role, email=f"{uname}@school.test")
            u.set_password(pw)
            db.session.add(u)
            users[uname] = u
    db.session.flush()
    for full, pos, dept, uname in [("Grace Ndlovu", "Principal", "Administration", "admin"),
                                   ("Peter Okafor", "Bursar", "Finance", "bursar")]:
        fn, ln = full.split()
        db.session.add(Staff(staff_no=next_number("staff", "STF", 3), first_name=fn, last_name=ln, position=pos,
                             department=dept, user=users[uname], salary_cents=rng.randint(1500, 2500) * 100,
                             hire_date=today - timedelta(days=rng.randint(400, 3000)), gender="Female" if fn == "Grace" else "Male"))

    structure.ensure_subjects(school_type)
    plan = DEMO_CLASSES[school_type]
    teachers = []
    for i in range(max(12, len(plan))):
        female = i % 2 == 0
        fn = rng.choice(FIRST_F if female else FIRST_M)
        ln = rng.choice(LAST)
        st = Staff(staff_no=next_number("staff", "STF", 3), first_name=fn, last_name=ln,
                   gender="Female" if female else "Male", position="Teacher",
                   department=DEPARTMENTS[i % 6], phone=f"+263 77{rng.randint(1000000, 9999999)}",
                   email=f"{fn.lower()}.{ln.lower()}@school.test", salary_cents=rng.randint(800, 1400) * 100,
                   hire_date=today - timedelta(days=rng.randint(100, 4000)))
        teachers.append(st)
    db.session.add_all(teachers)
    tu = User(username="teacher", full_name=teachers[0].name, role="teacher", email=teachers[0].email)
    tu.set_password(DEMO_ACCOUNTS["teacher"][0])
    teachers[0].user = tu

    # ---- classes & subject allocation ---------------------------------------
    db.session.flush()
    all_subjects = {sub.code: sub for sub in Subject.query}
    classes = []
    for code, stream in plan:
        classes.append(SchoolClass(name=structure.class_name(code, stream), level=structure.ORDER[code],
                                   grade_code=code, stream=stream, capacity=30, room=f"R{len(classes) + 1}"))
    db.session.add_all(classes)
    db.session.flush()
    for idx, c in enumerate(classes):
        c.class_teacher_id = teachers[idx].id
        codes = structure.default_subject_codes(c.grade_code) or ["ENGL", "MATHS", "BIO", "CHEM", "PHYS"]
        for s_idx, sc in enumerate(codes):
            # Spread subjects over the staff room; the class teacher takes the first one.
            teacher = teachers[(idx + s_idx) % len(teachers)]
            db.session.add(ClassSubject(class_id=c.id, subject_id=all_subjects[sc].id, teacher_id=teacher.id))
    db.session.flush()

    # ---- timetable (greedy, clash-free) -------------------------------------
    busy_teacher, busy_class = set(), set()
    for c in classes:
        per_week = max(1, min(5, 5 * len(PERIODS) // max(len(c.subjects), 1)))
        lessons = [cs for cs in c.subjects for _ in range(per_week)]
        rng.shuffle(lessons)
        slots = [(d, p) for d in range(5) for p in range(len(PERIODS))]
        for cs in lessons:
            for d, p in slots:
                if (c.id, d, p) in busy_class or (cs.teacher_id, d, p) in busy_teacher:
                    continue
                # Avoid the same subject twice a day where possible.
                if any(s.day == d for s in cs.slots) and len([x for x in slots if (c.id, x[0], x[1]) not in busy_class]) > 6:
                    continue
                busy_class.add((c.id, d, p))
                busy_teacher.add((cs.teacher_id, d, p))
                db.session.add(TimetableSlot(class_subject=cs, day=d, start_time=PERIODS[p][0], end_time=PERIODS[p][1]))
                break
    db.session.flush()

    # ---- guardians & students -----------------------------------------------
    students = []
    guardians = []
    for c in classes:
        for _ in range(students_per_class):
            female = rng.random() < 0.5
            ln = rng.choice(LAST)
            # ~20% of children share a guardian with an earlier sibling of the same surname.
            sibling_g = next((g for g in guardians if g.name.endswith(ln)), None) if rng.random() < 0.2 else None
            if sibling_g:
                g = sibling_g
            else:
                g = Guardian(name=f"{rng.choice(FIRST_F + FIRST_M)} {ln}", phone=f"+263 71{rng.randint(1000000, 9999999)}",
                             relationship=rng.choice(["Mother", "Father", "Guardian"]),
                             email=None, address=f"{rng.randint(1, 250)} {rng.choice(['Main', 'Park', 'Hill', 'Lake'])} Road")
                guardians.append(g)
                db.session.add(g)
            age = _typical_age(c.grade_code) + rng.choice([0, 0, 1])
            dob = today - timedelta(days=age * 365 + rng.randint(0, 300))
            adm = t1s - timedelta(days=rng.randint(0, 365 * max(_typical_age(c.grade_code) - 4, 0) + 1))
            st = Student(first_name=rng.choice(FIRST_F if female else FIRST_M), last_name=ln,
                         gender="Female" if female else "Male", dob=dob, school_class=c, guardian=g,
                         admission_date=adm,
                         admission_no=next_number(f"adm-{adm.year}", f"ADM{adm.year}-", 4))
            students.append(st)
            db.session.add(st)
    db.session.flush()

    # Parent demo account: a guardian with two children.
    fam = next(g for g in guardians if len(g.students) >= 2) if any(len(g.students) >= 2 for g in guardians) else guardians[0]
    if len(fam.students) < 2:
        students[-1].guardian = fam
    pu = User(username="parent", full_name=fam.name, role="parent", email="parent@school.test")
    pu.set_password(DEMO_ACCOUNTS["parent"][0])
    fam.user = pu

    for st in rng.sample(students, 8):
        db.session.add(Scholarship(student=st, name=rng.choice(["Academic merit", "Sports", "Staff child", "Bursary"]),
                                   percent=rng.choice([25, 50, 100])))
    db.session.flush()

    # ---- opening balances (statement of financial position brought forward) ----
    a = ledger.acct
    ledger.post(t1s - timedelta(days=1), "Opening balances brought forward", [
        {"account": a("1010"), "debit": 6_000_000}, {"account": a("1000"), "debit": 150_000},
        {"account": a("1500"), "debit": 25_000_000}, {"account": a("1590"), "credit": 4_000_000},
        {"account": a("3000"), "credit": 27_150_000},
    ], reference="OPENING", force=True)
    # The assets behind those PPE balances, in the register (already in the books, so no posting).
    for name, cat, cost, accum, life, loc in [
            ("Main classroom block", "Buildings", 18_000_000, 2_700_000, 480, "Main campus"),
            ("Classroom desks and chairs (360 sets)", "Furniture & Fittings", 3_000_000, 700_000, 96, "Classrooms"),
            ("School bus, 65-seater", "Motor Vehicles", 4_000_000, 600_000, 60, "Garage")]:
        db.session.add(FixedAsset(asset_no=next_number("asset", "FA-", 5), name=name, category=cat, location=loc,
                                  acquisition_date=t1s - timedelta(days=rng.randint(400, 2000)), funding="existing",
                                  cost_cents=cost, opening_accum_cents=accum, method="straight_line",
                                  useful_life_months=life, depreciation_start=assets.period_of(t1s)))
    db.session.flush()

    # ---- fees, invoices & payments -----------------------------------------
    bursar = users["bursar"]
    for term in terms:
        due = term.start_date + timedelta(days=21)
        db.session.add(FeeItem(term=term, name="Development levy", amount_cents=5000, due_date=due, discountable=False))
        db.session.add(FeeItem(term=term, name="Examination fee", amount_cents=2500, due_date=due, discountable=False))
        for c in classes:
            db.session.add(FeeItem(term=term, class_id=c.id, name="Tuition", amount_cents=(400 + c.level * 50) * 100, due_date=due))
            if c.level >= structure.ORDER["G4"]:
                db.session.add(FeeItem(term=term, class_id=c.id, name="ICT & Lab", amount_cents=3500, due_date=due))
        db.session.flush()
        for item in FeeItem.query.filter_by(term_id=term.id):
            item.account_id = ledger.fee_account_for(item.name).id
        # Invoices are issued on the first day of each term.
        finance.generate_term_invoices(term, issue_date=term.start_date)

    # Plan every payment first, then post them in date order so receipt numbers are chronological.
    planned = []
    for st in students:
        habit = rng.random()  # payment behaviour per family
        for term in terms:
            inv = next((i for i in st.invoices if i.term_id == term.id), None)
            if not inv or inv.balance_cents <= 0:
                continue
            last_day = min(term.end_date, today)
            if term.is_current:
                share = 1.0 if habit > 0.55 else (0.5 if habit > 0.25 else 0)
            else:
                share = 1.0 if habit > 0.12 else 0.6
            pay = int(inv.balance_cents * share / 100) * 100
            if pay <= 0:
                continue
            instalments = 1 if habit > 0.7 else 2
            span = max((last_day - term.start_date).days, 0)
            days = sorted(rng.randint(0, span) for _ in range(instalments))
            for k in range(instalments):
                amount = pay // instalments if k < instalments - 1 else pay - (pay // instalments) * (instalments - 1)
                method = rng.choice(["cash", "bank", "mobile", "mobile", "card"])
                ref = None if method == "cash" else f"TX{rng.randint(10 ** 8, 10 ** 9 - 1)}"
                planned.append((min(term.start_date + timedelta(days=days[k]), today), st.id, amount, method, ref, st))
    for paid_on, _, amount, method, ref, st in sorted(planned, key=lambda p: (p[0], p[1])):
        finance.record_payment(st, amount, method, ref, paid_on, bursar)

    # ---- attendance (previous + current term) -------------------------------
    rows = []
    for term in terms[1:]:
        # Stop yesterday so today's register is left for the demo teacher to take.
        for d in _school_days(term.start_date, min(term.end_date, today - timedelta(days=1))):
            for st in students:
                r = rng.random()
                weak = (st.id % 17 == 0)  # a few chronically absent pupils for the at-risk report
                status = ("absent" if r < (0.25 if weak else 0.04) else "late" if r < 0.08 else
                          "excused" if r < 0.095 else "present")
                rows.append(Attendance(student_id=st.id, class_id=st.class_id, date=d, status=status))
    db.session.bulk_save_objects(rows)

    # ---- exams & marks ------------------------------------------------------
    ability = {st.id: rng.gauss(64, 13) for st in students}
    for term in terms:
        exams = [Exam(term=term, name="Continuous Assessment", weight=20, max_score=50, date=term.start_date + timedelta(days=24)),
                 Exam(term=term, name="Mid-term Test", weight=30, max_score=100, date=term.start_date + timedelta(days=45)),
                 Exam(term=term, name="End of Term Exam", weight=50, max_score=100, date=term.end_date - timedelta(days=7))]
        db.session.add_all(exams)
        db.session.flush()
        marks = []
        for ex in exams:
            if ex.date > today:
                continue
            ex.locked = not term.is_current
            for st in students:
                for subj in [cs.subject for cs in st.school_class.subjects]:
                    pct = max(5, min(100, ability[st.id] + rng.gauss(0, 9) + (subj.id % 3 - 1) * 4))
                    marks.append(Mark(exam_id=ex.id, student_id=st.id, subject_id=subj.id,
                                      score=round(pct / 100 * ex.max_score)))
        db.session.bulk_save_objects(marks)

    # ---- expenses (accrued on approval, paid from bank/cash through the ledger) ----
    admin = users["admin"]
    cats = [("Utilities", "Electricity bill", "City Power", 600, 900), ("Utilities", "Water bill", "City Water", 150, 300),
            ("Maintenance", "Classroom repairs", "BuildRight Ltd", 300, 1500), ("Supplies", "Stationery restock", "Office Hub", 200, 700),
            ("Transport", "Bus fuel", "FuelCo", 400, 800), ("Food", "Kitchen provisions", "FreshMart", 500, 1100),
            ("IT", "Internet subscription", "NetConnect", 120, 120), ("Events", "Sports day", "Various", 300, 900)]
    bank, cash = a("1010"), a("1000")

    def add_expense(cat, desc, vendor, cents, day, status):
        e = Expense(category=cat, description=desc, vendor=vendor, amount_cents=cents, expense_date=day,
                    status=status, requested_by=bursar.id, approved_by=admin.id if status != "pending" else None)
        db.session.add(e)
        db.session.flush()
        if status in ("approved", "paid"):
            e.approved_at = day
            ledger.post_expense_accrual(e, force=True)
        if status == "paid":
            src = cash if cents <= 30_000 else bank
            e.paid_at = min(day + timedelta(days=rng.randint(0, 10)), today)
            e.paid_from_account_id = src.id
            ledger.post_expense_payment(e, src, e.paid_at, force=True)

    d = t1s
    while d <= today:
        for cat, desc, vendor, lo, hi in cats:
            if rng.random() < 0.7:
                day = min(d + timedelta(days=rng.randint(0, 27)), today)
                age = (today - day).days
                status = "paid" if age > 20 else rng.choice(["pending", "approved", "paid"])
                add_expense(cat, desc, vendor, rng.randint(lo, hi) * 100, day, status)
        d += timedelta(days=30)

    # ---- monthly banking: cash and mobile money swept to the bank (keeps a cash float) ----
    sweep = t1s + timedelta(days=27)
    while sweep < today:
        for src, keep in ((cash, 200_000), (a("1020"), 0)):
            amt = ledger.balance(src, sweep) - keep
            if amt > 0:
                ledger.post(sweep, f"Banking of {src.name.lower()} collections", [
                    {"account": bank, "debit": amt}, {"account": src, "credit": amt}],
                    reference="DEPOSIT", user_id=bursar.id, force=True)
        sweep += timedelta(days=30)

    # ---- a capital purchase through the asset register ----
    laptops = FixedAsset(asset_no=next_number("asset", "FA-", 5), name="20 laptops for the computer lab",
                         category="Computer Equipment", location="Computer lab", supplier="TechWorld",
                         acquisition_date=t2s + timedelta(days=10), funding="purchase", cost_cents=1_600_000,
                         residual_cents=100_000, method="straight_line", useful_life_months=36,
                         depreciation_start=assets.period_of(t2s + timedelta(days=10)), created_by=bursar.id)
    db.session.add(laptops)
    db.session.flush()
    ledger.post(laptops.acquisition_date, f"Asset {laptops.asset_no} acquired: {laptops.name}", [
        {"account": a("1500"), "debit": laptops.cost_cents, "memo": laptops.category},
        {"account": bank, "credit": laptops.cost_cents, "memo": laptops.supplier}],
        "asset", laptops.id, laptops.asset_no, bursar.id, force=True)
    ledger.post(t2s + timedelta(days=40), "Donation from the Parents' Association", [
        {"account": bank, "debit": 500_000}, {"account": a("4950"), "credit": 500_000}],
        reference="DON-007", user_id=bursar.id, force=True)

    # ---- staff payroll details (ZIMRA / NSSA) and recurring pay items ----
    for i, st in enumerate(Staff.query.order_by(Staff.id)):
        st.national_id = f"{rng.randint(10, 80)}-{rng.randint(100000, 999999)}{chr(65 + i % 26)}{rng.randint(10, 99)}"
        st.tax_number = f"2000{rng.randint(100000, 999999)}"
        st.nssa_number = f"{rng.randint(1000000, 9999999)}"
        st.dob = today - timedelta(days=365 * (58 if i == 3 else rng.randint(26, 52)))  # one elderly-credit case
        st.bank_name = rng.choice(["CBZ", "Stanbic", "FBC", "Steward"])
        st.bank_account = str(rng.randint(10 ** 9, 10 ** 10 - 1))
        db.session.add(StaffPayItem(staff=st, type="allowance", name="Transport allowance", amount_cents=4000))
        if st.position != "Teacher":
            db.session.add(StaffPayItem(staff=st, type="allowance", name="Housing allowance", amount_cents=25000))
        if i % 2 == 0:
            db.session.add(StaffPayItem(staff=st, type="pension", name="Pension fund (5%)",
                                        amount_cents=st.salary_cents * 5 // 100))
        if i % 3 == 0:
            db.session.add(StaffPayItem(staff=st, type="medical_aid", name="Medical aid", amount_cents=4500))
        if i % 4 == 1:
            db.session.add(StaffPayItem(staff=st, type="deduction", name="Union dues", amount_cents=500))
    db.session.flush()

    # ---- monthly payroll: approved by the head, paid on the 25th, remitted by the 10th ----
    # This month's payroll is left as a draft so the approval workflow can be tried out.
    period, current = assets.period_of(t1s), assets.period_of(today)
    while period <= current:
        pay_day = assets.month_start(period).replace(day=25)
        run = payroll.create_run(period, pay_day, None, bursar.id)
        if period < current:
            payroll.approve(run, admin)
            try:
                payroll.pay(run, {bank.currency or structure.currency(): bank}, min(pay_day, today), bursar.id)
            except ApiError:
                pass  # left approved and unpaid when the bank can't cover it
            due = payroll.zimra_due_date(period)
            if due <= today:
                for cur, kinds in payroll.liabilities(run).items():
                    for kind, amount in kinds.items():
                        if amount > 0:
                            try:
                                payroll.remit(run, kind, cur, bank, due - timedelta(days=2), f"{kind.upper()}-{period}",
                                              bursar.id)
                            except ApiError:
                                pass
        period = assets.month_add(period, 1)

    # ---- depreciation for every completed month ----
    period = assets.period_of(t1s)
    while assets.month_end(period) < today:
        assets.run_depreciation(period, bursar.id)
        period = assets.month_add(period, 1)

    db.session.add_all([
        Announcement(title="Welcome to the new term", pinned=True, audience="all", created_by=admin.id,
                     body="Classes run 08:00-13:40. Please ensure all fees are settled by the due date to avoid penalties."),
        Announcement(title="Staff meeting on Friday", audience="staff", created_by=admin.id,
                     body="All teaching staff: meeting in the staff room at 14:00 to review mid-term results."),
        Announcement(title="Parents' consultation day", audience="parents", created_by=admin.id,
                     body="Meet your child's teachers next Saturday from 09:00. Report cards will be available on the portal."),
    ])
    db.session.commit()
    return {"students": len(students), "classes": len(classes), "teachers": len(teachers)}


from .services.currency import CURRENCIES as CURRENCY_CODES  # noqa: E402


def register_cli(app):
    from contextlib import nullcontext

    from .tenancy import get_school, use_school

    school_option = click.option("--school", "slug", help="School code (multi-school mode)")

    def scope(slug):
        """Run against one school in multi-school mode, or the only database otherwise."""
        if not app.config.get("MULTI_SCHOOL"):
            if slug:
                raise click.UsageError("--school only applies in multi-school mode (MULTI_SCHOOL=1)")
            return nullcontext()
        if not slug:
            raise click.UsageError("Multi-school mode: say which school with --school <code>")
        school = get_school(slug)
        if school is None:
            raise click.UsageError(f"No school with code '{slug}'")
        return use_school(school)

    @app.cli.command("seed")
    @school_option
    @click.option("--type", "school_type", type=click.Choice(sorted(structure.SCHOOL_TYPES)), default="primary",
                  show_default=True, help="Demo school type")
    @click.option("--yes", is_flag=True, help="Skip confirmation")
    def seed_cmd(slug, school_type, yes):
        """Erase a school's data and load demo data."""
        if not yes:
            click.confirm("This ERASES all of the school's data and loads demo data. Continue?", abort=True)
        with scope(slug):
            stats = seed_demo(school_type=school_type)
        click.echo(f"Seeded {stats}. Logins:")
        for uname, (pw, role, _) in DEMO_ACCOUNTS.items():
            click.echo(f"  {role:8} {uname} / {pw}")

    @app.cli.command("ledger-rebuild")
    @school_option
    def ledger_rebuild_cmd(slug):
        """Regenerate automatic ledger postings from invoices, receipts and expenses."""
        with scope(slug):
            n = ledger.rebuild_ledger()
            db.session.commit()
        click.echo(f"{n} postings regenerated.")

    @app.cli.command("create-admin")
    @school_option
    @click.option("--username", prompt=True)
    @click.option("--name", prompt="Full name")
    @click.password_option()
    def create_admin(slug, username, name, password):
        """Create a school administrator account."""
        from .api.auth import validate_password
        validate_password(password)
        with scope(slug):
            u = User(username=username.lower(), full_name=name, role="admin")
            u.set_password(password)
            db.session.add(u)
            db.session.commit()
        click.echo(f"Administrator {username} created.")

    @app.cli.command("create-platform-admin")
    @click.option("--username", prompt=True)
    @click.option("--name", prompt="Full name")
    @click.password_option()
    def create_platform_admin(username, name, password):
        """Create a platform operator (multi-school mode) who can create and manage schools."""
        from .api.auth import validate_password
        from .models import PlatformAdmin
        if not app.config.get("MULTI_SCHOOL"):
            raise click.UsageError("Set MULTI_SCHOOL=1 first")
        validate_password(password)
        op = PlatformAdmin(username=username.strip().lower(), full_name=name)
        op.set_password(password)
        db.session.add(op)
        db.session.commit()
        click.echo(f"Platform operator {username} created. Sign in at /platform/")

    @app.cli.command("create-school")
    @click.option("--code", "slug", prompt="School code (used in the address)")
    @click.option("--name", prompt="School name")
    @click.option("--type", "school_type", type=click.Choice(sorted(structure.SCHOOL_TYPES)), prompt=True)
    @click.option("--currency", type=click.Choice(list(CURRENCY_CODES)), default="USD", show_default=True)
    @click.option("--admin-username", default="admin", show_default=True)
    @click.option("--admin-password", prompt=True, hide_input=True, confirmation_prompt=True)
    @click.option("--demo", is_flag=True, help="Fill the school with demo data")
    def create_school_cmd(slug, name, school_type, currency, admin_username, admin_password, demo):
        """Create a school with its own database (multi-school mode)."""
        from .api.platform import provision_school
        if not app.config.get("MULTI_SCHOOL"):
            raise click.UsageError("Set MULTI_SCHOOL=1 first")
        school = provision_school(name, slug, school_type, currency,
                                  {"username": admin_username, "password": admin_password}, demo=demo)
        click.echo(f"{school.name} created. Address: /s/{school.slug}/")

    @app.cli.command("adopt-school")
    @click.option("--code", "slug", prompt="School code (used in the address)")
    @click.option("--name", prompt="School name")
    @click.option("--database-url", prompt=True, help="e.g. sqlite:///C:/path/instance/school.db")
    @click.option("--schema", default=None, help="PostgreSQL schema holding the tables, if any")
    @click.option("--currency", type=click.Choice(list(CURRENCY_CODES)), default="USD", show_default=True)
    def adopt_school(slug, name, database_url, schema, currency):
        """Register an existing single-school database as a school (multi-school mode).

        Its data, users and passwords are kept; the database is upgraded on first use.
        """
        from .models import School
        from .tenancy import valid_slug
        if not app.config.get("MULTI_SCHOOL"):
            raise click.UsageError("Set MULTI_SCHOOL=1 first")
        if not valid_slug(slug) or get_school(slug):
            raise click.UsageError("That school code is invalid or already taken")
        school = School(slug=slug, name=name, school_type="primary", currency=currency,
                        database_url=database_url, db_schema=schema)
        db.session.add(school)
        db.session.commit()
        with use_school(school):
            kind = structure.school_type()
            users = User.query.count()
        row = get_school(slug)
        row.school_type = kind
        db.session.commit()
        click.echo(f"{name} registered as a {kind} school with {users} user accounts. Address: /s/{slug}/")

    @app.cli.command("list-schools")
    def list_schools():
        """List the schools on this platform (multi-school mode)."""
        from .models import School
        if not app.config.get("MULTI_SCHOOL"):
            raise click.UsageError("Set MULTI_SCHOOL=1 first")
        for sch in School.query.order_by(School.slug):
            click.echo(f"{sch.slug:24} {sch.status:10} {sch.school_type:10} {sch.currency:4} {sch.name}")
