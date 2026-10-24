"""#98: first-run welcome W0 / W1 (Wren 16, 16b, 16c @ 9978deb), with the frame page in a real browser."""
import pytest

from test_frontend_browser import evaluate, overlay, text, visible  # noqa: F401
from test_frontend_browser import page, server, net  # noqa: F401  (fixtures)

pytestmark = pytest.mark.no_media

BAD = ("ssid", "hotspot", " ip ", "password")


def screen_text(page):
    return evaluate(page, "document.getElementById('pairBox').innerText + ' ' + "
                          "document.getElementById('emptyOverlay').innerText").lower()


def inside(page, ids):
    js = ("(" + repr(ids) + ").every(i=>{const e=document.getElementById(i); if(!e||!e.offsetParent) return true;"
          "const r=e.getBoundingClientRect(); return r.left>=0&&r.top>=0&&r.right<=800&&r.bottom<=480})")
    return evaluate(page, js)


def no_overlap(page):
    """Wren must-fix: no visible step line (name, URL, hint) overlaps the footer or another line."""
    js = ("(()=>{const els=[...document.querySelectorAll('#pairBox .stepText, #welcomeFoot, #pairCode')]"
          ".filter(e=>e.offsetParent&&e.getBoundingClientRect().height>0);"
          "const r=els.map(e=>e.getBoundingClientRect());"
          "for(let i=0;i<r.length;i++)for(let j=i+1;j<r.length;j++){const a=r[i],b=r[j];"
          "if(a.left<b.right-1&&b.left<a.right-1&&a.top<b.bottom-1&&b.top<a.bottom-1)return els[i].id+'|'+els[j].id+'|'+els[i].innerText}"
          "return ''})()")
    return evaluate(page, js)


def titles_inside_cards(page):
    """Wren: every visible step title's box sits inside its card (no run-over), text >= 20 px."""
    js = ("[...document.querySelectorAll('#pairBox .step')].filter(s=>s.offsetParent).map(s=>{"
          "const t=s.querySelector('.stepTitle'), a=t.getBoundingClientRect(), c=s.getBoundingClientRect();"
          "const fs=parseFloat(getComputedStyle(t).fontSize);"
          "return (a.left>=c.left&&a.right<=c.right&&a.top>=c.top&&a.bottom<=c.bottom&&t.scrollWidth<=t.clientWidth+1&&fs>=20)"
          "?'':t.innerText}).filter(x=>x).join('|')")
    return evaluate(page, js)


def test_w0_three_steps_fit_and_no_forbidden_words(page, server, mf_app, net):
    page.pump(3)
    assert overlay(page)["visible"] and visible(page, "welcomeHead") and visible(page, "wifiStep")
    assert "Welcome to your Memory Frame" in text(page, "welcomeHead")
    assert text(page, "welcomeFoot") == "Then tap Upload on your phone."
    assert not visible(page, "pairClose")
    for q in ("wifiQr", "pairQr"):
        assert evaluate(page, f"Math.round(document.querySelector('#{q} svg').getBoundingClientRect().width)") == 240
    assert inside(page, ["welcomeHead", "wifiStep", "codeStep", "welcomeFoot", "pairQr", "wifiQr", "pairCode"])
    assert no_overlap(page) == "" and titles_inside_cards(page) == ""
    shown = evaluate(page, "document.getElementById('pairBox').innerText")
    assert "Wi-Fi name" not in shown and "or open" not in shown
    assert evaluate(page, "document.documentElement.scrollHeight <= 480 && document.documentElement.scrollWidth <= 800")
    words = screen_text(page)
    assert not any(b in words for b in BAD), words


def test_w0_two_steps_without_wifi_qr(page, server, mf_app, net, monkeypatch):
    from memoryframe import pairing
    real = pairing._state
    monkeypatch.setattr(pairing, "_state", lambda: {**real(), "wifi_qr_svg": None, "wifi_join_name": "Frame-WiFi"})
    page.pump(3)
    assert visible(page, "welcomeHead") and not visible(page, "wifiStep")
    assert text(page, "siteTitle") == "Open the site on your phone" and text(page, "codeTitle") == "Type this code"
    assert text(page, "siteBadge") == "1" and text(page, "codeBadge") == "2"
    assert inside(page, ["welcomeHead", "codeStep", "welcomeFoot", "pairQr", "pairCode"])
    assert titles_inside_cards(page) == ""
    assert no_overlap(page) == "" and "or open" not in evaluate(page, "document.getElementById('pairBox').innerText")


def test_w0_to_w1_without_reload(page, server, mf_app, net):
    from memoryframe import auth, db as mfdb
    page.pump(3)
    assert visible(page, "welcomeHead")
    conn = mfdb.connect(mf_app.config["DB_PATH"])
    auth.create_session(conn, "phone")
    conn.commit()
    page.pump(7)                                           # paired toast (3 s), then W1
    assert visible(page, "emptyOverlay") and not overlay(page)["visible"]
    assert "You're connected" in text(page, "emptyOverlay") and "tap Upload." in text(page, "emptyOverlay")
    assert visible(page, "emptyClose") and text(page, "pairCode") == ""
    assert evaluate(page, "document.getElementById('emptyClose').getBoundingClientRect().height") >= 48
    w1 = text(page, "emptyOverlay").lower()
    assert not any(b in w1 for b in BAD), w1


def test_no_w0_when_hotspot_error(page, server, mf_app, net, monkeypatch):
    from memoryframe import pairing
    real = pairing._state

    def st():
        s = real()
        s["netmode"] = {**s.get("netmode", {}), "hotspot_error": True}
        return s
    monkeypatch.setattr(pairing, "_state", st)
    page.pump(3)
    assert visible(page, "hotspotError") and not overlay(page)["visible"]
