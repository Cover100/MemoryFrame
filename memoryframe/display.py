"""Display blueprint: the on-device slideshow.

Read routes are open (the kiosk and, later, the phone preview use them).
Write routes are localhost-only (security.localhost_only): only the kiosk
running on the frame itself may change display state.
"""
import logging
import os

from flask import (Blueprint, abort, current_app, jsonify, redirect, render_template, request,
                   send_from_directory)

from . import brightness as bl
from . import imaging, storage
from .db import get_db
from .security import is_local_request, localhost_only

log = logging.getLogger(__name__)

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
    return render_template("index.html", media=get_media(), frame_token=frame_token())


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



# ---------------- MF-49: shut down (kiosk only) ----------------

POWEROFF_CMD = ["systemctl", "poweroff"]   # #62: logind via polkit (no sudo; the service keeps NoNewPrivileges)
SYNC_CMD = ["sync"]
POWEROFF_DELAY = 1.0          # let the kiosk paint "Shutting down…" first


def frame_token():
    """Per-process token embedded only in the kiosk page: CSRF protection for frame actions."""
    import secrets
    ext = current_app.extensions
    if "mf_frame_token" not in ext:
        ext["mf_frame_token"] = secrets.token_urlsafe(24)
    return ext["mf_frame_token"]


def _check_token():
    import hmac
    sent = request.headers.get("X-Frame-Token", "")
    if not sent or not hmac.compare_digest(sent.encode(), frame_token().encode()):
        abort(403)


def _sync():
    """Flush everything to the SD card. True only if sync really succeeded."""
    import subprocess
    try:
        return subprocess.run(list(current_app.config.get("SYNC_CMD") or SYNC_CMD),
                              timeout=60, check=False).returncode == 0
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning("sync failed: %s", type(e).__name__)
        return False


def _schedule_poweroff(app, delay):
    import subprocess
    import threading
    st = app.extensions.setdefault("mf_shutdown", {})
    if st.get("timer"):
        st["timer"].cancel()
    cmd = list(app.config.get("POWEROFF_CMD") or POWEROFF_CMD)

    def run():
        if st.get("done"):
            return
        st["done"] = True
        try:
            subprocess.run(cmd, timeout=30, check=False)
        except (OSError, subprocess.TimeoutExpired) as e:
            log.warning("poweroff failed: %s", type(e).__name__)
    t = threading.Timer(delay, run)
    t.daemon = True
    st["timer"] = t
    t.start()


@bp.before_app_request
def _refuse_writes_while_shutting_down():
    st = current_app.extensions.get("mf_shutdown") or {}
    if st.get("synced") and request.method not in ("GET", "HEAD", "OPTIONS"):
        abort(503)


@bp.route("/frame/shutdown", methods=["POST"])
@localhost_only
def shutdown():
    """MF-49: stop accepting writes, sync, then power off. "Safe to unplug" is drawn later by the
    systemd-shutdown hook (deploy/safe-screen), after filesystems are unmounted/read-only."""
    _check_token()
    app = current_app._get_current_object()
    st = app.extensions.setdefault("mf_shutdown", {})
    st["synced"] = True                       # refuse further writes from now on
    log.warning("shutdown requested from the frame menu; syncing")
    if not _sync():
        st["synced"] = False
        return jsonify({"shutting_down": False}), 500
    _schedule_poweroff(app, float(app.config.get("POWEROFF_DELAY", POWEROFF_DELAY)))
    return jsonify({"shutting_down": True})


# ---------------- MF-51: Dev mode until reboot (kiosk only) ----------------

DEV_MODE_UNIT = "memoryframe-devtemp.service"   # #62: root oneshot started via polkit


@bp.route("/frame/dev-mode", methods=["POST"])
@localhost_only
def dev_mode():
    """Run the helper now and report the result as JSON: {"ok": bool, "reason": str}.
    The kiosk is on localhost, so switching Wi-Fi doesn't cut this request."""
    from . import privileged
    _check_token()
    log.warning("dev mode (until reboot) requested from the frame menu")
    cfg = current_app.config
    timeout = float(cfg.get("DEV_MODE_TIMEOUT", 120))
    if cfg.get("DEV_MODE_CMD"):                    # tests: a stub argv instead of the unit
        import subprocess
        try:
            p = subprocess.run(list(current_app.config["DEV_MODE_CMD"]), timeout=timeout, check=False,
                               capture_output=True, text=True)
            rc, text = p.returncode, p.stdout + "\n" + p.stderr
        except subprocess.TimeoutExpired:
            rc, text = 124, ""
        except OSError:
            rc, text = 99, ""
    else:
        rc, text = privileged.run(cfg, "dev-temp", timeout=timeout)
    if rc == 124:
        return jsonify({"ok": False, "reason": "timed out"}), 502
    if rc == 99:
        return jsonify({"ok": False, "reason": "could not run the helper"}), 502
    if rc == 0:
        return jsonify({"ok": True, "reason": ""})
    text = text.lower()
    if "access denied" in text or "interactive authentication" in text or "not authorized" in text:
        reason = "not permitted (polkit rule missing)"
    elif rc == 3 or "busy" in text:
        reason = "busy (another network change is in progress)"
    elif "no matching networkmanager wi-fi profile" in text:
        reason = "no saved home Wi-Fi on this frame"
    else:
        reason = "home Wi-Fi did not connect"
    log.warning("dev mode switch failed (exit %s): %s", rc, reason)
    return jsonify({"ok": False, "reason": reason}), 502
