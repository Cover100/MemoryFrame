from flask import Flask, render_template, send_from_directory, jsonify, request
import os
import subprocess
import time
import platform
import webbrowser

app = Flask(__name__)

# ======================================================
# Configuration
# ======================================================

UPLOAD_FOLDER = "uploads"

IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".gif"
}

VIDEO_EXTENSIONS = {
    ".mp4",
    ".mov",
    ".webm"
}

# Raspberry Pi DSI backlight
# The official DSI display usually exposes /sys/class/backlight/10-0045, but
# the node name can differ, so it is detected at runtime.
# Override with MEMORYFRAME_BACKLIGHT=/sys/class/backlight/<node>.

BACKLIGHT_ROOT = "/sys/class/backlight"

PREFERRED_BACKLIGHT = "10-0045"

MIN_BRIGHTNESS = 40

# ======================================================
# Launch Browser
# ======================================================

def launch_browser():

    time.sleep(3)

    # Windows
    if platform.system() == "Windows":
        webbrowser.open("http://localhost:5000")
        return

    # Raspberry Pi / Linux
    try:

        result = subprocess.run(
            ["pgrep", "chromium"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

        if result.returncode != 0:

            subprocess.Popen([

                "chromium",

                "--kiosk",

                "--noerrdialogs",

                "--disable-infobars",

                "--disable-session-crashed-bubble",

                "--disable-features=Translate",

                "http://localhost:5000"

            ])

    except FileNotFoundError:
        print("Chromium not found.")

# ======================================================
# Media Scanner
# ======================================================

def get_media():

    media = []

    if not os.path.exists(
        UPLOAD_FOLDER
    ):

        os.makedirs(
            UPLOAD_FOLDER
        )

    for filename in os.listdir(
        UPLOAD_FOLDER
    ):

        filepath = os.path.join(
            UPLOAD_FOLDER,
            filename
        )

        if not os.path.isfile(filepath):

            continue

        extension = os.path.splitext(
            filename
        )[1].lower()

        if extension in IMAGE_EXTENSIONS:

            media.append({

                "type": "image",

                "filename": filename

            })

        elif extension in VIDEO_EXTENSIONS:

            media.append({

                "type": "video",

                "filename": filename

            })

    return sorted(
        media,
        key=lambda x: x["filename"].lower()
    )

# ======================================================
# Main page
# ======================================================

@app.route("/")
def index():

    return render_template(
        "index.html",
        media=get_media()
    )

# ======================================================
# Media API
# ======================================================

@app.route("/media")
def media():

    return jsonify(
        get_media()
    )

# ======================================================
# Serve uploads
# ======================================================

@app.route("/uploads/<path:filename>")
def uploads(filename):

    return send_from_directory(
        UPLOAD_FOLDER,
        filename
    )

# ======================================================
# Brightness control
# ======================================================

def find_backlight():
    """Return the backlight sysfs directory, or None if unsupported."""

    override = os.environ.get("MEMORYFRAME_BACKLIGHT")

    if override:
        candidates = [override]
    else:
        candidates = [os.path.join(BACKLIGHT_ROOT, PREFERRED_BACKLIGHT)]
        if os.path.isdir(BACKLIGHT_ROOT):
            candidates += [
                os.path.join(BACKLIGHT_ROOT, n)
                for n in sorted(os.listdir(BACKLIGHT_ROOT))
            ]

    for path in candidates:
        if (
            os.path.isfile(os.path.join(path, "brightness"))
            and os.path.isfile(os.path.join(path, "max_brightness"))
        ):
            return path

    return None


def read_int(path):

    with open(path, "r") as f:
        return int(f.read().strip())


def unsupported():

    return jsonify({
        "status": "unsupported",
        "supported": False,
        "message": "Brightness control is only available on a Raspberry Pi with a DSI backlight."
    })


@app.route("/brightness", methods=["GET"])
def get_brightness():

    backlight = find_backlight()

    if backlight is None:
        return unsupported()

    try:
        current = read_int(os.path.join(backlight, "brightness"))
        maximum = read_int(os.path.join(backlight, "max_brightness"))
    except (OSError, ValueError) as e:
        app.logger.warning("Brightness read failed: %s", e)
        return unsupported()

    return jsonify({
        "status": "success",
        "supported": True,
        "brightness": current,
        "minimum": min(MIN_BRIGHTNESS, maximum),
        "maximum": maximum
    })


@app.route("/brightness", methods=["POST"])
def set_brightness():

    data = request.get_json(silent=True)

    value = data.get("value") if isinstance(data, dict) else None

    # bool is a subclass of int; reject it. value != value rejects NaN.
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or value != value
        or value in (float("inf"), float("-inf"))
    ):
        return jsonify({
            "status": "error",
            "message": "Body must be JSON like {\"value\": <number>}."
        }), 400

    backlight = find_backlight()

    if backlight is None:
        return unsupported(), 409

    try:
        maximum = read_int(os.path.join(backlight, "max_brightness"))
        minimum = min(MIN_BRIGHTNESS, maximum)
        value = int(round(max(minimum, min(value, maximum))))

        with open(os.path.join(backlight, "brightness"), "w") as f:
            f.write(str(value))

    except (OSError, ValueError) as e:
        app.logger.error("Brightness write failed: %s", e)
        return jsonify({
            "status": "error",
            "message": "Could not write backlight brightness."
        }), 503

    return jsonify({
        "status": "success",
        "supported": True,
        "brightness": value,
        "minimum": minimum,
        "maximum": maximum
    })

# ======================================================
# Start application
# ======================================================

if __name__ == "__main__":

    launch_browser()

    app.run(

        host="0.0.0.0",

        port=5000,

        debug=False

    )