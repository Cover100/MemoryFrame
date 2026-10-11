"""Pairing blueprint (MF-12).

  GET  /pair                   phone: code entry page (or QR landing page with ?t=)
  POST /pair                   phone: redeem code/token -> session cookie -> /admin/
  GET  /pairing/state          frame (localhost): paired count, live code + QR, SSID
  POST /pairing/start          frame (localhost): new 6-digit code + QR token
  POST /pairing/sign-out-all   frame (localhost): revoke every phone session
  GET  /frame/status           frame (localhost): IP, SSID, free storage, version (MF-19 menu)
"""
import os

from flask import Blueprint, current_app, jsonify, make_response, redirect, render_template, request

from . import auth, frameinfo, storage
from .db import get_db
from .security import localhost_only

bp = Blueprint("pairing", __name__)


def ssid():
    return frameinfo.hotspot_conf()[0]


QR_BORDER = 4


def qr_svg(data):
    import segno
    # MF-23: 4-module white quiet zone (QR spec minimum) and a viewBox with no fixed size, so CSS
    # scales the whole symbol + margin to fit the 800x480 screen instead of cropping it.
    svg = segno.make(data, error="m").svg_inline(border=QR_BORDER, omitsize=True, dark="#000", light="#fff")
    return svg.replace("<svg ", f'<svg data-border="{QR_BORDER}" preserveAspectRatio="xMidYMid meet" ', 1)


def _state():
    rnd = auth.pairing().current()
    urls = frameinfo.site_urls()
    base = current_app.config.get("PAIR_BASE_URL") or urls["primary"]
    fallback = None if current_app.config.get("PAIR_BASE_URL") else urls["fallback"]
    conn = get_db()
    name, password = frameinfo.hotspot_conf()
    out = {"paired": auth.paired_count(conn), "ssid": name, "url": base + "/pair", "round": None,
           "network": urls["mode"], "url_fallback": fallback + "/pair" if fallback else None,
           "site_url": base + "/", "media": conn.execute("SELECT COUNT(*) FROM media WHERE hidden = 0").fetchone()[0],
           "wifi_qr_svg": None}
    # MF-19 / D3: Wi-Fi join QR (shown before the site QR), only while no phone is paired and
    # only when the frame's hotspot is the active network (on home Wi-Fi it would not exist).
    if (out["paired"] == 0 and urls["mode"] == "hotspot" and name and password
            and frameinfo.show_wifi_qr(conn)):
        out["wifi_qr_svg"] = qr_svg(frameinfo.wifi_qr_payload(name, password))
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


@bp.route("/frame/status")
@localhost_only
def frame_status():
    name, _ = frameinfo.hotspot_conf()
    urls = frameinfo.site_urls()
    return jsonify({"ips": frameinfo.own_ips(), "ssid": name, "version": frameinfo.version(),
                    "network": urls["mode"], "site": urls["primary"] + "/", "site_fallback":
                    urls["fallback"] + "/" if urls["fallback"] else None,
                    "storage": frameinfo.free_storage(current_app.config["DATA_DIR"]),
                    "paired": auth.paired_count(get_db())})
