"""MF-18 (reduced): change the frame's hotspot name/password from a paired phone.

Flow: POST /admin/wifi (validated) -> row `pending` in wifi_change (current profile untouched)
-> after the response the restricted helper (deploy/netmode.sh as a root oneshot unit started via polkit, password via the app's 0700 runtime dir)
starts MemoryFrame-AP-pending -> the frame shows the new Wi-Fi QR + countdown -> a paired phone
on the new network taps "Keep new Wi-Fi" -> commit. Otherwise rollback on timeout, activation
failure or restart. The password is never logged and never returned by any endpoint; it is
only inside the frame's Wi-Fi QR.
"""
import fcntl
import ipaddress
import logging
import os
import re
import threading
import time

from flask import Blueprint, current_app, redirect, render_template, request

from . import auth, frameinfo
from .db import connect, get_db, get_setting, set_setting

log = logging.getLogger(__name__)

WINDOW = 300                       # seconds to confirm
OLD_PROFILE, NEW_PROFILE = "MemoryFrame-hotspot", "MemoryFrame-AP-pending"
HOTSPOT_NET = ipaddress.ip_network("10.42.0.0/24")
LOCK_PATH = "/run/memoryframe/netmode.lock"
COMMITTED_SHOW = 3                 # frame shows "Wi-Fi updated" for 3 s

MSG_NAME = "Name must be 1-32 characters."
MSG_PASSWORD = "Password must be 8-63 letters, numbers or symbols (no emoji or accents)."
MSG_SAME = "That's the current Wi-Fi. Change the name or password."
MSG_BUSY = "A Wi-Fi change is already waiting for confirmation."
MSG_UNAVAILABLE = "Wi-Fi can't be changed right now."
MSG_FAILED = "Couldn't start the new Wi-Fi. Nothing was changed."

bp = Blueprint("wifi", __name__)
bp.before_request(auth.require_session("admin"))
_lock = threading.RLock()


# ---------------- validation ----------------

def validate(ssid, password, current_ssid, current_password):
    """Returns an error message or None. Mirrors deploy/netmode.sh and static/wifi.js."""
    if not isinstance(ssid, str) or not (1 <= len(ssid.encode("utf-8")) <= 32) \
            or any(ord(c) < 32 or 127 <= ord(c) < 160 for c in ssid):
        return MSG_NAME
    if not isinstance(password, str) or not (8 <= len(password) <= 63) \
            or any(not (32 <= ord(c) <= 126) for c in password):
        return MSG_PASSWORD
    if ssid == current_ssid and password == current_password:
        return MSG_SAME
    return None


# ---------------- current Wi-Fi ----------------

def current_wifi(conn):
    """(ssid, password) the hotspot uses now: a committed MF-18 change wins over hotspot.conf."""
    ssid, password = get_setting(conn, "wifi_ssid"), get_setting(conn, "wifi_password")
    if ssid and password:
        return ssid, password
    return frameinfo.hotspot_conf()


def _row(conn):
    return conn.execute("SELECT * FROM wifi_change WHERE id = 1").fetchone()


def pending(conn):
    r = _row(conn)
    return r if r is not None and r["state"] == "pending" else None


def busy():
    """Another network operation (MF-36 mode switch / helper) holds the lock right now."""
    path = current_app.config.get("NETMODE_LOCK", LOCK_PATH)
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    except OSError:
        return True
    finally:
        os.close(fd)


def unavailable():
    return frameinfo.dev_mode() or busy()


# ---------------- helper ----------------

def _helper(app, cmd, stdin=None, out=None):
    from . import privileged
    rc, text = privileged.run(app.config, cmd, stdin=stdin)
    if rc:
        log.warning("network helper %s exited %d", cmd, rc)   # output not logged (may echo input)
    if out is not None:
        for line in (text or "").splitlines():
            if line.startswith("uuid=") and re.fullmatch(r"[0-9A-Za-z-]{1,64}", line[5:]):
                out["uuid"] = line[5:]
    return rc


def _set_state(conn, state, reason=None, only_if="pending"):
    cur = conn.execute("UPDATE wifi_change SET state = ?, reason = ?, new_password = NULL, updated = ?, "
                       "notice_shown = 0 WHERE id = 1 AND state = ?", (state, reason, time.time(), only_if))
    conn.commit()
    return cur.rowcount == 1


