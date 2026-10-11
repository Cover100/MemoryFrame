"""MF-23 (#29): both QRs fully visible at 800x480 with a >= 4-module white quiet zone, and decodable."""
import base64
import json
import os

import pytest

from test_frontend_browser import BROWSER, evaluate, net, page, server, text  # noqa: F401  (fixtures)
from memoryframe import frameinfo

pytestmark = pytest.mark.skipif(BROWSER is None, reason="no headless Chrome")

GEOM = """JSON.stringify((()=>{
 const out={vw:innerWidth, vh:innerHeight, qrs:{}};
 for (const id of ['wifiQr','pairQr']) {
   const box=document.getElementById(id); const svg=box && box.querySelector('svg');
   if(!svg || getComputedStyle(box.closest('.step')).display==='none') continue;
   const r=svg.getBoundingClientRect(), c=box.getBoundingClientRect(),
         p=document.getElementById('pairBox').getBoundingClientRect();
   const vb=(svg.getAttribute('viewBox')||'').split(/[ ,]+/).map(Number);
   let clipped=false;
   for(let e=svg.parentElement;e;e=e.parentElement){const o=getComputedStyle(e).overflow;
     if(o!=='visible'){const b=e.getBoundingClientRect();
       if(r.left<b.left-0.5||r.top<b.top-0.5||r.right>b.right+0.5||r.bottom>b.bottom+0.5) clipped=true;}}
   out.qrs[id]={svg:[r.left,r.top,r.right,r.bottom], box:[c.left,c.top,c.right,c.bottom],
     pairBox:[p.left,p.top,p.right,p.bottom], viewBox:vb, border:Number(svg.dataset.border||0), clipped};
 }
 return out;})())"""


def screenshot(page):
    return base64.b64decode(page.call("Page.captureScreenshot", {"format": "png"})["result"]["data"])


def save(png, name):
    d = os.environ.get("MF_QR_SHOTS")
    if d:
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, name), "wb") as f:
            f.write(png)


def inside(inner, outer, tol=0.5):
    return (inner[0] >= outer[0] - tol and inner[1] >= outer[1] - tol
            and inner[2] <= outer[2] + tol and inner[3] <= outer[3] + tol)


def quiet_zone_modules(png, rect, modules):
    """Measure the white margin around the dark modules in the screenshot, in modules."""
    import cv2
    import numpy as np
    import math
    img = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_GRAYSCALE)
    # round inwards: a fractional edge pixel is blended with the black overlay behind the SVG
    x0, y0, x1, y1 = math.ceil(rect[0]), math.ceil(rect[1]), math.floor(rect[2]), math.floor(rect[3])
    crop = img[y0:y1, x0:x1]
    dark = np.argwhere(crop < 128)
    assert len(dark), "no dark modules rendered"
    (top, left), (bottom, right) = dark.min(0), dark.max(0)
    module_px = (rect[2] - rect[0]) / modules
    margins = [left + 1, top + 1, (x1 - x0) - right, (y1 - y0) - bottom]   # +1: the trimmed edge px
    # everything in the margin must be white (no clipping artefacts or dark frame)
    return min(margins) / module_px


def decode(png, rect):
    import cv2
    import numpy as np
    img = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
    x0, y0, x1, y1 = (int(round(v)) for v in rect)
    pad = 10
    crop = img[max(0, y0 - pad):y1 + pad, max(0, x0 - pad):x1 + pad]
    data, _, _ = cv2.QRCodeDetector().detectAndDecode(crop)
    return data


def check(page, shot_name, expect_ids):
    g = json.loads(evaluate(page, GEOM))
    png = screenshot(page)
    save(png, shot_name)
    assert (g["vw"], g["vh"]) == (800, 480)
    assert sorted(g["qrs"]) == sorted(expect_ids), g
    view = [0, 0, g["vw"], g["vh"]]
    decoded = {}
    for qid, q in g["qrs"].items():
        assert inside(q["svg"], view), (qid, q)
        assert inside(q["svg"], q["box"]), (qid, q)
        assert inside(q["box"], q["pairBox"]) and inside(q["pairBox"], view), (qid, q)
        assert not q["clipped"], (qid, q)
        w, h = q["svg"][2] - q["svg"][0], q["svg"][3] - q["svg"][1]
        assert abs(w - h) < 1 and w >= 150, (qid, w, h)           # square, not squashed
        assert len(q["viewBox"]) == 4 and q["viewBox"][2] == q["viewBox"][3], q
        assert q["border"] >= 4, q
        assert quiet_zone_modules(png, q["svg"], q["viewBox"][2]) >= 3.9, qid
        decoded[qid] = decode(png, q["svg"])
    return decoded


