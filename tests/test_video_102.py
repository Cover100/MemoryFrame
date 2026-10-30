"""#102: videos. ffmpeg/ffprobe are always mocked here (no real conversion in the test suite)."""
import io
import json
import os
import subprocess

import pytest

from memoryframe import db as mfdb
from memoryframe import video

PHONE = {"REMOTE_ADDR": "10.42.0.23"}
MP4 = b"\0\0\0\x18ftypmp42" + b"\0" * 2000
PHOTO_JSON = None


def stream(w=1920, h=1080, codec="h264", rotation=None, duration="12.5", fps="30/1", **extra):
    s = {"codec_type": "video", "codec_name": codec, "width": w, "height": h, "avg_frame_rate": fps,
         "duration": duration}
    if rotation is not None:
        s["side_data_list"] = [{"side_data_type": "Display Matrix", "rotation": rotation}]
    s.update(extra)
    return s


class FakeFF:
    """Stands in for subprocess.run: answers ffprobe with `info`, and 'converts' by writing the output file."""

    def __init__(self, streams=None, fail_convert=False):
        self.info = {"streams": streams if streams is not None else [stream()], "format": {"duration": "12.5"}}
        self.calls = []
        self.fail_convert = fail_convert
        self.out = None

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        prog = next(c for c in cmd if c in ("ffprobe", "ffmpeg"))
        if prog == "ffprobe":
            info = self.info
            if cmd[-1].endswith(".mp4.part"):          # the output check
                info = self.out if self.out is not None else {"streams": [
                    {"codec_type": "video", "codec_name": "h264", "width": 800, "height": 480, "duration": "12.5"}]}
            return subprocess.CompletedProcess(cmd, 0, json.dumps(info).encode(), b"")
        if self.fail_convert:
            return subprocess.CompletedProcess(cmd, 1, b"", b"boom")
        out = cmd[-1][len("file:"):]
        if out.endswith(".jpg.tmp"):
            from PIL import Image
            Image.new("RGB", (800, 480), (30, 60, 90)).save(out, "JPEG")
        else:
            with open(out, "wb") as f:
                f.write(b"\0\0\0\x18ftypisom converted")
        return subprocess.CompletedProcess(cmd, 0, b"", b"")


@pytest.fixture
def ff(monkeypatch):
    fake = FakeFF()
    monkeypatch.setattr(video.subprocess, "run", fake)
    monkeypatch.setattr(video, "available", lambda: True)
    return fake


@pytest.fixture
def vapp(mf_app):
    mf_app.config["VIDEOS"] = True
    return mf_app


def post(client, data=MP4, name="clip.mp4"):
    return client.post("/api/v1/media", data={"file": (io.BytesIO(data), name)},
                       content_type="multipart/form-data", environ_base=PHONE)


def rows(app):
    conn = mfdb.connect(app.config["DB_PATH"])
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM media ORDER BY id")]
    finally:
        conn.close()


def work(app):
    conn = mfdb.connect(app.config["DB_PATH"])
    try:
        return video.work_one(conn, app.config["DATA_DIR"], runner=video.subprocess.run)
    finally:
        conn.close()


# ---------------- probe / filter graph ----------------

@pytest.mark.parametrize("rot,expect", [(-90, 90), (90, 270), (180, 180), (-180, 180), (0, 0), (None, 0)])
def test_rotation_from_display_matrix(rot, expect):
    assert video._rotation(stream(rotation=rot)) == expect


def test_rotation_from_old_rotate_tag():
    assert video._rotation({"tags": {"rotate": "90"}}) == 90


def test_s22_portrait_clip_is_rotated_then_blur_filled(tmp_path):
    """The user's S22: 2320x1080 coded, rotate 90 -> displayed portrait 1080x2320 -> B2 blur-fill."""
    info = video.probe(str(tmp_path / "x"), runner=FakeFF([stream(2320, 1080, "hevc", rotation=-90)]))
    assert (info["width"], info["height"], info["rotation"]) == (1080, 2320, 90)
    g = video.filter_graph(info)
    assert g.startswith("[0:v:0]split=2")           # framed from the UPRIGHT size (autorotate runs first)
    assert "gblur" in g and "overlay" in g and "crop=800:480" not in g
    cmd = video.convert_cmd("in", "out", info)
    assert cmd[cmd.index("-hwaccel") + 1] == "drm"
    assert "-noautorotate" not in cmd          # autorotate (the default) stays on


