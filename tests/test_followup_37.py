"""#37 follow-up: FORCE_NETMODE is a gated test hook; failed remakes; truncated copies."""
import io
import logging
import os

import pytest
from PIL import Image

from memoryframe import db as mfdb, frameinfo, imaging

T = (1000, 600)


# ---------- (1) MEMORYFRAME_FORCE_NETMODE ----------

@pytest.mark.parametrize("raw, mode, ip", [("hotspot", "hotspot", "10.42.0.1"), ("unknown", "unknown", None),
                                           ("lan:192.168.50.124", "lan", "192.168.50.124")])
def test_force_valid_under_pytest(monkeypatch, raw, mode, ip):
    monkeypatch.setenv("MEMORYFRAME_FORCE_NETMODE", raw)
    n = frameinfo._forced_network()
    assert n["mode"] == mode and n["lan_ip"] == ip


@pytest.mark.parametrize("raw", ["lan", "lan:", "lan:999.1.1.1", "lan:abc", "lan:127.0.0.1", "lan:0.0.0.0",
                                 "lan:::1", "dev", "HOTSPOT", "hotspot;rm"])
def test_force_invalid_values_ignored(monkeypatch, caplog, raw):
    monkeypatch.setenv("MEMORYFRAME_FORCE_NETMODE", raw)
    with caplog.at_level(logging.WARNING):
        assert frameinfo._forced_network() is None


def test_force_ignored_outside_tests(monkeypatch, caplog):
    monkeypatch.setenv("MEMORYFRAME_FORCE_NETMODE", "hotspot")
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    with caplog.at_level(logging.WARNING):
        assert frameinfo._forced_network() is None


def test_force_allowed_in_testing_app(monkeypatch, mf_app):
    monkeypatch.setenv("MEMORYFRAME_FORCE_NETMODE", "hotspot")
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    with mf_app.app_context():
        assert frameinfo._forced_network()["mode"] == "hotspot"


def test_force_ignored_in_non_testing_app(monkeypatch, mf_app):
    monkeypatch.setenv("MEMORYFRAME_FORCE_NETMODE", "hotspot")
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    mf_app.testing = False
    try:
        with mf_app.app_context():
            assert frameinfo._forced_network() is None
    finally:
        mf_app.testing = True


# ---------- (2) failed remakes ----------

def _add(lib, mf_app, name="p.jpg"):
    b = io.BytesIO()
    Image.new("RGB", (3000, 4000), (90, 30, 30)).save(b, "JPEG")
    lib.add(name, b.getvalue())
    conn = mfdb.connect(mf_app.config["DB_PATH"])
    stored = conn.execute("SELECT stored_name FROM media WHERE original_name = ?", (name,)).fetchone()[0]
    return conn, stored, os.path.join(mf_app.config["DATA_DIR"], "display", imaging.display_name(stored))


def test_missing_original_with_stale_copy_counts_failed_and_is_not_retried(mf_app, lib, monkeypatch):
    conn, stored, dst = _add(lib, mf_app)
    d = mf_app.config["DATA_DIR"]
    Image.new("RGB", (1000, 750)).save(dst)                 # stale copy
    os.remove(os.path.join(d, "originals", stored))
    assert imaging.backfill(conn, d, T) == (0, 1)
    calls = []
    monkeypatch.setattr(imaging, "_ensure_copy", lambda *a: calls.append(a) or (None, None))
    assert imaging.backfill(conn, d, T) == (0, 1)           # next start: skipped
    assert calls == []
    conn.close()


def test_unreadable_original_recorded_then_retried_when_it_changes(mf_app, lib):
    conn, stored, dst = _add(lib, mf_app)
    d = mf_app.config["DATA_DIR"]
    orig = os.path.join(d, "originals", stored)
    if os.path.exists(dst):
        os.remove(dst)
    with open(orig, "wb") as f:
        f.write(b"not an image")
    assert imaging.backfill(conn, d, T) == (0, 1)
    assert stored in imaging._load_failures(d)
    b = io.BytesIO()
    Image.new("RGB", (3000, 4000)).save(b, "JPEG")
    with open(orig, "wb") as f:
        f.write(b.getvalue())
    os.utime(orig, (1, 1))                                  # different mtime/size -> retried
    assert imaging.backfill(conn, d, T) == (1, 0)
    assert stored not in imaging._load_failures(d)
    conn.close()


def test_force_retries_known_failures(mf_app, lib):
    conn, stored, dst = _add(lib, mf_app)
    d = mf_app.config["DATA_DIR"]
    os.remove(os.path.join(d, "originals", stored))
    assert imaging.backfill(conn, d, T) == (0, 1)
    assert imaging.backfill(conn, d, T, force=True) == (0, 1)
    conn.close()


# ---------- (3) truncated copies ----------

def test_truncated_copy_is_not_current(tmp_path):
    src, dst = tmp_path / "s.jpg", tmp_path / "d.jpg"
    Image.new("RGB", (3000, 4000), (1, 2, 3)).save(src)
    imaging.make_display_copy(str(src), str(dst), T)
    assert imaging.copy_is_current(str(dst), T)
    data = dst.read_bytes()
    trunc = tmp_path / "t.jpg"
    trunc.write_bytes(data[: len(data) // 2])
    assert not imaging.copy_is_current(str(trunc), T)
