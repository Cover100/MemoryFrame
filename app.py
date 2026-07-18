from flask import Flask, render_template, send_from_directory, jsonify
import os


app = Flask(__name__)


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
    ".webm",
    ".mov"
}



def get_media():

    media = []


    if not os.path.exists(UPLOAD_FOLDER):

        os.makedirs(UPLOAD_FOLDER)



    for filename in os.listdir(UPLOAD_FOLDER):

        path = os.path.join(
            UPLOAD_FOLDER,
            filename
        )


        # Ignore folders

        if not os.path.isfile(path):

            continue



        extension = os.path.splitext(filename)[1].lower()



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





@app.route("/")
def index():

    return render_template(
        "index.html",
        media=get_media()
    )





@app.route("/media")
def media():

    return jsonify(
        get_media()
    )





@app.route("/uploads/<path:filename>")
def uploads(filename):

    return send_from_directory(
        UPLOAD_FOLDER,
        filename
    )





if __name__ == "__main__":


    app.run(

        host="0.0.0.0",

        port=5000,

        debug=False

    )