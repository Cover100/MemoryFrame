"""API blueprint (/api/v1). Every route requires a paired session (MF-12): 401 otherwise."""
import threading

from flask import Blueprint, abort, current_app, g, jsonify, request, send_file
from werkzeug.exceptions import RequestEntityTooLarge

from . import auth, imaging, upload, video
from .display import bump_media_rev, clear_show_now
from .db import get_db

bp = Blueprint("api", __name__)
bp.before_request(auth.require_session("api"))


@bp.route("/settings", methods=["GET"])
def get_settings():
    from . import settings
    return jsonify(settings.get_all(get_db()))


@bp.route("/settings", methods=["PUT"])
def put_settings():
    from . import settings
    clean, err = settings.validate(request.get_json(silent=True))
    if err:
        return jsonify({"error": err}), 400
    conn = get_db()
    settings.save(conn, clean)
    return jsonify(settings.get_all(conn))


@bp.route("/message", methods=["GET"])
def get_message():
    from . import message
    return jsonify({"message": message.get(current_app.config["DATA_DIR"]), "max": message.MAX_LEN})


@bp.route("/message", methods=["PUT"])
def put_message():
    """#73: set (or clear with "") the frame message. Paired session + CSRF like /settings."""
    import json
    from . import message
    if (request.content_length or 0) > message.MAX_BODY:
        return jsonify({"error": "request too large"}), 413
    raw = request.stream.read(message.MAX_BODY + 1)          # chunked bodies: bounded read
    if len(raw) > message.MAX_BODY:
        return jsonify({"error": "request too large"}), 413
    try:
        data = json.loads(raw.decode("utf-8")) if request.is_json else None
    except (ValueError, UnicodeDecodeError):
        data = None
    if not isinstance(data, dict) or set(data) != {"message"}:
        return jsonify({"error": "expected a JSON object with message"}), 400
    text, err = message.clean(data["message"])
    if err:
        return jsonify({"error": err}), 400
    message.save(current_app.config["DATA_DIR"], text)
    return jsonify({"message": text, "max": message.MAX_LEN})


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
    videos = _videos_enabled()
    min_free, _ = _limits()
    if videos:
        # #102: videos up to 350 MB. The body is spooled to DATA_DIR/tmp (the SD card, never /run or a
        # tmpfs; see MFRequest). #96: refuse before reading it unless the reserve survives 3x its size.
        request.max_content_length = video.MAX_UPLOAD_BYTES + 1024 * 1024    # + multipart overhead
        g.mf_video_limit = True
        if request.content_length and not video.space_ok(current_app.config["DATA_DIR"], min_free,
                                                          request.content_length):
            return jsonify({"error": upload.FULL}), 507
    f = request.files.get("file")
    if f is None or not f.filename:
        return jsonify({"error": "No file in the request (field 'file')."}), 400
    try:
        stored, name = upload.save_upload(get_db(), current_app.config["DATA_DIR"], f, min_free,
                                          current_app.config["DISPLAY_TARGET"],
                                          current_app.config.get("MAX_IMAGE_PIXELS", upload.MAX_PIXELS),
                                          current_app.config.get("MAX_NONJPEG_PIXELS", upload.MAX_NONJPEG_PIXELS),
                                          videos=videos,
                                          max_photo_bytes=current_app.config["MAX_CONTENT_LENGTH"] if videos else None)
    except upload.UploadError as e:
        return jsonify({"error": e.message}), e.status
    is_video = name.endswith(".mp4")              # photo labels never end in .mp4
    if stored is None:
        what = "video" if is_video else "photo"
        return jsonify({"status": "duplicate", "name": name,
                        "message": f"This {what} is already on the frame."}), 200
    row = get_db().execute("SELECT id, kind, state FROM media WHERE stored_name = ?", (stored,)).fetchone()
    if row is not None and row["kind"] == "video":
        # 202: accepted, converted in the background; the Library tile shows Waiting / Preparing.
        return jsonify({"status": "queued", "name": name, "id": stored, "media_id": row["id"],
                        "kind": "video", "state": row["state"]}), 202
    bump_media_rev(current_app)
    # "id" stays the stored name (existing contract); media_id lets the phone show the new thumbnail.
    return jsonify({"status": "added", "name": name, "id": stored,
                    "media_id": row["id"] if row else None}), 201


