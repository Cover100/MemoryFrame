"""MF-16: library management API (list, thumbnails, hide, delete, show now)."""
import io
import os

import pytest
from PIL import Image

from memoryframe import db as mfdb
from memoryframe import imaging

PHONE = {"REMOTE_ADDR": "10.42.0.23"}


def jpeg(w=1600, h=1200, color=(10, 120, 200)):
    b = io.BytesIO()
    Image.new("RGB", (w, h), color).save(b, "JPEG", quality=90)
    return b.getvalue()


@pytest.fixture
def item(paired, mf_app):
    r = paired.post("/api/v1/media", data={"file": (io.BytesIO(jpeg()), "beach.jpg")},
                    content_type="multipart/form-data", environ_base=PHONE)
    assert r.status_code == 201
    return paired.get("/api/v1/media", environ_base=PHONE).get_json()[0]


def stored_name(app, media_id):
    conn = mfdb.connect(app.config["DB_PATH"])
    try:
        return conn.execute("SELECT stored_name FROM media WHERE id = ?", (media_id,)).fetchone()[0]
    finally:
        conn.close()


def test_list_and_thumb(paired, item, mf_app):
    assert item["name"] == "beach.jpg" and item["hidden"] is False and item["thumb"].endswith("/thumb")
    r = paired.get(item["thumb"], environ_base=PHONE)
    assert r.status_code == 200 and r.mimetype == "image/jpeg"
    im = Image.open(io.BytesIO(r.data))
    assert im.size == (240, 240) and len(r.data) < 30_000
    assert "max-age" in r.headers["Cache-Control"]


def test_thumb_made_on_demand_for_legacy_items(paired, lib, mf_app):
    lib.add("old.jpg", jpeg(800, 600))
    it = paired.get("/api/v1/media", environ_base=PHONE).get_json()[0]
    assert not os.path.exists(imaging.thumb_path(mf_app.config["DATA_DIR"], stored_name(mf_app, it["id"])))
    assert paired.get(it["thumb"], environ_base=PHONE).status_code == 200


def test_thumb_of_undecodable_is_404(paired, lib):
    lib.add("broken.jpg", b"nope")
    it = paired.get("/api/v1/media", environ_base=PHONE).get_json()[0]
    assert paired.get(it["thumb"], environ_base=PHONE).status_code == 404


def test_hide_and_unhide(paired, item, client, mf_app):
    name = stored_name(mf_app, item["id"])
    url = f"/api/v1/media/{item['id']}/hidden"
    assert paired.put(url, json={"hidden": True}, environ_base=PHONE).get_json()["hidden"] is True
    assert name not in [m["filename"] for m in client.get("/media").get_json()]
    assert client.get("/uploads/" + name).status_code == 404
    assert paired.get("/api/v1/media", environ_base=PHONE).get_json()[0]["hidden"] is True
    assert paired.put(url, json={"hidden": False}, environ_base=PHONE).status_code == 200
    assert name in [m["filename"] for m in client.get("/media").get_json()]


@pytest.mark.parametrize("body", [None, {}, {"hidden": "yes"}, {"hidden": 1}, ["x"]])
def test_hide_validates_body(paired, item, body):
    assert paired.put(f"/api/v1/media/{item['id']}/hidden", json=body, environ_base=PHONE).status_code == 400


def test_delete_removes_original_and_derivatives(paired, item, mf_app, client):
    d = mf_app.config["DATA_DIR"]
    name = stored_name(mf_app, item["id"])
    paired.get(item["thumb"], environ_base=PHONE)
    files = [os.path.join(d, "originals", name), os.path.join(d, "display", imaging.display_name(name)),
             imaging.thumb_path(d, name)]
    assert all(os.path.exists(f) for f in files)
    r = paired.delete(f"/api/v1/media/{item['id']}", environ_base=PHONE)
    assert r.status_code == 200 and r.get_json()["files_removed"] == 3
    assert not any(os.path.exists(f) for f in files)                      # disk check
    assert paired.get("/api/v1/media", environ_base=PHONE).get_json() == []
    assert client.get("/media").get_json() == []
    assert paired.delete(f"/api/v1/media/{item['id']}", environ_base=PHONE).status_code == 404


def test_reupload_after_delete(paired, item):
    paired.delete(f"/api/v1/media/{item['id']}", environ_base=PHONE)
    r = paired.post("/api/v1/media", data={"file": (io.BytesIO(jpeg()), "beach.jpg")},
                    content_type="multipart/form-data", environ_base=PHONE)
    assert r.status_code == 201


def test_show_now(paired, item, client, mf_app):
    assert client.get("/show-now").get_json() == {"seq": 0, "filename": None, "rev": 1}   # upload bumped rev
    r = paired.post(f"/api/v1/media/{item['id']}/show-now", environ_base=PHONE)
    assert r.status_code == 200 and r.get_json()["seq"] == 1
    assert client.get("/show-now").get_json() == {"seq": 1, "filename": stored_name(mf_app, item["id"]), "rev": 1}
    paired.put(f"/api/v1/media/{item['id']}/hidden", json={"hidden": True}, environ_base=PHONE)
    assert paired.post(f"/api/v1/media/{item['id']}/show-now", environ_base=PHONE).status_code == 409


def test_unknown_ids_404(paired):
    assert paired.get("/api/v1/media/999/thumb", environ_base=PHONE).status_code == 404
    assert paired.post("/api/v1/media/999/show-now", environ_base=PHONE).status_code == 404


def test_library_page(paired):
    html = paired.get("/admin/library", environ_base=PHONE).data
    assert b'id="grid"' in html and b"library.js" in html and b'name="csrf-token"' in html


def test_rev_bumps_on_hide_and_delete(paired, item, client):
    rev = client.get("/show-now").get_json()["rev"]
    paired.put(f"/api/v1/media/{item['id']}/hidden", json={"hidden": True}, environ_base=PHONE)
    assert client.get("/show-now").get_json()["rev"] == rev + 1
    paired.delete(f"/api/v1/media/{item['id']}", environ_base=PHONE)
    assert client.get("/show-now").get_json()["rev"] == rev + 2
