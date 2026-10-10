"""MF-05 tests. Written against the MF-03 brightness contract and MF-04 launcher."""
import math
import sys
import types

import pytest

import app as app_module


# ---------------- media scanning ----------------

def test_media_filters_types_and_sorts(client, uploads):
    for name in ["b.JPG", "a.png", "c.mp4", "d.MOV", "notes.txt", "x.exe", "e.webm", "f.jpeg", "g.gif", "h.webp"]:
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
        {"type": "image", "filename": "f.jpeg"},
        {"type": "image", "filename": "g.gif"},
        {"type": "image", "filename": "h.webp"},
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


# ---------------- GET /brightness ----------------

def test_get_brightness_success_body(client, backlight):
    res = client.get("/brightness")
    assert res.status_code == 200
    assert res.get_json() == {"status": "success", "supported": True,
                              "brightness": 100, "minimum": 40, "maximum": 255}


def test_get_brightness_minimum_is_min_of_40_and_max(client, make_backlight):
    make_backlight(current="10", maximum="31")
    body = client.get("/brightness").get_json()
    assert body["minimum"] == 31 and body["maximum"] == 31


def _assert_unsupported(body):
    assert body["status"] == "unsupported"
    assert body["supported"] is False
    assert isinstance(body["message"], str) and body["message"]


def test_get_brightness_no_backlight(client, no_backlight):
    res = client.get("/brightness")
    assert res.status_code == 200
    _assert_unsupported(res.get_json())


