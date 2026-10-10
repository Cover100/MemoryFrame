"""API blueprint (/api/v1). Every route requires a paired session (MF-12): 401 otherwise."""
from flask import Blueprint, jsonify

from . import auth
from .db import get_db

bp = Blueprint("api", __name__)
bp.before_request(auth.require_session("api"))


@bp.route("/status")
def status():
    db = get_db()
    total = db.execute("SELECT COUNT(*) FROM media").fetchone()[0]
    visible = db.execute("SELECT COUNT(*) FROM media WHERE hidden = 0").fetchone()[0]
    return jsonify({"media": {"total": total, "visible": visible}})
