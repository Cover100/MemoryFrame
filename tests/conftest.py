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
def no_backlight(tmp_path, monkeypatch):
    """No MEMORYFRAME_BACKLIGHT and an empty/absent BACKLIGHT_ROOT."""
    monkeypatch.delenv("MEMORYFRAME_BACKLIGHT", raising=False)
    monkeypatch.setattr(app_module, "BACKLIGHT_ROOT", str(tmp_path / "no-sysfs"))


@pytest.fixture
def make_backlight(tmp_path, monkeypatch):
    """Create a fake sysfs backlight dir and point MEMORYFRAME_BACKLIGHT at it."""
    def _make(current="100\n", maximum="255\n", name="10-0045"):
        node = tmp_path / name
        node.mkdir()
        (node / "brightness").write_text(current)
        if maximum is not None:
            (node / "max_brightness").write_text(maximum)
        monkeypatch.setenv("MEMORYFRAME_BACKLIGHT", str(node))
        return node
    return _make


@pytest.fixture
def backlight(make_backlight):
    return make_backlight()
