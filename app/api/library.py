"""Library: catalogue, copies, the issue/return desk, overdue books and fines."""
import io
import os
from datetime import date

from flask import Blueprint, current_app, jsonify, request
from flask_login import current_user, login_required
from sqlalchemy import or_

from .. import db
from ..models import (BOOK_CATEGORIES, BOOK_CONDITIONS, Book, BookCopy, Loan, SchoolClass, Staff, Student, Subject)
from ..services import access, permissions
from ..services import currency as fx
from ..services import library as lib
from ..services import structure
from ..utils import (ApiError, audit, body, clean_str, current_term, get_or_404, parse_date, parse_int, permission_required,
                     require, to_cents)

bp = Blueprint("library", __name__)


# --------------------------------------------------------------------------- #
# Catalogue
# --------------------------------------------------------------------------- #
@bp.get("/library/books")
@permission_required("library.view")
def list_books():
    q = Book.query
    if request.args.get("q"):
        like = f"%{request.args['q'].strip()}%"
        q = q.filter(or_(Book.title.ilike(like), Book.author.ilike(like), Book.isbn.ilike(like),
                         Book.id.in_(db.session.query(BookCopy.book_id).filter(BookCopy.accession_no.ilike(like)))))
    if request.args.get("category"):
        q = q.filter_by(category=request.args["category"])
    if request.args.get("subject_id"):
        q = q.filter_by(subject_id=parse_int(request.args["subject_id"], "subject_id"))
    if request.args.get("level"):
        q = q.filter_by(level=request.args["level"])
    items = [lib.book_dict(b) for b in q.order_by(Book.title).all()]
    if request.args.get("available") == "1":
        items = [b for b in items if b["available"]]
    return jsonify(items=items, categories=BOOK_CATEGORIES, conditions=BOOK_CONDITIONS,
                   subjects=[{"id": s.id, "name": s.name} for s in Subject.query.order_by(Subject.name)],
                   levels=[{"code": c, "label": structure.LONG_LABEL[c]} for c in structure.codes_for(structure.school_type())])


@bp.get("/library/books/<int:bid>")
@permission_required("library.view")
def get_book(bid):
    return jsonify(lib.book_dict(get_or_404(Book, bid, "Book"), detail=True))


def _apply_book(b, data):
    b.title = clean_str(data["title"], 200)
    if not b.title:
        raise ApiError("Title is required", fields={"title": "Required"})
    b.author = clean_str(data.get("author"), 150)
    b.isbn = clean_str((data.get("isbn") or "").replace("-", "").replace(" ", ""), 20)
    b.publisher = clean_str(data.get("publisher"), 120)
    b.year = parse_int(data.get("year"), "year", required=False, minimum=1400, maximum=date.today().year + 1)
    b.edition = clean_str(data.get("edition"), 40)
    cat = data.get("category") or "Other"
    if cat not in BOOK_CATEGORIES:
        raise ApiError("Invalid category", fields={"category": "Invalid"})
    b.category = cat
    sid = parse_int(data.get("subject_id"), "subject_id", required=False)
    b.subject_id = get_or_404(Subject, sid, "Subject").id if sid else None
    level = data.get("level") or None
    if level and level not in structure.CODES:
        raise ApiError("Invalid level", fields={"level": "Invalid"})
    b.level = level
    b.shelf = clean_str(data.get("shelf"), 40)
    b.notes = clean_str(data.get("notes"), 300)


@bp.post("/library/books")
@permission_required("library.manage")
def create_book():
    data = body()
    require(data, "title")
    b = Book()
    _apply_book(b, data)
    if b.isbn and Book.query.filter_by(isbn=b.isbn).first():
        raise ApiError("A book with this ISBN is already in the catalogue; add copies to it instead",
                       fields={"isbn": "Duplicate"})
    db.session.add(b)
    db.session.flush()
    count = parse_int(data.get("copies", 1), "copies", minimum=0, maximum=500)
    numbers = [n for n in str(data.get("accession_numbers") or "").replace(",", "\n").split("\n") if n.strip()]
    cur = fx.pick(data.get("currency"))
    lib.add_copies(b, count, to_cents(data.get("replacement_cost") or 0, allow_zero=True), cur,
                   data.get("condition") or "New", numbers=numbers)
    audit("create", "book", b.id, f"{b.title} ({len(b.copies)} copies)")
    db.session.commit()
    return jsonify(lib.book_dict(b, detail=True)), 201


