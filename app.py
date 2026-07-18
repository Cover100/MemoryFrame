from flask import Flask, render_template, send_from_directory, jsonify
import os


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


# Raspberry Pi DSI display backlight
BACKLIGHT_PATH = (
    "/sys/class/backlight/"
    "10-0045/brightness"
)


MAX_BRIGHTNESS_PATH = (
    "/sys/class/backlight/"
    "10-0045/max_brightness"
)



# ======================================================
# Media Scanner
# ======================================================

def get_media():

    media = []


    # Create uploads folder if missing

    if not os.path.exists(UPLOAD_FOLDER):

        os.makedirs(UPLOAD_FOLDER)



    for filename in os.listdir(UPLOAD_FOLDER):


        filepath = os.path.join(
            UPLOAD_FOLDER,
            filename
        )


        # Ignore folders

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
# Main Page
# ======================================================

@app.route("/")
def index():

    return render_template(
        "index.html",
        media=get_media()
    )





# ======================================================
# Media API
# Used by Javascript auto refresh
# ======================================================

@app.route("/media")
def media():

    return jsonify(
        get_media()
    )





# ======================================================
# Serve uploaded photos/videos
# ======================================================

@app.route("/uploads/<path:filename>")
def uploads(filename):

    return send_from_directory(
        UPLOAD_FOLDER,
        filename
    )





# ======================================================
# Brightness Control
# ======================================================

@app.route("/brightness/<int:value>")
def set_brightness(value):


    try:


        # Read maximum brightness

        with open(
            MAX_BRIGHTNESS_PATH,
            "r"
        ) as f:

            maximum = int(
                f.read()
            )



        # Clamp value

        value = max(
            0,
            min(
                value,
                maximum
            )
        )



        # Write brightness

        with open(
            BACKLIGHT_PATH,
            "w"
        ) as f:

            f.write(
                str(value)
            )



        return jsonify({

            "status": "success",

            "brightness": value,

            "maximum": maximum

        })


    except Exception as e:


        return jsonify({

            "status": "error",

            "message": str(e)

        }), 500





# ======================================================
# Get current brightness
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

            "brightness": current,

            "maximum": maximum

        })


    except Exception as e:


        return jsonify({

            "status": "error",

            "message": str(e)

        }), 500





# ======================================================
# Run Server
# ======================================================

if __name__ == "__main__":


    app.run(

        host="0.0.0.0",

        port=5000,

        debug=False

    )