def test_landscape_is_cover_cropped_and_h264_is_software_decoded(tmp_path):
    info = video.probe(str(tmp_path / "x"), runner=FakeFF([stream(1920, 1080)]))
    g = video.filter_graph(info)
    assert "scale=800:480:force_original_aspect_ratio=increase,crop=800:480" in g and "gblur" not in g
    cmd = video.convert_cmd("in", "out", info)
    assert "-hwaccel" not in cmd and "h264_v4l2m2m" not in cmd


def test_blurfill_numbers_match_photo_b2():
    from memoryframe import imaging
    info = {"width": 1080, "height": 1920, "rotation": 0, "codec": "h264", "duration": 5}
    g = video.filter_graph(info)
    k = max(1.0, min(imaging.TRIM, imaging.MAX_SHARP_W * 800 / (1080 / 1920 * 480)))
    assert f"scale=200:120" in g                                     # background at 1/4 size
    assert f"sigma={imaging.BG_BLUR * 0.8 / imaging.BG_SCALE:g}" in g
    assert f"colorchannelmixer=rr={1 - imaging.BG_DIM:g}" in g
    assert f":{round(480 * k)}:flags=lanczos" in g                   # 15 % trim (k = TRIM here)


def test_convert_cmd_is_locked_down():
    info = {"width": 1920, "height": 1080, "rotation": 0, "codec": "h264", "duration": 400}
    cmd = video.convert_cmd("in", "out", info)
    s = " ".join(cmd)
    for part in ("-protocol_whitelist file", "-format_whitelist", "-an", "-sn", "-dn", "-map_metadata -1",
                 "-map_chapters -1", "-fflags +bitexact", "-t 30", "-fs 60M", "libx264", "-crf 26",
                 "-maxrate 2.5M", "-bufsize 5M", "-preset veryfast", "-g 60", "-movflags +faststart",
                 "-threads 2", "format=yuv420p"):
        assert part in s, part
    assert video.timeout_for(info) == 300 and video.timeout_for({"duration": 2}) == 60


def test_prefix_is_low_priority(monkeypatch):
    monkeypatch.setattr(video.shutil, "which", lambda n: "/usr/bin/" + n)
    monkeypatch.setattr(video.os, "cpu_count", lambda: 4)
    p = video._prefix(60)
    assert p[:4] == ["timeout", "-k", "10", "60"]
    assert p[4:] == ["nice", "-n", "19", "ionice", "-c", "3", "taskset", "-c", "2,3"]


@pytest.mark.parametrize("streams", [
    [],                                                              # no video
    [stream(), stream()],                                            # two video streams
    [stream(), {"codec_type": "subtitle", "codec_name": "mov_text"}],
    [stream(), {"codec_type": "data", "codec_name": "bin_data"}],
    [stream(5000, 3000)],                                            # over 4K
    [stream(fps="240/1")],
    [stream(codec="prores")],
    [stream(duration="0")],
])
def test_probe_refuses(tmp_path, streams):
    with pytest.raises(video.VideoError):
        video.probe(str(tmp_path / "x"), runner=FakeFF(streams))


def test_probe_refuses_over_ten_minutes(tmp_path):
    with pytest.raises(video.VideoError, match="10 minutes"):
        video.probe(str(tmp_path / "x"), runner=FakeFF([stream(duration="601")]))


def test_audio_stream_is_allowed_and_dropped(tmp_path):
    info = video.probe(str(tmp_path / "x"), runner=FakeFF([stream(), {"codec_type": "audio", "codec_name": "aac"}]))
    assert "-an" in video.convert_cmd("i", "o", info)


