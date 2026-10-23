"""#70 mock 14 (14-SHUTDOWN-FINAL.md): the final #safeScreen restyle, plus the two #74 follow-up tweaks
(mock 04 site-only pairing copy; #pairHint hidden while the brightness pill shows)."""
import json
import re
import time
from pathlib import Path

from memoryframe import display
from test_double_tap import move, press, release
from test_frontend_browser import evaluate, net, page, server, text, visible  # noqa: F401  (fixtures)

ROOT = Path(__file__).resolve().parents[1]
CSS = (ROOT / "static" / "style.css").read_text()
HTML = (ROOT / "templates" / "index.html").read_text()
LOCAL = {"REMOTE_ADDR": "127.0.0.1"}


def safe_rules():
    """Every #safeScreen rule in style.css (the mock 14 block)."""
    return re.findall(r"(#safeScreen[^{,]*)\{([^}]*)\}", CSS)


def block(html, start, end):
    return html[html.index(start):html.index(end, html.index(start))]


# ---- 1. wording -------------------------------------------------------------------------------

def test_ac1_wording_byte_identical_from_safe_screen_copy(client):
    assert display.SAFE_SCREEN_COPY == {"title": "Shutting down", "sub": "Wait 10 seconds, then unplug",
                                        "note": "Photos are saved on the frame"}
    tpl = block(HTML, '<div id="safeScreen"', '<div id="shuttingDown"')
    for k in ("title", "sub", "note"):
        assert "{{ safe_copy.%s }}" % k in tpl, k
    page_html = client.get("/", environ_base=LOCAL).get_data(as_text=True)
    out = block(page_html, '<div id="safeScreen"', '<div id="shuttingDown"')
    for v in display.SAFE_SCREEN_COPY.values():
        assert v in out, v


# ---- 2/3. static, no effects ------------------------------------------------------------------

def test_ac2_ac3_spinner_gone_no_animation_or_effects_in_new_rules():
    tpl = block(HTML, '<div id="safeScreen"', '<div id="shuttingDown"')
    assert "mf-spinner" not in tpl and "safeSpin" not in tpl and "safeSpin" not in CSS
    rules = safe_rules()
    assert {sel.strip() for sel, _ in rules} >= {
        "#safeScreen", "#safeScreen[hidden]", "#safeScreen .safeCard", "#safeScreen .safePlug",
        "#safeScreen .safeText", "#safeScreen .safeTitle", "#safeScreen .safeRule", "#safeScreen .safeSub",
        "#safeScreen .safeClock", "#safeScreen .safeNote"}
    for sel, body in rules:
        for bad in ("animation", "transition", "filter", "box-shadow", "text-shadow", "gradient"):
            assert bad not in body, (sel, bad)
    assert "<animate" not in tpl and "<set " not in tpl               # no SMIL in the SVGs either
    assert tpl.count("currentColor") >= 4 and "#" not in re.sub(r'id="[^"]*"', "", tpl.split(">", 1)[1])  # no colours
    # #shuttingDown (Don't unplug yet) keeps its spinner, unchanged
    sd = block(HTML, '<div id="shuttingDown"', "</div>\n    </div>")
    assert '<div class="mf-spinner" aria-hidden="true"></div>' in sd and "Don't unplug yet" in sd


def show_safe(page):
    page.pump(2.5)
    evaluate(page, "document.getElementById('safeScreen').hidden=false;document.body.classList.add('mf-safe-final')")
    page.pump(0.3)