def _videos_enabled():
    """#102: videos need ffmpeg on the frame; without it the old "videos coming later" answer stays."""
    v = current_app.config.get("VIDEOS")
    return video.available() if v is None else bool(v)


@bp.app_errorhandler(RequestEntityTooLarge)
def too_large(_e):
    if g.get("mf_video_limit"):
        return jsonify({"error": video.TOO_LARGE}), 413        # #102: only a video can be this big
    mb = current_app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024)
    return jsonify({"error": upload.PHOTO_TOO_LARGE.format(mb=mb)}), 413



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
        "SELECT id, original_name, kind, size_bytes, created_at, state, duration_ms FROM media "
        "ORDER BY created_at DESC, id DESC").fetchall()
    return jsonify([{"id": r["id"], "name": r["original_name"], "kind": r["kind"],
                     "size": r["size_bytes"], "created_at": r["created_at"],
                     "state": r["state"], "duration_ms": r["duration_ms"],      # #102
                     "thumb": f"/api/v1/media/{r['id']}/thumb"} for r in rows])


@bp.route("/media/<int:media_id>/thumb")
def thumb(media_id):
    row = _item(media_id)
    data_dir = current_app.config["DATA_DIR"]
    if row["kind"] == "image":
        path = imaging.ensure_thumb(data_dir, row["stored_name"])
    elif row["state"] == "ready":                   # #102: made from the poster (first frame)
        path = video.make_video_thumb(data_dir, row["stored_name"])
    else:
        path = None
    if path is None:
        abort(404)
    resp = send_file(path, mimetype="image/jpeg", max_age=86400)
    resp.headers["Cache-Control"] = "private, max-age=86400"
    return resp


@bp.route("/media/<int:media_id>/retry", methods=["POST"])
def retry(media_id):
    """#102: a failed video goes back in the queue (its upload is kept until it is ready)."""
    row = _item(media_id)
    data_dir = current_app.config["DATA_DIR"]
    if row["kind"] != "video" or row["state"] != "failed":
        return jsonify({"error": "Only a video that couldn't be added can be retried."}), 409
    import os
    if not os.path.exists(video.input_path(data_dir, row["stored_name"])):
        return jsonify({"error": "The frame no longer has this video. Please remove it and upload it again."}), 410
    get_db().execute("UPDATE media SET state = 'queued' WHERE id = ? AND state = 'failed'", (media_id,))
    get_db().commit()
    video.wake()
    return jsonify({"id": media_id, "state": "queued"})


# #80: the Hide feature is gone (PUT /media/<id>/hidden removed); db.init_db clears old flags.


@bp.route("/media/<int:media_id>", methods=["DELETE"])
def delete_media(media_id):
    _item(media_id)                                 # 404 for bad / unknown ids
    try:
        found, removed = _delete_one(media_id)
    except imaging.UnsafeName:                      # a stored name that isn't a bare file name: nothing was moved
        current_app.logger.error("refused to delete media %s: unsafe stored name", media_id)
        return jsonify({"error": "This photo's file name is not safe to delete."}), 400
    if not found:                                   # another delete finished while we waited
        abort(404)
    bump_media_rev(current_app)
    return jsonify({"id": media_id, "deleted": True, "files_removed": removed})


# #80 bulk delete (phone Library). Paired session (blueprint hook), Origin + CSRF (websec), JSON only.
MAX_BULK = 1000                 # ids per request; the phone sends larger selections in chunks of this size
MAX_BULK_BODY = 32 * 1024       # 1000 ids of up to 19 digits plus commas fit with room to spare


