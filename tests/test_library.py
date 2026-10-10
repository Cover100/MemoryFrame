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


# ---------- Tester #27 blockers ----------

@pytest.mark.parametrize("mid", ["99999999999999999999999", str(2**63), str(2**64), "0"])
def test_huge_ids_are_404(paired, mid):
    assert paired.delete(f"/api/v1/media/{mid}", environ_base=PHONE).status_code == 404
    assert paired.get(f"/api/v1/media/{mid}/thumb", environ_base=PHONE).status_code == 404
    assert paired.put(f"/api/v1/media/{mid}/hidden", json={"hidden": True}, environ_base=PHONE).status_code == 404
    assert paired.post(f"/api/v1/media/{mid}/show-now", environ_base=PHONE).status_code == 404


def test_max_sqlite_id_is_plain_404(paired):
    assert paired.delete(f"/api/v1/media/{2**63 - 1}", environ_base=PHONE).status_code == 404


def test_show_now_cleared_on_delete(paired, item, client):
    paired.post(f"/api/v1/media/{item['id']}/show-now", environ_base=PHONE)
    paired.delete(f"/api/v1/media/{item['id']}", environ_base=PHONE)
    assert client.get("/show-now").get_json()["filename"] is None


def test_show_now_cleared_on_hide(paired, item, client):
    paired.post(f"/api/v1/media/{item['id']}/show-now", environ_base=PHONE)
    paired.put(f"/api/v1/media/{item['id']}/hidden", json={"hidden": True}, environ_base=PHONE)
    assert client.get("/show-now").get_json()["filename"] is None


def test_show_now_never_returns_missing_file_even_if_state_stale(paired, item, client, mf_app):
    mf_app.extensions["mf_show_now"] = {"seq": 9, "filename": "gone.jpg", "at": 0}
    assert client.get("/show-now").get_json() == {"seq": 9, "filename": None, "rev": 1}


def test_delete_db_failure_keeps_files_and_row(paired, item, mf_app, monkeypatch):
    import sqlite3
    d = mf_app.config["DATA_DIR"]
    name = stored_name(mf_app, item["id"])
    paired.get(item["thumb"], environ_base=PHONE)
    files = [os.path.join(d, "originals", name), os.path.join(d, "display", imaging.display_name(name)),
             imaging.thumb_path(d, name)]

    real_connect = mfdb.connect

    class FailingDelete:
        def __init__(self, conn):
            self._c = conn

        def execute(self, sql, *a):
            if sql.startswith("DELETE FROM media"):
                raise sqlite3.OperationalError("database is locked")
            return self._c.execute(sql, *a)

        def __getattr__(self, k):
            return getattr(self._c, k)

    monkeypatch.setattr(mfdb, "connect", lambda p: FailingDelete(real_connect(p)))
    mf_app.config["PROPAGATE_EXCEPTIONS"] = False
    r = paired.delete(f"/api/v1/media/{item['id']}", environ_base=PHONE)
    assert r.status_code == 500
    monkeypatch.setattr(mfdb, "connect", real_connect)
    assert all(os.path.exists(f) for f in files)                 # restored from the trash
    assert stored_name(mf_app, item["id"]) == name               # row kept
    assert os.listdir(os.path.join(d, "tmp", "trash")) == []


def test_file_purge_failure_is_swept_at_start(paired, item, mf_app, monkeypatch):
    from memoryframe import create_app
    d = mf_app.config["DATA_DIR"]
    real_remove = os.remove
    monkeypatch.setattr(imaging.os, "remove", lambda p: (_ for _ in ()).throw(PermissionError(p)))
    r = paired.delete(f"/api/v1/media/{item['id']}", environ_base=PHONE)
    assert r.status_code == 200 and r.get_json()["files_removed"] == 0
    monkeypatch.setattr(imaging.os, "remove", real_remove)
    trash = os.path.join(d, "tmp", "trash")
    assert len(os.listdir(trash)) >= 2                    # left over, row already gone
    create_app({"TESTING": True, "DATA_DIR": d, "DB_PATH": mf_app.config["DB_PATH"]})
    assert os.listdir(trash) == []
    assert os.listdir(os.path.join(d, "originals")) == []


def test_sweep_removes_orphan_derivatives_keeps_originals(mf_app, lib):
    from memoryframe import create_app
    d = mf_app.config["DATA_DIR"]
    lib.add("keep.jpg", jpeg(400, 300))
    for sub in ("display", "thumbs", "originals"):
        with open(os.path.join(d, sub, "deadbeef.jpg"), "wb") as f:
            f.write(b"x")
    create_app({"TESTING": True, "DATA_DIR": d, "DB_PATH": mf_app.config["DB_PATH"]})
    assert not os.path.exists(os.path.join(d, "display", "deadbeef.jpg"))
    assert not os.path.exists(os.path.join(d, "thumbs", "deadbeef.jpg"))
    assert os.path.exists(os.path.join(d, "originals", "deadbeef.jpg"))   # never delete user data blindly
    assert len(os.listdir(os.path.join(d, "originals"))) == 2


