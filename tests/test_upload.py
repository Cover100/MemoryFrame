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
        stored_im = Image.open(f)                                      # #82: one shrunk JPEG, not the upload
        assert stored_im.format == "JPEG" and stored_im.size[0] <= 1500
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
    assert media_rows(mf_app)[0]["stored_name"].endswith(".jpg")      # #82: every still is stored as a JPEG


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
    assert s["free"] == 250 * 1024**3 and 250_000 < s["photos_remaining_estimate"] < 256_000   # #82: ~1 MB per photo


def test_upload_requires_session_and_csrf(mf_app, client, paired):
    anon = mf_app.test_client()
    assert post(anon, jpeg()).status_code == 401
    assert post(paired, jpeg(), csrf=False).status_code == 403
    assert paired.post("/api/v1/media", data={}, environ_base=PHONE).status_code == 400


def test_upload_page(paired):
    html = paired.get("/admin/upload", environ_base=PHONE).data
    assert b'type="file"' in html and b"multiple" in html and b'name="csrf-token"' in html


# ---------- #25 follow-up: pixel cap, disk full mid-write, Unicode labels ----------

def _tmp_empty(app):
    d = os.path.join(app.config["DATA_DIR"], "tmp")
    return [f for f in os.listdir(d) if f != "trash"] == []


def test_over_pixel_cap_is_415_before_decoding(mf_app, paired, monkeypatch):
    mf_app.config["MAX_IMAGE_PIXELS"] = 1600 * 1200 - 1
    monkeypatch.setattr(Image.Image, "verify", lambda self: pytest.fail("decoded past the cap"))
    r = post(paired, jpeg(1600, 1200))
    assert r.status_code == 415 and "megapixels" in r.get_json()["error"]
    assert media_rows(mf_app) == [] and _tmp_empty(mf_app)


def test_default_cap_is_150mp_and_bomb_is_415(mf_app, paired, monkeypatch):
    assert mf_app.config["MAX_IMAGE_PIXELS"] == 150_000_000
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 1000)      # header > 2x this -> DecompressionBombError
    r = post(paired, jpeg(1600, 1200))
    assert r.status_code == 415 and "megapixels" in r.get_json()["error"]


@pytest.mark.parametrize("stage", ["receive", "copy"])
def test_disk_full_while_writing_is_507_and_tmp_cleaned(mf_app, paired, monkeypatch, stage):
    import errno
    import shutil
    from werkzeug.datastructures import FileStorage

    def full(*a, **k):
        dst = a[1] if stage == "copy" else a[1]
        with open(dst if isinstance(dst, str) else a[1], "wb") as f:
            f.write(b"partial")
        raise OSError(errno.ENOSPC, "No space left on device")

    if stage == "receive":
        monkeypatch.setattr(FileStorage, "save", lambda self, dst, *a: full(self, dst))
    else:
        from memoryframe import imaging
        monkeypatch.setattr(imaging, "make_stored_copy", lambda src, dst, target: full(src, dst))   # #82: no copy2 any more
    r = post(paired, jpeg())
    assert r.status_code == 507 and "full" in r.get_json()["error"]
    assert media_rows(mf_app) == [] and _tmp_empty(mf_app)
    assert os.listdir(os.path.join(mf_app.config["DATA_DIR"], "originals")) == []


@pytest.mark.parametrize("name,expected", [
    ("Été à Paris.jpg", "Été_à_Paris.jpg"),
    ("北京 2024.JPG", "北京_2024.jpg"),
    ("صورة.jpeg", "صورة.jpg"),
    ("😀😀.jpg", "photo.jpg"),
    ("../../..\\evil.jpg", "evil.jpg"),
    ("\u202egpj.exe", "gpj.jpg"),
    ("", "photo.jpg"),
])
def test_unicode_labels(name, expected):
    assert upload.label(name, ".jpg") == expected


def test_unicode_name_upload_label(mf_app, paired):
    r = post(paired, jpeg(), name="Été à Paris.jpg")
    assert r.status_code == 201 and r.get_json()["name"] == "Été_à_Paris.jpg"


# ---------- #28 review: DB insert failures, other OSErrors, nits ----------

