"""API blueprint (/api/v1). Every route requires a paired session (MF-12): 401 otherwise."""
from flask import Blueprint, current_app, jsonify, request
from werkzeug.exceptions import RequestEntityTooLarge

from . import auth, upload
from .db import get_db

bp = Blueprint("api", __name__)
bp.before_request(auth.require_session("api"))


@bp.route("/status")
def status():
    db = get_db()
    total = db.execute("SELECT COUNT(*) FROM media").fetchone()[0]
    visible = db.execute("SELECT COUNT(*) FROM media WHERE hidden = 0").fetchone()[0]
    return jsonify({"media": {"total": total, "visible": visible}})


def _limits():
    c = current_app.config
    return c["MIN_FREE_BYTES"], c["AVG_PHOTO_BYTES"]


@bp.route("/storage")
def storage():
    min_free, avg = _limits()
    return jsonify(upload.storage_info(current_app.config["DATA_DIR"], min_free, avg))


@bp.route("/media", methods=["POST"])
def upload_media():
    """Multipart upload, one file per request (field "file"), so the phone shows per-file progress."""
    f = request.files.get("file")
    if f is None or not f.filename:
        return jsonify({"error": "No file in the request (field 'file')."}), 400
    min_free, _ = _limits()
    try:
        stored, name = upload.save_upload(get_db(), current_app.config["DATA_DIR"], f, min_free,
                                          current_app.config["DISPLAY_TARGET"])
    except upload.UploadError as e:
        return jsonify({"error": e.message}), e.status
    if stored is None:
        return jsonify({"status": "duplicate", "name": name,
                        "message": "This photo is already on the frame."}), 200
    return jsonify({"status": "added", "name": name, "id": stored}), 201


@bp.app_errorhandler(RequestEntityTooLarge)
def too_large(_e):
    mb = current_app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024)
    return jsonify({"error": f"This file is too large. The limit is {mb} MB per photo."}), 413
