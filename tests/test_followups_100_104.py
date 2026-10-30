"""Follow-ups: #100 O_NOFOLLOW + fstat, Hale's L1 (JPEG trailer after FFD9), #104 ENOSPC does not use up tries,
directory fsync after os.replace."""
import errno
import io
import logging
import os
import uuid

import pytest
from PIL import Image

from memoryframe import imaging, library
from test_orphan_reconcile import drop_file, jpeg, reconcile, rows
from test_followup_37 import T, _add


def _exif_thumb_jpeg():
    thumb = io.BytesIO()
    Image.new("RGB", (160, 120), (200, 10, 10)).save(thumb, "JPEG")
    exif = Image.Exif()
    b = io.BytesIO()
    im = Image.new("RGB", (1600, 1200), (10, 120, 200))
    # APP1 carrying a full JPEG (with its own FFD9) before the main image, like a camera thumbnail
    im.save(b, "JPEG", exif=exif.tobytes() + thumb.getvalue())
    return b.getvalue()


def test_l1_samsung_trailer_is_adopted(mf_app):
    trailer = b"\x00" * 100 + b"SEFH" + os.urandom(2000) + b"SEFT"
    drop_file(mf_app, uuid.uuid4().hex + ".jpg", jpeg() + trailer)
    assert reconcile(mf_app) == (1, 0)


@pytest.mark.parametrize("drop", [2, 40, 5000])
def test_l1_real_truncation_still_rejected_even_with_thumbnail_ffd9(mf_app, caplog, drop):
    data = _exif_thumb_jpeg()
    assert data.count(b"\xff\xd9") >= 2
    name = uuid.uuid4().hex + ".jpg"
    drop_file(mf_app, name, data[:-drop])
    with caplog.at_level(logging.WARNING):
        assert reconcile(mf_app) == (0, 1)
    assert f"{name} (truncated)" in caplog.text


def test_jpeg_parser_units():
    good = jpeg(400, 300)
    assert library.jpeg_ends_cleanly(good)
    assert library.jpeg_ends_cleanly(good + os.urandom(70000))
    assert not library.jpeg_ends_cleanly(good[:-1])
    assert not library.jpeg_ends_cleanly(b"not a jpeg")
    p = io.BytesIO()
    Image.new("RGB", (300, 200), (5, 6, 7)).save(p, "JPEG", progressive=True)
    assert library.jpeg_ends_cleanly(p.getvalue()) and not library.jpeg_ends_cleanly(p.getvalue()[:-300])


def test_reconcile_opens_with_o_nofollow_and_uses_fstat(mf_app, monkeypatch):
    drop_file(mf_app, uuid.uuid4().hex + ".jpg", jpeg())
    flags, lstats = [], []
    real_open = os.open
    monkeypatch.setattr(library.os, "open", lambda p, f, *a: flags.append(f) or real_open(p, f, *a))
    monkeypatch.setattr(library.os, "lstat", lambda *a: lstats.append(a))
    assert reconcile(mf_app) == (1, 0)
    assert any(f & os.O_NOFOLLOW for f in flags) and lstats == []


def test_fifo_orphan_does_not_hang_and_is_reported(mf_app, caplog):
    name = uuid.uuid4().hex + ".jpg"
    os.mkfifo(os.path.join(mf_app.config["DATA_DIR"], "originals", name))
    with caplog.at_level(logging.WARNING):
        assert reconcile(mf_app) == (0, 1)
    assert f"{name} (not a regular file (symlink or special), not followed)" in caplog.text and rows(mf_app) == []


def test_enospc_does_not_use_up_tries(mf_app, lib, monkeypatch):
    conn, stored, dst = _add(lib, mf_app)
    d = mf_app.config["DATA_DIR"]
    if os.path.exists(dst):
        os.remove(dst)
    calls = []

    def full(*a, **k):
        calls.append(1)
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(imaging, "make_display_copy", full)
    for _ in range(6):                                       # six boots with a full disk
        assert imaging.backfill(conn, d, T) == (0, 1)
    assert len(calls) == 6                                   # retried every time, never made permanent
    assert imaging._load_failures(d)[stored]["tries"] == 0
    monkeypatch.undo()
    assert imaging.backfill(conn, d, T) == (1, 0)            # space freed: the copy is made
    conn.close()


def test_write_atomic_fsyncs_the_directory(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(imaging, "fsync_dir", lambda p: seen.append(p))
    dst = str(tmp_path / "x.jpg")
    imaging.write_atomic(dst, jpeg(100, 100))
    assert seen == [str(tmp_path)]
