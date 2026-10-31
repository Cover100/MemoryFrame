"""#96: storage safety — reserve, headroom check, almost_full, backups pruning, log/journal caps."""
import io
import os
from collections import namedtuple
from pathlib import Path

import shutil

import pytest
from PIL import Image

from memoryframe import db as mfdb
from memoryframe import migrate, storage, upload

PHONE = {"REMOTE_ADDR": "10.42.0.23"}
du = namedtuple("du", "total used free")
MB = 1024 * 1024
ROOT = Path(__file__).resolve().parent.parent


def jpeg():
    b = io.BytesIO()
    Image.new("RGB", (800, 600), (10, 120, 200)).save(b, "JPEG", quality=90)
    return b.getvalue()


def post(client, data):
    return client.post("/api/v1/media", data={"file": (io.BytesIO(data), "p.jpg")},
                       content_type="multipart/form-data", environ_base=PHONE)


def rows(app):
    conn = mfdb.connect(app.config["DB_PATH"])
    try:
        return conn.execute("SELECT COUNT(*) FROM media").fetchone()[0]
    finally:
        conn.close()


def tmp_empty(app):
    t = os.path.join(app.config["DATA_DIR"], "tmp")
    return not os.path.isdir(t) or os.listdir(t) == []


def test_min_free_is_max_of_500mb_and_2pct(monkeypatch, tmp_path):
    cfg = {"MIN_FREE_BYTES": None, "DATA_DIR": str(tmp_path)}
    monkeypatch.setattr(shutil, "disk_usage", lambda p: du(16 * 1024**3, 0, 0))
    assert storage.min_free(cfg) == 500 * MB                       # 2% of 16 GB < 500 MB
    monkeypatch.setattr(shutil, "disk_usage", lambda p: du(256 * 1024**3, 0, 0))
    assert storage.min_free(cfg) == int(256 * 1024**3 * 0.02)
    assert storage.min_free({"MIN_FREE_BYTES": 42 * MB}) == 42 * MB  # env override wins


def test_env_override(monkeypatch):
    monkeypatch.setenv("MEMORYFRAME_MIN_FREE_MB", "7")
    assert storage.default_config()["MIN_FREE_BYTES"] == 7 * MB


def _free(monkeypatch, free):
    d = lambda p: du(10 * 1024**3, 0, free)
    monkeypatch.setattr(shutil, "disk_usage", d)


@pytest.mark.parametrize("extra,ok", [(-1, False), (0, False), (50 * MB, True)])
def test_threshold(mf_app, paired, monkeypatch, extra, ok):
    data = jpeg()
    _free(monkeypatch, 500 * MB + extra)           # 0 headroom: no room for 3 copies
    r = post(paired, data)
    assert (r.status_code in (200, 201)) is ok
    if not ok:
        assert r.status_code == 507 and r.get_json()["error"] == upload.FULL
        assert rows(mf_app) == 0 and tmp_empty(mf_app)


def test_file_larger_than_headroom_refused(mf_app, paired, monkeypatch):
    data = jpeg()
    _free(monkeypatch, 500 * MB + 2 * len(data))   # above reserve, but < 3x the upload
    r = post(paired, data)
    assert r.status_code == 507 and rows(mf_app) == 0 and tmp_empty(mf_app)


def test_enospc_is_507_and_clean(mf_app, paired, monkeypatch):
    _free(monkeypatch, 10 * 1024**3)
    data = jpeg()
    real = Image.Image.save
    def boom(*a, **k):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(Image.Image, "save", boom)
    r = post(paired, data)
    monkeypatch.setattr(Image.Image, "save", real)
    assert r.status_code == 507 and rows(mf_app) == 0 and tmp_empty(mf_app)


def test_frame_status_almost_full_and_orphans(mf_app, monkeypatch):
    _free(monkeypatch, 100 * MB)
    with mf_app.test_client() as c:
        s = c.get("/frame/status").get_json()
    assert s["storage"]["almost_full"] is True and "orphans" in s
    _free(monkeypatch, 5 * 1024**3)
    with mf_app.test_client() as c:
        assert c.get("/frame/status").get_json()["storage"]["almost_full"] is False


def test_prune_backups_keeps_newest_only(tmp_path):
    for n in ("legacy-uploads-1.tar", "legacy-uploads-2.tar", "legacy-uploads-3.tar", "other.tar"):
        (tmp_path / n).write_bytes(b"x")
    keep = tmp_path / "legacy-uploads-3.tar"
    assert migrate.prune_backups(str(tmp_path), str(keep), log=lambda m: None) == 2
    assert sorted(os.listdir(tmp_path)) == ["legacy-uploads-3.tar", "other.tar"]


def test_journal_and_coredump_caps_installed():
    j = (ROOT / "deploy/journald-memoryframe.conf").read_text()
    for k in ("SystemMaxUse=50M", "SystemKeepFree=1G", "SystemMaxFileSize=8M", "MaxRetentionSec=2week"):
        assert k in j
    c = (ROOT / "deploy/coredump-memoryframe.conf").read_text()
    assert "Storage=none" in c and "ProcessSizeMax=0" in c
    assert "LimitCORE=0" in (ROOT / "deploy/memoryframe.service").read_text()
    i = (ROOT / "deploy/install.sh").read_text()
    assert "for d in journald coredump" in i and "/etc/systemd/$d.conf.d/memoryframe.conf" in i
