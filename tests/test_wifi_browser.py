"""MF-18 in headless Chrome: frame rejoin screen (800x480, QR decodes, countdown, not dismissable)
and the phone form (Show/Hide, client-side validation, confirm/cancel)."""
import time
from urllib.parse import urlsplit

from memoryframe import auth, db as mfdb, wifi
from test_frontend_browser import evaluate, net, page, server, text  # noqa: F401  (fixtures)
from test_qr_fit import decode, screenshot
from test_wifi_change import NEW_PW, helper  # noqa: F401  (fixture)


def _pending(app, remaining=180, ssid="Frame Home"):
    conn = mfdb.connect(app.config["DB_PATH"])
    conn.execute("INSERT OR REPLACE INTO wifi_change (id, state, old_ssid, new_ssid, new_password, old_profile, "
                 "new_profile, deadline, updated) VALUES (1, 'pending', 'MemoryFrame-AP', ?, ?, 'a', 'b', ?, ?)",
                 (ssid, NEW_PW, time.time() + remaining, time.time()))
    conn.commit()
    conn.close()


def rect(page, sel):
    return evaluate(page, f"(()=>{{const r=document.querySelector('{sel}').getBoundingClientRect();"
                          f"return [r.left,r.top,r.right,r.bottom];}})()")


def test_frame_rejoin_screen(page, server, mf_app, net, helper):
    _pending(mf_app, remaining=180)
    page.pump(2.5)
    assert evaluate(page, "!document.getElementById('wifiRejoin').hidden")
    card = rect(page, "#wifiRejoin .wifi-card")
    assert card[0] >= 0 and card[1] >= 0 and card[2] <= 800 and card[3] <= 480, card
    assert evaluate(page, "(()=>{const c=document.querySelector('#wifiRejoin .wifi-card');"
                          "return c.scrollHeight<=c.clientHeight;})()")
    body = text(page, "wifiRejoin")
    assert "Wi-Fi changed: scan to rejoin" in body and "Wi-Fi: Frame Home" in body
    assert 'Then open http://10.42.0.1/ and tap "Keep new Wi-Fi"' in body
    assert NEW_PW not in body
    cd = text(page, "wifiCountdown")
    assert cd in ("3:00", "2:59", "2:58", "2:57"), cd
    q = rect(page, "#wifiRejoinQr")
    assert round(q[2] - q[0]) == 240 and round(q[3] - q[1]) == 240
    bar = evaluate(page, "document.getElementById('wifiBar').style.transform")       # #70 mock 13: brass bar
    assert bar.startswith("scaleX(0.5") or bar.startswith("scaleX(0.6"), bar
    data = decode(screenshot(page), q)
    assert data.startswith("WIFI:T:WPA;S:Frame Home;P:Brand new pw!;"), data
    # not dismissable; the menu is ignored
    evaluate(page, "document.getElementById('wifiRejoin').click()")
    evaluate(page, "memoryFramePairing.openMenu()")
    page.pump(0.5)
    assert evaluate(page, "!document.getElementById('wifiRejoin').hidden")
    assert not evaluate(page, "document.getElementById('menuOverlay').classList.contains('visible')")


def test_frame_cancelled_then_closes(page, server, mf_app, net, helper):
    _pending(mf_app)
    page.pump(2.5)
    wifi.rollback(mf_app, "timeout")
    page.pump(2.5)
    assert evaluate(page, "document.getElementById('wifiRejoin').hidden")
    assert "Wi-Fi change cancelled — old Wi-Fi restored" in text(page, "wifiCancelled")
    assert "Wi-Fi: MemoryFrame-AP" in text(page, "wifiCancelled")
    evaluate(page, "document.getElementById('wifiCancelledClose').click()")
    page.pump(2.5)
    assert evaluate(page, "document.getElementById('wifiCancelled').hidden")


def _phone(page, server, mf_app, path):
    conn = mfdb.connect(mf_app.config["DB_PATH"])
    token = auth.create_session(conn, "test phone")
    conn.close()
    page.call("Network.enable")
    page.call("Network.setCookie", {"name": "mf_session", "value": token, "domain": urlsplit(server).hostname,
                                    "path": "/", "httpOnly": True, "sameSite": "Strict"})
    page.call("Page.navigate", {"url": server + path})
    page.pump(2)


def test_phone_form_show_hide_validate_confirm_cancel(page, server, mf_app, net, helper):
    _phone(page, server, mf_app, "admin/wifi")
    js = "document.getElementById"
    assert evaluate(page, f"{js}('password').type") == "password"
    evaluate(page, f"{js}('showPw').click()")
    assert evaluate(page, f"{js}('password').type") == "text" and text(page, "showPw") == "Hide"
    evaluate(page, f"{js}('showPw').click()")
    assert evaluate(page, f"{js}('password').type") == "password"
    evaluate(page, f"{js}('password').value='short'")
    evaluate(page, f"{js}('changeBtn').click()")
    page.pump(0.3)
    assert text(page, "error") == wifi.MSG_PASSWORD and evaluate(page, f"{js}('confirmStep').hidden")
    evaluate(page, f"{js}('ssid').value='x'.repeat(33)")
    evaluate(page, f"{js}('password').value='longenough'")
    evaluate(page, f"{js}('changeBtn').click()")
    page.pump(0.3)
    assert text(page, "error") == wifi.MSG_NAME
    evaluate(page, f"{js}('ssid').value='Frame Home'")
    evaluate(page, f"{js}('changeBtn').click()")
    page.pump(0.3)
    assert not evaluate(page, f"{js}('confirmStep').hidden")
    assert ("All phones will disconnect. Rejoin with the new password within 5 minutes or the old Wi-Fi comes back."
            in text(page, "confirmStep"))
    assert text(page, "confirmName") == "Frame Home"
    evaluate(page, f"{js}('cancelBtn').click()")
    page.pump(0.3)
    assert evaluate(page, f"{js}('confirmStep').hidden")
    assert evaluate(page, f"{js}('ssid').value") == "Frame Home" and evaluate(page, f"{js}('password').value") == ""
    # confirm for real -> waiting page
    evaluate(page, f"{js}('password').value='{NEW_PW}'")
    evaluate(page, f"{js}('changeBtn').click()")
    page.pump(0.3)
    evaluate(page, f"{js}('confirmBtn').click()")
    page.pump(2)
    assert "Rejoin the new Wi-Fi" in evaluate(page, "document.body.innerText")
