"""#167 (Wren, devwifi-fail-copy/wren.md): the developer Wi-Fi join-failure dialog. Every failure maps to a title in
the "Developer Wi-Fi ..." pattern and one plain sentence (capital letter, full stop, no internal terms, no raw reason);
the JSON carries a short code, the title and the sentence; the kiosk shows exactly those, and with no usable answer
the same text as the "didn't connect" entry; the title fits on one line of the 600 px dialog at 800x480 (Chromium);
the raw helper output never reaches the page, only a code goes to the log. MF_SHOTS=<dir> saves the dialogs."""
import base64
import json
import os
import re
from pathlib import Path

import pytest

from test_frontend_browser import BROWSER, evaluate, net, page, server  # noqa: F401  (fixtures)

ROOT = Path(__file__).resolve().parents[1]
LOCAL = {"REMOTE_ADDR": "127.0.0.1"}
NOT_FOUND = ("Developer Wi-Fi not found", "The frame is back on its own Wi-Fi. Phones can connect as usual.")
INTERNAL = ("home", "hotspot", "polkit", "helper", "networkmanager", "nmcli", "profile", "dev mode", "netmode",
            "sudo", "exit", "error", "unit", "ssid", "rc ")
# script for DEV_MODE_CMD -> expected code
CASES = {
    "no-profile": "echo 'Error: no matching NetworkManager Wi-Fi profile (SECRET-SSID)'; exit 1",
    "not-connected": "echo 'error: home Wi-Fi SECRET-SSID did not connect; hotspot kept on'; exit 1",
    "busy": "exit 3",
    "not-permitted": "echo 'Failed to start memoryframe-devtemp.service: Access denied' >&2; exit 4",
    "timeout": "sleep 5",
    "no-helper": None,
}


def _token(client):
    html = client.get("/", environ_base=LOCAL).get_data(as_text=True)
    return html.split('name="mf-frame-token" content="', 1)[1].split('"', 1)[0]


def _cmd(mf_app, code, tmp_path):
    if code == "no-helper":
        mf_app.config["DEV_MODE_CMD"] = [str(tmp_path / "missing-helper")]
    else:
        mf_app.config["DEV_MODE_CMD"] = ["sh", "-c", CASES[code]]
    if code == "timeout":
        mf_app.config["DEV_MODE_TIMEOUT"] = 0.5


def test_wrens_copy_for_the_missing_profile():
    from memoryframe.display import DEV_WIFI_FAIL
    assert DEV_WIFI_FAIL["no-profile"] == NOT_FOUND


def test_every_entry_is_a_titled_plain_sentence():
    from memoryframe.display import DEV_WIFI_FAIL
    assert set(DEV_WIFI_FAIL) == set(CASES)
    for code, (title, body) in DEV_WIFI_FAIL.items():
        assert title.startswith("Developer Wi-Fi ") and len(title) <= 32 and not title.endswith("."), code
        assert re.fullmatch(r"[A-Z][^.]*\.( [A-Z][^.]*\.)*", body), (code, body)      # capitalised full sentences
        low = (title + " " + body).lower()
        for bad in INTERNAL:
            assert bad not in low, (code, bad)
        assert "\u2014" not in title and " - " not in title                         # no dash to wrap at


@pytest.mark.parametrize("code", list(CASES))
def test_endpoint_maps_each_failure_and_never_returns_the_raw_output(client, mf_app, tmp_path, code, caplog):
    from memoryframe.display import DEV_WIFI_FAIL
    _cmd(mf_app, code, tmp_path)
    with caplog.at_level("WARNING"):
        r = client.post("/frame/dev-mode", headers={"X-Frame-Token": _token(client)}, environ_base=LOCAL)
    j = r.get_json()
    assert r.status_code == 502 and j == {"ok": False, "code": code, "title": DEV_WIFI_FAIL[code][0],
                                           "reason": DEV_WIFI_FAIL[code][1]}
    assert "SECRET" not in r.get_data(as_text=True)
    logs = [m for m in caplog.messages if "dev mode switch failed" in m]
    assert logs and logs[-1].endswith(": " + code) and "SECRET" not in " ".join(caplog.messages)


