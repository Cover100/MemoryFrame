import os
import sys

import pytest
from flask.testing import FlaskClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import tempfile  # noqa: E402

# Never touch /var/lib/memoryframe or ./data from tests: point the module-level
# app (created at import time in app.py) at a throwaway directory.
os.environ["MEMORYFRAME_DATA_DIR"] = tempfile.mkdtemp(prefix="mf-test-data-")
os.environ["MEMORYFRAME_AUTO_MIGRATE"] = "0"

import app as app_module  # noqa: E402
import memoryframe.brightness as brightness_module  # noqa: E402
from memoryframe import create_app, db as mfdb, library  # noqa: E402


@pytest.fixture
def data_dir(tmp_path):
    d = tmp_path / "data"
    return d


class CsrfClient(FlaskClient):
    """Test client that behaves like our pages: echoes the mf_csrf cookie in
    X-CSRF-Token on non-GET requests. Pass csrf=False to send nothing (MF-13 tests)."""

    def open(self, *args, csrf=True, **kwargs):
        method = (kwargs.get("method") or "GET").upper()
        if csrf and method not in ("GET", "HEAD", "OPTIONS"):
            if self.get_cookie("mf_csrf") is None:
                super().open("/healthz", environ_base={"REMOTE_ADDR": "127.0.0.1"})
            headers = dict(kwargs.pop("headers", None) or {})
            headers.setdefault("X-CSRF-Token", self.get_cookie("mf_csrf").value)
            kwargs["headers"] = headers
        return super().open(*args, **kwargs)


@pytest.fixture
def mf_app(tmp_path, data_dir):
    """A fresh app with its own data dir and legacy folder per test."""
    legacy = tmp_path / "uploads"
    legacy.mkdir()
    a = create_app({
        "TESTING": True,
        "DATA_DIR": str(data_dir),
        "DB_PATH": str(data_dir / "memoryframe.db"),
        "LEGACY_UPLOADS": str(legacy),
    })
    a.test_client_class = CsrfClient
    return a


@pytest.fixture
def client(mf_app):
    with mf_app.test_client() as c:
        yield c


class Library:
    """Test helper: add files to the library like an import/upload would."""

    def __init__(self, app, tmp_path):
        self.app = app
        self.src = tmp_path / "src"
        self.src.mkdir(exist_ok=True)

    def add(self, name, data=b"x"):
        p = self.src / name
        p.write_bytes(data)
        conn = mfdb.connect(self.app.config["DB_PATH"])
        try:
            return library.add_file(conn, self.app.config["DATA_DIR"], str(p), name, "upload")
        finally:
            conn.close()


@pytest.fixture
def lib(mf_app, tmp_path):
    return Library(mf_app, tmp_path)


@pytest.fixture
def backlight(tmp_path, monkeypatch):
    """Fake sysfs backlight node (selected via MEMORYFRAME_BACKLIGHT)."""
    node = tmp_path / "backlight"
    node.mkdir()
    (node / "brightness").write_text("100\n")
    (node / "max_brightness").write_text("255\n")
    monkeypatch.setenv("MEMORYFRAME_BACKLIGHT", str(node))
    return node


@pytest.fixture
def no_backlight(tmp_path, monkeypatch):
    """No backlight node at all (Windows / non-Pi Linux)."""
    monkeypatch.delenv("MEMORYFRAME_BACKLIGHT", raising=False)
    monkeypatch.setattr(brightness_module, "BACKLIGHT_ROOT", str(tmp_path / "none"))


def pair_client(c):
    """Pair a test client the way a phone does: the frame starts a round, the phone enters the code."""
    code = c.post("/pairing/start", environ_base={"REMOTE_ADDR": "127.0.0.1"}).get_json()["round"]["code"]
    r = c.post("/pair", data={"code": code}, environ_base={"REMOTE_ADDR": "10.42.0.23"})
    assert r.status_code == 303, r.data
    return c


@pytest.fixture
def paired(client):
    return pair_client(client)


# Network detection is mocked for every test (deterministic: the frame is on its hotspot and
# holds no other addresses). Tests of the detection itself use the originals below.
from memoryframe import frameinfo as _frameinfo  # noqa: E402

REAL_NETWORK = _frameinfo.network
REAL_OWN_IPS = _frameinfo.own_ips


@pytest.fixture(autouse=True)
def _mock_network(monkeypatch):
    # Hermetic: no cached `hostname -I` result from the runner, no leaked overrides.
    monkeypatch.setitem(_frameinfo._cache, "at", None)
    monkeypatch.setitem(_frameinfo._cache, "ips", [])
    for var in ("MEMORYFRAME_SITE_URL", "MEMORYFRAME_FORCE_NETMODE", "MEMORYFRAME_PORTS", "MEMORYFRAME_PORT"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(_frameinfo, "network",
                        lambda: {"mode": "hotspot", "lan_ip": "10.42.0.1", "ips": ["10.42.0.1"]})
    monkeypatch.setattr(_frameinfo, "own_ips", lambda max_age=0: [])
