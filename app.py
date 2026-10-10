from flask import Flask, render_template, send_from_directory, jsonify
import os
import time
import platform
import webbrowser
import threading

app = Flask(__name__)

# ======================================================
# Configuration
# ======================================================

UPLOAD_FOLDER = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "uploads"
)

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

BACKLIGHT_PATH = (
    "/sys/class/backlight/"
    "10-0045/brightness"
)

MAX_BRIGHTNESS_PATH = (
    "/sys/class/backlight/"
    "10-0045/max_brightness"
)

# ======================================================
# Launch Browser
# ======================================================

def launch_browser():
    """Development helper: open the default browser once the server is up."""

    time.sleep(3)

    webbrowser.open("http://localhost:5000")


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

@app.route("/brightness/<int:value>")
def set_brightness(value):

    # Windows doesn't have Raspberry Pi backlight controls
    if platform.system() == "Windows":
        return jsonify({
            "status": "unsupported",
            "message": "Brightness control is only available on Raspberry Pi."
        })

    try:

        with open(
            MAX_BRIGHTNESS_PATH,
            "r"
        ) as f:

            maximum = int(
                f.read()
            )

        value = max(
            0,
            min(
                value,
                maximum
            )
        )

        with open(
            BACKLIGHT_PATH,
            "w"
        ) as f:

            f.write(
                str(value)
            )

        return jsonify({

            "status":
                "success",

            "brightness":
                value,

            "maximum":
                maximum

        })

    except Exception as e:

        return jsonify({

            "status":
                "error",

            "message":
                str(e)

        }), 500

# ======================================================
# Get brightness
# ======================================================

@app.route("/brightness")
def get_brightness():

    # Windows doesn't have Raspberry Pi backlight controls
    if platform.system() == "Windows":
        return jsonify({
            "status": "unsupported",
            "message": "Brightness control is only available on Raspberry Pi."
        })

    try:

        with open(
            BACKLIGHT_PATH,
            "r"
        ) as f:

            current = int(
                f.read()
            )

        with open(
            MAX_BRIGHTNESS_PATH,
            "r"
        ) as f:

            maximum = int(
                f.read()
            )

        return jsonify({

            "brightness":
                current,

            "maximum":
                maximum

        })

    except Exception as e:

        return jsonify({

            "status":
                "error",

            "message":
                str(e)

        }), 500

# ======================================================
# Health check (used by the kiosk launcher)
# ======================================================

@app.route("/healthz")
def healthz():

    return jsonify({"status": "ok"})

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

    os.makedirs(UPLOAD_FOLDER, exist_ok=True)

    from waitress import serve

    print(f"Memory Frame serving on http://{host}:{port}")

    serve(app, host=host, port=port, threads=4)


if __name__ == "__main__":

    main()
