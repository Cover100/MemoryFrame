import app as app_module


# ---------------- media scanning ----------------

def test_media_filters_types_and_sorts(client, uploads):
    for name in ["b.JPG", "a.png", "c.mp4", "d.MOV", "notes.txt", "x.exe", "e.webm"]:
        (uploads / name).write_bytes(b"x")
    (uploads / "sub.jpg").mkdir()  # directory with a media-like name is ignored
    res = client.get("/media")
    assert res.status_code == 200
    assert res.get_json() == [
        {"type": "image", "filename": "a.png"},
        {"type": "image", "filename": "b.JPG"},
        {"type": "video", "filename": "c.mp4"},
        {"type": "video", "filename": "d.MOV"},
        {"type": "video", "filename": "e.webm"},
    ]


def test_media_empty_folder(client):
    assert client.get("/media").get_json() == []


def test_get_media_creates_missing_folder(tmp_path, monkeypatch):
    missing = tmp_path / "nope"
    monkeypatch.setattr(app_module, "UPLOAD_FOLDER", str(missing))
    assert app_module.get_media() == []
    assert missing.is_dir()


def test_index_renders(client, uploads):
    (uploads / "a.jpg").write_bytes(b"x")
    res = client.get("/")
    assert res.status_code == 200
    assert b"<html" in res.data.lower()


# ---------------- uploads serving ----------------

def test_uploads_serves_file(client, uploads):
    (uploads / "a.jpg").write_bytes(b"JPEGDATA")
    res = client.get("/uploads/a.jpg")
    assert res.status_code == 200
    assert res.data == b"JPEGDATA"


def test_uploads_missing_404(client):
    assert client.get("/uploads/none.jpg").status_code == 404


def test_uploads_path_traversal_rejected(client, uploads, tmp_path):
    (tmp_path / "secret.txt").write_text("secret")
    for path in ["/uploads/../secret.txt", "/uploads/..%2fsecret.txt",
                 "/uploads/%2e%2e/secret.txt", "/uploads/" + str(tmp_path / "secret.txt")]:
        res = client.get(path, follow_redirects=True)
        assert res.status_code in (400, 404), path
        assert b"secret" not in res.data


# ---------------- brightness: GET ----------------

def test_get_brightness_reads_sysfs(client, backlight):
    res = client.get("/brightness")
    assert res.status_code == 200
    assert res.get_json() == {"brightness": 100, "maximum": 255}


def test_get_brightness_windows_unsupported(client, monkeypatch):
    monkeypatch.setattr(app_module.platform, "system", lambda: "Windows")
    res = client.get("/brightness")
    assert res.status_code == 200
    assert res.get_json()["status"] == "unsupported"


def test_get_brightness_missing_sysfs(client, tmp_path, monkeypatch):
    # Non-Pi Linux: node absent. Current behaviour is 500; MF-03 AC expects "unsupported".
    monkeypatch.setattr(app_module.platform, "system", lambda: "Linux")
    monkeypatch.setattr(app_module, "BACKLIGHT_PATH", str(tmp_path / "none"))
    res = client.get("/brightness")
    assert res.status_code == 500
    assert res.get_json()["status"] == "error"


# ---------------- brightness: set ----------------
# NOTE: tests the current GET /brightness/<int> route. MF-03 replaces it with a
# validated POST (floor 40); update these when that lands.

def test_set_brightness_writes_value(client, backlight):
    res = client.get("/brightness/150")
    assert res.status_code == 200
    assert res.get_json() == {"status": "success", "brightness": 150, "maximum": 255}
    assert (backlight / "brightness").read_text() == "150"


def test_set_brightness_clamped_to_max(client, backlight):
    res = client.get("/brightness/9999")
    assert res.get_json()["brightness"] == 255
    assert (backlight / "brightness").read_text() == "255"


def test_set_brightness_non_int_404(client, backlight):
    assert client.get("/brightness/NaN").status_code == 404


def test_set_brightness_windows_unsupported(client, monkeypatch):
    monkeypatch.setattr(app_module.platform, "system", lambda: "Windows")
    assert client.get("/brightness/100").get_json()["status"] == "unsupported"


def test_set_brightness_missing_sysfs(client, tmp_path, monkeypatch):
    monkeypatch.setattr(app_module.platform, "system", lambda: "Linux")
    monkeypatch.setattr(app_module, "MAX_BRIGHTNESS_PATH", str(tmp_path / "none"))
    res = client.get("/brightness/100")
    assert res.status_code == 500
    assert res.get_json()["status"] == "error"


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


def test_main_serves_with_waitress_without_browser_on_linux(monkeypatch, uploads):
    served = _run_main(monkeypatch, "Linux")
    assert served["host"] == "0.0.0.0" and served["port"] == 5000
    assert _Thread.started == []


def test_main_opens_browser_on_windows(monkeypatch, uploads):
    _run_main(monkeypatch, "Windows")
    assert _Thread.started == [app_module.launch_browser]


def test_main_env_overrides(monkeypatch, uploads):
    served = _run_main(monkeypatch, "Linux",
                       {"MEMORYFRAME_PORT": "8080", "MEMORYFRAME_HOST": "127.0.0.1",
                        "MEMORYFRAME_OPEN_BROWSER": "1"})
    assert served["port"] == 8080 and served["host"] == "127.0.0.1"
    assert _Thread.started == [app_module.launch_browser]