def test_pairing_js_fallback_is_the_same_text_as_display_py():
    from memoryframe.display import DEV_WIFI_FAIL
    js = (ROOT / "static" / "pairing.js").read_text()
    title, body = DEV_WIFI_FAIL["not-connected"]
    assert f'|| "{title}"' in js and f'"{body}"' in js
    for old in ("hotspot kept on", "Couldn't join home Wi-Fi", "unknown error", "no answer from the frame"):
        assert old not in js
    py = (ROOT / "memoryframe" / "display.py").read_text()
    dev = py[py.index("def dev_mode"):py.index("#99: Reset the frame")]
    assert "no saved home Wi-Fi" not in py and "polkit rule missing" not in dev and '"timed out"' not in dev


def test_runbook_quotes_the_new_copy():
    rb = (ROOT / "docs" / "runbooks" / "MF-36-product-mode.md").read_text()
    assert "hotspot kept on" not in rb and "Developer Wi-Fi not found" in rb


# ---------------- Chromium, 800x480 ----------------

def _open(page):
    page.pump(2.5)
    evaluate(page, "document.getElementById('devModeOverlay').classList.add('visible');"
                   "document.getElementById('devModeAsk').hidden=false;")
    evaluate(page, "document.getElementById('devModeConfirm').click()")


def _wait_after(page):
    for _ in range(60):
        if evaluate(page, "!document.getElementById('devModeAfter').hidden"):
            return
        page.pump(0.1)
    raise AssertionError("no result")


def _dialog(page):
    return json.loads(evaluate(page, """JSON.stringify((()=>{
        const t = document.getElementById('devModeStatus'), b = document.getElementById('devModeDetail');
        const r = document.createRange(); r.selectNodeContents(t);
        const lines = new Set([...r.getClientRects()].map(x => Math.round(x.top))).size;
        return {title: t.textContent, body: b.textContent, lines: lines,
                fits: t.scrollWidth <= t.clientWidth, ok: document.getElementById('devModeOk').textContent,
                vw: innerWidth, vh: innerHeight};
    })())"""))


def _shot(page, name):
    out = os.environ.get("MF_SHOTS")
    if out:
        os.makedirs(out, exist_ok=True)
        png = page.call("Page.captureScreenshot", {"format": "png"})["result"]["data"]
        Path(out, name).write_bytes(base64.b64decode(png))


@pytest.mark.skipif(BROWSER is None, reason="no Chromium/Chrome installed")
@pytest.mark.parametrize("code", list(CASES))
def test_dialog_shows_the_plain_copy_with_a_one_line_title(page, server, mf_app, net, tmp_path, code):
    from memoryframe.display import DEV_WIFI_FAIL
    _cmd(mf_app, code, tmp_path)
    _open(page)
    _wait_after(page)
    d = _dialog(page)
    assert (d["vw"], d["vh"]) == (800, 480)
    assert (d["title"], d["body"]) == DEV_WIFI_FAIL[code] and d["ok"] == "OK"
    assert d["lines"] == 1 and d["fits"], d                                            # one line, no overflow
    page.pump(0.4)
    _shot(page, f"devwifi-fail-{code}.png")
    assert page.exceptions() == []


@pytest.mark.skipif(BROWSER is None, reason="no Chromium/Chrome installed")
@pytest.mark.parametrize("how", ["no-answer", "not-json", "raw-reason"])
def test_dialog_falls_back_to_the_same_plain_copy(page, server, mf_app, net, how):
    from memoryframe.display import DEV_WIFI_FAIL
    page.pump(2.5)
    fake = {"no-answer": "Promise.reject(new TypeError('Failed to fetch'))",
            "not-json": "Promise.resolve(new Response('<html>', {status: 502}))",
            "raw-reason": "Promise.resolve(new Response(JSON.stringify({ok:false, reason:'no saved home Wi-Fi',"
                          " title:'hotspot kept on'}), {status: 502}))"}[how]
    evaluate(page, f"window.fetch = () => {fake};")
    _open(page)
    _wait_after(page)
    d = _dialog(page)
    assert (d["title"], d["body"]) == DEV_WIFI_FAIL["not-connected"] and d["lines"] == 1
    if how == "no-answer":
        _shot(page, "devwifi-fail-no-answer.png")
