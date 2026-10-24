"""#68: set or change the developer (home) Wi-Fi from the paired phone (Juniper's design on issue #68).

Developer feature only. It exists while developer mode has fallen back to the hotspot (netmode.json:
requested dev|dev-temp, dev_fallback true) and the phone is on the hotspot; otherwise 404 and no menu
entry. The app validates, writes a v1 request (SSID as hex, password) to its own 0700 runtime dir with
O_EXCL|O_NOFOLLOW (privileged.run) and starts memoryframe-home-wifi.service via polkit after the
response. The root helper (deploy/netmode.sh home-wifi) tests it with the hotspot down and always brings
the hotspot back; saving never switches the frame. The password is never logged, stored in the DB,
returned, or put in argv. Status comes from /run/memoryframe/home-wifi.json (no secrets).
"""
import collections
import json
import logging
import os
import threading
import time

from flask import Blueprint, abort, current_app, jsonify, render_template, request

from . import auth, frameinfo, wifi

log = logging.getLogger(__name__)

bp = Blueprint("homewifi", __name__)
bp.before_request(auth.require_session("admin"))

MAX_BODY = 512
RATE_N, RATE_WINDOW = 3, 600
STATE_FILE = "/run/memoryframe/home-wifi.json"
REASONS = {"ok", "connect_failed", "timeout", "busy", "invalid"}
_attempts = collections.deque()
_lock = threading.Lock()
_running = {"since": 0.0}


def available():
    """The gate: dev mode fell back to the hotspot. (Test hook on the Pi: /run/memoryframe/force-dev-fallback,
    read by netmode.sh in dev mode only; it produces exactly this state.)"""
    nm = frameinfo.netmode_state()
    return nm["mode"] == "product" and nm["dev_fallback"] and nm.get("requested") in ("dev", "dev-temp")


def _gate():
    if not available() or not wifi._from_hotspot():
        abort(404)


@bp.after_request
def _no_store(resp):
    resp.headers["Cache-Control"] = "no-store"
    return resp


def valid_ssid(ssid, hotspot_ssid):
    return (isinstance(ssid, str) and 1 <= len(ssid.encode("utf-8")) <= 32
            and not any(ord(c) < 32 or ord(c) == 127 for c in ssid) and ssid != hotspot_ssid)


def valid_psk(psk):
    if not isinstance(psk, str):
        return False
    if len(psk) == 64 and all(c in "0123456789abcdefABCDEF" for c in psk):
        return True
    return 8 <= len(psk) <= 63 and all(32 <= ord(c) <= 126 for c in psk)


def _state_path():
    return current_app.config.get("HOME_WIFI_STATE") or STATE_FILE


def read_status():
    out = {"state": "idle", "ts": 0, "ssid": "", "reason": ""}
    try:
        with open(_state_path(), "rb") as f:
            raw = json.loads(f.read(4096).decode("utf-8", "replace"))
    except (OSError, ValueError):
        raw = {}
    if isinstance(raw, dict) and raw.get("state") in ("testing", "saved", "failed"):
        out["state"] = raw["state"]
        out["reason"] = raw.get("reason") if raw.get("reason") in REASONS else ""
        out["ts"] = raw.get("ts") if isinstance(raw.get("ts"), int) else 0
        try:
            out["ssid"] = bytes.fromhex(raw.get("ssid_hex") or "").decode("utf-8", "replace")
        except (TypeError, ValueError):
            out["ssid"] = ""
    if time.time() - _running["since"] < 150 and out["state"] != "testing" and out["ts"] < _running["since"]:
        out.update(state="testing", reason="")             # started, helper not yet at its first write
    return out


@bp.route("", methods=["GET"])
@bp.route("/", methods=["GET"])
def page():
    _gate()
    st = read_status()
    return render_template("home_wifi.html", saved=st["ssid"] if st["state"] == "saved" else "")


@bp.route("/status", methods=["GET"])
def status():
    _gate()
    return jsonify(read_status())


def _bad(code, http=400):
    return jsonify({"error": code}), http


@bp.route("", methods=["POST"])
@bp.route("/", methods=["POST"])
def change():
    _gate()
    if (request.content_length or 0) > MAX_BODY:
        return _bad("too_large", 413)
    if request.mimetype != "application/json":
        return _bad("json_only", 415)
    data = request.get_data(cache=False)
    if len(data) > MAX_BODY:
        return _bad("too_large", 413)
    try:
        body = json.loads(data)
    except ValueError:
        return _bad("bad_request")
    if not isinstance(body, dict) or set(body) != {"ssid", "password", "confirm"} or body["confirm"] is not True:
        return _bad("bad_request")
    ssid, psk = body["ssid"], body["password"]
    if not valid_ssid(ssid, frameinfo.netmode_state()["hotspot_ssid"]):
        return _bad("invalid_ssid")
    if not valid_psk(psk):
        return _bad("invalid_password")
    now = time.time()
    with _lock:
        while _attempts and now - _attempts[0] > RATE_WINDOW:
            _attempts.popleft()
        if len(_attempts) >= RATE_N:
            return _bad("cooldown", 429)
        if read_status()["state"] == "testing":
            return _bad("busy", 409)
        _attempts.append(now)
        _running["since"] = now
    req = "v1\n%s\n%s\n" % (ssid.encode("utf-8").hex(), psk)
    log.info("home Wi-Fi change requested (ssid_len %d)", len(ssid.encode("utf-8")))   # audit: no SSID, no password
    app = current_app._get_current_object()
    starter = app.config.get("HOME_WIFI_START") or (
        lambda: threading.Thread(target=_run, args=(app, req), daemon=True).start())
    resp = jsonify({"state": "testing"})
    resp.status_code = 202
    resp.call_on_close(starter)
    return resp


def _run(app, req):
    from . import privileged
    rc, _ = privileged.run(app.config, "home-wifi", stdin=req)   # output never logged
    log.info("home Wi-Fi change finished (%s)", "saved" if rc == 0 else "failed")