@bp.route("/media/delete", methods=["POST"])
def delete_many():
    """Body {"ids": [1, 2, 3]}. Deletes each photo (original + derivatives) with the same steps as the
    single delete. 200 {"deleted": [...], "missing": [...], "failed": [...]}; 400 for any bad id."""
    import json
    if not request.is_json:
        return jsonify({"error": "expected JSON"}), 415
    if (request.content_length or 0) > MAX_BULK_BODY:
        return jsonify({"error": "request too large"}), 413
    raw = request.stream.read(MAX_BULK_BODY + 1)            # chunked bodies: bounded read
    if len(raw) > MAX_BULK_BODY:
        return jsonify({"error": "request too large"}), 413
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, RecursionError):  # deeply nested brackets -> 400, not 500
        data = None
    ids = data.get("ids") if isinstance(data, dict) and set(data) == {"ids"} else None
    # bool is an int subclass in Python: refuse it explicitly, like every other non-integer
    if (not isinstance(ids, list) or not ids
            or not all(type(i) is int and 0 < i <= SQLITE_MAX_INT for i in ids)):
        return jsonify({"error": "ids must be a non-empty list of photo ids"}), 400
    ids = list(dict.fromkeys(ids))                          # drop repeats, keep order
    if len(ids) > MAX_BULK:
        return jsonify({"error": f"at most {MAX_BULK} photos per request"}), 413
    deleted, missing, failed = [], [], []
    for media_id in ids:
        try:
            found, _ = _delete_one(media_id)
        except Exception:  # noqa: BLE001 - one bad photo must not stop the rest; it stays in the library
            current_app.logger.exception("bulk delete of %s failed", media_id)
            failed.append(media_id)
            continue
        (deleted if found else missing).append(media_id)
    if deleted:
        bump_media_rev(current_app)
    return jsonify({"deleted": deleted, "missing": missing, "failed": failed})


# Deletes of the same id are serialised in-process (waitress runs one process, many threads).
# Striped locks keep memory bounded.
_DELETE_LOCKS = [threading.Lock() for _ in range(64)]


def _delete_one(media_id):
    """Delete one photo. Returns (found, files_removed); found is False if the row is already gone."""
    with _DELETE_LOCKS[media_id % len(_DELETE_LOCKS)]:
        data_dir = current_app.config["DATA_DIR"]
        db = get_db()
        rows = db.execute("SELECT stored_name FROM media WHERE id = ?", (media_id,)).fetchall()
        if not rows:
            return False, 0
        stored = rows[0]["stored_name"]
        # 1) move every file into tmp/trash (atomic renames, same filesystem; refuses unsafe names)
        staged = imaging.stage_delete(data_dir, stored, media_id)
        try:
            # 2) remove the row
            gone = db.execute("DELETE FROM media WHERE id = ?", (media_id,)).rowcount
            db.commit()
        except Exception:
            db.rollback()
            imaging.unstage_delete(staged)       # DB step failed: row survives, so put files back
            raise
        # 3) the row is gone (by us, or by another writer): the files must go too, never restore
        removed = imaging.purge_staged(staged)
        if gone == 0:
            return False, removed
        clear_show_now(current_app, stored)
        video.remove_files(data_dir, stored)     # #102: the queued input / partial output, if any
        return True, removed


@bp.route("/media/<int:media_id>/show-now", methods=["POST"])
def show_now(media_id):
    row = _item(media_id)
    if row["state"] != "ready":                     # #102: a clip still converting cannot be shown
        return jsonify({"error": "This video isn't ready yet."}), 409
    import time
    state = current_app.extensions.setdefault("mf_show_now", {"seq": 0, "filename": None, "at": 0})
    state.update(seq=state["seq"] + 1, filename=row["stored_name"], at=time.time())
    return jsonify({"id": media_id, "seq": state["seq"]})
