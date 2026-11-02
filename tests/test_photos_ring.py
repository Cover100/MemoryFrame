"""Photos 13d preparing ring: the tile ring must not pick up the 07 Wi-Fi ring's border, margin or ::before arc."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def flat(name):
    return re.sub(r"/\*.*?\*/", "", (ROOT / "static" / name).read_text(), flags=re.S)


def test_tile_ring_drops_the_wifi_ring_border():
    css = flat("video.css")
    assert re.search(r"\.mf-phone \.tile \.ring \{ border: 0; margin: 0; border-radius: 0; \}", css)
    assert ".mf-phone .tile .ring::before { content: none; }" in css


def test_wifi_ring_is_unchanged():
    css = flat("phone.css")
    assert re.search(r"\.mf-phone \.ring \{ width: 120px; height: 120px; border-radius: 50%; border: 8px solid", css)
    assert "content: none" not in css.split(".mf-phone .ring::before", 1)[1].split("}", 1)[0]


# Home's Upload button uses the Photos page's label rule (admin.videos_on + templates/_upload_button.html).
import html as htmlmod  # noqa: E402

import pytest  # noqa: E402


def _button(c, url):
    h = htmlmod.unescape(c.get(url).get_data(as_text=True))
    i = h.index('href="/admin/upload">')
    return h[i:h.index("</a>", i)].split("\xa0")[-1].strip()


@pytest.mark.parametrize("on,label", [(True, "Upload"), (False, "Upload photos")])
def test_home_upload_button_matches_photos(paired, mf_app, on, label):
    mf_app.config["VIDEOS"] = on
    assert _button(paired, "/admin/") == _button(paired, "/admin/library") == label
