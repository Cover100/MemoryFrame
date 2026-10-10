import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import app as app_module  # noqa: E402


@pytest.fixture
def uploads(tmp_path, monkeypatch):
    folder = tmp_path / "uploads"
    folder.mkdir()
    monkeypatch.setattr(app_module, "UPLOAD_FOLDER", str(folder))
    return folder


@pytest.fixture
def client(uploads):
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


@pytest.fixture
def backlight(tmp_path, monkeypatch):
    """Fake sysfs backlight node on a Linux host."""
    node = tmp_path / "backlight"
    node.mkdir()
    (node / "brightness").write_text("100\n")
    (node / "max_brightness").write_text("255\n")
    monkeypatch.setattr(app_module, "BACKLIGHT_PATH", str(node / "brightness"))
    monkeypatch.setattr(app_module, "MAX_BRIGHTNESS_PATH", str(node / "max_brightness"))
    monkeypatch.setattr(app_module.platform, "system", lambda: "Linux")
    return node
