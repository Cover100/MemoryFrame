"""Memory Frame application package.

create_app() builds the Flask app with three blueprints:
  display  - the on-device slideshow (/, /media, /uploads/<name>, /brightness, /healthz)
  admin    - phone management site (/admin/...)   [pages arrive in MF-12/14/16]
  api      - JSON API for the phone site (/api/v1/...)
"""
import logging

from flask import Flask

from . import db, storage

log = logging.getLogger("memoryframe")


def create_app(config=None):
    app = Flask(
        __name__,
        static_folder="../static",
        template_folder="../templates",
    )
    app.config.update(storage.default_config())
    if config:
        app.config.update(config)

    from . import imaging
    app.config["DISPLAY_TARGET"] = imaging.target_or_default(app.config.get("DISPLAY_SIZE", imaging.DEFAULT_TARGET))
    storage.ensure_layout(app.config["DATA_DIR"])
    db.init_app(app)
    _sweep(app)
    from . import websec
    websec.init_app(app)

    from .display import bp as display_bp
    from .admin import bp as admin_bp
    from .api import bp as api_bp
    from .pairing import bp as pairing_bp

    app.register_blueprint(display_bp)
    app.register_blueprint(admin_bp, url_prefix="/admin")
    app.register_blueprint(api_bp, url_prefix="/api/v1")
    app.register_blueprint(pairing_bp)

    return app


def _sweep(app):
    """MF-16: clean up after an interrupted delete (tmp/trash, orphan derivatives)."""
    from . import imaging
    conn = db.connect(app.config["DB_PATH"])
    try:
        imaging.sweep(conn, app.config["DATA_DIR"])
    except Exception as e:  # noqa: BLE001 - never block start-up
        log.warning("start-up sweep failed: %s", e)
    finally:
        conn.close()
