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
    monkeypatch.setattr(frameinfo, "own_ips", lambda max_age=0: REAL_OWN_IPS(0))   # no cache here
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


# ---------- #26 review: MEMORYFRAME_SITE_URL validation, detection cache ----------

@pytest.mark.parametrize("bad", [
    "javascript:alert(1)", "data:text/html,<script>x</script>", "ftp://frame.home",
    "JAVASCRIPT://frame.home", "http://", "http:///pair", "frame.home", "//frame.home",
    "http://frame home", "http://frame.home\nX: y", "http://fr\x07ame.home", "http://user:pw@frame.home",
    "http://frame.home/other/path", "http://frame.home/?q=1", "http://frame.home:99999", "http://fráme.home",
])
def test_bad_site_url_never_reaches_qr(client, monkeypatch, caplog, bad):
    payloads = []
    import segno
    real_make = segno.make
    monkeypatch.setattr(segno, "make", lambda data, **kw: payloads.append(data) or real_make(data, **kw))
    monkeypatch.setattr(frameinfo, "_logged", set())
    monkeypatch.setenv("MEMORYFRAME_SITE_URL", bad)
    client.post("/pairing/start", environ_base=LOCAL)
    st = client.get("/pairing/state", environ_base=LOCAL).get_json()
    assert st["url"] == "http://10.42.0.1:5000/pair"                    # detected address instead
    assert payloads and all(p.startswith(("http://10.42.0.1", "WIFI:")) for p in payloads), payloads
    assert "MEMORYFRAME_SITE_URL" in caplog.text


def test_control_chars_rejected():
    assert frameinfo.validate_site_url("http://fr\x00ame.home") is None
    assert frameinfo.validate_site_url("http://frame.home\t") == "http://frame.home"   # outer whitespace trimmed
    assert frameinfo.validate_site_url("http://fra\tme.home") is None


@pytest.mark.parametrize("good,expected", [
    ("http://frame.home", "http://frame.home"), ("https://frame.home/", "https://frame.home"),
    ("HTTP://memoryframe.local:8080", "http://memoryframe.local:8080"), ("  http://10.0.0.5  ", "http://10.0.0.5"),
])
def test_valid_site_urls(good, expected):
    assert frameinfo.validate_site_url(good) == expected


def test_site_url_host_not_allowlisted_warns(client, monkeypatch, caplog):
    monkeypatch.setattr(frameinfo, "_logged", set())
    monkeypatch.setenv("MEMORYFRAME_SITE_URL", "http://frame.home")
    client.post("/pairing/start", environ_base=LOCAL)
    st = client.get("/pairing/state", environ_base=LOCAL).get_json()
    assert st["url"] == "http://frame.home/pair"
    assert "not in the Host allowlist" in caplog.text
    caplog.clear()
    monkeypatch.setenv("MEMORYFRAME_SITE_URL", "http://memoryframe.local")
    client.get("/pairing/state", environ_base=LOCAL)
    assert "allowlist" not in caplog.text


def test_network_detection_uses_cache_on_2s_polls(client, monkeypatch):
    import subprocess
    import time
    calls = []
    real_run = subprocess.run

    def counting_run(cmd, *a, **kw):
        if cmd[:2] == ["hostname", "-I"]:
            calls.append(1)
            return subprocess.CompletedProcess(cmd, 0, stdout="192.168.50.124\n", stderr="")
        return real_run(cmd, *a, **kw)

    monkeypatch.setattr(subprocess, "run", counting_run)
    monkeypatch.setattr(frameinfo, "network", REAL_NETWORK)
    monkeypatch.setattr(frameinfo, "own_ips", REAL_OWN_IPS)
    monkeypatch.setitem(frameinfo._cache, "at", -1e9)
    clock = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    client.post("/pairing/start", environ_base=LOCAL)
    for _ in range(3):                                   # polls at 0, 2, 4 s (state + status)
        client.get("/pairing/state", environ_base=LOCAL)
        client.get("/frame/status", environ_base=LOCAL)
        clock[0] += 2
    assert len(calls) == 1
    clock[0] += 1                                        # 7 s: cache (5 s) expired
    client.get("/pairing/state", environ_base=LOCAL)
    assert len(calls) == 2


