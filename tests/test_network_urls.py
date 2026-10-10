"""Pairing URL/QR follow the frame's current network; the Host check accepts the frame's own IPs."""
import socket

import pytest

from conftest import REAL_NETWORK, REAL_OWN_IPS
from memoryframe import frameinfo

LOCAL = {"REMOTE_ADDR": "127.0.0.1"}


@pytest.fixture
def ips(monkeypatch):
    state = {"ips": [], "route": None}
    monkeypatch.setattr(frameinfo, "ip_addresses", lambda: list(state["ips"]))
    monkeypatch.setattr(frameinfo, "default_route_ip", lambda: state["route"])
    monkeypatch.setattr(frameinfo, "network", REAL_NETWORK)
    return state


def test_detect_hotspot(ips):
    ips["ips"] = ["10.42.0.1"]
    ips["route"] = None
    assert frameinfo.network()["mode"] == "hotspot"


def test_detect_home_wifi_by_default_route(ips):
    ips["ips"] = ["192.168.50.124", "172.17.0.1"]
    ips["route"] = "192.168.50.124"
    assert frameinfo.network() == {"mode": "lan", "lan_ip": "192.168.50.124", "ips": ips["ips"]}


def test_detect_fallback_to_first_address(ips):
    ips["ips"] = ["192.168.1.7"]
    assert frameinfo.network()["lan_ip"] == "192.168.1.7"


def test_detect_offline(ips):
    assert frameinfo.network()["mode"] == "unknown"
    assert frameinfo.site_urls() == {"mode": "unknown", "primary": "http://memoryframe.local:5000", "fallback": None}


def test_default_route_ip_never_raises(monkeypatch):
    class Boom:
        def __init__(self, *a): pass
        def connect(self, *a): raise OSError("no route")
        def close(self): pass
    monkeypatch.setattr(frameinfo.socket, "socket", Boom)
    assert frameinfo.default_route_ip() is None


def test_default_route_ip_real():
    ip = frameinfo.default_route_ip()
    assert ip is None or socket.inet_aton(ip)


@pytest.mark.parametrize("ports,sfx", [("80,5000", ""), ("5000", ":5000"), ("8080", ":8080"), (None, ":5000")])
def test_port_only_dropped_when_80_is_served(monkeypatch, ports, sfx):
    monkeypatch.delenv("MEMORYFRAME_PORT", raising=False)
    if ports is None:
        monkeypatch.delenv("MEMORYFRAME_PORTS", raising=False)
    else:
        monkeypatch.setenv("MEMORYFRAME_PORTS", ports)
    assert frameinfo.port_suffix() == sfx


def test_single_port_env(monkeypatch):
    monkeypatch.delenv("MEMORYFRAME_PORTS", raising=False)
    monkeypatch.setenv("MEMORYFRAME_PORT", "80")
    assert frameinfo.port_suffix() == ""


def test_state_urls_on_hotspot(client, monkeypatch):
    monkeypatch.setenv("MEMORYFRAME_PORTS", "80,5000")
    client.post("/pairing/start", environ_base=LOCAL)
    st = client.get("/pairing/state", environ_base=LOCAL).get_json()
    assert st["network"] == "hotspot"
    assert st["url"] == "http://10.42.0.1/pair" and st["url_fallback"] == "http://memoryframe.lan/pair"
    token = client.application.extensions["mf_pairing"].token
    assert st["round"]["qr_svg"] == __import__("memoryframe.pairing", fromlist=["x"]).qr_svg(
        f"http://10.42.0.1/pair?t={token}")


def test_state_urls_on_home_wifi(client, monkeypatch):
    monkeypatch.setenv("MEMORYFRAME_PORTS", "5000")
    monkeypatch.setenv("MEMORYFRAME_SSID", "Frame")
    monkeypatch.setenv("MEMORYFRAME_WIFI_PASSWORD", "secret-pass-123")
    monkeypatch.setattr(frameinfo, "network",
                        lambda: {"mode": "lan", "lan_ip": "192.168.50.124", "ips": ["192.168.50.124"]})
    client.post("/pairing/start", environ_base=LOCAL)
    st = client.get("/pairing/state", environ_base=LOCAL).get_json()
    assert st["network"] == "lan"
    assert st["url"] == "http://memoryframe.local:5000/pair"
    assert st["url_fallback"] == "http://192.168.50.124:5000/pair"
    assert st["site_url"] == "http://memoryframe.local:5000/"
    assert st["wifi_qr_svg"] is None            # the hotspot is not up: no Wi-Fi QR
    token = client.application.extensions["mf_pairing"].token
    from memoryframe.pairing import qr_svg
    assert st["round"]["qr_svg"] == qr_svg(f"http://memoryframe.local:5000/pair?t={token}")
    fs = client.get("/frame/status", environ_base=LOCAL).get_json()
    assert fs["network"] == "lan" and fs["site"] == "http://memoryframe.local:5000/"
    assert fs["site_fallback"] == "http://192.168.50.124:5000/"


def test_pair_base_url_override(mf_app, client):
    mf_app.config["PAIR_BASE_URL"] = "http://frame.example"
    st = client.get("/pairing/state", environ_base=LOCAL).get_json()
    assert st["url"] == "http://frame.example/pair" and st["url_fallback"] is None


# ---------- Host allow-list: the frame's own current IPs ----------

def test_own_ip_allowed(client, monkeypatch):
    monkeypatch.setattr(frameinfo, "own_ips", lambda max_age=0: ["192.168.50.124"])
    assert client.get("/healthz", headers={"Host": "192.168.50.124:5000"}).status_code == 200
    assert client.get("/healthz", headers={"Host": "192.168.50.124"}).status_code == 200


@pytest.mark.parametrize("host", ["192.168.50.125", "192.168.50.124.nip.io", "evil.example",
                                  "3232248444", "0xc0a8327c"])
def test_other_ips_and_names_still_rejected(client, monkeypatch, host):
    monkeypatch.setattr(frameinfo, "own_ips", lambda max_age=0: ["192.168.50.124"])
    assert client.get("/healthz", headers={"Host": host}).status_code == 400


def test_own_ips_cached(monkeypatch):
    calls = []
    monkeypatch.setattr(frameinfo, "ip_addresses", lambda: calls.append(1) or ["192.168.50.124"])
    monkeypatch.setitem(frameinfo._cache, "at", 0.0)
    assert REAL_OWN_IPS() == ["192.168.50.124"]
    assert REAL_OWN_IPS() == ["192.168.50.124"]
    assert len(calls) == 1


def test_site_url_env_override(client, monkeypatch):
    monkeypatch.setenv("MEMORYFRAME_SITE_URL", "http://frame.home/")
    monkeypatch.setenv("MEMORYFRAME_SSID", "Frame")
    monkeypatch.setenv("MEMORYFRAME_WIFI_PASSWORD", "secret-pass-123")
    client.post("/pairing/start", environ_base=LOCAL)
    st = client.get("/pairing/state", environ_base=LOCAL).get_json()
    assert st["url"] == "http://frame.home/pair" and st["url_fallback"] is None
    assert st["site_url"] == "http://frame.home/"
    assert st["network"] == "hotspot" and st["wifi_qr_svg"]        # still on the hotspot -> Wi-Fi QR
    monkeypatch.setattr(frameinfo, "network",
                        lambda: {"mode": "lan", "lan_ip": "192.168.50.124", "ips": ["192.168.50.124"]})
    st = client.get("/pairing/state", environ_base=LOCAL).get_json()
    assert st["url"] == "http://frame.home/pair" and st["wifi_qr_svg"] is None
