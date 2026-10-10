import app as app_module
from memoryframe import db as mfdb


# ---------------- media listing (library-backed since MF-11) ----------------
# Same behaviour as before MF-11: only image/video types, case-insensitive
# extensions, sorted by name, JSON shape {"type", "filename"}. "filename" is now
# the server-generated stored name that /uploads/<filename> serves.

def test_media_filters_types_and_sorts(client, lib):
    stored = {}
    for i, name in enumerate(["b.JPG", "a.png", "c.mp4", "d.MOV", "notes.txt", "x.exe", "e.webm"]):
        stored[name] = lib.add(name, f"data{i}".encode())
    assert stored["notes.txt"] is None and stored["x.exe"] is None   # unsupported types rejected
    res = client.get("/media")
    assert res.status_code == 200
    assert res.get_json() == [
        {"type": "image", "filename": stored["a.png"]},
        {"type": "image", "filename": stored["b.JPG"]},
        {"type": "video", "filename": stored["c.mp4"]},
        {"type": "video", "filename": stored["d.MOV"]},
        {"type": "video", "filename": stored["e.webm"]},
    ]
    assert stored["b.JPG"].endswith(".jpg")      # server-generated, lower-case extension


def test_media_empty_library(client):
    assert client.get("/media").get_json() == []


def test_data_layout_created(mf_app, data_dir):
    for sub in ("originals", "display", "thumbs", "tmp", "backups"):
        assert (data_dir / sub).is_dir()
    assert (data_dir / "memoryframe.db").is_file()


def test_index_renders(client, lib):
    stored = lib.add("a.jpg")
    res = client.get("/")
    assert res.status_code == 200
    assert b"<html" in res.data.lower()
    assert stored.encode() in res.data       # playlist embedded for script.js


# ---------------- uploads serving ----------------

def test_uploads_serves_file(client, lib):
    stored = lib.add("a.jpg", b"JPEGDATA")
    res = client.get("/uploads/" + stored)
    assert res.status_code == 200
    assert res.data == b"JPEGDATA"


def test_uploads_missing_404(client):
    assert client.get("/uploads/none.jpg").status_code == 404


def test_uploads_unknown_file_in_originals_404(client, mf_app, data_dir):
    # A file that is in originals/ but not in the library is never served.
    (data_dir / "originals" / "stray.jpg").write_bytes(b"x")
    assert client.get("/uploads/stray.jpg").status_code == 404


def test_uploads_hidden_item_404(client, lib, mf_app):
    stored = lib.add("a.jpg")
    conn = mfdb.connect(mf_app.config["DB_PATH"])
    conn.execute("UPDATE media SET hidden = 1 WHERE stored_name = ?", (stored,))
    conn.commit()
    conn.close()
    assert client.get("/uploads/" + stored).status_code == 404
    assert client.get("/media").get_json() == []


def test_uploads_path_traversal_rejected(client, tmp_path):
    (tmp_path / "secret.txt").write_text("secret")
    for path in ["/uploads/../secret.txt", "/uploads/..%2fsecret.txt",
                 "/uploads/%2e%2e/secret.txt", "/uploads/" + str(tmp_path / "secret.txt"),
                 "/uploads/../data/memoryframe.db"]:
        res = client.get(path, follow_redirects=True)
        assert res.status_code in (400, 404), path
        assert b"secret" not in res.data


# Brightness API tests (MF-03 POST contract) live in tests/test_brightness.py.


# ---------------- launch_browser / main / healthz (MF-04) ----------------
# Chromium kiosk is now started by deploy/kiosk.sh after /healthz answers;
# app.py only opens a browser for development.

def test_launch_browser_opens_default_browser(monkeypatch):
    opened = []
    monkeypatch.setattr(app_module.time, "sleep", lambda s: None)
    monkeypatch.setattr(app_module.webbrowser, "open", lambda u: opened.append(u))
    app_module.launch_browser()
    assert opened == ["http://localhost:5000"]


def test_healthz(client):
    res = client.get("/healthz")
    assert res.status_code == 200
    assert res.get_json() == {"status": "ok"}


class _Thread:
    started = []

    def __init__(self, target, daemon):
        self.target = target

    def start(self):
        _Thread.started.append(self.target)


def _run_main(monkeypatch, system, env=None):
    import waitress
    served = {}
    _Thread.started = []
    monkeypatch.setattr(app_module.platform, "system", lambda: system)
    monkeypatch.setattr(app_module.threading, "Thread", _Thread)
    monkeypatch.setattr(waitress, "serve", lambda app, **kw: served.update(kw))
    monkeypatch.delenv("MEMORYFRAME_OPEN_BROWSER", raising=False)
    for k, v in (env or {}).items():
        monkeypatch.setenv(k, v)
    app_module.main()
    return served


def test_main_serves_with_waitress_without_browser_on_linux(monkeypatch):
    served = _run_main(monkeypatch, "Linux")
    assert served["host"] == "0.0.0.0" and served["port"] == 5000
    assert _Thread.started == []


def test_main_opens_browser_on_windows(monkeypatch):
    _run_main(monkeypatch, "Windows")
    assert _Thread.started == [app_module.launch_browser]


def test_main_env_overrides(monkeypatch):
    served = _run_main(monkeypatch, "Linux",
                       {"MEMORYFRAME_PORT": "8080", "MEMORYFRAME_HOST": "127.0.0.1",
                        "MEMORYFRAME_OPEN_BROWSER": "1"})
    assert served["port"] == 8080 and served["host"] == "127.0.0.1"
    assert _Thread.started == [app_module.launch_browser]
