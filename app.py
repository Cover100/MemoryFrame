"""Memory Frame entry point.

The application lives in the memoryframe package (blueprints, storage, SQLite).
This module keeps `python app.py` working and exposes `app` for WSGI servers.
"""
import os
import platform
import threading
import time
import webbrowser

from memoryframe import create_app
from memoryframe.migrate import MigrationError, migrate_legacy

app = create_app()


# ======================================================
# Launch Browser
# ======================================================

def launch_browser():
    """Development helper: open the default browser once the server is up."""

    time.sleep(3)

    webbrowser.open("http://localhost:5000")


# ======================================================
# Start application
# ======================================================

def main():

    host = os.environ.get("MEMORYFRAME_HOST", "0.0.0.0")

    port = int(os.environ.get("MEMORYFRAME_PORT", "5000"))

    # On the Pi the kiosk is started by deploy/kiosk.sh after /healthz
    # answers. For development (Windows, or MEMORYFRAME_OPEN_BROWSER=1)
    # open a browser from here instead.
    if (
        platform.system() == "Windows"
        or os.environ.get("MEMORYFRAME_OPEN_BROWSER") == "1"
    ):
        threading.Thread(target=launch_browser, daemon=True).start()

    # One-time import of the legacy uploads/ folder (with a verified backup first).
    if os.environ.get("MEMORYFRAME_AUTO_MIGRATE", "1") == "1":
        try:
            migrate_legacy(app.config, log=lambda m: print(f"migrate: {m}"))
        except MigrationError as e:
            print(f"migrate: ERROR: {e} - slideshow will only show already imported media")

    from waitress import serve

    print(f"Memory Frame serving on http://{host}:{port}")

    serve(app, host=host, port=port, threads=4)


if __name__ == "__main__":

    main()