# ---------------- upload -> queue -> ready ----------------

def test_without_videos_enabled_it_is_still_coming_later(mf_app, paired):
    r = post(paired)
    assert r.status_code == 415 and "coming later" in r.get_json()["error"]


def test_upload_queues_then_worker_makes_it_ready(vapp, paired, client, ff):
    r = post(paired)
    assert r.status_code == 202, r.get_json()
    j = r.get_json()
    assert j["status"] == "queued" and j["kind"] == "video" and j["name"] == "clip.mp4"
    row = rows(vapp)[0]
    assert row["state"] == "queued" and row["kind"] == "video"
    d = vapp.config["DATA_DIR"]
    assert os.path.exists(video.input_path(d, row["stored_name"]))       # on the card, under DATA_DIR
    assert client.get("/media").get_json() == []                        # not on the frame yet
    assert client.get("/uploads/" + row["stored_name"]).status_code == 404
    assert paired.post(f"/api/v1/media/{row['id']}/show-now", environ_base=PHONE).status_code == 409

    assert work(vapp) == row["stored_name"]
    row = rows(vapp)[0]
    assert row["state"] == "ready" and row["duration_ms"] == 12500
    assert not os.path.exists(video.input_path(d, row["stored_name"]))  # the upload is dropped once ready
    assert os.listdir(os.path.join(d, "tmp", "video-out")) == []
    assert client.get("/media").get_json() == [{"type": "video", "filename": row["stored_name"],
                                                 "duration_ms": 12500}]
    r = client.get("/uploads/" + row["stored_name"])
    assert r.status_code == 200 and r.mimetype == "video/mp4"
    t = paired.get(f"/api/v1/media/{row['id']}/thumb", environ_base=PHONE)
    assert t.status_code == 200 and t.mimetype == "image/jpeg"
    lst = paired.get("/api/v1/media", environ_base=PHONE).get_json()
    assert lst[0]["state"] == "ready" and lst[0]["duration_ms"] == 12500


def test_duplicate_video(vapp, paired, ff):
    assert post(paired).status_code == 202
    r = post(paired, name="again.mp4")
    assert r.status_code == 200 and r.get_json()["status"] == "duplicate"
    assert "video" in r.get_json()["message"] and len(rows(vapp)) == 1


def test_unplayable_video_is_refused_at_upload(vapp, paired, ff):
    ff.info["streams"] = [stream(codec="prores")]
    r = post(paired)
    assert r.status_code == 415 and rows(vapp) == []


def test_extension_alone_is_not_a_video(vapp, paired, ff):
    r = post(paired, b"not a video at all" * 10, "clip.mp4")
    assert r.status_code == 415 and rows(vapp) == []


def test_reserve_is_respected(vapp, paired, ff, monkeypatch):
    monkeypatch.setattr(video, "space_ok", lambda *a: False)
    r = post(paired)
    assert r.status_code == 507 and rows(vapp) == []


def test_space_ok_needs_three_times_above_reserve(monkeypatch, tmp_path):
    from collections import namedtuple
    du = namedtuple("du", "total used free")
    monkeypatch.setattr(video.shutil, "disk_usage", lambda p: du(10**10, 0, 1000))
    assert video.space_ok(str(tmp_path), 100, 300)
    assert not video.space_ok(str(tmp_path), 100, 301)


def test_350mb_cap(vapp, paired, ff, monkeypatch):
    monkeypatch.setattr(video, "MAX_UPLOAD_BYTES", 1000)
    r = post(paired, MP4 + b"\0" * (2 * 1024 * 1024))
    assert r.status_code == 413 and "350 MB" in r.get_json()["error"]


def test_photo_limit_unchanged_when_videos_on(vapp, paired, ff):
    from PIL import Image
    vapp.config["MAX_CONTENT_LENGTH"] = 1024 * 1024
    b = io.BytesIO()
    Image.new("RGB", (64, 64)).save(b, "JPEG")
    r = post(paired, b.getvalue() + b"\0" * (2 * 1024 * 1024), "big.jpg")
    assert r.status_code == 413 and "1 MB per photo" in r.get_json()["error"]


