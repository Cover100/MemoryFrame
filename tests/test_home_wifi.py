"""#68: developer (home) Wi-Fi from the phone: app gate, validation, rate limit, secrecy, and the
netmode.sh home-wifi helper (fake nmcli)."""
import json
import logging
import os
import stat

import pytest

from memoryframe import homewifi, privileged
from test_netmode_script import FAKE_NMCLI, HOT, active, env, profile, run, state  # noqa: F401

PHONE = {"REMOTE_ADDR": "10.42.0.23"}
SENT = "S3ntinel-Pass"


def write_nm(tmp_path, monkeypatch, **kw):
    s = {"mode": "product", "requested": "dev", "dev_fallback": True, "hotspot_ssid": "MemoryFrame-AP"}
    s.update(kw)
    p = tmp_path / "netmode.json"
    p.write_text(json.dumps(s))
    monkeypatch.setenv("MEMORYFRAME_NETMODE_STATE", str(p))


@pytest.fixture
def hw(mf_app, tmp_path, monkeypatch):
    homewifi._attempts.clear()
    homewifi._running["since"] = 0.0
    write_nm(tmp_path, monkeypatch)
    started = []
    mf_app.config["HOME_WIFI_START"] = lambda: started.append(1)
    mf_app.config["HOME_WIFI_STATE"] = str(tmp_path / "home-wifi.json")
    return started


def post(c, body, env=PHONE, **kw):
    return c.post("/admin/home-wifi", data=json.dumps(body), content_type="application/json", environ_base=env, **kw)


GOOD = {"ssid": "Home Net", "password": SENT, "confirm": True}


@pytest.mark.parametrize("kw", [dict(requested="product"), dict(dev_fallback=False), dict(mode="dev")])
def test_gate_404_and_no_menu_outside_dev_fallback(paired, hw, tmp_path, monkeypatch, kw):
    write_nm(tmp_path, monkeypatch, **kw)
    assert paired.get("/admin/home-wifi", environ_base=PHONE).status_code == 404
    assert post(paired, GOOD).status_code == 404 and hw == []
    assert "/admin/home-wifi" not in paired.get("/admin/", environ_base=PHONE).get_data(as_text=True)


def test_gate_needs_hotspot_and_pairing(client, paired, hw):
    assert paired.get("/admin/home-wifi", environ_base={"REMOTE_ADDR": "192.168.1.5"}).status_code == 404
    html = paired.get("/admin/", environ_base=PHONE).get_data(as_text=True)
    assert 'href="/admin/home-wifi"' in html
    r = paired.get("/admin/home-wifi", environ_base=PHONE)
    assert r.status_code == 200 and r.headers["Cache-Control"] == "no-store"


def test_csrf_required(paired, hw):
    assert post(paired, GOOD, csrf=False).status_code == 403 and hw == []


@pytest.mark.parametrize("body, code", [
    (dict(GOOD, ssid=""), "invalid_ssid"), (dict(GOOD, ssid="x" * 33), "invalid_ssid"),
    (dict(GOOD, ssid="a\x07b"), "invalid_ssid"), (dict(GOOD, ssid="MemoryFrame-AP"), "invalid_ssid"),
    (dict(GOOD, password="1234567"), "invalid_password"), (dict(GOOD, password="g" * 64), "invalid_password"),
    (dict(GOOD, confirm=False), "bad_request"), (dict(GOOD, extra=1), "bad_request"),
])
def test_validation(paired, hw, body, code):
    r = post(paired, body)
    assert r.status_code == 400 and r.get_json() == {"error": code} and hw == []


def test_64_hex_psk_ok_and_202(paired, hw, caplog):
    with caplog.at_level(logging.DEBUG):
        r = post(paired, dict(GOOD, password="a" * 64))
        r.close()
    assert r.status_code == 202 and r.get_json() == {"state": "testing"} and hw == [1]
    assert "ssid_len 8" in caplog.text and "Home Net" not in caplog.text


