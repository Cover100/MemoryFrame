from flask import Flask, render_template, send_from_directory, jsonify
import os
import subprocess
import time


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

BACKLIGHT_PATH = (
    "/sys/class/backlight/"
    "10-0045/brightness"
)


MAX_BRIGHTNESS_PATH = (
    "/sys/class/backlight/"
    "10-0045/max_brightness"
)





# ======================================================
# Launch Chromium kiosk
# ======================================================

def launch_browser():

    time.sleep(3)


    # Check if Chromium is already open

    result = subprocess.run(
        ["pgrep", "chromium"],
        stdout=subprocess.DEVNULL
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

        }),500





# ======================================================
# Get brightness
# ======================================================

@app.route("/brightness")
def get_brightness():


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

        }),500





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