"""MF-19: onboarding data (Wi-Fi QR, empty library) and the long-press menu status."""
import pytest

from memoryframe import db as mfdb
from memoryframe import frameinfo

LOCAL = {"REMOTE_ADDR": "127.0.0.1"}
PHONE = {"REMOTE_ADDR": "10.42.0.23"}


@pytest.fixture
def wifi(monkeypatch):
    monkeypatch.setenv("MEMORYFRAME_SSID", "Frame;WiFi")
    monkeypatch.setenv("MEMORYFRAME_WIFI_PASSWORD", 'p:a,s"s\\word')
    monkeypatch.delenv("MEMORYFRAME_SHOW_WIFI_QR", raising=False)


def test_wifi_payload_escaped():
    assert frameinfo.wifi_qr_payload("Frame;WiFi", 'p:a,s"s\\word') == \
        'WIFI:T:WPA;S:Frame\\;WiFi;P:p\\:a\\,s\\"s\\\\word;;'


def test_wifi_qr_shown_until_paired(client, wifi):
    st = client.get("/pairing/state", environ_base=LOCAL).get_json()
    assert "<svg" in st["wifi_qr_svg"] and st["ssid"] == "Frame;WiFi" and st["media"] == 0
    from conftest import pair_client
    pair_client(client)
    assert client.get("/pairing/state", environ_base=LOCAL).get_json()["wifi_qr_svg"] is None


def test_wifi_qr_can_be_disabled_by_env(client, wifi, monkeypatch):
    monkeypatch.setenv("MEMORYFRAME_SHOW_WIFI_QR", "0")
    assert client.get("/pairing/state", environ_base=LOCAL).get_json()["wifi_qr_svg"] is None


def test_wifi_qr_can_be_disabled_by_setting(client, wifi, mf_app):
    conn = mfdb.connect(mf_app.config["DB_PATH"])
    mfdb.set_setting(conn, "show_wifi_qr", "0")
    conn.commit()
    conn.close()
    assert client.get("/pairing/state", environ_base=LOCAL).get_json()["wifi_qr_svg"] is None


def test_no_wifi_qr_without_password(client, monkeypatch):
    monkeypatch.setenv("MEMORYFRAME_SSID", "x")
    monkeypatch.delenv("MEMORYFRAME_WIFI_PASSWORD", raising=False)
    monkeypatch.setattr(frameinfo, "hotspot_conf", lambda: ("x", None))
    assert client.get("/pairing/state", environ_base=LOCAL).get_json()["wifi_qr_svg"] is None


def test_state_counts_visible_media(client, lib):
    lib.add("a.jpg")
    assert client.get("/pairing/state", environ_base=LOCAL).get_json()["media"] == 1


def test_hotspot_conf_file(monkeypatch, tmp_path):
    (tmp_path / "deploy").mkdir()
    (tmp_path / "deploy" / "hotspot.conf").write_text('# c\nSSID="Home Frame"\nPASSWORD=\'secret-pass-123\'\n')
    monkeypatch.setattr(frameinfo.storage, "REPO_DIR", str(tmp_path))
    monkeypatch.delenv("MEMORYFRAME_SSID", raising=False)
    monkeypatch.delenv("MEMORYFRAME_WIFI_PASSWORD", raising=False)
    assert frameinfo.hotspot_conf() == ("Home Frame", "secret-pass-123")


def test_frame_status(client, wifi):
    s = client.get("/frame/status", environ_base=LOCAL).get_json()
    assert s["ssid"] == "Frame;WiFi" and s["paired"] == 0
    assert s["storage"]["free"] > 0 and s["version"].startswith(frameinfo.VERSION)
    assert isinstance(s["ips"], list)
    assert "password" not in str(s).lower()


def test_frame_status_and_wifi_password_localhost_only(client, wifi):
    assert client.get("/frame/status", environ_base=PHONE).status_code == 403
    assert client.get("/pairing/state", environ_base=PHONE).status_code == 403


def test_ip_addresses_fallback(monkeypatch):
    def boom(*a, **k):
        raise OSError("no hostname binary")
    monkeypatch.setattr(frameinfo.subprocess, "run", boom)
    assert isinstance(frameinfo.ip_addresses(), list)
    assert frameinfo.version() == frameinfo.VERSION
