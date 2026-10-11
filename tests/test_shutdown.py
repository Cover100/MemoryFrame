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
    mf_app.config["POWEROFF_DELAY"] = 0.05
    return out


def token(client):
    html = client.get("/", environ_base=LOCAL).get_data(as_text=True)
    return html.split('name="mf-frame-token" content="', 1)[1].split('"', 1)[0]


def test_kiosk_with_token_powers_off(client, stub):
    r = client.post("/frame/shutdown", headers={"X-Frame-Token": token(client)}, environ_base=LOCAL)
    assert r.status_code == 200 and r.get_json() == {"shutting_down": True}
    time.sleep(0.5)
    assert stub.read_text().strip() == "poweroff"


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


def test_menu_shutdown_confirm_then_safe_to_unplug(page, server, mf_app, net, tmp_path):
    out = tmp_path / "po.log"
    mf_app.config["POWEROFF_CMD"] = ["sh", "-c", f"echo poweroff >> {out}"]
    mf_app.config["POWEROFF_DELAY"] = 0.05
    page.pump(2.5)
    evaluate(page, "memoryFramePairing.openMenu()")
    page.pump(0.3)
    evaluate(page, "document.getElementById('menuShutdown').click()")
    page.pump(0.3)
    assert "Shut down the frame?" in text(page, "shutdownBox")
    evaluate(page, "document.getElementById('shutdownCancel').click()")    # cancel: nothing happens
    page.pump(0.5)
    assert not out.exists()
    evaluate(page, "memoryFramePairing.openMenu()")
    evaluate(page, "document.getElementById('menuShutdown').click()")
    evaluate(page, "document.getElementById('shutdownConfirm').click()")
    page.pump(1.0)
    assert "Safe to unplug" in text(page, "shutdownBox")
    assert out.read_text().strip() == "poweroff"
