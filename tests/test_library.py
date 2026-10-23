"""MF-16: library management API (list, thumbnails, delete, show now). Hide was removed in #80."""
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
    assert item["name"] == "beach.jpg" and "hidden" not in item and item["thumb"].endswith("/thumb")
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


def test_hide_route_is_gone(paired, item):
    """#80: nothing on the phone can hide a photo."""
    r = paired.put(f"/api/v1/media/{item['id']}/hidden", json={"hidden": True}, environ_base=PHONE)
    assert r.status_code in (404, 405)


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


def test_single_delete_of_an_unsafe_stored_name_is_400_and_touches_nothing_outside(paired, lib, mf_app, tmp_path):
    """Hollis on #77: a stored_name that is a path (never made by the server) gave a 500; refuse it with 400."""
    victim = tmp_path / "victim.txt"
    victim.write_text("keep me")
    conn = mfdb.connect(mf_app.config["DB_PATH"])
    ids = []
    for name in (f"../../{victim.name}", str(victim), "..", "a/b.jpg", ".hidden.jpg"):
        cur = conn.execute("INSERT INTO media (stored_name, original_name, kind, sha256, size_bytes, source, created_at)"
                           " VALUES (?, 'x', 'image', 'h', 1, 'upload', 1)", (name,))
        ids.append(cur.lastrowid)
    conn.commit()
    conn.close()
    for i in ids:
        r = paired.delete(f"/api/v1/media/{i}", environ_base=PHONE)
        assert r.status_code == 400 and "error" in r.get_json(), i
    assert victim.read_text() == "keep me"
    left = [m["id"] for m in paired.get("/api/v1/media", environ_base=PHONE).get_json()]
    assert set(ids) <= set(left)                                            # refused rows stay, visibly
    assert not os.listdir(os.path.join(mf_app.config["DATA_DIR"], "tmp", "trash")) if os.path.isdir(
        os.path.join(mf_app.config["DATA_DIR"], "tmp", "trash")) else True


def test_reupload_after_delete(paired, item):
    paired.delete(f"/api/v1/media/{item['id']}", environ_base=PHONE)
    r = paired.post("/api/v1/media", data={"file": (io.BytesIO(jpeg()), "beach.jpg")},
                    content_type="multipart/form-data", environ_base=PHONE)
    assert r.status_code == 201


def show_now(client):
    d = client.get("/show-now").get_json()
    d.pop("settings")                                  # MF-17, covered in test_settings.py
    d.pop("message", None)                             # #73, covered in test_frame_message.py
    return d


def test_show_now(paired, item, client, mf_app):
    assert show_now(client) == {"seq": 0, "filename": None, "rev": 1}   # upload bumped rev
    r = paired.post(f"/api/v1/media/{item['id']}/show-now", environ_base=PHONE)
    assert r.status_code == 200 and r.get_json()["seq"] == 1
    assert show_now(client) == {"seq": 1, "filename": stored_name(mf_app, item["id"]), "rev": 1}


def test_unknown_ids_404(paired):
    assert paired.get("/api/v1/media/999/thumb", environ_base=PHONE).status_code == 404
    assert paired.post("/api/v1/media/999/show-now", environ_base=PHONE).status_code == 404


def test_library_page(paired, item):
    html = paired.get("/admin/library", environ_base=PHONE).data
    assert b'id="grid"' in html and b"library.js" in html and b'name="csrf-token"' in html


def test_rev_bumps_on_delete(paired, item, client):
    rev = client.get("/show-now").get_json()["rev"]
    paired.delete(f"/api/v1/media/{item['id']}", environ_base=PHONE)
    assert client.get("/show-now").get_json()["rev"] == rev + 1


# ---------- Tester #27 blockers ----------

@pytest.mark.parametrize("mid", ["99999999999999999999999", str(2**63), str(2**64), "0"])
def test_huge_ids_are_404(paired, mid):
    assert paired.delete(f"/api/v1/media/{mid}", environ_base=PHONE).status_code == 404
    assert paired.get(f"/api/v1/media/{mid}/thumb", environ_base=PHONE).status_code == 404
    assert paired.post(f"/api/v1/media/{mid}/show-now", environ_base=PHONE).status_code == 404


def test_max_sqlite_id_is_plain_404(paired):
    assert paired.delete(f"/api/v1/media/{2**63 - 1}", environ_base=PHONE).status_code == 404