def rollback(app, reason):
    """Old Wi-Fi back (helper), row -> rolled_back. Idempotent, safe from any thread."""
    with _lock:
        conn = connect(app.config["DB_PATH"])
        try:
            if pending(conn) is None:
                return False
            _helper(app, "wifi-rollback")
            ok = _set_state(conn, "rolled_back", reason)
        finally:
            conn.close()
    log.warning("Wi-Fi change rolled back (%s)", reason)
    return ok


def _apply_after_response(app, ssid, password, deadline):
    got = {}
    rc = _helper(app, "wifi-apply", stdin=f"{ssid}\n{password}\n", out=got)
    if rc == 0 and got.get("uuid"):
        conn = connect(app.config["DB_PATH"])
        try:
            conn.execute("UPDATE wifi_change SET new_uuid = ? WHERE id = 1 AND state = 'pending'", (got["uuid"],))
            conn.commit()
        finally:
            conn.close()
    if rc != 0:
        rollback(app, "activation")
        return
    delay = max(0.0, deadline - time.time())
    t = threading.Timer(delay, rollback, args=(app, "timeout"))
    t.daemon = True
    t.start()


def _commit_row(conn, r, reason):
    set_setting(conn, "wifi_ssid", r["new_ssid"])
    set_setting(conn, "wifi_password", r["new_password"])
    conn.commit()
    conn.execute("UPDATE wifi_change SET state = 'committed', reason = ?, new_password = NULL, updated = ? "
                 "WHERE id = 1 AND state = 'pending'", (reason, time.time()))
    conn.commit()


def reconcile(conn, nm):
    """Startup: match a 'pending' row with the profiles that actually exist (netmode.json).
    Returns "committed" | "cleared" | "rollback" | None (nothing pending)."""
    r = pending(conn)
    if r is None:
        return None
    new_uuid = r["new_uuid"] if "new_uuid" in r.keys() else None
    if (nm.get("commit_recovered") or (new_uuid and nm.get("hotspot_uuid") == new_uuid)) and r["new_password"]:
        _commit_row(conn, r, "recovered")             # the new profile IS the hotspot now
        log.info("Wi-Fi change found committed at start-up; recorded as kept")
        return "committed"
    if not nm.get("wifi_profiles"):
        # No pending/old profile left (e.g. the boot already cleaned up): nothing to roll back and
        # nothing to announce. Clear silently.
        conn.execute("UPDATE wifi_change SET state = 'cleared', reason = 'reconciled', new_password = NULL, "
                     "updated = ?, notice_shown = 1 WHERE id = 1 AND state = 'pending'", (time.time(),))
        conn.commit()
        log.info("stale pending Wi-Fi change cleared at start-up (no pending profile)")
        return "cleared"
    return "rollback"


def startup_check(app):
    """Reconcile a pending change with the real profiles (see reconcile); only a still-existing
    pending profile is rolled back. After a memoryframe-reset boot the hotspot.conf defaults are
    back: drop the override."""
    conn = connect(app.config["DB_PATH"])
    try:
        nm = frameinfo.netmode_state()
        if nm.get("reset") and nm.get("boot_id") and get_setting(conn, "wifi_reset_boot") != nm["boot_id"]:
            conn.execute("DELETE FROM settings WHERE key IN ('wifi_ssid', 'wifi_password')")
            set_setting(conn, "wifi_reset_boot", nm["boot_id"])
            conn.commit()
        has_pending = reconcile(conn, nm) == "rollback"
    finally:
        conn.close()
    if has_pending:
        threading.Thread(target=rollback, args=(app, "restart"), daemon=True).start()


# ---------------- frame (/pairing/state) ----------------

def frame_state(conn):
    from .pairing import qr_svg
    r = _row(conn)
    if r is None:
        return None
    now = time.time()
    if r["state"] == "pending":
        out = {"state": "pending", "id": int(r["updated"]), "new_ssid": r["new_ssid"],
               "remaining": max(0, int(round(r["deadline"] - now)))}
        if r["new_password"]:
            out["qr_svg"] = qr_svg(frameinfo.wifi_qr_payload(r["new_ssid"], r["new_password"]))
        return out
    if r["state"] == "committed" and now - r["updated"] < COMMITTED_SHOW:
        return {"state": "committed", "id": int(r["updated"]), "new_ssid": r["new_ssid"]}
    if r["state"] == "rolled_back" and now - r["updated"] < 3600:
        ssid, password = current_wifi(conn)
        out = {"state": "rolled_back", "id": int(r["updated"]), "old_ssid": r["old_ssid"]}
        if ssid and password:
            out["qr_svg"] = qr_svg(frameinfo.wifi_qr_payload(ssid, password))
        return out
    return None


