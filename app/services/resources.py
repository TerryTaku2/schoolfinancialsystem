"""Digital library: PDF textbooks, past exam papers, marking schemes and notes uploaded by teachers.

Storage:
- database (default): the PDF is stored in the school's own database. Works anywhere, including
  hosts whose disk is wiped on each deploy (Render), and is backed up with the database.
- disk: set UPLOADS_DIR (e.g. a Render Disk mounted at /var/data/uploads) to keep files in a
  folder instead, one sub-folder per school. Cheaper for large collections.

Only real PDFs are accepted (checked by content, not by name), up to MAX_UPLOAD_MB.
"""
import hashlib
import os

from flask import current_app
from flask_login import current_user

from .. import db
from ..models import Resource, ResourceBlob
from ..tenancy import current_school
from ..utils import ApiError


def max_bytes():
    return int(current_app.config.get("MAX_UPLOAD_MB", 25)) * 1024 * 1024


def uploads_dir():
    return (current_app.config.get("UPLOADS_DIR") or "").strip()


def _school_folder():
    school = current_school()
    folder = os.path.join(uploads_dir(), school.slug if school else "default")
    os.makedirs(folder, exist_ok=True)
    return folder


def read_upload(file):
    """Bytes of an uploaded PDF, checked. Raises ApiError for anything else."""
    if file is None or not file.filename:
        raise ApiError("Choose a PDF file to upload", fields={"file": "Required"})
    data = file.read(max_bytes() + 1)
    if len(data) > max_bytes():
        raise ApiError(f"The file is larger than {current_app.config.get('MAX_UPLOAD_MB', 25)} MB. "
                       "Compress the PDF or split it into parts.", 413, fields={"file": "Too large"})
    if not data:
        raise ApiError("The file is empty", fields={"file": "Empty"})
    if not data[:1024].lstrip().startswith(b"%PDF-"):
        raise ApiError("Only PDF files can be uploaded", fields={"file": "Not a PDF"})
    return data


def store(resource, data):
    resource.size_bytes = len(data)
    resource.sha256 = hashlib.sha256(data).hexdigest()
    if uploads_dir():
        resource.storage, resource.storage_key = "disk", f"{resource.sha256}.pdf"
        path = os.path.join(_school_folder(), resource.storage_key)
        if not os.path.exists(path):  # identical files are stored once
            tmp = path + ".part"
            with open(tmp, "wb") as fh:
                fh.write(data)
            os.replace(tmp, path)
    else:
        resource.storage, resource.storage_key = "db", None
        db.session.flush()
        db.session.add(ResourceBlob(resource_id=resource.id, data=data))


def load(resource):
    if resource.storage == "disk":
        path = os.path.join(_school_folder(), resource.storage_key or "")
        if not os.path.exists(path):
            raise ApiError("The file is missing from storage. Ask the uploader to upload it again.", 410)
        with open(path, "rb") as fh:
            return fh.read()
    blob = db.session.get(ResourceBlob, resource.id)
    if blob is None:
        raise ApiError("The file is missing from storage. Ask the uploader to upload it again.", 410)
    return blob.data


def remove(resource):
    if resource.storage == "disk":
        others = Resource.query.filter(Resource.sha256 == resource.sha256, Resource.id != resource.id,
                                       Resource.storage == "disk").count()
        path = os.path.join(_school_folder(), resource.storage_key or "")
        if not others and os.path.exists(path):
            os.remove(path)
    else:
        blob = db.session.get(ResourceBlob, resource.id)
        if blob:
            db.session.delete(blob)
    db.session.delete(resource)


def is_staff():
    return current_user.is_authenticated and current_user.role != "parent"


def visible_query():
    q = Resource.query
    if not is_staff():
        q = q.filter(Resource.audience == "everyone")
    return q


def can_edit(resource):
    from .permissions import has
    return has("library.approve") or (has("library.upload") and resource.uploaded_by == current_user.id)


def resource_dict(r):
    return {"id": r.id, "title": r.title, "kind": r.kind, "subject_id": r.subject_id,
            "subject": r.subject.name if r.subject else None, "level": r.level, "exam_board": r.exam_board,
            "year": r.year, "paper": r.paper, "description": r.description, "audience": r.audience,
            "file_name": r.file_name, "size_bytes": r.size_bytes, "downloads": r.downloads,
            "uploaded_by": r.uploader.full_name if r.uploader else None,
            "created_at": r.created_at.isoformat(), "can_edit": can_edit(r)}
