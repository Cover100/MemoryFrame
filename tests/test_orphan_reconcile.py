"""#91: a cut between add_file's os.replace and its DB commit leaves a photo in originals/ with no row.
The start-up reconcile adopts valid ones and reports the rest; it never deletes anything."""
import hashlib
import io
import logging
import os
import uuid

import pytest
from PIL import Image

from memoryframe import create_app, db as mfdb, imaging, library

TARGET = (1000, 600)


def jpeg(w=1600, h=1200, color=(10, 120, 200)):
    b = io.BytesIO()
    Image.new("RGB", (w, h), color).save(b, "JPEG", quality=90)
    return b.getvalue()


def cut_after_replace(app, upload):
    """add_file up to and including os.replace, then 'power off': the file is in originals/, no row."""
    d = app.config["DATA_DIR"]
    src = os.path.join(d, "tmp", "upload-src")
    with open(src, "wb") as f:
        f.write(upload)
    stored = uuid.uuid4().hex + ".jpg"
    tmp = os.path.join(d, "tmp", stored)
    imaging.make_stored_copy(src, tmp, TARGET)
    os.replace(tmp, os.path.join(d, "originals", stored))
    os.remove(src)
    age(os.path.join(d, "originals", stored))
    return stored


def age(path):
    os.utime(path, (1_000_000, 1_000_000))       # older than storage.SETTLE_S: not a live upload


def drop_file(app, name, data, fresh=False):
    path = os.path.join(app.config["DATA_DIR"], "originals", name)
    with open(path, "wb") as f:
        f.write(data)
    if not fresh:
        age(path)
    return path


def rows(app):
    conn = mfdb.connect(app.config["DB_PATH"])
    try:
        return conn.execute("SELECT * FROM media ORDER BY id").fetchall()
    finally:
        conn.close()


def reconcile(app):
    conn = mfdb.connect(app.config["DB_PATH"])
    try:
        return library.reconcile_orphans(conn, app.config["DATA_DIR"])
    finally:
        conn.close()


def originals(app):
    return sorted(os.listdir(os.path.join(app.config["DATA_DIR"], "originals")))


def restart(app):
    return create_app({k: app.config[k] for k in ("TESTING", "DATA_DIR", "DB_PATH", "LEGACY_UPLOADS")})


def test_cut_then_restart_shows_the_photo(mf_app, caplog):
    stored = cut_after_replace(mf_app, jpeg())
    assert rows(mf_app) == [] and originals(mf_app) == [stored]
    with caplog.at_level(logging.WARNING):
        app2 = restart(mf_app)                       # the next start
    (row,) = rows(app2)
    path = os.path.join(app2.config["DATA_DIR"], "originals", stored)
    assert row["stored_name"] == stored and row["kind"] == "image" and row["source"] == "upload"
    assert row["size_bytes"] == os.path.getsize(path)
    assert row["sha256"] == hashlib.sha256(open(path, "rb").read()).hexdigest()
    assert row["created_at"] == os.path.getmtime(path)
    assert originals(app2) == [stored]
    assert f"orphan file adopted into the library: {stored}" in caplog.text
    with app2.test_client() as c:                    # it is in the slideshow list and its file is served
        assert c.get("/media").get_json() == [{"type": "image", "filename": stored}]
        assert c.get(f"/uploads/{stored}").status_code == 200


def test_corrupt_orphan_is_reported_and_left_in_place(mf_app, caplog):
    name = uuid.uuid4().hex + ".jpg"
    path = drop_file(mf_app, name, b"\xff\xd8\xff garbage, not a photo")
    before = open(path, "rb").read()
    with caplog.at_level(logging.WARNING):
        assert reconcile(mf_app) == (0, 1)
    assert rows(mf_app) == [] and open(path, "rb").read() == before
    assert (f"orphan file left in place: {name} (truncated)" in caplog.text      # no JPEG end marker
            or f"orphan file left in place: {name} (unreadable" in caplog.text)
    assert "garbage" not in caplog.text                                    # never the contents


def test_truncated_orphan_is_not_adopted(mf_app):
    name = uuid.uuid4().hex + ".png"
    b = io.BytesIO()
    Image.new("RGB", (300, 300), (1, 2, 3)).save(b, "PNG")
    drop_file(mf_app, name, b.getvalue()[:60])
    assert reconcile(mf_app) == (0, 1) and rows(mf_app) == [] and originals(mf_app) == [name]