def test_get_brightness_env_points_to_missing_dir(client, tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORYFRAME_BACKLIGHT", str(tmp_path / "missing"))
    res = client.get("/brightness")
    assert res.status_code == 200
    _assert_unsupported(res.get_json())


def test_get_brightness_dir_without_max_file_is_unsupported(client, make_backlight, monkeypatch, tmp_path):
    make_backlight(maximum=None)
    monkeypatch.setattr(app_module, "BACKLIGHT_ROOT", str(tmp_path / "no-sysfs"))
    res = client.get("/brightness")
    assert res.status_code == 200
    _assert_unsupported(res.get_json())


def test_get_brightness_read_failure_is_unsupported(client, make_backlight):
    make_backlight(current="garbage")
    res = client.get("/brightness")
    assert res.status_code == 200
    _assert_unsupported(res.get_json())


def test_backlight_root_fallback(client, tmp_path, monkeypatch):
    monkeypatch.delenv("MEMORYFRAME_BACKLIGHT", raising=False)
    root = tmp_path / "sysfs"
    node = root / "some_panel"
    node.mkdir(parents=True)
    (node / "brightness").write_text("80")
    (node / "max_brightness").write_text("200")
    monkeypatch.setattr(app_module, "BACKLIGHT_ROOT", str(root))
    body = client.get("/brightness").get_json()
    assert body["supported"] is True and body["brightness"] == 80 and body["maximum"] == 200


def test_old_path_constants_removed():
    assert not hasattr(app_module, "BACKLIGHT_PATH")
    assert not hasattr(app_module, "MAX_BRIGHTNESS_PATH")


# ---------------- POST /brightness ----------------

@pytest.mark.parametrize("value,expected", [
    (150, 150), (0, 40), (-5, 40), (39.4, 40), (255, 255), (9999, 255),
    (120.4, 120), (120.6, 121), (1e300, 255),
])
def test_post_brightness_clamps_and_rounds(client, backlight, value, expected):
    res = client.post("/brightness", json={"value": value})
    assert res.status_code == 200
    assert res.get_json() == {"status": "success", "supported": True,
                              "brightness": expected, "minimum": 40, "maximum": 255}
    assert (backlight / "brightness").read_text().strip() == str(expected)


def test_post_brightness_small_max(client, make_backlight):
    node = make_backlight(maximum="31")
    body = client.post("/brightness", json={"value": 5}).get_json()
    assert body["brightness"] == 31 and body["minimum"] == 31
    assert (node / "brightness").read_text().strip() == "31"


@pytest.mark.parametrize("raw", [
    '{"value": true}', '{"value": false}', '{"value": NaN}', '{"value": Infinity}',
    '{"value": -Infinity}', '{"value": "100"}', '{"value": null}', '{}', '[100]',
    '100', '{"val": 100}', 'not json',
])
def test_post_brightness_invalid_body_400(client, backlight, raw):
    res = client.post("/brightness", data=raw, content_type="application/json")
    assert res.status_code == 400
    assert res.get_json()["status"] == "error"
    assert (backlight / "brightness").read_text() == "100\n"


def test_post_brightness_form_encoded_400(client, backlight):
    res = client.post("/brightness", data={"value": "100"})
    assert res.status_code == 400
    assert res.get_json()["status"] == "error"


def test_post_brightness_no_backlight_409(client, no_backlight):
    res = client.post("/brightness", json={"value": 100})
    assert res.status_code == 409
    _assert_unsupported(res.get_json())


def test_post_brightness_invalid_takes_precedence_over_unsupported(client, no_backlight):
    assert client.post("/brightness", json={"value": True}).status_code == 400


def test_post_brightness_write_failure_503(client, backlight, monkeypatch):
    real_open = open

    def failing_open(path, mode="r", *a, **k):
        if "w" in mode and str(path).endswith("brightness"):
            raise PermissionError("read-only")
        return real_open(path, mode, *a, **k)

    monkeypatch.setattr("builtins.open", failing_open)
    res = client.post("/brightness", json={"value": 100})
    assert res.status_code == 503
    assert res.get_json()["status"] == "error"


def test_post_brightness_bad_max_503(client, make_backlight):
    make_backlight(maximum="garbage")
    assert client.post("/brightness", json={"value": 100}).status_code == 503


def test_old_get_set_route_404(client, backlight):
    assert client.get("/brightness/200").status_code == 404
    assert (backlight / "brightness").read_text() == "100\n"


# ---------------- MF-04: health + launcher ----------------

def test_healthz(client):
    res = client.get("/healthz")
    assert res.status_code == 200
    assert res.get_json() == {"status": "ok"}


def test_launch_browser_opens_localhost(monkeypatch):
    opened = []
    monkeypatch.setattr(app_module.time, "sleep", lambda s: None)
    monkeypatch.setattr(app_module.webbrowser, "open", opened.append)
    app_module.launch_browser()
    assert opened == ["http://localhost:5000"]


def test_app_never_spawns_chromium():
    assert not hasattr(app_module, "subprocess")


@pytest.fixture
def run_main(monkeypatch, tmp_path):
    """Run app.main() with waitress and threads stubbed out."""
    served, threads = [], []
    fake_waitress = types.ModuleType("waitress")
    fake_waitress.serve = lambda app, **kw: served.append(kw)
    monkeypatch.setitem(sys.modules, "waitress", fake_waitress)

    class FakeThread:
        def __init__(self, target, daemon=None):
            threads.append(target)

        def start(self):
            pass

    monkeypatch.setattr(app_module.threading, "Thread", FakeThread)
    monkeypatch.setattr(app_module, "UPLOAD_FOLDER", str(tmp_path / "up"))
    for var in ("MEMORYFRAME_OPEN_BROWSER", "MEMORYFRAME_HOST", "MEMORYFRAME_PORT"):
        monkeypatch.delenv(var, raising=False)

    def _run(system="Linux", **env):
        monkeypatch.setattr(app_module.platform, "system", lambda: system)
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        app_module.main()
        return served, threads
    return _run


def test_main_linux_does_not_open_browser(run_main, tmp_path):
    served, threads = run_main("Linux")
    assert threads == []
    assert served == [{"host": "0.0.0.0", "port": 5000, "threads": 4}]
    assert (tmp_path / "up").is_dir()


def test_main_windows_opens_browser(run_main):
    _, threads = run_main("Windows")
    assert threads == [app_module.launch_browser]


def test_main_env_opt_in_and_host_port(run_main):
    served, threads = run_main("Linux", MEMORYFRAME_OPEN_BROWSER="1",
                               MEMORYFRAME_HOST="127.0.0.1", MEMORYFRAME_PORT="8080")
    assert threads == [app_module.launch_browser]
    assert served[0]["host"] == "127.0.0.1" and served[0]["port"] == 8080


def test_main_env_other_value_does_not_open(run_main):
    _, threads = run_main("Linux", MEMORYFRAME_OPEN_BROWSER="yes")
    assert threads == []