def test_show_now_cleared_on_delete(paired, item, client):
    paired.post(f"/api/v1/media/{item['id']}/show-now", environ_base=PHONE)
    paired.delete(f"/api/v1/media/{item['id']}", environ_base=PHONE)
    assert client.get("/show-now").get_json()["filename"] is None


def test_show_now_never_returns_missing_file_even_if_state_stale(paired, item, client, mf_app):
    mf_app.extensions["mf_show_now"] = {"seq": 9, "filename": "gone.jpg", "at": 0}
    assert show_now(client) == {"seq": 9, "filename": None, "rev": 1}


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


def test_concurrent_delete_both_stage_no_orphans(paired, item, mf_app, monkeypatch):
    """Deterministic version of Tester's race: request A stages the original, request B stages the
    derivatives and its DELETE hits 0 rows. Nothing may be restored - no orphans, empty trash."""
    from memoryframe import api
    d = mf_app.config["DATA_DIR"]
    name = stored_name(mf_app, item["id"])
    paired.get(item["thumb"], environ_base=PHONE)
    files = _files(mf_app, name)
    real_stage = imaging.stage_delete
    calls = []

    def split(data_dir, stored, media_id):
        calls.append(1)
        # the "other" request: stage the original only, then commit its DELETE
        other = [(os.path.join(d, "tmp", "trash", f"0-{media_id}-{stored}"), files[0])]
        os.makedirs(os.path.dirname(other[0][0]), exist_ok=True)
        os.replace(files[0], other[0][0])
        conn = mfdb.connect(mf_app.config["DB_PATH"])
        conn.execute("DELETE FROM media WHERE id = ?", (media_id,))
        conn.commit()
        conn.close()
        imaging.purge_staged(other)
        return real_stage(data_dir, stored, media_id)     # this request stages the rest

    monkeypatch.setattr(imaging, "stage_delete", split)
    r = paired.delete(f"/api/v1/media/{item['id']}", environ_base=PHONE)
    assert r.status_code == 404 and calls
    assert not any(os.path.exists(f) for f in files)       # no orphan derivatives restored
    assert os.listdir(os.path.join(d, "tmp", "trash")) == []


def test_threaded_concurrent_deletes_leave_no_orphans(mf_app, lib, paired):
    import threading
    for n in range(10):
        lib.add(f"p{n}.jpg", jpeg(300 + n, 200))
    d = mf_app.config["DATA_DIR"]
    for media_id in range(1, 11):
        clients = []
        for _ in range(4):                      # one client per thread, same phone session
            c = mf_app.test_client()
            c._cookies = dict(paired._cookies)
            clients.append(c)
        codes = []
        barrier = threading.Barrier(4)

        def go(c):
            barrier.wait()
            codes.append(c.delete(f"/api/v1/media/{media_id}", environ_base=PHONE).status_code)

        ts = [threading.Thread(target=go, args=(c,)) for c in clients]
        [t.start() for t in ts]
        [t.join() for t in ts]
        assert sorted(codes) == [200, 404, 404, 404]
    for sub in ("originals", "display", "thumbs", "tmp/trash"):
        p = os.path.join(d, sub)
        assert [f for f in os.listdir(p) if f != "trash"] == [] if os.path.isdir(p) else True


def test_sweep_restores_legacy_trash_names(mf_app, lib):
    from memoryframe import create_app
    lib.add("a.jpg", jpeg(300, 200))
    d = mf_app.config["DATA_DIR"]
    name = stored_name(mf_app, 1)
    trash = os.path.join(d, "tmp", "trash")
    os.makedirs(trash, exist_ok=True)
    os.replace(os.path.join(d, "originals", name), os.path.join(trash, f"0-{name}"))
    open(os.path.join(trash, "0-" + "f" * 32 + ".jpg"), "wb").write(b"x")    # no matching row
    create_app({"TESTING": True, "DATA_DIR": d, "DB_PATH": mf_app.config["DB_PATH"]})
    assert os.path.exists(os.path.join(d, "originals", name))
    assert os.listdir(trash) == ["0-" + "f" * 32 + ".jpg"]                   # kept, logged
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


# ---------- #80: bulk delete, hidden flags cleared, the Library page ----------

import json as _json

BULK = "/api/v1/media/delete"