@bp.put("/library/books/<int:bid>")
@permission_required("library.manage")
def update_book(bid):
    b = get_or_404(Book, bid, "Book")
    data = body()
    require(data, "title")
    _apply_book(b, data)
    if b.isbn and Book.query.filter(Book.isbn == b.isbn, Book.id != b.id).first():
        raise ApiError("Another book has this ISBN", fields={"isbn": "Duplicate"})
    audit("update", "book", b.id, b.title)
    db.session.commit()
    return jsonify(lib.book_dict(b, detail=True))


@bp.delete("/library/books/<int:bid>")
@permission_required("library.approve")
def delete_book(bid):
    b = get_or_404(Book, bid, "Book")
    if Loan.query.join(BookCopy).filter(BookCopy.book_id == b.id).first():
        raise ApiError("This book has loan history, so it can't be deleted. Withdraw its copies instead.", 409)
    audit("delete", "book", b.id, b.title)
    db.session.delete(b)
    db.session.commit()
    return jsonify(ok=True)


@bp.post("/library/books/<int:bid>/copies")
@permission_required("library.manage")
def add_copies(bid):
    b = get_or_404(Book, bid, "Book")
    data = body()
    numbers = [n for n in str(data.get("accession_numbers") or "").replace(",", "\n").split("\n") if n.strip()]
    count = parse_int(data.get("count", 0 if numbers else 1), "count", minimum=0, maximum=500)
    if not count and not numbers:
        raise ApiError("Add at least one copy", fields={"count": "Required"})
    cond = data.get("condition") or "New"
    if cond not in BOOK_CONDITIONS:
        raise ApiError("Invalid condition", fields={"condition": "Invalid"})
    made = lib.add_copies(b, count, to_cents(data.get("replacement_cost") or 0, allow_zero=True),
                          fx.pick(data.get("currency")), cond,
                          parse_date(data.get("acquired_on"), "acquired_on", required=False), numbers)
    audit("add_copies", "book", b.id, ", ".join(c.accession_no for c in made))
    db.session.commit()
    return jsonify(lib.book_dict(b, detail=True)), 201


@bp.put("/library/copies/<int:cid>")
@permission_required("library.manage")
def update_copy(cid):
    c = get_or_404(BookCopy, cid, "Copy")
    data = body()
    if "condition" in data:
        if data["condition"] not in BOOK_CONDITIONS:
            raise ApiError("Invalid condition", fields={"condition": "Invalid"})
        c.condition = data["condition"]
    if "replacement_cost" in data:
        c.replacement_cents = to_cents(data["replacement_cost"] or 0, allow_zero=True)
    if "currency" in data:
        c.currency = fx.pick(data["currency"])
    if "notes" in data:
        c.notes = clean_str(data["notes"], 200)
    if "status" in data and data["status"] != c.status:
        new = data["status"]
        if c.status == "on_loan":
            raise ApiError("This copy is on loan; return it first", 409)
        if new not in ("available", "withdrawn"):
            raise ApiError("A copy can be made available or withdrawn here", fields={"status": "Invalid"})
        if c.status == "lost" and new == "available":
            audit("found", "book_copy", c.id, f"{c.accession_no} found")
        c.status = new
    audit("update", "book_copy", c.id, c.accession_no)
    db.session.commit()
    return jsonify(lib.copy_dict(c))


@bp.get("/library/copies/lookup")
@permission_required("library.view")
def lookup_copy():
    code = (request.args.get("code") or "").strip().upper()
    if not code:
        raise ApiError("Enter or scan an accession number", fields={"code": "Required"})
    c = BookCopy.query.filter(db.func.upper(BookCopy.accession_no) == code).first()
    if c is None:
        raise ApiError(f"No copy with accession number {code}", 404)
    return jsonify(lib.copy_dict(c))


# --------------------------------------------------------------------------- #
# Loans
# --------------------------------------------------------------------------- #
@bp.get("/library/borrowers")
@permission_required("library.view")
def find_borrowers():
    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return jsonify(items=[])
    return jsonify(items=lib.search_borrowers(q))


@bp.get("/library/borrowers/<kind>/<int:bid>")
@permission_required("library.view")
def borrower(kind, bid):
    if kind == "student":
        person = get_or_404(Student, bid, "Student")
        d, q = lib.borrower_dict(student=person), Loan.query.filter_by(student_id=bid)
    elif kind == "staff":
        person = get_or_404(Staff, bid, "Staff member")
        d, q = lib.borrower_dict(staff=person), Loan.query.filter_by(staff_id=bid)
    else:
        raise ApiError("Unknown borrower type", 404)
    d["loans"] = [lib.loan_dict(l) for l in q.order_by(Loan.returned_on.isnot(None), Loan.issued_on.desc()).limit(100)]
    return jsonify(d)