# ---------- MF-24 (#31): never a null/None/empty host; AP-mode detection ----------

@pytest.fixture
def hostname_out(monkeypatch):
    """Fake `hostname -I` (stdout, returncode or exception) with the real detection + cache."""
    import subprocess
    import time
    st = {"out": "", "rc": 0, "raise": None, "calls": 0, "route": None}
    real_run = subprocess.run

    def fake_run(cmd, *a, **kw):
        if cmd[:2] == ["hostname", "-I"]:
            st["calls"] += 1
            if st["raise"]:
                raise st["raise"]
            return subprocess.CompletedProcess(cmd, st["rc"], stdout=st["out"], stderr="")
        return real_run(cmd, *a, **kw)

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(socket, "gethostbyname_ex", lambda h: (_ for _ in ()).throw(OSError("no")))
    monkeypatch.setattr(frameinfo, "default_route_ip", lambda: st["route"])
    monkeypatch.setattr(frameinfo, "network", REAL_NETWORK)
    monkeypatch.setattr(frameinfo, "own_ips", REAL_OWN_IPS)
    monkeypatch.setitem(frameinfo._cache, "at", None)
    monkeypatch.setitem(frameinfo._cache, "ips", [])
    clock = [5000.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    st["clock"] = clock
    monkeypatch.setenv("MEMORYFRAME_PORTS", "80,5000")
    return st


def _state(client):
    client.post("/pairing/start", environ_base=LOCAL)
    return client.get("/pairing/state", environ_base=LOCAL).get_json()


def _no_null(st):
    blob = repr(st).lower()
    for bad in ("//null", "//none", "//undefined", "http:///"):
        assert bad not in blob, st


@pytest.mark.parametrize("out", ["10.42.0.1\n", "10.42.0.1 fe80::1 \n", "fe80::1 10.42.0.1",
                                 "172.17.0.1 10.42.0.1 2001:db8::5"])
def test_ap_only_or_reordered_hostname_output_is_hotspot(client, hostname_out, out):
    hostname_out["out"] = out
    st = _state(client)
    assert st["network"] == "hotspot" and st["url"] == "http://10.42.0.1/pair"
    assert st["url_fallback"] == "http://memoryframe.lan/pair"
    _no_null(st)


@pytest.mark.parametrize("case", ["empty", "rc1", "oserror", "timeout", "garbage"])
def test_failed_or_empty_detection_never_null(client, hostname_out, case):
    import subprocess
    if case == "empty":
        hostname_out["out"] = "   \n"
    elif case == "rc1":
        hostname_out["rc"] = 1
    elif case == "oserror":
        hostname_out["raise"] = FileNotFoundError("hostname")
    elif case == "timeout":
        hostname_out["raise"] = subprocess.TimeoutExpired("hostname", 2)
    else:
        hostname_out["out"] = "null None undefined 999.1.1.1"
    st = _state(client)
    assert st["url"] == "http://memoryframe.local/pair" and st["site_url"] == "http://memoryframe.local/"
    _no_null(st)
    status = client.get("/frame/status", environ_base=LOCAL).get_json()
    assert status["site"] == "http://memoryframe.local/"
    _no_null(status)


def test_network_switch_followed_within_10s_and_back(client, hostname_out):
    hostname_out["out"] = "192.168.50.124"
    hostname_out["route"] = "192.168.50.124"
    assert _state(client)["url"] == "http://memoryframe.local/pair"
    # hotspot comes up: no default route, only 10.42.0.1
    hostname_out["out"], hostname_out["route"] = "10.42.0.1", None
    hostname_out["clock"][0] += 2
    assert _state(client)["url"] == "http://memoryframe.local/pair"      # still cached (2 s)
    hostname_out["clock"][0] += 4                                         # 6 s after the switch
    st = _state(client)
    assert st["network"] == "hotspot" and st["url"] == "http://10.42.0.1/pair"
    # revert to home Wi-Fi
    hostname_out["out"], hostname_out["route"] = "192.168.50.124", "192.168.50.124"
    hostname_out["clock"][0] += 6
    st = _state(client)
    assert st["network"] == "lan" and st["url"] == "http://memoryframe.local/pair"


def test_failed_read_is_not_cached_keeps_last_good(client, hostname_out):
    hostname_out["out"] = "10.42.0.1"
    assert _state(client)["network"] == "hotspot"
    hostname_out["clock"][0] += 6
    hostname_out["raise"] = OSError("boom")
    assert _state(client)["network"] == "hotspot"                        # last good value
    n = hostname_out["calls"]
    hostname_out["raise"] = None
    hostname_out["clock"][0] += 1
    _state(client)
    assert hostname_out["calls"] > n                                     # retried, not cached


@pytest.mark.parametrize("bad", [None, "", "http://null", "http://None:5000", "null", "http://undefined/"])
def test_site_urls_sanitises_any_bad_primary(monkeypatch, bad):
    for mode, expected in (("hotspot", "http://10.42.0.1"), ("lan", "http://memoryframe.local")):
        monkeypatch.setattr(frameinfo, "_site_urls", lambda net=None, m=mode: {"mode": m, "primary": bad,
                                                                                "fallback": bad})
        monkeypatch.setenv("MEMORYFRAME_PORTS", "80")
        u = frameinfo.site_urls()
        assert u["primary"] == expected and u["fallback"] is None


def test_detection_exception_falls_back(monkeypatch):
    monkeypatch.setattr(frameinfo, "_site_urls", lambda net=None: 1 / 0)
    monkeypatch.setenv("MEMORYFRAME_PORTS", "80")
    assert frameinfo.site_urls()["primary"] == "http://memoryframe.local"


def test_pair_base_url_null_ignored(client, mf_app):
    mf_app.config["PAIR_BASE_URL"] = "http://null"
    st = _state(client)
    assert st["url"].startswith("http://10.42.0.1")                      # conftest: hotspot
    _no_null(st)


# ---------- MF-24 (#31, rewritten): QRs always encode http://; no HSTS ever ----------

def test_https_site_url_downgraded_to_http_and_logged(client, monkeypatch, caplog):
    import segno
    payloads = []
    real_make = segno.make
    monkeypatch.setattr(segno, "make", lambda data, **kw: payloads.append(data) or real_make(data, **kw))
    monkeypatch.setattr(frameinfo, "_logged", set())
    monkeypatch.setenv("MEMORYFRAME_SITE_URL", "https://memoryframe.lan")
    st = _state(client)
    assert st["url"] == "http://memoryframe.lan/pair" and st["site_url"] == "http://memoryframe.lan/"
    assert "https" in caplog.text and "using http" in caplog.text
    assert payloads and all(p.startswith(("http://", "WIFI:")) for p in payloads), payloads


@pytest.mark.parametrize("mode", ["hotspot", "lan", "unknown"])
def test_every_qr_encodes_http(client, monkeypatch, mode):
    import segno
    payloads = []
    real_make = segno.make
    monkeypatch.setattr(segno, "make", lambda data, **kw: payloads.append(data) or real_make(data, **kw))
    monkeypatch.setattr(frameinfo, "network",
                        lambda: {"mode": mode, "lan_ip": "192.168.50.124" if mode == "lan" else None, "ips": []})
    st = _state(client)
    urls = [p for p in payloads if not p.startswith("WIFI:")]
    assert urls and all(p.startswith("http://") for p in urls), payloads
    assert st["url"].startswith("http://")


def test_no_response_sends_hsts(client, paired, mf_app):
    from conftest import pair_client  # noqa: F401
    seen = []
    for rule in mf_app.url_map.iter_rules():
        if "GET" not in rule.methods or "<" in rule.rule:
            continue
        for env in (LOCAL, {"REMOTE_ADDR": "10.42.0.23"}):
            r = paired.get(rule.rule, environ_base=env)
            seen.append(rule.rule)
            assert "Strict-Transport-Security" not in r.headers, rule.rule
    r = paired.get("/", environ_base=LOCAL, headers={"X-Forwarded-Proto": "https"}, base_url="https://10.42.0.1")
    assert "Strict-Transport-Security" not in r.headers
    assert len(seen) > 10
