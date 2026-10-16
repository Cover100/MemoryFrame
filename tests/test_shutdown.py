"""MF-49: Shut down from the frame menu (poweroff stubbed)."""
import time
from pathlib import Path

import pytest

from test_frontend_browser import evaluate, net, page, server, text  # noqa: F401  (fixtures)

ROOT = Path(__file__).resolve().parents[1]
LOCAL = {"REMOTE_ADDR": "127.0.0.1"}


@pytest.fixture
def stub(mf_app, tmp_path):
    out = tmp_path / "poweroff.log"
    mf_app.config["POWEROFF_CMD"] = ["sh", "-c", f"echo poweroff >> {out}"]
    mf_app.config["SYNC_CMD"] = ["sh", "-c", f"echo sync >> {out}"]
    mf_app.config["POWEROFF_DELAY"] = 0.05
    mf_app.config["POWEROFF_FALLBACK"] = 5
    return out


def token(client):
    html = client.get("/", environ_base=LOCAL).get_data(as_text=True)
    return html.split('name="mf-frame-token" content="', 1)[1].split('"', 1)[0]


def test_safe_only_after_sync_then_poweroff_on_step_two(client, stub):
    h = {"X-Frame-Token": token(client)}
    r = client.post("/frame/shutdown", headers=h, environ_base=LOCAL)
    assert r.status_code == 200 and r.get_json() == {"safe": True}
    time.sleep(0.3)
    assert stub.read_text().split() == ["sync"]                  # synced, not powered off yet
    assert client.post("/brightness", json={"value": 50}, environ_base=LOCAL).status_code == 503   # no writes
    assert client.post("/frame/poweroff", headers=h, environ_base=LOCAL).status_code == 200
    time.sleep(0.4)
    assert stub.read_text().split() == ["sync", "sync", "poweroff"]


def test_sync_failure_is_not_safe_and_no_poweroff(client, stub, mf_app):
    mf_app.config["SYNC_CMD"] = ["false"]
    h = {"X-Frame-Token": token(client)}
    r = client.post("/frame/shutdown", headers=h, environ_base=LOCAL)
    assert r.status_code == 500 and r.get_json() == {"safe": False}
    assert client.post("/frame/poweroff", headers=h, environ_base=LOCAL).status_code == 409
    time.sleep(0.3)
    assert not stub.exists()


def test_fallback_powers_off_if_kiosk_never_confirms(client, stub, mf_app):
    mf_app.config["POWEROFF_FALLBACK"] = 0.1
    client.post("/frame/shutdown", headers={"X-Frame-Token": token(client)}, environ_base=LOCAL)
    time.sleep(0.6)
    assert stub.read_text().split() == ["sync", "poweroff"]


def test_poweroff_needs_token_and_localhost(client, stub):
    t = token(client)
    client.post("/frame/shutdown", headers={"X-Frame-Token": t}, environ_base=LOCAL)
    assert client.post("/frame/poweroff", environ_base=LOCAL).status_code == 403
    assert client.post("/frame/poweroff", headers={"X-Frame-Token": t},
                       environ_base={"REMOTE_ADDR": "10.42.0.23"}).status_code == 403


@pytest.mark.parametrize("hdr", [{}, {"X-Frame-Token": "wrong"}])
def test_missing_or_wrong_token_403(client, stub, hdr):
    token(client)
    assert client.post("/frame/shutdown", headers=hdr, environ_base=LOCAL).status_code == 403
    time.sleep(0.3)
    assert not stub.exists()


def test_phone_cannot_shut_down_even_with_token(client, paired, stub):
    t = token(client)
    for c in (client, paired):
        r = c.post("/frame/shutdown", headers={"X-Frame-Token": t}, environ_base={"REMOTE_ADDR": "10.42.0.23"})
        assert r.status_code == 403
    assert client.get("/frame/shutdown", environ_base=LOCAL).status_code == 405
    time.sleep(0.3)
    assert not stub.exists()


def test_token_only_in_kiosk_page(client):
    r = client.get("/", environ_base={"REMOTE_ADDR": "10.42.0.23"})
    assert r.status_code == 302 and b"mf-frame-token" not in r.data


def test_sudoers_rule_is_only_poweroff():
    rules = [l for l in (ROOT / "deploy" / "memoryframe-poweroff-sudoers").read_text().splitlines()
             if l.strip() and not l.startswith("#")]
    assert rules == ["@USER@ ALL=(root) NOPASSWD: /usr/bin/systemctl poweroff"]
    inst = (ROOT / "deploy" / "install.sh").read_text()
    assert "visudo -cf" in inst and "/etc/sudoers.d/memoryframe-poweroff" in inst and "-m 0440" in inst


def shown(page):
    return evaluate(page, "document.getElementById('shutdownBox').innerText")


def test_menu_shutdown_shows_safe_only_after_server_sync(page, server, mf_app, net, tmp_path):
    out = tmp_path / "po.log"
    gate = tmp_path / "gate"
    mf_app.config["POWEROFF_CMD"] = ["sh", "-c", f"echo poweroff >> {out}"]
    # sync blocks until the test opens the gate: the screen must say "Shutting down" meanwhile
    mf_app.config["SYNC_CMD"] = ["sh", "-c", f"while [ ! -e {gate} ]; do sleep 0.05; done; echo sync >> {out}"]
    mf_app.config["POWEROFF_DELAY"] = 0.05
    mf_app.config["POWEROFF_FALLBACK"] = 30
    page.pump(2.5)
    evaluate(page, "memoryFramePairing.openMenu()")
    page.pump(0.3)
    evaluate(page, "document.getElementById('menuShutdown').click()")
    page.pump(0.3)
    assert "Shut down the frame?" in shown(page)
    evaluate(page, "document.getElementById('shutdownCancel').click()")
    page.pump(0.4)
    assert not out.exists()
    evaluate(page, "memoryFramePairing.openMenu()")
    evaluate(page, "document.getElementById('menuShutdown').click()")
    evaluate(page, "document.getElementById('shutdownConfirm').click()")
    page.pump(1.0)
    box = shown(page)
    assert "Shutting down" in box and "Safe to unplug" not in box
    assert not out.exists()
    gate.write_text("")
    page.pump(1.5)
    assert "Safe to unplug" in shown(page) and "Shutting down" not in shown(page)
    assert evaluate(page, "document.body.classList.contains('mf-final')")
    assert out.read_text().split()[-1] == "poweroff" and out.read_text().split()[0] == "sync"


def test_menu_shutdown_sync_failure_shows_error(page, server, mf_app, net, tmp_path):
    mf_app.config["SYNC_CMD"] = ["false"]
    mf_app.config["POWEROFF_CMD"] = ["true"]
    page.pump(2.5)
    evaluate(page, "memoryFramePairing.openMenu()")
    evaluate(page, "document.getElementById('menuShutdown').click()")
    evaluate(page, "document.getElementById('shutdownConfirm').click()")
    page.pump(1.0)
    box = shown(page)
    assert "Couldn't shut down" in box and "Safe to unplug" not in box
