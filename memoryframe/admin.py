"""Admin blueprint: the phone management site.

Every route requires a paired session (MF-12); unauthenticated requests are
redirected to /pair. Upload (MF-14) and the library (MF-16) add the real pages.
"""
from flask import Blueprint, g, redirect, render_template, request

from . import auth
from .db import get_db

bp = Blueprint("admin", __name__)
bp.before_request(auth.require_session("admin"))

PREVIEW = 9        # home library preview: 3 x 3 thumbnails


def slide_label(v):
    """5 s / 1 min / 5 min, as on the Settings segments."""
    return f"{v} s" if v < 60 else ("1 min" if v == 60 else f"{v // 60} min")


@bp.app_context_processor
def _phone_context():
    def phone_wifi():
        # Offline panel (#70 mock 08): which Wi-Fi to rejoin. Paired pages only (pair.html leaves it out).
        # Juniper's #77 review: no SSID unless this visitor is paired, and never the hotspot SSID in dev
        # mode (the frame is on the developer's Wi-Fi then, and the hotspot name would be wrong and leak).
        from . import frameinfo, wifi
        device = g.get("device_id")                         # set by require_session; else one cookie/session lookup
        if device is None:
            device = auth.current_device()
        if device is None:
            return {"ssid": "", "mode": "unknown"}          # unpaired visitors never see the SSID
        try:
            mode = frameinfo.network().get("mode")
        except Exception:  # noqa: BLE001 - never break a page over this hint
            mode = "unknown"
        if frameinfo.dev_mode():
            return {"ssid": "", "mode": mode}
        return {"ssid": wifi.current_wifi(get_db())[0] or "", "mode": mode}
    return {"phone_wifi": phone_wifi, "slide_label": slide_label, "phone_storage": phone_storage}


def phone_storage():
    """#79: the free-space meter, with the frame menu's numbers (#74: disk free / total, used = 1 - free/total)."""
    from flask import current_app
    from . import upload
    c = current_app.config
    try:
        from . import storage
        s = upload.storage_info(c["DATA_DIR"], storage.min_free(c), c["AVG_PHOTO_BYTES"])
    except OSError:
        return None
    total, free = s["total"], s["free"]
    used = min(1.0, max(0.0, 1 - free / total)) if total > 0 else 0.0
    gb = lambda n: f"{n / 1073741824:.1f}"                                       # noqa: E731
    return {"free_gb": gb(free), "total_gb": gb(total), "used": f"{used:.3f}", "pct": round(used * 100),
            "photos_left": s["photos_remaining_estimate"]}


_PHONE_ERRORS = {404: ("Page not found", "This page isn't on the frame."),
                 405: ("That didn't work", "This page can't be opened that way."),
                 500: ("Something went wrong", "The frame couldn't show this page. Try again in a moment.")}


def _phone_error(e):
    """#79: phone pages that fail (404 / 405 / 500) stay on the phone (dark) theme; API and frame paths keep the default."""
    from werkzeug.exceptions import HTTPException, InternalServerError
    code = e.code if isinstance(e, HTTPException) else 500
    p = request.path
    if code not in _PHONE_ERRORS or not (p == "/pair" or p.startswith(("/admin", "/pair/"))):
        return e if isinstance(e, HTTPException) else InternalServerError()
    title, text = _PHONE_ERRORS[code]
    try:
        return render_template("phone_error.html", title=title, text=text), code
    except Exception:  # noqa: BLE001 - never fail the error page itself
        return e if isinstance(e, HTTPException) else InternalServerError()


for _code in _PHONE_ERRORS:
    bp.app_errorhandler(_code)(_phone_error)


@bp.route("/")
def home():
    from . import wifi
    conn = get_db()
    if wifi.pending(conn) is not None:              # MF-18 5f: on the new Wi-Fi -> confirm page
        return redirect("/admin/wifi/keep", code=302)
    from . import settings
    # Restyle PR C (#70 mock 02): home and library on one page (Upload, 3x3 preview, rows).
    preview = conn.execute("SELECT id, original_name FROM media ORDER BY created_at DESC, id DESC "
                           "LIMIT ?", (PREVIEW,)).fetchall()
    total = conn.execute("SELECT COUNT(*) FROM media").fetchone()[0]
    # #92: the Wi-Fi row names the configured frame Wi-Fi to a PAIRED phone, in dev mode too (the Wi-Fi page shows
    # the same name). Only the offline panel (phone_wifi) hides it in dev: there it says which network to rejoin.
    return render_template("admin_home.html", wifi_notice=wifi.admin_notice(conn),
                           just_paired=request.args.get("paired") == "1", preview=preview, total=total,
                           st=phone_storage(), s=settings.get_all(conn), ssid=wifi.current_wifi(conn)[0] or "")


@bp.route("/sign-out-all", methods=["POST"])
def sign_out_all():
    auth.sign_out_all(get_db())
    return redirect("/pair", code=303)


@bp.route("/upload")
def upload_page():
    from flask import current_app
    from . import imaging
    store = imaging.stored_size(current_app.config["DISPLAY_TARGET"])      # #82: the browser shrinks to this
    return render_template("upload.html", st=phone_storage(), store_w=store[0], store_h=store[1])


@bp.route("/library")
def library_page():
    """#80 (phone/09-LIBRARY.md): one grid, newest first, rendered here so it shows without a second request.
    Only ids and kinds go to the page (tile labels are "Photo 4 of 128", never file names)."""
    items = get_db().execute("SELECT id, kind FROM media ORDER BY created_at DESC, id DESC").fetchall()
    return render_template("library.html", items=items, n=len(items), st=phone_storage())


@bp.route("/settings")
def settings_page():
    from flask import current_app
    from . import frameinfo, message, settings, wifi
    net = frameinfo.network()
    about = {"ip": ", ".join(net.get("ips") or []) or net.get("lan_ip") or "Unknown",
             "site": frameinfo.site_urls(net)["primary"], "version": frameinfo.version(),
             "wifi": wifi.current_wifi(get_db())[0] or "Unknown", "phones": auth.paired_count(get_db())}
    return render_template("settings.html", s=settings.get_all(get_db()), slides=settings.SLIDE_SECONDS,
                           message=message.get(current_app.config["DATA_DIR"]),
                           message_max=message.MAX_LEN, about=about)