def test_failed_then_retry_then_remove(vapp, paired, ff):
    ff.fail_convert = True
    post(paired)
    work(vapp)
    row = rows(vapp)[0]
    d = vapp.config["DATA_DIR"]
    assert row["state"] == "failed"
    assert not os.path.exists(os.path.join(d, "originals", row["stored_name"]))
    assert os.path.exists(video.input_path(d, row["stored_name"]))       # kept so Retry works
    r = paired.post(f"/api/v1/media/{row['id']}/retry", environ_base=PHONE)
    assert r.status_code == 200 and rows(vapp)[0]["state"] == "queued"
    ff.fail_convert = False
    work(vapp)
    assert rows(vapp)[0]["state"] == "ready"
    assert paired.post(f"/api/v1/media/{row['id']}/retry", environ_base=PHONE).status_code == 409
    assert paired.delete(f"/api/v1/media/{row['id']}", environ_base=PHONE).status_code == 200
    assert rows(vapp) == []
    assert os.listdir(os.path.join(d, "originals")) == []


def test_remove_while_queued_drops_the_upload(vapp, paired, ff):
    post(paired)
    row = rows(vapp)[0]
    assert paired.delete(f"/api/v1/media/{row['id']}", environ_base=PHONE).status_code == 200
    assert os.listdir(os.path.join(vapp.config["DATA_DIR"], "tmp", "video-in")) == []
    assert work(vapp) is None


def test_power_cut_recovery(vapp, paired, ff, monkeypatch):
    post(paired)
    post(paired, MP4 + b"different", "two.mp4")
    a, b = rows(vapp)
    d = vapp.config["DATA_DIR"]
    conn = mfdb.connect(vapp.config["DB_PATH"])
    conn.execute("UPDATE media SET state = 'processing'")
    conn.commit()
    os.remove(video.input_path(d, b["stored_name"]))
    open(video.part_path(d, a["stored_name"]), "wb").write(b"half")
    open(os.path.join(d, "tmp", "video-in", "f" * 32), "wb").write(b"orphan")
    monkeypatch.setattr(video.storage if hasattr(video, "storage") else __import__("memoryframe.storage").storage,
                        "settled", lambda p: True)
    assert video.recover(conn, d) == (1, 1, 2)
    conn.close()
    st = {r["stored_name"]: r["state"] for r in rows(vapp)}
    assert st == {a["stored_name"]: "queued", b["stored_name"]: "failed"}
    assert os.listdir(os.path.join(d, "tmp", "video-out")) == []


def test_migration_from_schema_1(tmp_path):
    p = str(tmp_path / "old.db")
    conn = mfdb.connect(p)
    conn.executescript("""
        CREATE TABLE schema_version (version INTEGER NOT NULL); INSERT INTO schema_version VALUES (1);
        CREATE TABLE media (id INTEGER PRIMARY KEY AUTOINCREMENT, stored_name TEXT NOT NULL UNIQUE,
          original_name TEXT NOT NULL, kind TEXT NOT NULL, sha256 TEXT NOT NULL, size_bytes INTEGER NOT NULL,
          source TEXT NOT NULL, hidden INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL);
        INSERT INTO media (stored_name, original_name, kind, sha256, size_bytes, source, created_at)
          VALUES ('a.jpg', 'a.jpg', 'image', 'x', 1, 'upload', 0);""")
    conn.commit()
    conn.close()
    mfdb.init_db(p)
    mfdb.init_db(p)                                                  # idempotent
    conn = mfdb.connect(p)
    r = dict(conn.execute("SELECT * FROM media").fetchone())
    assert r["state"] == "ready" and r["duration_ms"] is None
    assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == 2


def test_photo_media_json_unchanged(mf_app, lib, client):
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", (10, 10)).save(b, "JPEG")
    stored = lib.add("p.jpg", b.getvalue())
    assert client.get("/media").get_json() == [{"type": "image", "filename": stored}]


