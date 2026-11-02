"""#126: the home Library preview shows a not-ready video as the Photos tile (never a broken thumbnail), and the
"See all" link names what is in the Library (Wren's wording, admin.see_all_label)."""
import html as htmlmod
import re
import uuid
from pathlib import Path

import pytest

from memoryframe import admin, db as mfdb

ROOT = Path(__file__).resolve().parent.parent
PNG = (ROOT / "tests" / "fixture_astronaut.jpg").read_bytes()


def body(c, url="/admin/"):
    r = c.get(url)
    assert r.status_code == 200
    return htmlmod.unescape(r.get_data(as_text=True))


def video(app, state, pct=0, cancel=0, resumed=0):
    conn = mfdb.connect(app.config["DB_PATH"])
    cur = conn.execute("INSERT INTO media (stored_name, original_name, kind, sha256, size_bytes, source, created_at, state,"
                       " progress_pct, cancel_requested, resumed, duration_ms) VALUES (?, 'clip.mp4', 'video', ?, 1,"
                       " 'upload', ?, ?, ?, ?, ?, 12000)",
                       (uuid.uuid4().hex + ".mp4", uuid.uuid4().hex, mfdb.now(), state, pct, cancel, resumed))
    conn.commit()
    vid = cur.lastrowid
    conn.close()
    return vid


def grid(h):
    return h[h.index('id="homeGrid"'):h.index("</ul>", h.index('id="homeGrid"'))]


def see_all(h):
    m = re.search(r'<a class="btn btn-neutral block see-all" href="/admin/library"><span>([^<]*)</span>', h)
    return m and m.group(1)


@pytest.mark.parametrize("photos,videos,text", [
    (0, 0, None), (1, 0, "See 1 photo"), (0, 1, "See 1 video"), (2, 0, "See all 2 photos"), (12, 0, "See all 12 photos"),
    (0, 2, "See all 2 videos"), (1, 1, "See all 2 photos and videos"), (1, 2, "See all 3 photos and videos"),
    (5, 1, "See all 6 photos and videos")])
def test_see_all_wording(photos, videos, text):
    assert admin.see_all_label(photos, videos) == text


@pytest.mark.parametrize("photos,videos,text", [
    (0, 0, None), (1, 0, "See 1 photo"), (3, 0, "See all 3 photos"), (0, 1, "See 1 video"), (0, 2, "See all 2 videos"),
    (1, 2, "See all 3 photos and videos")])
def test_home_link_counts_everything_including_preparing_videos(paired, lib, mf_app, photos, videos, text):
    for i in range(photos):
        lib.add(f"pic{i}.jpg", PNG + bytes([i]))
    for i in range(videos):
        video(mf_app, "processing" if i == 0 else "ready", pct=40)
    h = body(paired)
    assert see_all(h) == text
    if text is None:                                   # empty Library: no link, the empty card as before
        assert "see-all" not in h and "No photos yet" in h


def test_preparing_video_on_home_is_the_photos_tile_without_the_x(paired, lib, mf_app):
    lib.add("pic.jpg", PNG)
    vid = video(mf_app, "processing", pct=38)
    h = body(paired)
    g = grid(h)
    assert f"/api/v1/media/{vid}/thumb" not in h                # never a broken image
    li = g[g.index(f'data-id="{vid}"'):]
    li = li[:li.index("</li>")]
    assert 'data-state="processing"' in li
    assert '<a class="tile vtile" href="/admin/library" aria-label="Video preparing, 38%">' in li
    assert 'class="ring"' in li and '<span class="lbl">Preparing 38%</span>' in li and 'stroke-dashoffset="66.2"' in li
    assert "vx" not in li and "<button" not in li and "Cancel" not in li
    assert "video.css" in h and "vtile.js" in h and "home.js" in h        # same styles and poller as Photos


def test_waiting_and_cancelling_videos_on_home(paired, mf_app):
    a, b = video(mf_app, "queued"), video(mf_app, "processing", pct=10, cancel=1)
    g = grid(body(paired))
    assert f'data-id="{a}" data-kind="video" data-state="queued"' in g and 'aria-label="Video waiting"' in g
    assert f'data-id="{b}" data-kind="video" data-state="cancelling"' in g and 'aria-label="Video cancelling"' in g
    assert "/thumb" not in g and "<button" not in g


def test_failed_video_on_home_shows_couldnt_add_without_buttons(paired, mf_app):
    vid = video(mf_app, "failed")
    g = grid(body(paired))
    assert f'data-state="failed"' in g and "Couldn't add" in g and f"/api/v1/media/{vid}/thumb" not in g
    assert '<a class="tile vtile" href="/admin/library" aria-label="Video couldn\'t be added">' in g
    assert "<button" not in g and "vretry" not in g and "vremove" not in g


def test_ready_video_on_home_shows_its_thumbnail(paired, mf_app):
    vid = video(mf_app, "ready")
    g = grid(body(paired))
    assert f'<a href="/admin/library" aria-label="Video 1"><img src="/api/v1/media/{vid}/thumb"' in g


def test_photos_only_home_loads_no_video_assets(paired, lib):
    lib.add("pic.jpg", PNG)
    h = body(paired)
    assert "video.css" not in h and "vtile.js" not in h


def test_photos_page_uses_the_same_partial(paired, mf_app):
    vid = video(mf_app, "processing", pct=38)
    h = body(paired, "/admin/library")
    assert 'aria-label="Video preparing, 38%"' in h and '<span class="lbl">Preparing 38%</span>' in h
    assert f'class="vx" data-id="{vid}"' in h and "vtile.js" in h
    tpl = (ROOT / "templates")
    assert "Preparing {{ pct }}%" not in (tpl / "library.html").read_text() + (tpl / "admin_home.html").read_text()
    js = (ROOT / "static" / "library.js").read_text() + (ROOT / "static" / "home.js").read_text()
    assert '"Preparing "' not in js and "function ring" not in js and '"/status"' not in js     # only in vtile.js
