"""MF-11: blueprints, storage layout, SQLite, legacy import with backup, guards."""
import json
import os
import shutil
import tarfile

import pytest

import app as app_module
from memoryframe import db as mfdb
from memoryframe.migrate import MigrationError, migrate_legacy

REMOTE = {"REMOTE_ADDR": "10.42.0.23"}     # a phone on the frame's hotspot


# ---------------- blueprints ----------------

def test_blueprints_registered(mf_app):
    assert {"display", "admin", "api"} <= set(mf_app.blueprints)
    rules = {r.rule: r.endpoint for r in mf_app.url_map.iter_rules()}
    assert rules["/"] == "display.index"
    assert rules["/media"] == "display.media"
    assert rules["/admin/"] == "admin.home"
    assert rules["/api/v1/status"] == "api.status"


def test_admin_and_api_placeholders(paired, lib):
    assert paired.get("/admin/").status_code == 200
    lib.add("a.jpg")
    assert paired.get("/api/v1/status").get_json() == {"media": {"total": 1, "visible": 1}}


def test_schema_tables(mf_app):
    conn = mfdb.connect(mf_app.config["DB_PATH"])
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert {"media", "devices", "sessions", "settings", "schema_version"} <= names


# ---------------- route guards ----------------

# Every route that accepts a state-changing method on the display blueprint.
DISPLAY_WRITE_ROUTES = [("/brightness", "post", {"json": {"value": 100}})]


def test_display_write_routes_are_enumerated(mf_app):
    """Fails if a new display write route is added without being listed above."""
    found = set()
    for r in mf_app.url_map.iter_rules():
        if r.endpoint.startswith("display.") and r.methods - {"GET", "HEAD", "OPTIONS"}:
            for m in r.methods - {"GET", "HEAD", "OPTIONS"}:
                found.add((r.rule, m.lower()))
    separately_tested = {("/frame/shutdown", "post"), ("/frame/dev-mode", "post")}     # MF-49: tests/test_shutdown.py (token + localhost)
    assert found == {(p, m) for p, m, _ in DISPLAY_WRITE_ROUTES} | separately_tested


@pytest.mark.parametrize("path,method,kwargs", DISPLAY_WRITE_ROUTES)
def test_display_write_routes_forbidden_from_network(client, backlight, path, method, kwargs):
    r = getattr(client, method)(path, environ_base=REMOTE, **kwargs)
    assert r.status_code == 403
    assert (backlight / "brightness").read_text() == "100\n"     # unchanged


@pytest.mark.parametrize("addr", ["127.0.0.1", "::1"])
def test_display_write_routes_allowed_from_localhost(client, backlight, addr):
    r = client.post("/brightness", json={"value": 120}, environ_base={"REMOTE_ADDR": addr})
    assert r.status_code == 200


def test_display_read_routes_open_to_network(client):
    for path in ("/media", "/healthz", "/brightness"):
        assert client.get(path, environ_base=REMOTE).status_code == 200, path
    r = client.get("/", environ_base=REMOTE)          # phones are sent to pairing/admin
    assert r.status_code == 302 and r.headers["Location"].endswith("/admin/")


# Routes that are allowed to answer GET. All must be read-only.
READ_ONLY_GET_ENDPOINTS = {
    "static", "display.index", "display.media", "display.uploads", "display.healthz",
    "display.get_brightness", "display.shutdown_status", "admin.home", "api.status",
    "pairing.state", "pairing.pair_page", "pairing.frame_status", "admin.upload_page", "api.storage",
    "admin.library_page", "admin.settings_page", "api.get_settings", "wifi.page", "wifi.keep_page", "api.list_media", "api.thumb", "display.show_now_state",
    "display.frame_fonts",
}


def test_no_state_changing_get_routes(mf_app):
    """Enumerates app.url_map: every GET route must be a known read-only endpoint,
    and no rule may mix GET with a write method."""
    for r in mf_app.url_map.iter_rules():
        if "GET" in r.methods:
            assert r.endpoint in READ_ONLY_GET_ENDPOINTS, f"unreviewed GET route {r.rule} ({r.endpoint})"
            assert not (r.methods & {"POST", "PUT", "PATCH", "DELETE"}), r.rule


# ---------------- legacy import ----------------

@pytest.fixture
def legacy(mf_app):
    d = mf_app.config["LEGACY_UPLOADS"]
    files = {"a.jpg": b"A" * 1000, "b.PNG": b"B" * 2000, "clip.mp4": b"V" * 500,
             "notes.txt": b"ignore me"}
    for n, data in files.items():
        with open(os.path.join(d, n), "wb") as f:
            f.write(data)
    os.mkdir(os.path.join(d, "subdir.jpg"))
    return d


