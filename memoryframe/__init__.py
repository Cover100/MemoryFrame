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
    from . import message
    message.sweep_tmp(app.config["DATA_DIR"])        # #73: crash leftovers
    db.init_app(app)
    from . import assets
    assets.init_app(app)
    _sweep(app)
    if not app.config.get("TESTING") or app.config.get("WIFI_STARTUP_CHECK"):
        from . import wifi
        wifi.startup_check(app)
    _refresh_copies_in_background(app)
    from . import websec
    websec.init_app(app)

    from .display import bp as display_bp
    from .admin import bp as admin_bp
    from .api import bp as api_bp
    from .pairing import bp as pairing_bp

    app.register_blueprint(display_bp)
    app.register_blueprint(admin_bp, url_prefix="/admin")
    from .wifi import bp as wifi_bp
    app.register_blueprint(wifi_bp, url_prefix="/admin/wifi")
    app.register_blueprint(api_bp, url_prefix="/api/v1")
    app.register_blueprint(pairing_bp)

    return app


def _refresh_copies_in_background(app):
    """Remake stale display copies (older rules or another MEMORYFRAME_DISPLAY_SIZE) after a
    deploy, without blocking start-up; the slideshow also remakes them lazily on serve."""
    if app.config.get("TESTING") and not app.config.get("REFRESH_COPIES_AT_START"):
        return
    import threading
    from . import imaging

    def run():
        conn = db.connect(app.config["DB_PATH"])
        try:
            made, failed = imaging.backfill(conn, app.config["DATA_DIR"], app.config["DISPLAY_TARGET"])
            if made or failed:
                app.logger.warning("display copies refreshed at start: %d made, %d failed", made, failed)
        except Exception as e:  # noqa: BLE001
            app.logger.warning("display copy refresh failed: %s", e)
        finally:
            conn.close()

    threading.Thread(target=run, name="mf-refresh-copies", daemon=True).start()


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
