"""#92 cleanup: phone_wifi() looks the device up once; --force never loses the old copy; transient
remake failures (memory, disk, timeout) are retried instead of being recorded as permanent."""
import errno
import os

import pytest
from PIL import Image

from memoryframe import auth, imaging
from test_followup_37 import T, _add

PHONE = {"REMOTE_ADDR": "10.42.0.23"}


# ---------- phone_wifi(): one lookup ----------

@pytest.fixture
def product_mode(monkeypatch):
    monkeypatch.setenv("MEMORYFRAME_SSID", "Hotspot-92")
    monkeypatch.setattr("memoryframe.frameinfo.dev_mode", lambda: False)


def _phone_wifi(mf_app, setup=None):
    from flask import g
    with mf_app.test_request_context("/admin/", environ_base=PHONE):
        if setup:
            setup(g)
        ctx = {}
        mf_app.update_template_context(ctx)
        return ctx["phone_wifi"]()


def test_phone_wifi_uses_the_session_lookup_of_the_request(mf_app, product_mode, monkeypatch):
    calls = []
    monkeypatch.setattr(auth, "current_device", lambda: calls.append(1) or 5)
    assert _phone_wifi(mf_app, lambda g: setattr(g, "device_id", 5)) == {"ssid": "Hotspot-92", "mode": "hotspot"}
    assert calls == []                                    # require_session already did it: no second lookup


def test_phone_wifi_looks_up_once_when_the_request_has_not(mf_app, product_mode, monkeypatch):
    calls = []
    monkeypatch.setattr(auth, "current_device", lambda: calls.append(1) or 5)
    assert _phone_wifi(mf_app)["ssid"] == "Hotspot-92"
    assert calls == [1]
    calls.clear()
    monkeypatch.setattr(auth, "current_device", lambda: calls.append(1))        # unpaired: nothing shown
    assert _phone_wifi(mf_app) == {"ssid": "", "mode": "unknown"}
    assert calls == [1]


# ---------- --force keeps the old copy until the new one is ready ----------

def _current_copy(lib, mf_app):
    conn, stored, dst = _add(lib, mf_app)
    d = mf_app.config["DATA_DIR"]
    imaging.backfill(conn, d, T)
    assert imaging.copy_is_current(dst, T)
    return conn, d, stored, dst


def test_force_remakes_to_a_temp_file_and_replaces_atomically(mf_app, lib, monkeypatch):
    conn, d, stored, dst = _current_copy(lib, mf_app)
    real, seen = imaging.make_display_copy, []

    def spy(src, out, target, **kw):
        seen.append(os.path.exists(dst))                  # the old copy is still in place while the new one is made
        return real(src, out, target, **kw)

    monkeypatch.setattr(imaging, "make_display_copy", spy)
    assert imaging.backfill(conn, d, T, force=True) == (1, 0)
    assert seen == [True] and imaging.copy_is_current(dst, T)
    assert not os.path.exists(dst + ".tmp")
    conn.close()


@pytest.mark.parametrize("err", [OSError(errno.ENOSPC, "No space left on device"), MemoryError(), ValueError("bad")])
def test_force_failure_keeps_the_old_copy_and_leaves_no_temp(mf_app, lib, monkeypatch, err):
    conn, d, stored, dst = _current_copy(lib, mf_app)
    before = open(dst, "rb").read()

    def failing(src, out, target, **kw):
        open(out + ".tmp", "wb").write(b"half")             # a partial write, as a full disk would leave
        raise err

    monkeypatch.setattr(imaging, "make_display_copy", failing)
    assert imaging.backfill(conn, d, T, force=True) == (0, 1)
    assert open(dst, "rb").read() == before and imaging.copy_is_current(dst, T)
    assert not os.path.exists(dst + ".tmp")
    conn.close()


# ---------- transient vs permanent failures ----------

@pytest.mark.parametrize("e, transient", [
    (MemoryError(), True), (TimeoutError(), True), (OSError(errno.ENOSPC, "disk full"), True),
    (OSError(errno.EIO, "I/O error"), True), (PermissionError(errno.EACCES, "denied"), True),
    (OSError("image file is truncated (3 bytes not processed)"), False),       # Pillow: corrupt photo, no errno
    (Image.UnidentifiedImageError("cannot identify image file"), False),
    (Image.DecompressionBombError("too many pixels"), False), (ValueError("x"), False), (ZeroDivisionError(), False),
])
def test_transient_failure_kinds(e, transient):
    assert imaging.transient_failure(e) is transient


@pytest.mark.parametrize("err", [MemoryError(), TimeoutError(), OSError(errno.ENOSPC, "No space left on device")])
def test_transient_failure_is_not_recorded_and_is_retried(mf_app, lib, monkeypatch, err):
    conn, stored, dst = _add(lib, mf_app)
    d = mf_app.config["DATA_DIR"]
    if os.path.exists(dst):
        os.remove(dst)
    real = imaging.make_display_copy

    def failing(*a, **k):
        raise err

    monkeypatch.setattr(imaging, "make_display_copy", failing)
    assert imaging.backfill(conn, d, T) == (0, 1)           # counted as failed ...
    assert stored not in imaging._load_failures(d)          # ... but not remembered
    monkeypatch.setattr(imaging, "make_display_copy", real)
    assert imaging.backfill(conn, d, T) == (1, 0)           # the next run (start-up) just makes it
    conn.close()


def test_corrupt_photo_stays_recorded_as_permanent(mf_app, lib):
    conn, stored, dst = _add(lib, mf_app)
    d = mf_app.config["DATA_DIR"]
    if os.path.exists(dst):
        os.remove(dst)
    with open(os.path.join(d, "originals", stored), "wb") as f:
        f.write(b"\xff\xd8\xff\xe0 truncated nonsense")
    assert imaging.backfill(conn, d, T) == (0, 1)
    assert stored in imaging._load_failures(d)
    assert imaging.backfill(conn, d, T) == (0, 1)           # skipped, not retried
    conn.close()