def test_upload_body_is_spooled_on_the_card(mf_app):
    import tempfile
    with mf_app.test_request_context("/api/v1/media", method="POST"):
        from flask import request
        f = request._get_file_stream(10, "application/octet-stream")
        try:
            assert os.path.dirname(f.name if isinstance(f.name, str) else
                                   os.readlink(f"/proc/self/fd/{f.name}")) == os.path.join(
                mf_app.config["DATA_DIR"], "tmp")
        finally:
            f.close()
    assert tempfile is not None


# ---------------- phone pages and frame script ----------------

def test_library_page_with_videos(vapp, paired, ff):
    post(paired)
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", (64, 64)).save(b, "JPEG")
    post(paired, b.getvalue(), "p.jpg")
    h = paired.get("/admin/library", environ_base=PHONE).get_data(as_text=True)
    assert "＋&nbsp; Upload</a>" in h and "Videos play silently, up to 30 seconds" in h
    assert 'id="chipPhotos" aria-pressed="true"' in h and 'id="chipVideos" aria-pressed="true"' in h
    assert "1 photo · 1 video" in h and 'role="status"' in h
    assert 'data-state="queued"' in h and "Waiting" in h
    work(vapp)
    h = paired.get("/admin/library", environ_base=PHONE).get_data(as_text=True)
    assert "▶ 0:13" in h and 'data-state="ready"' in h


def test_failed_tile_has_retry_and_remove(vapp, paired, ff):
    ff.fail_convert = True
    post(paired)
    work(vapp)
    h = paired.get("/admin/library", environ_base=PHONE).get_data(as_text=True)
    assert "Couldn't add" in h and 'class="vretry"' in h and 'class="vremove"' in h
    assert 'aria-label="Retry video"' in h and 'aria-label="Remove video"' in h and h.count("<svg") >= 2   # Wren 63267ba
    assert ">Retry<" not in h and ">Remove<" not in h


def test_upload_page_copy(vapp, paired):
    h = paired.get("/admin/upload", environ_base=PHONE).get_data(as_text=True)
    assert "<title>Upload - Memory Frame" in h and "video/*" in h and "Videos play silently" in h


def test_frame_script_plays_once_loops_short_and_releases():
    src = open(os.path.join(os.path.dirname(__file__), "..", "static", "script.js")).read()
    assert "VIDEO_MIN_MS = 5000" in src and "VIDEO_STALL_MS = 5000" in src
    assert 'videoElement.removeAttribute("src")' in src and "armVideoTimers(token, playMs)" in src


# ---------------- Sage's should-fixes (review on 71ee4c1) ----------------

def test_codec_whitelist_on_every_ffmpeg_call(tmp_path):
    info = {"codec": "hevc", "width": 1920, "height": 1080, "rotation": 0, "duration": 5, "fps": 30}
    cmd = video.convert_cmd("a", "b", info)
    assert cmd[cmd.index("-codec_whitelist") + 1] == video.CODEC_WHITELIST
    assert cmd.index("-codec_whitelist") < cmd.index("-i")
    p = video.poster_cmd("a", "b")
    assert p[p.index("-codec_whitelist") + 1] == "h264" and p.index("-codec_whitelist") < p.index("-i")
    fake = FakeFF()
    video.probe(str(tmp_path / "x"), runner=fake)
    assert "-codec_whitelist" in fake.calls[0]
    assert set(video.CODEC_WHITELIST.split(",")) >= video.CODECS


def test_prlimit_prefix_not_preexec(monkeypatch):
    seen = {}

    def runner(cmd, **kw):
        seen.update(kw, cmd=cmd)
        return subprocess.CompletedProcess(cmd, 0, b"", b"")
    monkeypatch.setattr(video.shutil, "which", lambda n: "/usr/bin/" + n)
    video._run(["ffmpeg", "-x"], 60, runner)
    assert "preexec_fn" not in seen
    i = seen["cmd"].index("prlimit")
    assert seen["cmd"][i:i + 3] == ["prlimit", "--as=%d" % video.RLIMIT_AS, "--"]
    assert seen["cmd"][-2:] == ["ffmpeg", "-x"]