def test_body_cap_and_json_only(paired, hw):
    r = paired.post("/admin/home-wifi", data="x" * 600, content_type="application/json", environ_base=PHONE)
    assert r.status_code == 413
    r = paired.post("/admin/home-wifi", data={"ssid": "a", "password": SENT}, environ_base=PHONE)
    assert r.status_code == 415


def test_rate_limit_and_busy(paired, hw, mf_app, tmp_path):
    for _ in range(3):
        r = post(paired, GOOD)
        r.close()
        homewifi._running["since"] = 0.0
        assert r.status_code == 202
    assert post(paired, GOOD).status_code == 429
    homewifi._attempts.clear()
    (tmp_path / "home-wifi.json").write_text('{"state": "testing", "ts": 1}')
    assert post(paired, GOOD).status_code == 409


def test_status_never_has_a_password(paired, hw, tmp_path):
    (tmp_path / "home-wifi.json").write_text(json.dumps({"state": "saved", "reason": "ok", "ts": 5,
                                                         "ssid_hex": "Home Net".encode().hex()}))
    j = paired.get("/admin/home-wifi/status", environ_base=PHONE).get_json()
    assert j == {"state": "saved", "reason": "ok", "ts": 5, "ssid": "Home Net"}


def test_sentinel_never_logged_or_stored(paired, hw, mf_app, caplog, tmp_path):
    with caplog.at_level(logging.DEBUG):
        r = post(paired, GOOD)
        body = r.get_data(as_text=True)
        r.close()
    assert SENT not in caplog.text and SENT not in body
    db = open(mf_app.config["DB_PATH"], "rb").read()
    assert SENT.encode() not in db


def test_request_file_is_0600_and_never_follows_a_symlink(tmp_path):
    d = tmp_path / "in"
    d.mkdir(mode=0o700)
    target = tmp_path / "victim"
    target.write_text("keep")
    os.symlink(target, d / "home-wifi")
    cfg = {"PRIV_INPUT_DIR": str(d), "PRIV_OUT_DIR": str(tmp_path)}
    import subprocess
    orig = subprocess.run
    subprocess.run = lambda *a, **k: subprocess.CompletedProcess(a, 0, "", "")
    try:
        privileged.run(cfg, "home-wifi", stdin="v1\n41\n" + SENT + "\n")
    finally:
        subprocess.run = orig
    assert target.read_text() == "keep"
    st = os.lstat(d / "home-wifi")
    assert stat.S_ISREG(st.st_mode) and stat.S_IMODE(st.st_mode) == 0o600


# ---------- netmode.sh home-wifi ----------

def fallback(env, requested="dev"):
    nm = env["d"] / "nmcli"
    nm.write_text(nm.read_text().replace('[ "$1" = -e ] && shift 2\n', '[ "$1" = -e ] && shift 2\n'
                  '[ "$1 $2" = "-g IP4.ADDRESS" ] && { [ "$(cat "$D/active")" = MemoryFrame-home-pending ] && echo 192.168.1.9/24; exit 0; }\n', 1))
    (env["d"] / "active").write_text(HOT + "\n")
    env["state"].mkdir(parents=True, exist_ok=True)
    (env["state"] / "netmode.json").write_text(json.dumps(
        {"mode": "product", "requested": requested, "dev_fallback": True, "hotspot_error": False}))


def request(env, ssid="Home Net", psk=SENT, raw=None):
    rq = env["tmp"] / "app" / "home-wifi"
    rq.parent.mkdir(mode=0o700, exist_ok=True)
    rq.write_text(raw if raw is not None else f"v1\n{ssid.encode().hex()}\n{psk}\n")
    env["env"]["MEMORYFRAME_HOME_REQ"] = str(rq)
    env["env"]["MEMORYFRAME_HOME_WAIT"] = "1"
    return rq


def hstate(env):
    return json.loads((env["state"] / "home-wifi.json").read_text())


