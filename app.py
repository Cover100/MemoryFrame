"""Memory Frame entry point.

The application lives in the memoryframe package (blueprints, storage, SQLite).
This module keeps `python app.py` working and exposes `app` for WSGI servers.
"""
import os
import platform
import threading
import time
import webbrowser

from memoryframe import create_app, watchdog
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

    # MEMORYFRAME_PORTS="80,5000" listens on several ports (the Pi service uses
    # 80 for http://10.42.0.1/ and keeps 5000 for existing bookmarks/tools).
    # MEMORYFRAME_PORT (single port) is still honoured; default 5000.
    ports_env = os.environ.get("MEMORYFRAME_PORTS") or os.environ.get("MEMORYFRAME_PORT", "5000")
    ports = [int(p) for p in ports_env.replace(" ", "").split(",") if p]

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

    listen = " ".join(f"{host}:{p}" for p in ports)

    print(f"Memory Frame serving on {listen}")

    progress = watchdog.Progress(app.wsgi_app)       # #95: upload progress, so a busy upload isn't a hang
    app.wsgi_app = progress
    watchdog.start(app, progress, ports[-1] if 5000 not in ports else 5000)

    serve(app, listen=listen, threads=4)


if __name__ == "__main__":

    main()
