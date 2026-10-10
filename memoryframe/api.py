"""API blueprint (/api/v1). Write endpoints arrive with MF-12/MF-14."""
from flask import Blueprint, jsonify

from .db import get_db

bp = Blueprint("api", __name__)


@bp.route("/status")
def status():
    db = get_db()
    total = db.execute("SELECT COUNT(*) FROM media").fetchone()[0]
    visible = db.execute("SELECT COUNT(*) FROM media WHERE hidden = 0").fetchone()[0]
    return jsonify({"media": {"total": total, "visible": visible}})