def test_helper_saves_and_restores_hotspot(env):
    fallback(env)
    profile(env["d"], "MemoryFrame-home", ssid="Old")
    rq = request(env)
    r = run(env, "home-wifi", "--request")
    assert r.returncode == 0, r.stderr
    p = env["d"] / "p"
    assert (p / "MemoryFrame-home" / "keyfile").exists() and not (p / "MemoryFrame-home-old").exists()
    assert not (p / "MemoryFrame-home-pending").exists() and not rq.exists()
    assert active(env) == HOT and hstate(env)["state"] == "saved"
    assert SENT not in (env["d"] / "log").read_text() and SENT not in r.stdout + r.stderr
    assert SENT not in (env["state"] / "home-wifi.json").read_text()
    kf = (p / "MemoryFrame-home" / "keyfile").read_text()
    assert "ssid=72;111;109;101;32;78;101;116;" in kf and "autoconnect=false" in kf


def test_helper_wrong_password_keeps_old_profile(env):
    fallback(env)
    profile(env["d"], "MemoryFrame-home", ssid="Old")
    (env["d"] / "fail").write_text("MemoryFrame-home-pending\n")
    request(env)
    r = run(env, "home-wifi", "--request")
    assert r.returncode != 0
    p = env["d"] / "p"
    assert (p / "MemoryFrame-home" / "ssid").read_text().strip() == "Old"
    assert not (p / "MemoryFrame-home-pending").exists() and active(env) == HOT
    assert hstate(env)["state"] == "failed"


@pytest.mark.parametrize("raw", ["v1\n\nlongenough\n", "v1\n" + "41" * 33 + "\nlongenough\n",
                                 "v1\n4107\nlongenough\n", "v1\n41\nshort\n", "v1\n41\n" + "g" * 64 + "\n",
                                 "v2\n41\nlongenough\n", "v1\n410a\nlongenough\n"])
def test_helper_validation(env, raw):
    fallback(env)
    request(env, raw=raw)
    r = run(env, "home-wifi", "--request")
    assert r.returncode == 2 and not (env["d"] / "p" / "MemoryFrame-home-pending").exists()


def test_helper_refused_outside_dev_fallback(env):
    fallback(env, requested="product")
    request(env)
    assert run(env, "home-wifi", "--request").returncode == 3
    assert not (env["d"] / "p" / "MemoryFrame-home-pending").exists()


def test_helper_refuses_symlink_request(env):
    fallback(env)
    rq = request(env)
    real = env["tmp"] / "elsewhere"
    real.write_text(rq.read_text())
    rq.unlink()
    os.symlink(real, rq)
    assert run(env, "home-wifi", "--request").returncode == 2


def test_dev_prefers_memoryframe_home_and_boot_recovers(env):
    profile(env["d"], "MemoryFrame-home-old", ssid="Old")
    profile(env["d"], "MemoryFrame-home-pending", ssid="Untested")
    (env["boot"] / "memoryframe-mode.txt").write_text("dev\n")
    r = run(env, "boot")
    assert r.returncode == 0, r.stderr
    p = env["d"] / "p"
    assert not (p / "MemoryFrame-home-pending").exists() and (p / "MemoryFrame-home").exists()
    assert active(env) == "MemoryFrame-home", r.stderr + (env["d"] / "log").read_text()


def test_force_dev_fallback_hook_dev_only(env):
    (env["boot"] / "memoryframe-mode.txt").write_text("dev\n")
    env["state"].mkdir(parents=True, exist_ok=True)
    (env["state"] / "force-dev-fallback").write_text("")
    before = sorted(os.listdir(env["d"] / "p"))
    assert run(env, "boot").returncode == 0
    s = state(env)
    assert s["dev_fallback"] is True and s["requested"] == "dev" and active(env) == HOT
    assert sorted(os.listdir(env["d"] / "p")) == before
    (env["boot"] / "memoryframe-mode.txt").write_text("product\n")      # product: hook ignored
    assert run(env, "boot").returncode == 0
    assert state(env)["dev_fallback"] is False and state(env)["requested"] == "product"