def add_photos(lib, n):
    """n distinct JPEGs through the library importer; returns their media ids (oldest first)."""
    ids = []
    for i in range(n):
        ids.append(lib.add(f"p{i}.jpg", jpeg(64, 64, (i * 20 % 256, 50, 90))))
    return ids


def media_ids(app):
    conn = mfdb.connect(app.config["DB_PATH"])
    try:
        return [r[0] for r in conn.execute("SELECT id FROM media ORDER BY id")]
    finally:
        conn.close()


def test_bulk_delete_removes_rows_and_files_and_bumps_rev(paired, lib, mf_app, client):
    add_photos(lib, 3)
    ids = media_ids(mf_app)
    d = mf_app.config["DATA_DIR"]
    names = {i: stored_name(mf_app, i) for i in ids}
    rev = client.get("/show-now").get_json()["rev"]
    r = paired.post(BULK, json={"ids": ids[:2]}, environ_base=PHONE)
    assert r.status_code == 200 and r.get_json() == {"deleted": ids[:2], "missing": [], "failed": []}
    assert media_ids(mf_app) == ids[2:]
    for i in ids[:2]:
        assert not os.path.exists(os.path.join(d, "originals", names[i]))      # disk check
    assert os.path.exists(os.path.join(d, "originals", names[ids[2]]))
    assert client.get("/show-now").get_json()["rev"] == rev + 1                 # one bump for the whole batch


def test_bulk_delete_repeats_unknown_and_already_gone(paired, lib, mf_app):
    add_photos(lib, 2)
    ids = media_ids(mf_app)
    r = paired.post(BULK, json={"ids": [ids[0], ids[0], 999999]}, environ_base=PHONE)
    assert r.get_json() == {"deleted": [ids[0]], "missing": [999999], "failed": []}
    assert media_ids(mf_app) == [ids[1]]


@pytest.mark.parametrize("body", [
    [], {}, {"ids": []}, {"ids": "1"}, {"ids": 1}, {"ids": [None]}, {"ids": ["1"]}, {"ids": [1.0]},
    {"ids": [True]}, {"ids": [0]}, {"ids": [-1]}, {"ids": [2**63]}, {"ids": [[1]]}, {"ids": [1], "extra": 1},
    {"ids": {"1": 1}}])
def test_bulk_delete_validates_ids_and_deletes_nothing(paired, lib, mf_app, body):
    add_photos(lib, 2)
    before = media_ids(mf_app)
    r = paired.post(BULK, json=body, environ_base=PHONE)
    assert r.status_code == 400
    assert media_ids(mf_app) == before


def test_bulk_delete_deeply_nested_json_is_400_not_500(paired, lib, mf_app):
    add_photos(lib, 2)
    before = media_ids(mf_app)
    body = "[" * 15000 + "]" * 15000                       # under the 32 KB cap; json.loads raises RecursionError
    r = paired.post(BULK, data=body, content_type="application/json", environ_base=PHONE)
    assert r.status_code == 400 and media_ids(mf_app) == before


def test_bulk_delete_cap_on_count_and_body_size(paired, lib, mf_app):
    from memoryframe import api
    add_photos(lib, 1)
    before = media_ids(mf_app)
    r = paired.post(BULK, json={"ids": list(range(1, api.MAX_BULK + 2))}, environ_base=PHONE)
    assert r.status_code == 413 and media_ids(mf_app) == before
    big = _json.dumps({"ids": [1] * (api.MAX_BULK_BODY)})                        # body over the byte cap
    r = paired.post(BULK, data=big, content_type="application/json", environ_base=PHONE)
    assert r.status_code == 413 and media_ids(mf_app) == before
    r = paired.post(BULK, json={"ids": list(range(1, api.MAX_BULK + 1))}, environ_base=PHONE)   # exactly the cap
    assert r.status_code == 200 and len(r.get_json()["missing"]) == api.MAX_BULK - 1


def test_bulk_delete_needs_json_and_a_paired_phone(paired, lib, mf_app, client):
    add_photos(lib, 1)
    ids = media_ids(mf_app)
    r = paired.post(BULK, data=_json.dumps({"ids": ids}), content_type="text/plain", environ_base=PHONE)
    assert r.status_code == 415 and media_ids(mf_app) == ids
    r = paired.post(BULK, data="{not json", content_type="application/json", environ_base=PHONE)
    assert r.status_code == 400
    paired.delete_cookie("mf_session")                                          # unpaired: nothing is deleted
    r = paired.post(BULK, json={"ids": ids}, environ_base=PHONE)
    assert r.status_code == 401 and media_ids(mf_app) == ids


