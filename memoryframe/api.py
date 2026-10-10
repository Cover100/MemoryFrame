"""API blueprint (/api/v1). Every route requires a paired session (MF-12): 401 otherwise."""
import threading

from flask import Blueprint, abort, current_app, jsonify, request, send_file
from werkzeug.exceptions import RequestEntityTooLarge

from . import auth, imaging, upload
from .display import bump_media_rev, clear_show_now
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
    bump_media_rev(current_app)
    return jsonify({"status": "added", "name": name, "id": stored}), 201


@bp.app_errorhandler(RequestEntityTooLarge)
def too_large(_e):
    mb = current_app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024)
    return jsonify({"error": f"This file is too large. The limit is {mb} MB per photo."}), 413



# ---------------- MF-16: library management ----------------

SQLITE_MAX_INT = 2**63 - 1


def _item(media_id):
    if not 0 < media_id <= SQLITE_MAX_INT:      # huge ids would raise OverflowError in sqlite3
        abort(404)
    rows = get_db().execute("SELECT * FROM media WHERE id = ?", (media_id,)).fetchall()  # no open cursor
    row = rows[0] if rows else None
    if row is None:
        abort(404)
    return row


@bp.route("/media")
def list_media():
    rows = get_db().execute(
        "SELECT id, original_name, kind, hidden, size_bytes, created_at FROM media "
        "ORDER BY created_at DESC, id DESC").fetchall()
    return jsonify([{"id": r["id"], "name": r["original_name"], "kind": r["kind"], "hidden": bool(r["hidden"]),
                     "size": r["size_bytes"], "created_at": r["created_at"],
                     "thumb": f"/api/v1/media/{r['id']}/thumb"} for r in rows])


@bp.route("/media/<int:media_id>/thumb")
def thumb(media_id):
    row = _item(media_id)
    path = imaging.ensure_thumb(current_app.config["DATA_DIR"], row["stored_name"]) \
        if row["kind"] == "image" else None
    if path is None:
        abort(404)
    resp = send_file(path, mimetype="image/jpeg", max_age=86400)
    resp.headers["Cache-Control"] = "private, max-age=86400"
    return resp


@bp.route("/media/<int:media_id>/hidden", methods=["PUT"])
def set_hidden(media_id):
    _item(media_id)
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not isinstance(data.get("hidden"), bool):
        return jsonify({"error": "Body must be JSON like {\"hidden\": true}."}), 400
    db = get_db()
    db.execute("UPDATE media SET hidden = ? WHERE id = ?", (int(data["hidden"]), media_id))
    db.commit()
    if data["hidden"]:
        clear_show_now(current_app, _item(media_id)["stored_name"])
    bump_media_rev(current_app)
    return jsonify({"id": media_id, "hidden": data["hidden"]})


# Deletes of the same id are serialised in-process (waitress runs one process, many threads).
# Striped locks keep memory bounded.
_DELETE_LOCKS = [threading.Lock() for _ in range(64)]


@bp.route("/media/<int:media_id>", methods=["DELETE"])
def delete_media(media_id):
    _item(media_id)                                 # 404 for bad / unknown ids
    with _DELETE_LOCKS[media_id % len(_DELETE_LOCKS)]:
        return _delete_locked(media_id)


def _delete_locked(media_id):
    data_dir = current_app.config["DATA_DIR"]
    db = get_db()
    rows = db.execute("SELECT stored_name FROM media WHERE id = ?", (media_id,)).fetchall()
    if not rows:                                    # another delete finished while we waited
        abort(404)
    stored = rows[0]["stored_name"]
    # 1) move every file into tmp/trash (atomic renames, same filesystem)
    staged = imaging.stage_delete(data_dir, stored, media_id)
    try:
        # 2) remove the row
        gone = db.execute("DELETE FROM media WHERE id = ?", (media_id,)).rowcount
        db.commit()
    except Exception:
        db.rollback()
        imaging.unstage_delete(staged)           # DB step failed: row survives, so put files back
        raise
    # 3) the row is gone (by us, or by another writer): the files must go too, never restore
    removed = imaging.purge_staged(staged)
    if gone == 0:
        abort(404)
    clear_show_now(current_app, stored)
    bump_media_rev(current_app)
    return jsonify({"id": media_id, "deleted": True, "files_removed": removed})


@bp.route("/media/<int:media_id>/show-now", methods=["POST"])
def show_now(media_id):
    row = _item(media_id)
    if row["hidden"]:
        return jsonify({"error": "This photo is hidden. Unhide it first."}), 409
    import time
    state = current_app.extensions.setdefault("mf_show_now", {"seq": 0, "filename": None, "at": 0})
    state.update(seq=state["seq"] + 1, filename=row["stored_name"], at=time.time())
    return jsonify({"id": media_id, "seq": state["seq"]})