def test_import_once_and_visible(client, mf_app, legacy):
    msgs = []
    first = migrate_legacy(mf_app.config, log=msgs.append)
    assert first["imported"] == 3 and first["skipped"] == 0
    media = client.get("/media").get_json()
    assert [m["type"] for m in media] == ["image", "image", "video"]   # a.jpg, b.PNG, clip.mp4
    for m in media:
        assert client.get("/uploads/" + m["filename"]).status_code == 200
    second = migrate_legacy(mf_app.config, log=msgs.append)
    assert second == {"imported": 0, "skipped": 0, "backup": None, "already_done": True}
    assert len(client.get("/media").get_json()) == 3


def test_import_never_deletes_or_changes_legacy(mf_app, legacy):
    before = {n: open(os.path.join(legacy, n), "rb").read() for n in os.listdir(legacy)
              if os.path.isfile(os.path.join(legacy, n))}
    migrate_legacy(mf_app.config, log=lambda m: None)
    after = {n: open(os.path.join(legacy, n), "rb").read() for n in os.listdir(legacy)
             if os.path.isfile(os.path.join(legacy, n))}
    assert before == after


def test_import_originals_byte_identical(mf_app, legacy):
    migrate_legacy(mf_app.config, log=lambda m: None)
    conn = mfdb.connect(mf_app.config["DB_PATH"])
    rows = conn.execute("SELECT stored_name, original_name FROM media").fetchall()
    conn.close()
    for r in rows:
        orig = open(os.path.join(legacy, r["original_name"]), "rb").read()
        copy = open(os.path.join(mf_app.config["DATA_DIR"], "originals", r["stored_name"]), "rb").read()
        assert orig == copy


def test_backup_written_and_verified(mf_app, legacy):
    res = migrate_legacy(mf_app.config, log=lambda m: None)
    tar_path = res["backup"]
    assert tar_path and os.path.isfile(tar_path)
    assert tar_path.startswith(os.path.join(mf_app.config["DATA_DIR"], "backups"))
    with tarfile.open(tar_path) as t:
        names = set(t.getnames())
        manifest = json.load(t.extractfile("MANIFEST.sha256.json"))
    assert names == {"uploads/a.jpg", "uploads/b.PNG", "uploads/clip.mp4", "MANIFEST.sha256.json"}
    assert set(manifest) == {"a.jpg", "b.PNG", "clip.mp4"}
    assert os.path.isfile(tar_path + ".sha256.json")
    conn = mfdb.connect(mf_app.config["DB_PATH"])
    assert mfdb.get_setting(conn, "legacy_import_backup") == tar_path
    conn.close()


def test_backup_aborts_cleanly_when_disk_full(mf_app, legacy, monkeypatch):
    monkeypatch.setattr(shutil, "disk_usage", lambda p: shutil._ntuple_diskusage(100, 99, 1))
    with pytest.raises(MigrationError, match="Not enough free space"):
        migrate_legacy(mf_app.config, log=lambda m: None)
    conn = mfdb.connect(mf_app.config["DB_PATH"])
    assert conn.execute("SELECT COUNT(*) FROM media").fetchone()[0] == 0
    assert mfdb.get_setting(conn, "legacy_import_done") is None      # can retry later
    conn.close()
    assert os.listdir(os.path.join(mf_app.config["DATA_DIR"], "backups")) == []


def test_duplicates_skipped(mf_app, legacy):
    with open(os.path.join(legacy, "a-copy.jpg"), "wb") as f:
        f.write(b"A" * 1000)                               # same bytes as a.jpg
    res = migrate_legacy(mf_app.config, log=lambda m: None)
    assert res["imported"] == 3 and res["skipped"] == 1


def test_missing_legacy_folder(mf_app, tmp_path):
    cfg = dict(mf_app.config, LEGACY_UPLOADS=str(tmp_path / "nope"))
    res = migrate_legacy(cfg, log=lambda m: None)
    assert res["imported"] == 0 and res["backup"] is None


def test_cli_migrate_and_status(mf_app, legacy, monkeypatch, capsys):
    from memoryframe.__main__ import main as cli
    monkeypatch.setenv("MEMORYFRAME_DATA_DIR", mf_app.config["DATA_DIR"])
    monkeypatch.setenv("MEMORYFRAME_LEGACY_UPLOADS", legacy)
    assert cli(["migrate"]) == 0
    assert cli(["status"]) == 0
    out = capsys.readouterr().out
    assert "Imported 3" in out and "media:         3" in out


def test_main_runs_migration_when_enabled(monkeypatch, mf_app, legacy):
    import waitress
    monkeypatch.setattr(app_module, "app", mf_app)
    monkeypatch.setattr(waitress, "serve", lambda app, **kw: None)
    monkeypatch.setattr(app_module.platform, "system", lambda: "Linux")
    monkeypatch.setenv("MEMORYFRAME_AUTO_MIGRATE", "1")
    app_module.main()
    conn = mfdb.connect(mf_app.config["DB_PATH"])
    assert conn.execute("SELECT COUNT(*) FROM media").fetchone()[0] == 3
    conn.close()
