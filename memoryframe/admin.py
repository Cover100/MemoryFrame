"""Admin blueprint: the phone management site.

MF-11 only reserves the URL space. Pairing/auth (MF-12), upload (MF-14) and
the library (MF-16) add the real pages; every route here will require a
session from MF-12 on.
"""
from flask import Blueprint

bp = Blueprint("admin", __name__)


@bp.route("/")
def home():
    return ("<!doctype html><title>Memory Frame</title>"
            "<p>Memory Frame management site: coming soon.</p>")