GEOM = """(()=>{const r=e=>{const b=document.querySelector(e).getBoundingClientRect();
return [Math.round(b.left),Math.round(b.top),Math.round(b.right),Math.round(b.bottom)];};
const s=e=>getComputedStyle(document.querySelector(e));const sub=document.querySelector('#safeScreen .safeSub');
const card=document.querySelector('#safeScreen .safeCard');
const clipped=[...document.querySelectorAll('#safeScreen *')].filter(e=>e.scrollWidth>e.clientWidth+1&&e.clientWidth>0).map(e=>e.className.baseVal??e.className);
return JSON.stringify({screen:r('#safeScreen'),card:r('#safeScreen .safeCard'),plug:r('#safeScreen .safePlug'),
text:r('#safeScreen .safeText'),title:r('#safeScreen .safeTitle'),sub:r('#safeScreen .safeSub'),
clock:r('#safeScreen .safeClock'),note:r('#safeScreen .safeNote'),rule:r('#safeScreen .safeRule'),
subLines:Math.round(sub.getBoundingClientRect().height/parseFloat(s('#safeScreen .safeSub').lineHeight)),
screenBg:s('#safeScreen').backgroundColor,cardBg:s('#safeScreen .safeCard').backgroundColor,
border:[s('#safeScreen .safeCard').borderTopWidth,s('#safeScreen .safeCard').borderTopColor,s('#safeScreen .safeCard').borderTopStyle],
align:s('#safeScreen .safeText').textAlign,plugColor:s('#safeScreen .safePlug').color,
titleS:[s('#safeScreen .safeTitle').fontSize,s('#safeScreen .safeTitle').color,s('#safeScreen .safeTitle').fontFamily],
subS:[s('#safeScreen .safeSub').fontSize,s('#safeScreen .safeSub').fontWeight,s('#safeScreen .safeSub').color],
noteS:[s('#safeScreen .safeNote').fontSize,s('#safeScreen .safeNote').color],
anims:document.getAnimations().length,spinners:document.querySelectorAll('#safeScreen .mf-spinner').length,
clipped,cardOverflow:card.scrollHeight>card.clientHeight+1||card.scrollWidth>card.clientWidth+1,
fontOk:document.fonts.check('700 44px "MF Frame"')&&document.fonts.check('400 20px "MF Frame"'),
banner:getComputedStyle(document.getElementById('devBanner')).opacity});})()"""


def inside(a, b):
    return a[0] >= b[0] and a[1] >= b[1] and a[2] <= b[2] and a[3] <= b[3]


# ---- 2/4/8. geometry, look and font at 800x480 -------------------------------------------------

def test_ac2_ac4_ac8_layout_matches_mock_at_800x480(page, server, net):
    show_safe(page)
    g = json.loads(evaluate(page, GEOM))
    assert g["anims"] == 0 and g["spinners"] == 0, g                            # AC2
    assert g["screen"] == [0, 0, 800, 480] and g["card"] == [20, 20, 780, 460], g  # 760x440, 20 px ink margin
    assert g["screenBg"] == "rgb(14, 19, 17)" and g["cardBg"] == "rgb(58, 44, 34)"     # ink / walnut-900
    assert g["border"] == ["3px", "rgb(214, 179, 106)", "solid"]                         # 3 px brass
    assert g["plug"][2] - g["plug"][0] == 144 and g["plug"][2] < g["text"][0]           # plug left of text
    assert g["text"][0] - g["plug"][2] == 32                                            # 32 px gap
    assert g["align"] == "left" and g["title"][0] == g["sub"][0] == g["note"][0] == g["text"][0]
    assert g["subLines"] == 1 and g["clock"][2] < g["sub"][2], g                        # sub on ONE line
    left, right = g["plug"][0] - g["card"][0], g["card"][2] - g["text"][2]
    assert abs(left - right) <= 2, g                                                     # group centred
    assert g["text"][2] - g["plug"][0] <= 760 - 2 * 32 - 6, g                              # group fits the card padding
    for k in ("plug", "text", "title", "sub", "clock", "note", "rule"):
        assert inside(g[k], [23, 23, 777, 457]), k                                       # nothing clipped
    assert g["clipped"] == [] and not g["cardOverflow"], g
    assert g["plugColor"] == "rgb(214, 179, 106)"
    assert g["titleS"][:2] == ["44px", "rgb(243, 237, 228)"]                              # linen 44
    assert g["subS"] == ["30px", "700", "rgb(214, 179, 106)"]                            # bold brass 30
    assert g["noteS"] == ["20px", "rgb(201, 187, 168)"]                                  # oat 20
    assert g["titleS"][2].startswith('"MF Frame"') and g["fontOk"], g                     # AC8 Atkinson loaded
    assert page.exceptions() == []


def test_ac8_frame_font_blocks_until_loaded(client):
    css = client.get("/frame-fonts.css", environ_base=LOCAL).get_data(as_text=True)
    assert css.count("font-display: block") == 2
    assert "font-family:var(--mf-font-frame)" in dict((s.strip(), b) for s, b in safe_rules())["#safeScreen"]