# ---------- Tester #27: crash in the middle of a delete must not lose the photo ----------

def _files(app, name):
    d = app.config["DATA_DIR"]
    return [os.path.join(d, "originals", name), os.path.join(d, "display", imaging.display_name(name)),
            imaging.thumb_path(d, name)]


def test_crash_after_move_before_db_delete_restores_on_restart(paired, item, mf_app, monkeypatch):
    from memoryframe import create_app
    name = stored_name(mf_app, item["id"])
    paired.get(item["thumb"], environ_base=PHONE)
    data = open(_files(mf_app, name)[0], "rb").read()
    real_connect = mfdb.connect

    class Crash(BaseException):
        """Simulated power loss: not an Exception, so no rollback/unstage code runs."""

    class CrashOnDelete:
        def __init__(self, conn):
            self._c = conn

        def execute(self, sql, *a):
            if sql.startswith("DELETE FROM media"):
                raise Crash()
            return self._c.execute(sql, *a)

        def __getattr__(self, k):
            return getattr(self._c, k)

    monkeypatch.setattr(mfdb, "connect", lambda p: CrashOnDelete(real_connect(p)))
    with pytest.raises(Crash):
        paired.delete(f"/api/v1/media/{item['id']}", environ_base=PHONE)
    monkeypatch.setattr(mfdb, "connect", real_connect)
    trash = os.path.join(mf_app.config["DATA_DIR"], "tmp", "trash")
    assert sorted(f[:2] for f in os.listdir(trash)) == ["0-", "1-", "2-"]
    assert not any(os.path.exists(f) for f in _files(mf_app, name))      # mid-delete state

    create_app({"TESTING": True, "DATA_DIR": mf_app.config["DATA_DIR"], "DB_PATH": mf_app.config["DB_PATH"]})
    assert all(os.path.exists(f) for f in _files(mf_app, name))          # restored, not deleted
    assert open(_files(mf_app, name)[0], "rb").read() == data
    assert os.listdir(trash) == []
    assert stored_name(mf_app, item["id"]) == name


def test_sweep_deletes_trash_only_when_row_gone(mf_app, lib):
    from memoryframe import create_app
    d = mf_app.config["DATA_DIR"]
    trash = os.path.join(d, "tmp", "trash")
    os.makedirs(trash, exist_ok=True)
    for f in ("0-9999-gone.jpg", "1-9999-gone.jpg"):
        open(os.path.join(trash, f), "wb").write(b"x")
    create_app({"TESTING": True, "DATA_DIR": d, "DB_PATH": mf_app.config["DB_PATH"]})
    assert os.listdir(trash) == []


@pytest.mark.parametrize("fname", ["random.jpg", "3-1-x.jpg", "0-abc-x.jpg", "0-1-wrongname.jpg",
                                   "0-99999999999999999999-x.jpg"])
def test_sweep_keeps_ambiguous_trash(mf_app, lib, fname):
    from memoryframe import create_app
    lib.add("a.jpg", jpeg(300, 200))       # row id 1 exists
    d = mf_app.config["DATA_DIR"]
    trash = os.path.join(d, "tmp", "trash")
    os.makedirs(trash, exist_ok=True)
    open(os.path.join(trash, fname), "wb").write(b"x")
    create_app({"TESTING": True, "DATA_DIR": d, "DB_PATH": mf_app.config["DB_PATH"]})
    assert os.listdir(trash) == [fname]


def test_sweep_keeps_trash_if_target_exists(mf_app, lib):
    from memoryframe import create_app
    lib.add("a.jpg", jpeg(300, 200))
    d = mf_app.config["DATA_DIR"]
    name = stored_name(mf_app, 1)
    trash = os.path.join(d, "tmp", "trash")
    os.makedirs(trash, exist_ok=True)
    open(os.path.join(trash, f"0-1-{name}"), "wb").write(b"other bytes")
    create_app({"TESTING": True, "DATA_DIR": d, "DB_PATH": mf_app.config["DB_PATH"]})
    assert os.listdir(trash) == [f"0-1-{name}"]                 # never overwrite, never delete
    assert open(os.path.join(d, "originals", name), "rb").read() != b"other bytes"


def test_concurrent_second_delete_is_404(paired, item, mf_app, monkeypatch):
    """Both requests passed _item(); the first committed first -> the second gets 404."""
    real_stage = imaging.stage_delete

    def first_wins(data_dir, stored, media_id):
        conn = mfdb.connect(mf_app.config["DB_PATH"])
        conn.execute("DELETE FROM media WHERE id = ?", (media_id,))      # the other request's commit
        conn.commit()
        conn.close()
        return real_stage(data_dir, stored, media_id)

    monkeypatch.setattr(imaging, "stage_delete", first_wins)
    r = paired.delete(f"/api/v1/media/{item['id']}", environ_base=PHONE)
    assert r.status_code == 404
