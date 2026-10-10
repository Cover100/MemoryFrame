"""Pairing blueprint (MF-12).

  GET  /pair                   phone: code entry page (or QR landing page with ?t=)
  POST /pair                   phone: redeem code/token -> session cookie -> /admin/
  GET  /pairing/state          frame (localhost): paired count, live code + QR, SSID
  POST /pairing/start          frame (localhost): new 6-digit code + QR token
  POST /pairing/sign-out-all   frame (localhost): revoke every phone session
"""
import os

from flask import Blueprint, current_app, jsonify, make_response, redirect, render_template, request

from . import auth, storage
from .db import get_db
from .security import localhost_only

bp = Blueprint("pairing", __name__)


def ssid():
    if os.environ.get("MEMORYFRAME_SSID"):
        return os.environ["MEMORYFRAME_SSID"]
    conf = os.path.join(storage.REPO_DIR, "deploy", "hotspot.conf")
    try:
        with open(conf) as f:
            for line in f:
                if line.strip().startswith("SSID="):
                    return line.split("=", 1)[1].strip().strip('"\'')
    except OSError:
        pass
    return None


def qr_svg(data):
    import segno
    return segno.make(data, error="m").svg_inline(scale=6, border=2, dark="#000", light="#fff")


def _state():
    rnd = auth.pairing().current()
    base = current_app.config.get("PAIR_BASE_URL", "http://10.42.0.1")
    out = {"paired": auth.paired_count(get_db()), "ssid": ssid(), "url": base + "/pair", "round": None}
    if rnd:
        link = f"{base}/pair?t={rnd['token']}"
        out["round"] = {"code": rnd["code"], "expires_in": rnd["expires_in"], "qr_svg": qr_svg(link)}
    return out


@bp.route("/pairing/state")
@localhost_only
def state():
    return jsonify(_state())


@bp.route("/pairing/start", methods=["POST"])
@localhost_only
def start():
    auth.pairing().start()
    return jsonify(_state())


@bp.route("/pairing/sign-out-all", methods=["POST"])
@localhost_only
def frame_sign_out_all():
    return jsonify({"revoked": auth.sign_out_all(get_db())})


@bp.route("/pair")
def pair_page():
    if auth.current_device() is not None:
        return redirect("/admin/", code=302)
    return render_template("pair.html", token=request.args.get("t", ""), error=None)


@bp.route("/pair", methods=["POST"])
def pair():
    p = auth.pairing()
    wants_json = request.is_json
    data = request.get_json(silent=True) if wants_json else request.form
    data = data if isinstance(data, dict) or hasattr(data, "get") else {}
    if p.rate_limited(request.remote_addr or "?"):
        msg = "Too many attempts. Wait a minute and try again."
        if wants_json:
            return jsonify({"error": msg}), 429
        return render_template("pair.html", token="", error=msg), 429
    if not p.redeem(code=data.get("code"), token=data.get("token")):
        msg = "That code is wrong or has expired. Check the code on the frame."
        if wants_json:
            return jsonify({"error": msg}), 400
        return render_template("pair.html", token="", error=msg), 400
    token = auth.create_session(get_db(), request.headers.get("User-Agent", "phone"))
    resp = jsonify({"paired": True, "next": "/admin/"}) if wants_json else redirect("/admin/", code=303)
    return auth.set_session_cookie(make_response(resp), token)
