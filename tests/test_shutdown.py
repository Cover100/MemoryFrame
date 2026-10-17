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
    return out


def token(client):
    html = client.get("/", environ_base=LOCAL).get_data(as_text=True)
    return html.split('name="mf-frame-token" content="', 1)[1].split('"', 1)[0]


def test_sync_then_poweroff_and_writes_refused(client, stub):
    h = {"X-Frame-Token": token(client)}
    r = client.post("/frame/shutdown", headers=h, environ_base=LOCAL)
    assert r.status_code == 200 and r.get_json() == {"synced": True}
    r.close()
    assert client.post("/brightness", json={"value": 50}, environ_base=LOCAL).status_code == 503
    time.sleep(0.5)
    assert stub.read_text().split() == ["sync", "poweroff"]      # sync strictly before poweroff


def test_poweroff_scheduled_only_after_the_response(client, stub, mf_app):
    mf_app.config["POWEROFF_DELAY"] = 0.05
    r = client.post("/frame/shutdown", headers={"X-Frame-Token": token(client)}, environ_base=LOCAL,
                    buffered=False)
    time.sleep(0.4)
    assert stub.read_text().split() == ["sync"]            # response not finished yet: no poweroff
    r.close()
    time.sleep(0.4)
    assert stub.read_text().split() == ["sync", "poweroff"]


def test_sync_failure_no_poweroff(client, stub, mf_app):
    mf_app.config["SYNC_CMD"] = ["false"]
    r = client.post("/frame/shutdown", headers={"X-Frame-Token": token(client)}, environ_base=LOCAL)
    assert r.status_code == 500 and r.get_json() == {"synced": False}
    time.sleep(0.3)
    assert not stub.exists()
    assert client.post("/brightness", json={"value": 50}, environ_base=LOCAL).status_code != 503


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


def cover(page):
    return evaluate(page, "(()=>{const c=document.getElementById('shuttingDown');"
                          "return c.hidden ? null : c.innerText;})()")


def safe(page):
    return evaluate(page, "(()=>{const c=document.getElementById('safeScreen');"
                          "return c.hidden ? null : c.innerText;})()")


def test_menu_shutdown_safe_only_after_synced(page, server, mf_app, net, tmp_path, backlight):
    out = tmp_path / "po.log"
    gate = tmp_path / "gate"
    mf_app.config["POWEROFF_CMD"] = ["sh", "-c", f"echo poweroff >> {out}"]
    mf_app.config["SYNC_CMD"] = ["sh", "-c", f"while [ ! -e {gate} ]; do sleep 0.05; done; echo sync >> {out}"]
    mf_app.config["POWEROFF_DELAY"] = 1.0
    page.pump(2.5)
    evaluate(page, "memoryFramePairing.openMenu()")
    evaluate(page, "document.getElementById('menuShutdown').click()")
    evaluate(page, "document.getElementById('shutdownConfirm').click()")
    page.pump(0.4)
    txt = cover(page)
    assert txt and "Shutting down" in txt and "Don't unplug yet" in txt
    assert safe(page) is None and not out.exists()
    for y in range(400, 100, -20):                              # taps/drags on the cover do nothing
        page.call("Input.dispatchMouseEvent", {"type": "mouseMoved" if y < 400 else "mousePressed", "x": 400,
                                               "y": y, "button": "left", "buttons": 1, "clickCount": 1})
    page.call("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": 400, "y": 100, "button": "left",
                                           "clickCount": 1})
    page.pump(0.3)
    assert (backlight / "brightness").read_text().strip() == "100"
    gate.write_text("")
    page.pump(0.5)
    s = safe(page)
    assert s and "Unplug once the screen goes dark" in s and "Photos saved · Memory Frame is turning off" in s
    st = evaluate(page, "(()=>{const t=document.querySelector('#safeScreen .safeTitle');const c=getComputedStyle(t);"
                        "return [c.fontSize,c.fontWeight,Math.round(t.getBoundingClientRect().width)<=720,"
                        "Math.round(t.getBoundingClientRect().height/parseFloat(c.lineHeight))<=2,"
                        "getComputedStyle(document.querySelector('#safeScreen .safeSub')).fontSize,"
                        "getComputedStyle(document.querySelector('#safeScreen .safeTick')).animationName];})()")
    assert st == ["56px", "700", True, True, "28px", "safePulse"]
    assert cover(page) is None
    assert out.read_text().split() == ["sync"]                 # poweroff ~delay after the response
    bg = evaluate(page, "getComputedStyle(document.getElementById('safeScreen')).backgroundColor")
    assert bg == "rgb(0, 107, 46)"
    page.pump(1.2)
    assert out.read_text().split() == ["sync", "poweroff"]


def test_menu_shutdown_sync_failure_shows_error(page, server, mf_app, net, tmp_path):
    mf_app.config["SYNC_CMD"] = ["false"]
    mf_app.config["POWEROFF_CMD"] = ["true"]
    page.pump(2.5)
    evaluate(page, "memoryFramePairing.openMenu()")
    evaluate(page, "document.getElementById('menuShutdown').click()")
    evaluate(page, "document.getElementById('shutdownConfirm').click()")
    page.pump(1.0)
    assert cover(page) is None and safe(page) is None
    assert "Couldn't shut down" in evaluate(page, "document.getElementById('shutdownBox').innerText")


def test_no_bare_safe_to_unplug_text_in_browser():
    for f in ("templates/index.html", "static/pairing.js", "static/script.js"):
        assert "Safe to unplug" not in (Path(__file__).resolve().parents[1] / f).read_text()