@bp.get("/library/loans")
@permission_required("library.view")
def list_loans():
    status = request.args.get("status", "open")
    q = Loan.query
    today = date.today()
    if status == "open":
        q = lib.open_loans_q()
    elif status == "overdue":
        q = lib.open_loans_q().filter(Loan.due_on < today)
    elif status == "returned":
        q = q.filter(Loan.returned_on.isnot(None))
    elif status == "fines":
        q = q.filter(Loan.fine_status == "unpaid")
    elif status == "lost":
        q = q.filter(Loan.lost.is_(True))
    elif status != "all":
        raise ApiError("Invalid status")
    if request.args.get("class_id"):
        q = q.join(Student, Loan.student_id == Student.id).filter(Student.class_id == parse_int(request.args["class_id"], "class_id"))
    order = Loan.due_on.asc() if status in ("open", "overdue") else Loan.issued_on.desc()
    rows = q.order_by(order, Loan.id.desc()).limit(500).all()
    return jsonify(items=[lib.loan_dict(l) for l in rows], summary=lib.summary())


def _copy_from(data):
    if data.get("copy_id"):
        return get_or_404(BookCopy, parse_int(data["copy_id"], "copy_id"), "Copy")
    code = str(data.get("accession_no") or "").strip().upper()
    if not code:
        raise ApiError("Scan or enter the book's accession number", fields={"accession_no": "Required"})
    c = BookCopy.query.filter(db.func.upper(BookCopy.accession_no) == code).first()
    if c is None:
        raise ApiError(f"No copy with accession number {code}", 404, fields={"accession_no": "Not found"})
    return c


@bp.post("/library/loans")
@permission_required("library.manage")
def create_loan():
    data = body()
    copy = _copy_from(data)
    student = staff = None
    if data.get("student_id"):
        student = get_or_404(Student, parse_int(data["student_id"], "student_id"), "Student")
    elif data.get("staff_id"):
        staff = get_or_404(Staff, parse_int(data["staff_id"], "staff_id"), "Staff member")
    else:
        raise ApiError("Choose who is borrowing the book", fields={"borrower": "Required"})
    due = parse_date(data.get("due_on"), "due_on", required=False)
    loan = lib.issue(copy, student=student, staff=staff, due_on=due)
    db.session.commit()
    return jsonify(lib.loan_dict(loan)), 201


@bp.post("/library/loans/<int:lid>/return")
@permission_required("library.manage")
def return_loan(lid):
    loan = get_or_404(Loan, lid, "Loan")
    data = body()
    cond = data.get("condition") or None
    if cond and cond not in BOOK_CONDITIONS:
        raise ApiError("Invalid condition", fields={"condition": "Invalid"})
    on = parse_date(data.get("returned_on"), "returned_on", required=False)
    if on and on > date.today():
        raise ApiError("The return date can't be in the future", fields={"returned_on": "Future date"})
    lib.return_loan(loan, on, cond)
    db.session.commit()
    return jsonify(lib.loan_dict(loan))


@bp.post("/library/returns")
@permission_required("library.manage")
def return_by_code():
    """The desk: scan a book to return it, whoever has it."""
    data = body()
    copy = _copy_from(data)
    loan = lib.open_loans_q().filter(Loan.copy_id == copy.id).first()
    if loan is None:
        raise ApiError(f"{copy.accession_no} ({copy.book.title}) isn't on loan", 409)
    cond = data.get("condition") or None
    if cond and cond not in BOOK_CONDITIONS:
        raise ApiError("Invalid condition", fields={"condition": "Invalid"})
    lib.return_loan(loan, None, cond)
    db.session.commit()
    return jsonify(lib.loan_dict(loan))


@bp.post("/library/loans/<int:lid>/renew")
@permission_required("library.manage")
def renew_loan(lid):
    loan = lib.renew(get_or_404(Loan, lid, "Loan"))
    db.session.commit()
    return jsonify(lib.loan_dict(loan))


@bp.post("/library/loans/<int:lid>/lost")
@permission_required("library.manage")
def lost_loan(lid):
    loan = lib.mark_lost(get_or_404(Loan, lid, "Loan"))
    db.session.commit()
    return jsonify(lib.loan_dict(loan))