@pytest.mark.parametrize("out", [
    {"streams": []},
    {"streams": [{"codec_type": "video", "codec_name": "hevc", "width": 800, "height": 480, "duration": "5"}]},
    {"streams": [{"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080, "duration": "5"}]},
    {"streams": [{"codec_type": "video", "codec_name": "h264", "width": 800, "height": 480, "duration": "31"}]},
    {"streams": [{"codec_type": "video", "codec_name": "h264", "width": 800, "height": 480, "duration": "0"}]},
])
def test_bad_output_marks_failed(vapp, paired, ff, out):
    ff.out = out
    assert post(paired).status_code == 202
    work(vapp)
    r = rows(vapp)[-1]
    assert r["state"] == "failed"
    assert not os.path.exists(os.path.join(vapp.config["DATA_DIR"], "originals", r["stored_name"]))


def test_good_output_duration_from_output(vapp, paired, ff):
    ff.out = {"streams": [{"codec_type": "video", "codec_name": "h264", "width": 800, "height": 480,
                           "duration": "30.02"}]}
    post(paired)
    work(vapp)
    r = rows(vapp)[-1]
    assert r["state"] == "ready" and r["duration_ms"] == 30000


def test_pending_cap_refuses_new_videos(vapp, paired, ff):
    for i in range(video.MAX_PENDING):
        assert post(paired, MP4 + bytes([i]) * 8, "c%d.mp4" % i).status_code == 202
    r = post(paired, MP4 + b"extra", "x.mp4")
    assert r.status_code == 429 and r.get_json()["error"] == video.QUEUE_FULL
    assert len(rows(vapp)) == video.MAX_PENDING
    assert os.listdir(os.path.join(vapp.config["DATA_DIR"], "tmp")) in ([], ["video-in"], ["video-in", "video-out"],
                                                                         ["video-out", "video-in"])
    work(vapp)                                      # one becomes ready -> room again
    assert post(paired, MP4 + b"extra", "x.mp4").status_code == 202


# ---------------- Reed (#115, Hale's Pi run): nothing over 4K ----------------

@pytest.mark.parametrize("w,h,rot", [(7680, 4320, None), (4320, 7680, None), (7680, 4320, -90), (3840, 2176, None),
                                     (4096, 2160, None), (2304, 4096, -90)])
def test_probe_refuses_over_4k(tmp_path, w, h, rot):
    with pytest.raises(video.VideoError) as e:
        video.probe(str(tmp_path / "x"), runner=FakeFF([stream(w, h, "hevc", rotation=rot)]))
    assert str(e.value) == video.OVER_4K


@pytest.mark.parametrize("w,h,rot,shown", [(3840, 2160, None, (3840, 2160)), (2160, 3840, None, (2160, 3840)),
                                           (3840, 2160, -90, (2160, 3840))])
def test_probe_allows_4k_either_way(tmp_path, w, h, rot, shown):
    info = video.probe(str(tmp_path / "x"), runner=FakeFF([stream(w, h, "hevc", rotation=rot)]))
    assert (info["width"], info["height"]) == shown


def test_8k_upload_refused_with_message_and_temp_removed(vapp, paired, ff):
    ff.info["streams"] = [stream(7680, 4320, "hevc")]
    r = post(paired)
    assert r.status_code == 415 and r.get_json()["error"] == video.OVER_4K
    assert rows(vapp) == []
    tmp = os.path.join(vapp.config["DATA_DIR"], "tmp")
    assert [f for _, _, fs in os.walk(tmp) for f in fs] == []


def test_portrait_4k_upload_accepted(vapp, paired, ff):
    ff.info["streams"] = [stream(2160, 3840, "hevc")]
    assert post(paired).status_code == 202


def test_rlimit_as_is_1_5_gib():
    assert video.RLIMIT_AS == 1536 * 1024 * 1024
