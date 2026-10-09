"""Digital library: teachers upload PDFs; who can see, open, change and delete them; storage."""
import io
import os

import pytest

from app import create_app, db
from app.models import Resource, ResourceBlob, Subject, User
from app.seed import seed_demo

H = {"X-Requested-With": "SchoolMS"}
PDF = b"%PDF-1.4\n1 0 obj << /Type /Catalog >> endobj\ntrailer << /Root 1 0 R >>\n%%EOF\n"


@pytest.fixture(scope="module")
def app():
    app = create_app("config.TestConfig")

    @app.before_request
    def _fresh_user():
        from flask import g
        g.pop("_login_user", None)

    with app.app_context():
        seed_demo(students_per_class=2)
        yield app


def login(app, username, password):
    c = app.test_client()
    assert c.post("/api/auth/login", json={"username": username, "password": password}, headers=H).status_code == 200
    return c


def upload(client, data=PDF, name="paper.pdf", **fields):
    form = {"title": "Maths Paper 1", "kind": "Past exam paper", "rights": "1", **fields,
            "file": (io.BytesIO(data), name)}
    return client.post("/api/elibrary", data=form, headers=H, content_type="multipart/form-data")


def test_teacher_uploads_and_everyone_reads(app):
    t = login(app, "teacher", "Teacher@2026")
    subj = Subject.query.first()
    r = upload(t, subject_id=subj.id, level="G7", exam_board="ZIMSEC", year="2023", paper="Paper 1")
    assert r.status_code == 201, r.json
    assert r.json["audience"] == "everyone" and r.json["subject"] == subj.name and r.json["can_edit"]
    rid = r.json["id"]
    assert db.session.get(ResourceBlob, rid).data == PDF  # database storage by default
    # Parents can find and read it.
    p = login(app, "parent", "Parent@2026")
    items = p.get("/api/elibrary").json["items"]
    assert [i["id"] for i in items] == [rid] and items[0]["can_edit"] is False
    f = p.get(f"/api/elibrary/{rid}/file")
    assert f.status_code == 200 and f.data == PDF and f.mimetype == "application/pdf"
    assert "sandbox" in f.headers["Content-Security-Policy"] and "inline" in f.headers["Content-Disposition"]
    assert "attachment" in p.get(f"/api/elibrary/{rid}/file?download=1").headers["Content-Disposition"]
    assert db.session.get(Resource, rid).downloads == 2
    # Filters.
    assert t.get("/api/elibrary?q=paper 1&year=2023&level=G7").json["items"][0]["id"] == rid
    assert t.get("/api/elibrary?kind=Notes").json["items"] == []


def test_only_real_pdfs_within_the_size_limit(app):
    t = login(app, "teacher", "Teacher@2026")
    assert upload(t, data=b"MZ\x90\x00 not a pdf", name="virus.pdf").status_code == 400
    assert upload(t, data=b"").status_code == 400
    r = upload(t, rights="")
    assert r.status_code == 400 and r.json["fields"]["rights"]
    r = upload(t, title="")
    assert r.status_code == 400 and r.json["fields"]["title"]
    app.config["MAX_UPLOAD_MB"] = 1
    try:
        r = upload(t, data=PDF + b"0" * (1024 * 1024 + 10))
        assert r.status_code == 413 and "1 MB" in r.json["error"]
    finally:
        app.config["MAX_UPLOAD_MB"] = 25


def test_marking_schemes_are_staff_only(app):
    t = login(app, "teacher", "Teacher@2026")
    r = upload(t, title="Maths Paper 1 marking scheme", kind="Marking scheme")
    assert r.status_code == 201 and r.json["audience"] == "staff"  # default for marking schemes
    rid = r.json["id"]
    p = login(app, "parent", "Parent@2026")
    assert rid not in [i["id"] for i in p.get("/api/elibrary").json["items"]]
    assert p.get(f"/api/elibrary/{rid}/file").status_code == 404
    assert login(app, "bursar", "Bursar@2026").get(f"/api/elibrary/{rid}/file").status_code == 200


def test_who_may_upload_change_and_delete(app):
    bursar = login(app, "bursar", "Bursar@2026")
    assert upload(bursar).status_code == 403  # not a teaching role by default
    assert login(app, "parent", "Parent@2026").post("/api/elibrary", headers=H).status_code == 403
    t = login(app, "teacher", "Teacher@2026")
    rid = upload(t, title="Own notes", kind="Notes").json["id"]
    # A second teacher can't touch the first one's file.
    other = User(username="teacher2", full_name="Second Teacher", role="teacher")
    other.set_password("Teacher2@2026")
    db.session.add(other)
    db.session.commit()
    t2 = login(app, "teacher2", "Teacher2@2026")
    assert t2.put(f"/api/elibrary/{rid}", json={"title": "Mine now"}, headers=H).status_code == 403
    assert t2.delete(f"/api/elibrary/{rid}", headers=H).status_code == 403
    # The uploader can.
    r = t.put(f"/api/elibrary/{rid}", json={"title": "Own notes (revised)", "kind": "Notes", "audience": "staff"}, headers=H)
    assert r.status_code == 200 and r.json["title"] == "Own notes (revised)" and r.json["audience"] == "staff"
    assert t.delete(f"/api/elibrary/{rid}", headers=H).status_code == 200
    assert db.session.get(ResourceBlob, rid) is None
    # An administrator (library approve) can remove anyone's file.
    rid = upload(t, title="Old paper").json["id"]
    assert login(app, "admin", "Admin@2026").delete(f"/api/elibrary/{rid}", headers=H).status_code == 200


def test_disk_storage(app, tmp_path):
    app.config["UPLOADS_DIR"] = str(tmp_path)
    try:
        t = login(app, "teacher", "Teacher@2026")
        a = upload(t, title="Disk copy A").json
        b = upload(t, title="Disk copy B").json  # same file: stored once
        assert "duplicate_of" in b
        files = os.listdir(tmp_path / "default")
        assert files == [f"{db.session.get(Resource, a['id']).sha256}.pdf"]
        assert db.session.get(ResourceBlob, a["id"]) is None
        assert t.get(f"/api/elibrary/{a['id']}/file").data == PDF
        t.delete(f"/api/elibrary/{a['id']}", headers=H)
        assert os.listdir(tmp_path / "default")  # still used by B
        t.delete(f"/api/elibrary/{b['id']}", headers=H)
        assert os.listdir(tmp_path / "default") == []
    finally:
        app.config["UPLOADS_DIR"] = ""