@bp.post("/library/loans/<int:lid>/fine")
@permission_required("library.manage")
def settle_fine(lid):
    data = body()
    require(data, "action")
    loan = lib.settle_fine(get_or_404(Loan, lid, "Loan"), data["action"], clean_str(data.get("note"), 120))
    db.session.commit()
    return jsonify(lib.loan_dict(loan))


@bp.post("/library/issue-class")
@permission_required("library.manage")
def issue_to_class():
    """Textbooks for the term: one copy to every active student in a class who hasn't got one."""
    data = body()
    require(data, "book_id", "class_id")
    book = get_or_404(Book, parse_int(data["book_id"], "book_id"), "Book")
    cls = get_or_404(SchoolClass, parse_int(data["class_id"], "class_id"), "Class")
    term = current_term()
    due = parse_date(data.get("due_on"), "due_on", required=False) or (term.end_date if term else None)
    if due is None:
        raise ApiError("Set a due date (there is no current term)", fields={"due_on": "Required"})
    students = Student.query.filter_by(class_id=cls.id, status="active").order_by(Student.last_name, Student.first_name).all()
    has_one = {l.student_id for l in lib.open_loans_q().join(BookCopy).filter(BookCopy.book_id == book.id,
                                                                                Loan.student_id.isnot(None))}
    need = [s for s in students if s.id not in has_one]
    free = BookCopy.query.filter_by(book_id=book.id, status="available").order_by(BookCopy.accession_no).all()
    issued, skipped = [], []
    for s in need:
        if not free:
            skipped.append({"student": s.name, "reason": "no copies left"})
            continue
        try:
            with db.session.begin_nested():
                loan = lib.issue(free[0], student=s, due_on=due, count_towards_limit=False)
            free.pop(0)
            issued.append({"student": s.name, "accession_no": loan.copy.accession_no})
        except ApiError as err:
            skipped.append({"student": s.name, "reason": err.message})
    db.session.commit()
    return jsonify(issued=issued, skipped=skipped, already=len(has_one & {s.id for s in students}))


# --------------------------------------------------------------------------- #
# Settings and the student page
# --------------------------------------------------------------------------- #
@bp.get("/library/settings")
@permission_required("library.view")
def get_settings():
    return jsonify(settings=lib.settings(), labels={k: v[1] for k, v in lib.SETTINGS.items()}, currency=fx.base())


@bp.put("/library/settings")
@permission_required("library.approve")
def put_settings():
    s = lib.save_settings(body())
    audit("update", "library_settings", None, ", ".join(f"{k}={v}" for k, v in s.items()))
    db.session.commit()
    return jsonify(settings=s)


@bp.get("/library/students/<int:sid>/loans")
@login_required
def student_loans(sid):
    """A student's books, for the student page and the parent portal."""
    student = get_or_404(Student, sid, "Student")
    if not permissions.has("library.view"):
        access.ensure_student_access(student)
    loans = Loan.query.filter_by(student_id=sid).order_by(Loan.returned_on.isnot(None), Loan.issued_on.desc()).limit(50)
    return jsonify(items=[lib.loan_dict(l) for l in loans], **lib.borrower_dict(student=student))


# --------------------------------------------------------------------------- #
# Digital library: PDFs uploaded by teachers
# --------------------------------------------------------------------------- #
def _resource_fields(r, form):
    from ..models import RESOURCE_AUDIENCES, RESOURCE_KINDS
    r.title = clean_str(form.get("title"), 200)
    if not r.title:
        raise ApiError("Give the file a title", fields={"title": "Required"})
    kind = form.get("kind") or "Other"
    if kind not in RESOURCE_KINDS:
        raise ApiError("Invalid type", fields={"kind": "Invalid"})
    r.kind = kind
    sid = parse_int(form.get("subject_id"), "subject_id", required=False)
    r.subject_id = get_or_404(Subject, sid, "Subject").id if sid else None
    level = form.get("level") or None
    if level and level not in structure.CODES:
        raise ApiError("Invalid level", fields={"level": "Invalid"})
    r.level = level
    r.exam_board = clean_str(form.get("exam_board"), 30)
    r.year = parse_int(form.get("year"), "year", required=False, minimum=1950, maximum=date.today().year + 1)
    r.paper = clean_str(form.get("paper"), 40)
    r.description = clean_str(form.get("description"), 500)
    audience = form.get("audience") or ("staff" if kind == "Marking scheme" else "everyone")
    if audience not in RESOURCE_AUDIENCES:
        raise ApiError("Invalid audience", fields={"audience": "Invalid"})
    r.audience = audience