def admin_notice(conn):
    """One-shot phone banner after a rollback (5g)."""
    r = _row(conn)
    if r is None or r["state"] != "rolled_back" or r["notice_shown"]:
        return None
    conn.execute("UPDATE wifi_change SET notice_shown = 1 WHERE id = 1")
    conn.commit()
    return f'Wi-Fi change cancelled. The frame is still on "{r["old_ssid"]}".'


# ---------------- phone pages ----------------

def _m_ss(seconds):
    seconds = max(0, int(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


@bp.route("", methods=["GET"])
@bp.route("/", methods=["GET"])
def page():
    conn = get_db()
    if pending(conn) is not None:
        return redirect("/admin/wifi/keep", code=302)
    ssid, _ = current_wifi(conn)
    return render_template("wifi.html", current=ssid or "", name=ssid or "", error=None,
                           unavailable=unavailable(), step="form")


@bp.route("", methods=["POST"])
@bp.route("/", methods=["POST"])
def change():
    app = current_app._get_current_object()
    conn = get_db()
    if pending(conn) is not None:
        return render_template("wifi.html", current="", name="", error=MSG_BUSY, unavailable=True, step="form"), 409
    cur_ssid, cur_password = current_wifi(conn)
    if unavailable():
        return render_template("wifi.html", current=cur_ssid or "", name=cur_ssid or "", error=None,
                               unavailable=True, step="form"), 409
    ssid = request.form.get("ssid", "")
    password = request.form.get("password", "")
    err = validate(ssid, password, cur_ssid, cur_password)
    if err:
        return render_template("wifi.html", current=cur_ssid or "", name=ssid, error=err,
                               unavailable=False, step="form"), 400
    deadline = time.time() + app.config.get("WIFI_WINDOW", WINDOW)
    with _lock:
        if pending(conn) is not None:
            return render_template("wifi.html", current="", name="", error=MSG_BUSY, unavailable=True,
                                   step="form"), 409
        conn.execute("INSERT OR REPLACE INTO wifi_change (id, state, old_ssid, new_ssid, new_password, old_profile, "
                     "new_profile, deadline, reason, updated, notice_shown) VALUES (1, 'pending', ?, ?, ?, ?, ?, ?, "
                     "NULL, ?, 0)", (cur_ssid or "", ssid, password, OLD_PROFILE, NEW_PROFILE, deadline, time.time()))
        conn.commit()
    log.info("Wi-Fi change requested (new name length %d)", len(ssid))
    resp = current_app.make_response(render_template("wifi.html", current=cur_ssid or "", name=ssid,
                                                     error=None, unavailable=False, step="waiting"))
    starter = app.config.get("WIFI_START") or (
        lambda: threading.Thread(target=_apply_after_response, args=(app, ssid, password, deadline),
                                 daemon=True).start())
    resp.call_on_close(starter)
    return resp


def _from_hotspot():
    try:
        return ipaddress.ip_address(request.remote_addr or "") in HOTSPOT_NET
    except ValueError:
        return False


@bp.route("/keep", methods=["GET"])
def keep_page():
    conn = get_db()
    r = pending(conn)
    if r is None:
        return redirect("/admin/", code=302)
    return render_template("wifi_keep.html", ssid=r["new_ssid"], remaining=_m_ss(r["deadline"] - time.time()),
                           result=None, old=r["old_ssid"])


@bp.route("/keep", methods=["POST"])
def keep():
    app = current_app._get_current_object()
    conn = get_db()
    r = _row(conn)
    if r is not None and r["state"] == "committed":
        return redirect("/admin/", code=303)                        # someone else already kept it
    if r is None or r["state"] != "pending" or time.time() > r["deadline"]:
        if r is not None and r["state"] == "pending":
            rollback(app, "timeout")
        old = r["old_ssid"] if r is not None else ""
        return render_template("wifi_keep.html", ssid="", remaining="", result="late", old=old), 409
    if not _from_hotspot():
        return render_template("wifi_keep.html", ssid=r["new_ssid"], remaining=_m_ss(r["deadline"] - time.time()),
                               result="wrongnet", old=r["old_ssid"]), 403
    with _lock:
        r = pending(conn)
        if r is None:
            return redirect("/admin/", code=303)
        if _helper(app, "wifi-commit") != 0:
            rollback(app, "activation")
            return render_template("wifi_keep.html", ssid=r["new_ssid"], remaining="", result="failed"), 500
        _commit_row(conn, r, None)
    log.info("Wi-Fi change kept")
    return render_template("wifi_keep.html", ssid=r["new_ssid"], remaining="", result="kept")