def _no_files(app):
    d = app.config["DATA_DIR"]
    return all([f for f in os.listdir(os.path.join(d, s)) if f != "trash"] == []
               for s in ("originals", "display", "thumbs", "tmp") if os.path.isdir(os.path.join(d, s)))


class _FailingInsert:
    def __init__(self, conn, exc):
        self._c, self._exc = conn, exc

    def execute(self, sql, *a):
        if sql.lstrip().startswith("INSERT INTO media"):
            raise self._exc
        return self._c.execute(sql, *a)

    def __getattr__(self, k):
        return getattr(self._c, k)


@pytest.mark.parametrize("exc,status", [
    (__import__("sqlite3").OperationalError("database or disk is full"), 507),
    (__import__("sqlite3").OperationalError("disk I/O error"), 500),
])
def test_db_insert_failure_cleans_up_and_returns_json(mf_app, paired, monkeypatch, exc, status):
    from memoryframe import api
    real = api.get_db
    monkeypatch.setattr(api, "get_db", lambda: _FailingInsert(real(), exc))
    r = post(paired, jpeg())
    assert r.status_code == status and r.is_json and r.get_json()["error"]
    assert media_rows(mf_app) == [] and _no_files(mf_app)


def test_eio_returns_json_not_html(mf_app, paired, monkeypatch):
    import errno
    from werkzeug.datastructures import FileStorage

    def eio(self, dst, *a):
        raise OSError(errno.EIO, "Input/output error")

    monkeypatch.setattr(FileStorage, "save", eio)
    r = post(paired, jpeg())
    assert r.status_code == 500 and r.is_json and "couldn't save" in r.get_json()["error"]
    assert _no_files(mf_app)


@pytest.mark.parametrize("name", [".....jpg", "....", ".jpg", "..", "._.jpg"])
def test_dot_only_names_fall_back_to_photo(name):
    assert upload.label(name, ".jpg") == "photo.jpg"


@pytest.mark.parametrize("raw", ["abc", "0", "-3", "1.5", " "])
def test_bad_max_megapixels_env_falls_back(monkeypatch, caplog, raw):
    from memoryframe import storage
    monkeypatch.setenv("MEMORYFRAME_MAX_MEGAPIXELS", raw)
    assert storage.default_config()["MAX_IMAGE_PIXELS"] == 150_000_000
    if raw.strip():
        assert "MEMORYFRAME_MAX_MEGAPIXELS" in caplog.text


def test_max_megapixels_env_applies(monkeypatch):
    from memoryframe import storage
    monkeypatch.setenv("MEMORYFRAME_MAX_MEGAPIXELS", "12")
    assert storage.default_config()["MAX_IMAGE_PIXELS"] == 12_000_000


def test_legacy_import_enforces_pixel_cap(mf_app):
    from memoryframe.migrate import migrate_legacy
    legacy = mf_app.config["LEGACY_UPLOADS"]
    open(os.path.join(legacy, "big.jpg"), "wb").write(jpeg(1600, 1200))
    open(os.path.join(legacy, "small.jpg"), "wb").write(jpeg(400, 300))
    mf_app.config["MAX_IMAGE_PIXELS"] = 1_000_000
    msgs = []
    res = migrate_legacy(mf_app.config, log=msgs.append)
    assert res["imported"] == 1 and res["too_large"] == 1
    assert [r["original_name"] for r in media_rows(mf_app)] == ["small.jpg"]
    assert any("big.jpg" in m and "megapixels" in m for m in msgs)


def test_disk_full_while_saving_the_stored_copy_is_507_not_415(mf_app, paired, monkeypatch):
    """#82: a full disk while writing the shrunk copy is 'frame is full', not 'not a photo'."""
    import errno
    real_save = Image.Image.save

    def save(self, fp, *a, **k):
        if str(fp).endswith(".tmp") or "tmp" in str(fp):
            with open(fp, "wb") as f:
                f.write(b"partial")
            raise OSError(errno.ENOSPC, "No space left on device")
        return real_save(self, fp, *a, **k)

    monkeypatch.setattr(Image.Image, "save", save)
    r = post(paired, jpeg())
    assert r.status_code == 507 and "full" in r.get_json()["error"]
    assert media_rows(mf_app) == [] and _tmp_empty(mf_app)
