"""MF-14: phone upload API."""
import io
import os
from collections import namedtuple

import pytest
from PIL import Image

from memoryframe import db as mfdb
from memoryframe import upload

PHONE = {"REMOTE_ADDR": "10.42.0.23"}


def jpeg(w=1600, h=1200, color=(10, 120, 200)):
    b = io.BytesIO()
    Image.new("RGB", (w, h), color).save(b, "JPEG", quality=90)
    return b.getvalue()


def post(client, data, name="photo.jpg", **kw):
    return client.post("/api/v1/media", data={"file": (io.BytesIO(data), name)},
                       content_type="multipart/form-data", environ_base=PHONE, **kw)


def media_rows(app):
    conn = mfdb.connect(app.config["DB_PATH"])
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM media ORDER BY id")]
    finally:
        conn.close()


def test_upload_adds_and_frame_sees_it(mf_app, paired, client):
    data = jpeg()
    r = post(paired, data, "IMG_0001.JPG")
    assert r.status_code == 201, r.get_json()
    rows = media_rows(mf_app)
    assert len(rows) == 1 and rows[0]["source"] == "upload" and rows[0]["original_name"] == "IMG_0001.jpg"
    stored = rows[0]["stored_name"]
    with open(os.path.join(mf_app.config["DATA_DIR"], "originals", stored), "rb") as f:
        assert f.read() == data                                        # byte-identical original
    assert os.path.exists(os.path.join(mf_app.config["DATA_DIR"], "display", stored[:-4] + ".jpg"))
    assert {"type": "image", "filename": stored} in client.get("/media").get_json()   # frame polls this
    assert os.listdir(os.path.join(mf_app.config["DATA_DIR"], "tmp")) == []


def test_twenty_photos(mf_app, paired):
    for i in range(20):
        assert post(paired, jpeg(800, 600, (i * 10, 50, 90)), f"p{i}.jpg").status_code == 201
    assert len(media_rows(mf_app)) == 20


def test_duplicate_is_reported_not_stored_twice(mf_app, paired):
    data = jpeg()
    assert post(paired, data).status_code == 201
    r = post(paired, data, "again.jpg")
    assert r.status_code == 200 and r.get_json()["status"] == "duplicate"
    assert len(media_rows(mf_app)) == 1


def test_renamed_text_file_is_415(mf_app, paired):
    r = post(paired, b"hello, I am not a photo\n" * 10, "holiday.jpg")
    assert r.status_code == 415 and "not a photo" in r.get_json()["error"]
    assert media_rows(mf_app) == []
    assert os.listdir(os.path.join(mf_app.config["DATA_DIR"], "tmp")) == []


def test_content_decides_type_not_name(mf_app, paired):
    b = io.BytesIO()
    Image.new("RGB", (900, 700)).save(b, "PNG")
    r = post(paired, b.getvalue(), "really-a-png.jpg")
    assert r.status_code == 201
    assert media_rows(mf_app)[0]["stored_name"].endswith(".png")


@pytest.mark.parametrize("name,data", [("clip.mp4", b"\0\0\0\x18ftypmp42" + b"\0" * 100),
                                       ("clip.jpg", b"\0\0\0\x18ftypisom" + b"\0" * 100),
                                       ("movie.mov", b"whatever")])
def test_video_is_415_coming_later(paired, name, data):
    r = post(paired, data, name)
    assert r.status_code == 415 and "coming later" in r.get_json()["error"]


def test_too_large_is_413_readable(mf_app, paired):
    mf_app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024
    r = post(paired, jpeg() + b"\0" * (2 * 1024 * 1024))
    assert r.status_code == 413
    assert "too large" in r.get_json()["error"] and "1 MB" in r.get_json()["error"]


@pytest.mark.parametrize("name", ["../x.jpg", "../../etc/passwd.jpg", "/abs/path.jpg", "..\\..\\win.jpg",
                                  "....//x.jpg", "a/../../b.jpg", "%2e%2e%2fx.jpg"])
def test_path_traversal_names_cannot_escape(mf_app, paired, tmp_path, name):
    before = set(os.listdir(tmp_path))
    r = post(paired, jpeg(), name)
    assert r.status_code == 201
    row = media_rows(mf_app)[0]
    assert "/" not in row["stored_name"] and "\\" not in row["stored_name"] and ".." not in row["stored_name"]
    assert "/" not in row["original_name"] and ".." not in row["original_name"]
    assert os.path.exists(os.path.join(mf_app.config["DATA_DIR"], "originals", row["stored_name"]))
    assert set(os.listdir(tmp_path)) == before          # nothing written next to the data dir


def test_507_when_disk_nearly_full(mf_app, paired, monkeypatch):
    du = namedtuple("du", "total used free")
    monkeypatch.setattr(upload.shutil, "disk_usage", lambda p: du(10**10, 10**10 - 100 * 1024**2, 100 * 1024**2))
    r = post(paired, jpeg())
    assert r.status_code == 507 and "almost full" in r.get_json()["error"]
    assert media_rows(mf_app) == []


def test_storage_endpoint(paired, monkeypatch):
    du = namedtuple("du", "total used free")
    monkeypatch.setattr(upload.shutil, "disk_usage", lambda p: du(256 * 1024**3, 6 * 1024**3, 250 * 1024**3))
    s = paired.get("/api/v1/storage", environ_base=PHONE).get_json()
    assert s["free"] == 250 * 1024**3 and 45_000 < s["photos_remaining_estimate"] < 50_000


def test_upload_requires_session_and_csrf(mf_app, client, paired):
    anon = mf_app.test_client()
    assert post(anon, jpeg()).status_code == 401
    assert post(paired, jpeg(), csrf=False).status_code == 403
    assert paired.post("/api/v1/media", data={}, environ_base=PHONE).status_code == 400


def test_upload_page(paired):
    html = paired.get("/admin/upload", environ_base=PHONE).data
    assert b'type="file"' in html and b"multiple" in html and b'name="csrf-token"' in html