@bp.get("/elibrary")
@login_required
def list_resources():
    from ..models import RESOURCE_KINDS, Resource
    from ..services import resources as res
    q = res.visible_query()
    if request.args.get("q"):
        like = f"%{request.args['q'].strip()}%"
        q = q.filter(or_(Resource.title.ilike(like), Resource.description.ilike(like), Resource.paper.ilike(like),
                         Resource.exam_board.ilike(like)))
    for key in ("kind", "level"):
        if request.args.get(key):
            q = q.filter(getattr(Resource, key) == request.args[key])
    if request.args.get("subject_id"):
        q = q.filter(Resource.subject_id == parse_int(request.args["subject_id"], "subject_id"))
    if request.args.get("year"):
        q = q.filter(Resource.year == parse_int(request.args["year"], "year"))
    if request.args.get("mine") == "1":
        q = q.filter(Resource.uploaded_by == current_user.id)
    rows = q.order_by(Resource.created_at.desc(), Resource.id.desc()).limit(500).all()
    return jsonify(items=[res.resource_dict(r) for r in rows], kinds=RESOURCE_KINDS,
                   subjects=[{"id": s.id, "name": s.name} for s in Subject.query.order_by(Subject.name)],
                   levels=[{"code": c, "label": structure.LONG_LABEL[c]} for c in structure.codes_for(structure.school_type())],
                   can_upload=permissions.has("library.upload"), staff=res.is_staff(),
                   max_mb=int(current_app.config.get("MAX_UPLOAD_MB", 25)))


@bp.post("/elibrary")
@permission_required("library.upload")
def upload_resource():
    from ..models import Resource
    from ..services import resources as res
    form = request.form
    if form.get("rights") not in ("1", "true", "on", "yes"):
        raise ApiError("Confirm that the school may share this file (e.g. ZIMSEC past papers, your own notes, "
                       "or a textbook the school has the right to distribute)", fields={"rights": "Required"})
    data = res.read_upload(request.files.get("file"))
    r = Resource(uploaded_by=current_user.id,
                 file_name=clean_str(os.path.basename(request.files["file"].filename), 200) or "document.pdf")
    _resource_fields(r, form)
    if not r.file_name.lower().endswith(".pdf"):
        r.file_name += ".pdf"
    db.session.add(r)
    res.store(r, data)
    dup = Resource.query.filter(Resource.sha256 == r.sha256, Resource.id != r.id).first()
    audit("upload", "resource", r.id, f"{r.kind}: {r.title} ({r.size_bytes // 1024} KB)")
    db.session.commit()
    out = res.resource_dict(r)
    if dup:
        out["duplicate_of"] = dup.title
    return jsonify(out), 201


@bp.get("/elibrary/<int:rid>/file")
@login_required
def resource_file(rid):
    """The PDF, to read in the browser (or ?download=1 to save it)."""
    from flask import send_file
    from ..services import resources as res
    r = res.visible_query().filter_by(id=rid).first()
    if r is None:
        raise ApiError("File not found", 404)
    data = res.load(r)
    r.downloads += 1
    db.session.commit()
    resp = send_file(io.BytesIO(data), mimetype="application/pdf", as_attachment=request.args.get("download") == "1",
                     download_name=r.file_name, max_age=0)
    resp.headers["Content-Security-Policy"] = "sandbox"  # an uploaded file never runs as part of the app
    resp.headers["Cache-Control"] = "private, no-store"
    return resp


@bp.put("/elibrary/<int:rid>")
@login_required
def update_resource(rid):
    from ..models import Resource
    from ..services import resources as res
    r = get_or_404(Resource, rid, "File")
    if not res.can_edit(r):
        raise ApiError("Only the person who uploaded this file (or a library approver) can change it", 403)
    _resource_fields(r, body())
    audit("update", "resource", r.id, r.title)
    db.session.commit()
    return jsonify(res.resource_dict(r))


@bp.delete("/elibrary/<int:rid>")
@login_required
def delete_resource(rid):
    from ..models import Resource
    from ..services import resources as res
    r = get_or_404(Resource, rid, "File")
    if not res.can_edit(r):
        raise ApiError("Only the person who uploaded this file (or a library approver) can delete it", 403)
    audit("delete", "resource", r.id, r.title)
    res.remove(r)
    db.session.commit()
    return jsonify(ok=True)