@pytest.mark.no_media
def test_onboarding_both_qrs_fit_and_decode(page, server, mf_app, net):
    page.pump(3)
    d = check(page, "qr-onboarding-800x480.png", ["wifiQr", "pairQr"])
    assert d["wifiQr"].startswith("WIFI:T:WPA;S:Frame-WiFi;")
    assert d["pairQr"].startswith("http://10.42.0.1/pair")


def test_pair_new_device_from_menu_fits_and_decodes(page, server, mf_app, net):
    import time
    net.update(mode="lan", lan_ip="192.168.50.124", ips=["192.168.50.124"])
    # Wait until the frame itself polled the new mode (no fixed sleeps: CI timing varies).
    end = time.monotonic() + 15
    while "memoryframe.local" not in (text(page, "pairUrl") or "") and time.monotonic() < end:
        page.pump(0.25)
    assert "memoryframe.local" in text(page, "pairUrl"), text(page, "pairUrl")
    evaluate(page, "memoryFramePairing.openMenu()")
    page.pump(0.5)
    evaluate(page, "document.getElementById('menuPair').click()")
    expected = f"http://memoryframe.local{frameinfo.port_suffix()}/pair"
    end = time.monotonic() + 15
    while True:
        page.pump(0.5)
        d = check(page, "qr-pair-new-device-800x480.png", ["pairQr"])
        if d["pairQr"].startswith(expected) or time.monotonic() > end:
            break
    assert d["pairQr"].startswith(expected), d


def test_empty_library_overlay_fits(page, server, mf_app, net):
    from memoryframe import auth, db as mfdb
    conn = mfdb.connect(mf_app.config["DB_PATH"])
    auth.create_session(conn, "phone")
    conn.execute("UPDATE media SET hidden = 1")
    conn.commit()
    conn.close()
    page.pump(3)
    r = json.loads(evaluate(page, "JSON.stringify((()=>{const e=document.querySelector('#emptyOverlay .emptyText')"
                                  ".getBoundingClientRect();return [e.left,e.top,e.right,e.bottom,"
                                  "getComputedStyle(document.getElementById('emptyOverlay')).display]})())"))
    save(screenshot(page), "qr-empty-library-800x480.png")
    assert r[4] != "none" and inside(r[:4], [0, 0, 800, 480]), r


@pytest.mark.no_media
def test_onboarding_fits_at_1000x600(page, server, mf_app, net):
    page.call("Emulation.setDeviceMetricsOverride", {"width": 1000, "height": 600, "deviceScaleFactor": 1,
                                                     "mobile": False})
    page.pump(3)
    g = json.loads(evaluate(page, GEOM))
    for q in g["qrs"].values():
        assert inside(q["svg"], [0, 0, 1000, 600]) and not q["clipped"]


# ---------- MF-24 (#31): the frame JS never shows null/undefined as an address ----------

@pytest.mark.no_media
@pytest.mark.parametrize("network,expected", [("hotspot", "http://10.42.0.1/pair"),
                                              ("lan", "http://memoryframe.local/pair")])
def test_js_guards_null_url_fields(page, server, mf_app, net, monkeypatch, network, expected):
    from memoryframe import pairing
    real = pairing._state

    def broken():
        st = real()
        st.update(url=None, url_fallback="http://null/pair", site_url="null", network=network)
        return st

    monkeypatch.setattr(pairing, "_state", broken)
    page.pump(3)
    body = evaluate(page, "document.getElementById('pairOverlay').innerText + "
                          "document.getElementById('emptyOverlay').innerText")
    assert evaluate(page, "document.getElementById('pairUrl').textContent") == expected
    assert "null" not in body.lower() and "undefined" not in body.lower(), body
    assert page.exceptions() == []