def test_bulk_delete_needs_csrf_and_same_origin(paired, lib, mf_app):
    add_photos(lib, 1)
    ids = media_ids(mf_app)
    r = paired.post(BULK, json={"ids": ids}, csrf=False, environ_base=PHONE)
    assert r.status_code == 403
    r = paired.post(BULK, json={"ids": ids}, headers={"Origin": "http://evil.example"}, environ_base=PHONE)
    assert r.status_code == 403
    assert media_ids(mf_app) == ids


def test_bulk_delete_never_touches_files_outside_the_photo_dirs(paired, lib, mf_app, tmp_path):
    """A row whose stored_name is a path (never made by the server) is refused: nothing outside moves."""
    add_photos(lib, 2)
    good = media_ids(mf_app)
    victim = tmp_path / "victim.txt"
    victim.write_text("keep me")
    conn = mfdb.connect(mf_app.config["DB_PATH"])
    bad = []
    for name in (f"../../{victim.name}", str(victim), "..", "a/b.jpg", ".hidden.jpg"):
        cur = conn.execute("INSERT INTO media (stored_name, original_name, kind, sha256, size_bytes, source, created_at)"
                           " VALUES (?, 'x', 'image', 'h', 1, 'upload', 1)", (name,))
        bad.append(cur.lastrowid)
    conn.commit()
    conn.close()
    r = paired.post(BULK, json={"ids": bad + good[:1]}, environ_base=PHONE)
    out = r.get_json()
    assert r.status_code == 200 and out["failed"] == bad and out["deleted"] == good[:1]     # others still go
    assert victim.read_text() == "keep me"
    assert set(bad) <= set(media_ids(mf_app))                                    # refused rows stay, visibly


def test_imaging_check_stored_name(mf_app):
    d = mf_app.config["DATA_DIR"]
    imaging.check_stored_name(d, "0123abcd.jpg")
    for bad in ("", None, "..", "../x.jpg", "a/b.jpg", "a\\b.jpg", ".x.jpg", "x\0.jpg", "/etc/passwd"):
        with pytest.raises(imaging.UnsafeName):
            imaging.check_stored_name(d, bad)


def test_old_hidden_flags_are_cleared_and_nothing_can_set_them(paired, lib, mf_app, client):
    add_photos(lib, 2)
    conn = mfdb.connect(mf_app.config["DB_PATH"])
    conn.execute("UPDATE media SET hidden = 1")
    conn.commit()
    conn.close()
    assert len(client.get("/media").get_json()) == 0                            # hidden: the slideshow skips them
    mfdb.init_db(mf_app.config["DB_PATH"])                                      # next start
    assert len(client.get("/media").get_json()) == 2                            # all show again
    assert len(paired.get("/api/v1/media", environ_base=PHONE).get_json()) == 2
    assert paired.get("/api/v1/status", environ_base=PHONE).get_json()["media"] == {"total": 2, "visible": 2}


def test_library_page_is_a_plain_grid_without_names_or_hide(paired, lib, mf_app):
    add_photos(lib, 3)
    h = paired.get("/admin/library", environ_base=PHONE).get_data(as_text=True)
    for gone in ("Show now", "Hide", "Unhide", "Hidden", "p0.jpg", "p1.jpg", "<a class=\"tile", "style="):
        assert gone not in h, gone
    assert h.count('class="tile') == 3 and h.count('/thumb"') == 3
    assert 'aria-label="Photo 1 of 3"' in h and 'aria-label="Photo 3 of 3"' in h
    assert "3 photos on the frame" in h and 'id="selectBtn"' in h and 'id="delBtn" disabled' in h
    assert 'role="alertdialog"' in h and 'aria-modal="true"' in h and 'id="confirmCancel"' in h
    assert 'alt=""' in h and 'loading="lazy"' in h


def test_library_page_empty(paired):
    h = paired.get("/admin/library", environ_base=PHONE).get_data(as_text=True)
    assert 'id="grid"' not in h and "No photos yet" in h and 'id="selectBtn"' not in h
