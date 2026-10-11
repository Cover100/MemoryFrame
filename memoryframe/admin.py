"""Admin blueprint: the phone management site.

Every route requires a paired session (MF-12); unauthenticated requests are
redirected to /pair. Upload (MF-14) and the library (MF-16) add the real pages.
"""
from flask import Blueprint, redirect, render_template

from . import auth
from .db import get_db

bp = Blueprint("admin", __name__)
bp.before_request(auth.require_session("admin"))


@bp.route("/")
def home():
    return render_template("admin_home.html")


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
    from . import settings
    return render_template("settings.html", s=settings.get_all(get_db()), slides=settings.SLIDE_SECONDS)