def test_orphan_with_the_sha_of_an_existing_row_is_reported_and_kept(mf_app, caplog):
    data = jpeg(300, 200)
    drop_file(mf_app, (first := uuid.uuid4().hex + ".jpg"), data)
    assert reconcile(mf_app) == (1, 0)
    dup = uuid.uuid4().hex + ".jpg"
    drop_file(mf_app, dup, data)                                           # same bytes, no row
    with caplog.at_level(logging.WARNING):
        assert reconcile(mf_app) == (0, 1)
    assert [r["stored_name"] for r in rows(mf_app)] == [first]
    assert originals(mf_app) == sorted([first, dup])
    assert f"orphan file left in place: {dup} (same content as a photo already in the library)" in caplog.text


@pytest.mark.parametrize("name,why", [
    (lambda: uuid.uuid4().hex + ".mp4", "video"),
    (lambda: "holiday.jpg", "not a server-made photo name"),
    (lambda: uuid.uuid4().hex + ".txt", "not a server-made photo name"),
])
def test_other_orphans_are_reported_not_adopted(mf_app, caplog, name, why):
    n = name()
    drop_file(mf_app, n, jpeg(100, 100))
    with caplog.at_level(logging.WARNING):
        assert reconcile(mf_app) == (0, 1)
    assert rows(mf_app) == [] and originals(mf_app) == [n]
    assert f"orphan file left in place: {n} (" in caplog.text and why in caplog.text


def test_hidden_files_and_directories_are_ignored(mf_app, caplog):
    d = os.path.join(mf_app.config["DATA_DIR"], "originals")
    drop_file(mf_app, ".hidden", b"x")
    os.mkdir(os.path.join(d, "sub"))
    with caplog.at_level(logging.WARNING):
        assert reconcile(mf_app) == (0, 0)
    assert originals(mf_app) == [".hidden", "sub"] and "orphan" not in caplog.text


def test_nothing_is_ever_deleted_and_a_second_run_changes_nothing(mf_app):
    good = cut_after_replace(mf_app, jpeg())
    bad = uuid.uuid4().hex + ".jpg"
    drop_file(mf_app, bad, b"nope")
    odd = "notes.txt"
    drop_file(mf_app, odd, b"hello")
    before = {n: open(os.path.join(mf_app.config["DATA_DIR"], "originals", n), "rb").read() for n in originals(mf_app)}
    assert reconcile(mf_app) == (1, 2)
    snapshot = rows(mf_app)
    assert reconcile(mf_app) == (0, 2)                                     # idempotent: no second row
    assert [tuple(r) for r in rows(mf_app)] == [tuple(r) for r in snapshot]
    after = {n: open(os.path.join(mf_app.config["DATA_DIR"], "originals", n), "rb").read() for n in originals(mf_app)}
    assert after == before and {good, bad, odd} == set(after)


def test_large_library_without_orphans_does_no_image_work(mf_app, monkeypatch):
    d = mf_app.config["DATA_DIR"]
    conn = mfdb.connect(mf_app.config["DB_PATH"])
    for i in range(2000):
        name = f"{i:032x}.jpg"
        open(os.path.join(d, "originals", name), "wb").close()
        conn.execute("INSERT INTO media (stored_name, original_name, kind, sha256, size_bytes, source, created_at)"
                     " VALUES (?, ?, 'image', ?, 0, 'upload', 0)", (name, name, f"{i:064x}"))
    conn.commit()
    calls = []
    monkeypatch.setattr(library, "sha256_file", lambda p: calls.append(p))
    monkeypatch.setattr(Image, "open", lambda *a, **k: calls.append(a))
    try:
        assert library.reconcile_orphans(conn, d) == (0, 0)
    finally:
        conn.close()
    assert calls == []


def test_a_failing_reconcile_never_blocks_start_up(mf_app, monkeypatch, caplog):
    def boom(*a, **k):
        raise RuntimeError("disk on fire")
    monkeypatch.setattr(library, "reconcile_orphans", boom)
    with caplog.at_level(logging.WARNING):
        app2 = restart(mf_app)
    assert app2 is not None and "start-up orphan reconcile failed: disk on fire" in caplog.text


# ---------------- #100 (Hollis) ----------------

