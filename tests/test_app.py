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


# Brightness API tests (MF-03 POST contract) live in tests/test_brightness.py.


# ---------------- launch_browser ----------------

class _Result:
    def __init__(self, rc):
        self.returncode = rc


def _patch_launch(monkeypatch, system, pgrep_rc=1, popen=None):
    calls = {"open": [], "popen": []}
    monkeypatch.setattr(app_module.time, "sleep", lambda s: None)
    monkeypatch.setattr(app_module.platform, "system", lambda: system)
    monkeypatch.setattr(app_module.webbrowser, "open", lambda u: calls["open"].append(u))
    monkeypatch.setattr(app_module.subprocess, "run", lambda *a, **k: _Result(pgrep_rc))
    monkeypatch.setattr(app_module.subprocess, "Popen",
                        popen or (lambda args: calls["popen"].append(args)))
    return calls


def test_launch_browser_windows(monkeypatch):
    calls = _patch_launch(monkeypatch, "Windows")
    app_module.launch_browser()
    assert calls["open"] == ["http://localhost:5000"]
    assert calls["popen"] == []


def test_launch_browser_linux_starts_kiosk(monkeypatch):
    calls = _patch_launch(monkeypatch, "Linux", pgrep_rc=1)
    app_module.launch_browser()
    assert calls["popen"][0][0] == "chromium"
    assert "--kiosk" in calls["popen"][0]


def test_launch_browser_linux_already_running(monkeypatch):
    calls = _patch_launch(monkeypatch, "Linux", pgrep_rc=0)
    app_module.launch_browser()
    assert calls["popen"] == []


def test_launch_browser_chromium_missing(monkeypatch, capsys):
    def boom(args):
        raise FileNotFoundError
    _patch_launch(monkeypatch, "Linux", pgrep_rc=1, popen=boom)
    app_module.launch_browser()
    assert "Chromium not found" in capsys.readouterr().out
