"""Admin blueprint: the phone management site.

Every route requires a paired session (MF-12); unauthenticated requests are
redirected to /pair. Upload (MF-14) and the library (MF-16) add the real pages.
"""
from flask import Blueprint, redirect, render_template, request

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
        from . import frameinfo, wifi
        try:
            mode = frameinfo.network().get("mode")
        except Exception:  # noqa: BLE001 - never break a page over this hint
            mode = "unknown"
        return {"ssid": wifi.current_wifi(get_db())[0] or "", "mode": mode}
    return {"phone_wifi": phone_wifi, "slide_label": slide_label}


@bp.route("/")
def home():
    from . import wifi
    conn = get_db()
    if wifi.pending(conn) is not None:              # MF-18 5f: on the new Wi-Fi -> confirm page
        return redirect("/admin/wifi/keep", code=302)
    from flask import current_app
    from . import frameinfo, settings
    # Restyle PR C (#70 mock 02): home and library on one page (Upload, 3x3 preview, rows).
    preview = conn.execute("SELECT id, original_name, hidden FROM media ORDER BY created_at DESC, id DESC "
                           "LIMIT ?", (PREVIEW,)).fetchall()
    total = conn.execute("SELECT COUNT(*) FROM media").fetchone()[0]
    try:
        free = frameinfo.free_storage(current_app.config["DATA_DIR"])["free"]
    except OSError:
        free = None
    return render_template("admin_home.html", wifi_notice=wifi.admin_notice(conn),
                           just_paired=request.args.get("paired") == "1", preview=preview, total=total,
                           free_gb=None if free is None else f"{free / 1073741824:.1f}",
                           s=settings.get_all(conn), ssid=wifi.current_wifi(conn)[0] or "")


@bp.route("/sign-out-all", methods=["POST"])
def sign_out_all():
    auth.sign_out_all(get_db())
    return redirect("/pair", code=303)


@bp.route("/upload")
def upload_page():
    return render_template("upload.html")


@bp.route("/library")
def library_page():
    return render_template("library.html")


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