def test_f1_fresh_orphan_and_fresh_tmp_files_are_left_for_the_next_start(mf_app):
    """Another process may be mid-upload: its file sits in originals/ before its commit, and its temp
    files in tmp/. A second start-up must neither adopt the first (its commit would then fail and
    add_file would remove the photo) nor delete the others."""
    d = mf_app.config["DATA_DIR"]
    live = uuid.uuid4().hex + ".jpg"
    drop_file(mf_app, live, jpeg(), fresh=True)
    temps = ["upload-" + "a" * 32, live, live + ".sha256"]
    for n in temps:
        open(os.path.join(d, "tmp", n), "wb").write(b"x")
    restart(mf_app)
    assert rows(mf_app) == [] and originals(mf_app) == [live]
    assert set(temps) <= set(os.listdir(os.path.join(d, "tmp")))
    age(os.path.join(d, "originals", live))                     # a minute later it is a real orphan
    assert reconcile(mf_app) == (1, 0)


def test_f2_add_file_notes_the_upload_hash_only_until_its_commit(mf_app, monkeypatch):
    d = mf_app.config["DATA_DIR"]
    src = os.path.join(d, "tmp", "upload-src")
    open(src, "wb").write(jpeg())
    seen = {}
    real_replace = os.replace

    def spy(a, b):
        real_replace(a, b)
        if "originals" in b:
            seen["side"] = open(library.sha_sidecar(d, os.path.basename(b))).read()
    monkeypatch.setattr(library.os, "replace", spy)
    conn = mfdb.connect(mf_app.config["DB_PATH"])
    try:
        stored = library.add_file(conn, d, src, "a.jpg", "upload", store_target=TARGET)
    finally:
        conn.close()
    assert seen["side"] == hashlib.sha256(open(src, "rb").read()).hexdigest()
    assert not os.path.exists(library.sha_sidecar(d, stored))                # removed after the commit


def test_f2_adopted_row_keeps_the_upload_hash_so_a_retry_is_a_duplicate(mf_app):
    d = mf_app.config["DATA_DIR"]
    upload = jpeg(2400, 1800)
    stored = cut_after_replace(mf_app, upload)
    with open(library.sha_sidecar(d, stored), "w") as f:                    # what add_file left before the cut
        f.write(hashlib.sha256(upload).hexdigest())
    assert reconcile(mf_app) == (1, 0)
    assert rows(mf_app)[0]["sha256"] == hashlib.sha256(upload).hexdigest()
    assert not os.path.exists(library.sha_sidecar(d, stored))
    src = os.path.join(d, "tmp", "upload-retry")
    open(src, "wb").write(upload)
    conn = mfdb.connect(mf_app.config["DB_PATH"])
    try:
        assert library.add_file(conn, d, src, "IMG_1.jpg", "upload", store_target=TARGET) is None   # duplicate
    finally:
        conn.close()
    assert len(rows(mf_app)) == 1


def test_f2_a_bad_sidecar_falls_back_to_the_file_hash(mf_app):
    stored = cut_after_replace(mf_app, jpeg())
    open(library.sha_sidecar(mf_app.config["DATA_DIR"], stored), "w").write("not a hash")
    assert reconcile(mf_app) == (1, 0)
    path = os.path.join(mf_app.config["DATA_DIR"], "originals", stored)
    assert rows(mf_app)[0]["sha256"] == hashlib.sha256(open(path, "rb").read()).hexdigest()


def png(w=300, h=300):
    b = io.BytesIO()
    Image.new("RGB", (w, h), (1, 2, 3)).save(b, "PNG")
    return b.getvalue()


@pytest.mark.parametrize("ext,drop", [(".jpg", 40), (".png", 12)], ids=["jpeg", "png-no-IEND"])
def test_f3_truncated_jpeg_and_png_are_reported_and_kept(mf_app, caplog, ext, drop):
    """Pillow's verify() passes these; only the end-marker check catches them."""
    name = uuid.uuid4().hex + ext
    data = jpeg(800, 600) if ext == ".jpg" else png()
    cut = data[: len(data) - drop]
    path = drop_file(mf_app, name, cut)
    with caplog.at_level(logging.WARNING):
        assert reconcile(mf_app) == (0, 1)
    assert rows(mf_app) == [] and open(path, "rb").read() == cut
    assert f"orphan file left in place: {name} (truncated)" in caplog.text


def test_f3_jpeg_with_trailing_padding_is_still_adopted(mf_app):
    drop_file(mf_app, uuid.uuid4().hex + ".jpg", jpeg(400, 300) + b"\x00" * 16)
    assert reconcile(mf_app) == (1, 0)
