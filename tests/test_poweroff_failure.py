"""#62: poweroff is `systemctl poweroff` via logind/polkit (no sudo). If that call fails, the server says so
and the kiosk shows "Couldn't shut down" instead of leaving the shutdown cover up."""
import time

import pytest

from test_frontend_browser import evaluate, net, page, server  # noqa: F401  (fixtures)
from test_shutdown import cover, token, LOCAL


def test_poweroff_cmd_is_logind_no_sudo():
    from memoryframe import display
    assert display.POWEROFF_CMD == ["systemctl", "poweroff"]


@pytest.mark.parametrize("cmd", [["false"], ["/nonexistent/systemctl", "poweroff"]])
def test_failed_poweroff_reported_and_writes_allowed_again(client, mf_app, cmd):
    mf_app.config.update(POWEROFF_CMD=cmd, SYNC_CMD=["true"], POWEROFF_DELAY=0.05)
    r = client.post("/frame/shutdown", headers={"X-Frame-Token": token(client)}, environ_base=LOCAL)
    r.close()
    assert r.status_code == 200
    time.sleep(0.5)
    assert client.get("/frame/shutdown-status", environ_base=LOCAL).get_json() == {"failed": True}
    assert client.post("/brightness", json={"value": 50}, environ_base=LOCAL).status_code != 503


def test_successful_poweroff_not_reported_as_failed(client, mf_app):
    mf_app.config.update(POWEROFF_CMD=["true"], SYNC_CMD=["true"], POWEROFF_DELAY=0.05)
    client.post("/frame/shutdown", headers={"X-Frame-Token": token(client)}, environ_base=LOCAL).close()
    time.sleep(0.4)
    assert client.get("/frame/shutdown-status", environ_base=LOCAL).get_json() == {"failed": False}


def test_status_is_localhost_only(client):
    assert client.get("/frame/shutdown-status", environ_base={"REMOTE_ADDR": "10.42.0.23"}).status_code in (403, 404)


def test_kiosk_shows_couldnt_shut_down_when_poweroff_fails(page, server, mf_app, net):
    mf_app.config.update(POWEROFF_CMD=["false"], SYNC_CMD=["true"], POWEROFF_DELAY=0.1)
    page.pump(2.5)
    evaluate(page, "memoryFramePairing.openMenu()")
    evaluate(page, "document.getElementById('menuShutdown').click()")
    evaluate(page, "document.getElementById('shutdownConfirm').click()")
    page.pump(2.5)
    assert cover(page) is None
    assert evaluate(page, "window.mfShuttingDown") is False
    assert "Couldn't shut down" in evaluate(page, "document.getElementById('shutdownBox').innerText")