# ---- 5. obviously different from the earlier screen -------------------------------------------

def lum(rgb):
    def ch(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (int(x) for x in re.findall(r"\d+", rgb)[:3])
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def test_ac5_distinct_from_dont_unplug_yet_screen(page, server, net):
    page.pump(2.5)
    q = ("(()=>{const el=document.getElementById('%s');el.hidden=false;const c=document.querySelector('%s');"
         "const s=getComputedStyle(c),b=c.getBoundingClientRect();const t=getComputedStyle(document.querySelector('%s'));"
         "const r=[s.backgroundColor,Math.round(b.width*b.height),t.textAlign,getComputedStyle(el).flexDirection,getComputedStyle(el).alignItems];el.hidden=true;return JSON.stringify(r);})()")
    early = json.loads(evaluate(page, q % ("shuttingDown", "#shuttingDown", "#shuttingDown .sdTitle")))
    final = json.loads(evaluate(page, q % ("safeScreen", "#safeScreen .safeCard", "#safeScreen .safeText")))
    assert early[0] == "rgb(14, 19, 17)" and final[0] == "rgb(58, 44, 34)"   # black screen vs brown card
    assert final[1] >= 0.85 * 800 * 480                                         # the brown fills the panel
    r, g, b = (int(x) for x in re.findall(r"\d+", final[0])[:3])
    assert r - b >= 20 and lum(final[0]) > 4 * lum(early[0])                    # warm brown vs near-black
    assert early[3] == "column" and early[4] == "center"                        # earlier: stacked, centred
    assert final[2] == "left"                                                   # final: icon left, text left-aligned


# ---- 7. dev banner hidden over the safe screen -------------------------------------------------

def test_ac7_dev_banner_hidden_while_safe_screen_shown(page, server, net):
    page.pump(2.5)
    evaluate(page, "(()=>{const b=document.getElementById('devBanner');b.hidden=false;b.textContent='Dev mode';})()")
    page.pump(0.3)
    assert evaluate(page, "getComputedStyle(document.getElementById('devBanner')).opacity") == "1"
    evaluate(page, "document.getElementById('safeScreen').hidden=false")
    page.pump(0.5)
    assert evaluate(page, "getComputedStyle(document.getElementById('devBanner')).opacity") == "0"


# ---- #74 follow-up tweaks ----------------------------------------------------------------------

def test_mock04_site_only_pairing_copy(page, server, mf_app, net, monkeypatch):
    net.update(mode="lan", lan_ip="192.168.50.124", ips=["192.168.50.124"])
    monkeypatch.setenv("MEMORYFRAME_PORTS", "5000")
    page.pump(2.5)
    evaluate(page, "memoryFramePairing.openMenu()")
    page.pump(0.5)
    evaluate(page, "document.getElementById('menuPair').click()")
    page.pump(1.0)
    assert not visible(page, "wifiStep")
    assert text(page, "pairTitle") == "Pair your phone" and text(page, "siteTitle") == "Scan with your phone"
    assert "Pair a phone" not in text(page, "pairBox") and "Open site" not in text(page, "pairBox")


def test_pair_hint_hidden_while_brightness_pill_shows(page, server, net, backlight):
    net.update(mode="lan", lan_ip="192.168.50.124", ips=["192.168.50.124"])
    page.pump(2.5)
    op = "getComputedStyle(document.getElementById('pairHint')).opacity"
    assert visible(page, "pairHint") and evaluate(page, op) == "1"
    press(page, 400, 300)
    for i in range(1, 30):
        move(page, 400, 300 - i * 5)
        time.sleep(0.02)
    assert visible(page, "brightnessOverlay")
    assert evaluate(page, op) == "0"                                   # mid-drag: no overlapping pills
    release(page, 400, 155)
    page.pump(0.3)
    assert evaluate(page, op) == "0"                                   # pill still up after lift
    page.pump(2.0)                                                     # BRIGHTNESS_DISPLAY_TIME 1.5 s
    assert not visible(page, "brightnessOverlay") and evaluate(page, op) == "1"
    assert (backlight / "brightness").read_text().strip() != "100"
    assert page.exceptions() == []


def test_pair_hint_hide_rule_is_opacity_only():
    assert re.search(r"body\.mf-brightness-active #pairHint\{opacity:0\}", CSS)
