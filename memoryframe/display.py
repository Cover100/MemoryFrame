"""Display blueprint: the on-device slideshow.

Read routes are open (the kiosk and, later, the phone preview use them).
Write routes are localhost-only (security.localhost_only): only the kiosk
running on the frame itself may change display state.
"""
import os

from flask import (Blueprint, abort, current_app, jsonify, redirect, render_template, request,
                   send_from_directory)

from . import brightness as bl
from . import imaging, storage
from .db import get_db
from .security import is_local_request, localhost_only

bp = Blueprint("display", __name__)


def get_media():
    """Visible media for the slideshow, same JSON shape as before MF-11."""
    rows = get_db().execute(
        "SELECT stored_name, kind FROM media WHERE hidden = 0 "
        "ORDER BY lower(original_name), id").fetchall()
    return [{"type": r["kind"], "filename": r["stored_name"]} for r in rows]


@bp.route("/")
def index():
    # The slideshow is for the frame's own kiosk browser only. Phones (NFC tag / QR /
    # typing http://10.42.0.1/) go to the phone site, which sends unpaired phones to /pair.
    # Decided by the TCP peer address (request.remote_addr), never by the Host header.
    if not is_local_request():
        return redirect("/admin/", code=302)
    return render_template("index.html", media=get_media())


@bp.route("/media")
def media():
    return jsonify(get_media())


@bp.route("/uploads/<path:filename>")
def uploads(filename):
    # Only files known to the library are served (no directory browsing).
    row = get_db().execute(
        "SELECT 1 FROM media WHERE stored_name = ? AND hidden = 0", (filename,)).fetchone()
    if row is None:
        abort(404)
    # MF-15: serve the downscaled display copy (made on first request if the
    # backfill has not run yet); originals are never modified.
    served = imaging.ensure_display_copy(
        current_app.config["DATA_DIR"], filename, current_app.config["DISPLAY_TARGET"])
    return send_from_directory(os.path.dirname(served), os.path.basename(served))


@bp.route("/show-now")
def show_now_state():
    """MF-16: the frame polls this; a new seq means "jump to filename now"."""
    state = current_app.extensions.get("mf_show_now", {"seq": 0, "filename": None})
    name = state["filename"]
    if name and get_db().execute("SELECT 1 FROM media WHERE stored_name = ? AND hidden = 0",
                                 (name,)).fetchone() is None:
        name = None                                  # deleted or hidden since
    from . import settings
    return jsonify({"seq": state["seq"], "filename": name,
                    "rev": current_app.extensions.get("mf_media_rev", 0),
                    "settings": settings.get_all(get_db())})       # MF-17: applied live by the frame


def clear_show_now(app, stored_name):
    """A deleted or hidden photo must never be offered by /show-now."""
    state = app.extensions.get("mf_show_now")
    if state and state.get("filename") == stored_name:
        state["filename"] = None


def bump_media_rev(app):
    """Library changed (upload/hide/delete): the frame refreshes on its next 2 s poll."""
    app.extensions["mf_media_rev"] = app.extensions.get("mf_media_rev", 0) + 1


@bp.route("/healthz")
def healthz():
    return jsonify({"status": "ok"})


# ---------------- brightness (MF-03 contract, unchanged) ----------------

def _unsupported():
    return jsonify({
        "status": "unsupported",
        "supported": False,
        "message": "Brightness control is only available on a Raspberry Pi with a DSI backlight.",
    })


@bp.route("/brightness", methods=["GET"])
def get_brightness():
    backlight = bl.find_backlight()
    if backlight is None:
        return _unsupported()
    try:
        current = bl.read_int(os.path.join(backlight, "brightness"))
        maximum = bl.read_int(os.path.join(backlight, "max_brightness"))
    except (OSError, ValueError) as e:
        current_app.logger.warning("Brightness read failed: %s", e)
        return _unsupported()
    return jsonify({
        "status": "success", "supported": True, "brightness": current,
        "minimum": min(bl.MIN_BRIGHTNESS, maximum), "maximum": maximum,
    })


@bp.route("/brightness", methods=["POST"])
@localhost_only
def set_brightness():
    data = request.get_json(silent=True)
    value = data.get("value") if isinstance(data, dict) else None
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or value != value or value in (float("inf"), float("-inf"))):
        return jsonify({"status": "error",
                        "message": "Body must be JSON like {\"value\": <number>}."}), 400
    backlight = bl.find_backlight()
    if backlight is None:
        return _unsupported(), 409
    try:
        maximum = bl.read_int(os.path.join(backlight, "max_brightness"))
        minimum = min(bl.MIN_BRIGHTNESS, maximum)
        value = int(round(max(minimum, min(value, maximum))))
        with open(os.path.join(backlight, "brightness"), "w") as f:
            f.write(str(value))
    except (OSError, ValueError) as e:
        current_app.logger.error("Brightness write failed: %s", e)
        return jsonify({"status": "error", "message": "Could not write backlight brightness."}), 503
    return jsonify({"status": "success", "supported": True, "brightness": value,
                    "minimum": minimum, "maximum": maximum})
