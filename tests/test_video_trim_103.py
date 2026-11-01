"""#103: trim at upload, conversion progress, Cancel, power-cut requeue. ffmpeg is mocked (FakeFF answers
ffprobe, FakePopen plays ffmpeg's -progress output); the real-ffmpeg trim check is in test_video_audio_real."""
import io
import os
import signal

import pytest

from memoryframe import db as mfdb
from memoryframe import video
from tests.test_video_102 import MP4, PHONE, ff, rows, stream, vapp  # noqa: F401


def post(client, start=None, end=None, data=MP4, name="clip.mp4"):
    form = {"file": (io.BytesIO(data), name)}
    if start is not None:
        form["trim_start_ms"] = start
    if end is not None:
        form["trim_end_ms"] = end
    return client.post("/api/v1/media", data=form, content_type="multipart/form-data", environ_base=PHONE)


def long_clip(ff, seconds="74.0"):
    ff.info = {"streams": [stream(duration=seconds)], "format": {"duration": seconds}}
    ff.out = {"streams": [{"codec_type": "video", "codec_name": "h264", "width": 800, "height": 480,
                           "duration": "30.0"}]}


class FakePopen:
    """ffmpeg run through Popen: writes the output, then yields `-progress` lines; on_line(i) runs before line i."""

    def __init__(self, lines=None, rc=0, on_line=None):
        self.lines = lines if lines is not None else [b"frame=1\n", b"out_time_us=15000000\n", b"progress=end\n"]
        self.rc, self.on_line, self.cmds, self.kw = rc, on_line, [], None
        self.killed = []

    def __call__(self, cmd, **kw):
        self.cmds.append(cmd)
        self.kw = kw
        with open(cmd[-1][len("file:"):], "wb") as f:
            f.write(b"\0\0\0\x18ftypisom converted")
        outer = self

        class Out(io.BytesIO):
            i = 0

            def readline(self, n=-1):
                if outer.on_line:
                    outer.on_line(Out.i)
                Out.i += 1
                return super().readline(n)

        class P:
            pid = 4242
            stdout = Out(b"".join(self.lines))
            returncode = None

            def wait(self, timeout=None):
                self.returncode = -15 if outer.killed else outer.rc
                return self.returncode

            def poll(self):
                return self.returncode
        return P()


@pytest.fixture
def killpg(monkeypatch):
    sent = []
    monkeypatch.setattr(video.os, "killpg", lambda pid, sig: sent.append((pid, sig)))
    return sent


def work(app, popen):
    conn = mfdb.connect(app.config["DB_PATH"])
    try:
        tick = iter(range(10**6)).__next__          # 1 s per clock read: the throttled checks all run
        return video.work_one(conn, app.config["DATA_DIR"], runner=video.subprocess.run, popen=popen, clock=tick)
    finally:
        conn.close()


def files_left(app):
    d = app.config["DATA_DIR"]
    return [os.path.join(r, f) for sub in ("originals", "display", "thumbs", "tmp")
            for r, _, fs in os.walk(os.path.join(d, sub)) for f in fs]


# ---------------- validation ----------------

@pytest.mark.parametrize("s,e", [("", "1000"), ("1000", None), (None, "1000"), ("-1", "1000"), ("1.5", "2000"),
                                 ("1e3", "5000"), (" 1", "5000"), ("NaN", "5000"), ("0x10", "5000"),
                                 ("١٢", "5000"), ("1" * 10, "5000"), ("0", "9" * 40)])
def test_parse_trim_refuses_malformed(s, e):
    with pytest.raises(video.TrimError):
        video.parse_trim(s, e)


def test_parse_trim():
    assert video.parse_trim(None, None) is None
    assert video.parse_trim("6000", "36000") == (6000, 36000)


@pytest.mark.parametrize("trim,dur,expect", [
    (None, 74.0, (0, 30000)), (None, 12.5, (0, 12500)), (None, 0.4, (0, 400)),
    ((6000, 36000), 74.0, (6000, 36000)), ((0, 1000), 74.0, (0, 1000)),
    ((44000, 74200), 74.0, (44000, 74000)),          # the phone's duration a little longer: end clamped
])
def test_resolve_trim(trim, dur, expect):
    assert video.resolve_trim(trim, dur) == expect


@pytest.mark.parametrize("trim,msg", [
    ((0, 42000), "That part is 0:42. Choose 30 seconds or less."),
    ((5000, 5999), "That part is under 1 second. Choose at least 1 second."),
    ((5000, 5000), video.TRIM_BAD), ((6000, 5000), video.TRIM_BAD), ((70000, 75000), video.TRIM_BAD),
    ((0, 999999999), video.TRIM_BAD),
])
def test_resolve_trim_refuses(trim, msg):
    with pytest.raises(video.TrimError) as e:
        video.resolve_trim(trim, 74.0)
    assert str(e.value) == msg


@pytest.mark.parametrize("s,e,code", [("abc", "1000", 400), ("1000", None, 400), ("-5", "1000", 400),
                                      ("0", "42000", 400), ("70000", "90000", 400), ("1000", "1500", 400),
                                      ("6000", "36000", 202)])
def test_upload_trim_is_checked_never_500(vapp, paired, ff, s, e, code):
    long_clip(ff)
    r = post(paired, s, e)
    assert r.status_code == code, r.get_json()
    if code == 400:
        assert rows(vapp) == [] and files_left(vapp) == []
        assert "Traceback" not in r.get_data(as_text=True)


def test_upload_stores_trim_and_convert_cuts_it(vapp, paired, ff, killpg):
    long_clip(ff)
    assert post(paired, "6000", "36000").status_code == 202
    assert (rows(vapp)[0]["trim_start_ms"], rows(vapp)[0]["trim_end_ms"]) == (6000, 36000)
    fp = FakePopen()
    work(vapp, fp)
    cmd = fp.cmds[0]
    i = cmd.index("-i")
    assert cmd[i - 4:i] == ["-ss", "6.000", "-t", "30.000"]        # input seek + length, %.3f only
    assert cmd[cmd.index("-t", i) + 1] == "30.000"                  # output cap
    assert "-progress" in cmd and cmd[cmd.index("-progress") + 1] == "pipe:1" and "-nostats" in cmd
    assert fp.kw["start_new_session"] is True                       # SIGKILL reaches ffmpeg under the wrappers
    assert rows(vapp)[0]["state"] == "ready" and rows(vapp)[0]["progress_pct"] == 100


def test_no_trim_keeps_first_30s_and_limits_stay(vapp, paired, ff, monkeypatch, killpg):
    long_clip(ff)
    monkeypatch.setattr(video.shutil, "which", lambda n: "/usr/bin/" + n)
    post(paired)
    assert rows(vapp)[0]["trim_start_ms"] is None
    fp = FakePopen()
    work(vapp, fp)
    cmd = fp.cmds[0]
    assert cmd[cmd.index("-ss") + 1] == "0.000" and cmd[cmd.index("-ss") + 3] == "30.000"
    s = " ".join(cmd)
    assert "prlimit --as=%d" % video.RLIMIT_AS in s and "MALLOC_ARENA_MAX=2" in s and "-protocol_whitelist file" in s


def test_trimmed_short_part_of_a_4k_clip_gets_a_shorter_estimate(vapp, paired, ff):
    long_clip(ff)
    ff.info["streams"] = [stream(3840 * 2, 2160 * 2, duration="74.0")]
    full = post(paired).get_json()["prep_minutes"]
    r = post(paired, "0", "5000", data=MP4 + b"2", name="b.mp4").get_json()
    assert r["prep_minutes"] < full


def test_stored_trim_that_no_longer_fits_fails_cleanly(vapp, paired, ff):
    long_clip(ff)
    post(paired, "6000", "36000")
    long_clip(ff, "20.0")                       # the probe at convert time disagrees: fail, never guess
    work(vapp, FakePopen())
    assert rows(vapp)[0]["state"] == "failed"


# ---------------- progress ----------------

def test_progress_is_written_capped_and_100_only_when_ready(vapp, paired, ff, killpg):
    long_clip(ff)
    post(paired, "0", "20000")
    seen = []

    def peek(i):
        seen.append(rows(vapp)[0]["progress_pct"])
    lines = [b"out_time_us=%d\n" % (t * 1000000) for t in (0, 5, 10, 25)] + [b"out_time_us=N/A\n",
                                                                              b"x" * 5000 + b"\n", b"progress=end\n"]
    clock = iter(range(0, 1000, 2)).__next__              # every line is 2 s apart: each change is written
    conn = mfdb.connect(vapp.config["DB_PATH"])
    video.work_one(conn, vapp.config["DATA_DIR"], runner=video.subprocess.run,
                   popen=FakePopen(lines, on_line=peek), clock=clock)
    conn.close()
    assert 25 in seen and 50 in seen and 99 in seen and 100 not in seen   # 25 s of 20 s: still 99 until ready
    assert seen == sorted(seen)
    assert rows(vapp)[0]["progress_pct"] == 100


def test_progress_writes_at_most_once_a_second(vapp, paired, ff, killpg):
    long_clip(ff)
    post(paired, "0", "20000")
    lines = [b"out_time_us=%d\n" % (t * 100000) for t in range(100)]       # 0.1 s of video per line
    seen = []

    def ticks():                                                           # 0.1 s per clock read
        seen.append(len(seen) * 0.1)
        return seen[-1]
    conn = mfdb.connect(vapp.config["DB_PATH"])
    writes = []
    conn.set_trace_callback(lambda q: writes.append(q) if q.startswith("UPDATE media SET progress_pct = ") else None)
    video.work_one(conn, vapp.config["DATA_DIR"], runner=video.subprocess.run, popen=FakePopen(lines), clock=ticks)
    conn.close()
    assert 0 < len(writes) <= seen[-1] / video.PROGRESS_WRITE_S + 1     # at most one write per second of clock
    assert len(writes) < 99                                                # not one per line


def test_status_endpoint(vapp, paired, ff):
    long_clip(ff)
    post(paired, "0", "10000")
    post(paired, "0", "10000", data=MP4 + b"2", name="b.mp4")
    a, b = rows(vapp)
    s = paired.get(f"/api/v1/media/{b['id']}/status", environ_base=PHONE).get_json()
    assert s == {"id": b["id"], "state": "queued", "progress": 0, "resumed": False, "queue_pos": 2}
    assert paired.get(f"/api/v1/media/{a['id']}/status", environ_base=PHONE).get_json()["queue_pos"] == 1
    assert paired.get("/api/v1/media/99999/status", environ_base=PHONE).status_code == 404


# ---------------- cancel ----------------

def test_cancel_queued_removes_it(vapp, paired, ff):
    long_clip(ff)
    post(paired, "0", "10000")
    mid = rows(vapp)[0]["id"]
    r = paired.post(f"/api/v1/media/{mid}/cancel", environ_base=PHONE)
    assert r.status_code == 200 and r.get_json()["state"] == "cancelled"
    assert rows(vapp) == [] and files_left(vapp) == []


def test_cancel_while_converting_kills_ffmpeg_and_cleans_up(vapp, paired, ff, killpg):
    long_clip(ff)
    post(paired, "0", "10000")
    mid = rows(vapp)[0]["id"]
    answers = []

    def press_cancel(i):
        if i == 1:
            r = paired.post(f"/api/v1/media/{mid}/cancel", environ_base=PHONE)
            answers.append((r.status_code, r.get_json()["state"],
                            paired.get(f"/api/v1/media/{mid}/status", environ_base=PHONE).get_json()["state"]))
    lines = [b"out_time_us=%d\n" % (i * 1000000) for i in range(8)]
    work(vapp, FakePopen(lines, on_line=press_cancel))
    assert answers == [(202, "cancelling", "cancelling")]
    assert killpg[0] == (4242, signal.SIGTERM)
    assert rows(vapp) == [] and files_left(vapp) == []


def test_delete_while_converting_also_stops_it(vapp, paired, ff, killpg):
    long_clip(ff)
    post(paired, "0", "10000")
    mid = rows(vapp)[0]["id"]

    def delete(i):
        if i == 1:
            paired.delete(f"/api/v1/media/{mid}", environ_base=PHONE)
    work(vapp, FakePopen([b"out_time_us=1\n"] * 6, on_line=delete))
    assert killpg and rows(vapp) == [] and files_left(vapp) == []


def test_stop_escalates_to_sigkill(monkeypatch, killpg):
    import subprocess

    class Stuck:
        pid = 7

        def wait(self, timeout=None):
            if len(killpg) < 2:
                raise subprocess.TimeoutExpired("ffmpeg", timeout)
            return -9
    monkeypatch.setattr(video, "CANCEL_GRACE_S", 0)
    video._stop(Stuck())
    assert killpg == [(7, signal.SIGTERM), (7, signal.SIGKILL)]


def test_cancel_a_ready_video_is_a_delete(vapp, paired, ff, killpg):
    long_clip(ff)
    post(paired, "0", "10000")
    work(vapp, FakePopen())
    mid = rows(vapp)[0]["id"]
    assert paired.post(f"/api/v1/media/{mid}/cancel", environ_base=PHONE).status_code == 200
    assert rows(vapp) == []


# ---------------- power cut ----------------

def test_power_cut_requeues_with_same_trim_and_finishes_cancels(vapp, paired, ff, monkeypatch, killpg):
    long_clip(ff)
    post(paired, "6000", "36000")
    post(paired, "0", "10000", data=MP4 + b"2", name="b.mp4")
    a, b = rows(vapp)
    d = vapp.config["DATA_DIR"]
    conn = mfdb.connect(vapp.config["DB_PATH"])
    conn.execute("UPDATE media SET state = 'processing', progress_pct = 40")
    conn.execute("UPDATE media SET cancel_requested = 1 WHERE id = ?", (b["id"],))
    conn.commit()
    open(video.part_path(d, a["stored_name"]), "wb").write(b"half")
    from memoryframe import storage
    monkeypatch.setattr(storage, "settled", lambda p: True)
    video.recover(conn, d)
    conn.close()
    (r,) = rows(vapp)
    assert (r["state"], r["progress_pct"], r["resumed"], r["trim_start_ms"], r["trim_end_ms"]) == \
        ("queued", 0, 1, 6000, 36000)
    assert paired.get(f"/api/v1/media/{r['id']}/status", environ_base=PHONE).get_json()["resumed"] is True
    assert not os.path.exists(video.input_path(d, b["stored_name"]))
    fp = FakePopen()
    work(vapp, fp)
    assert fp.cmds[0][fp.cmds[0].index("-ss") + 1] == "6.000"
    assert rows(vapp)[0]["resumed"] == 0 and rows(vapp)[0]["state"] == "ready"


def test_retry_reuses_trim_and_resets_progress(vapp, paired, ff, killpg):
    long_clip(ff)
    post(paired, "6000", "36000")
    work(vapp, FakePopen(rc=1))
    r = rows(vapp)[0]
    assert r["state"] == "failed"
    paired.post(f"/api/v1/media/{r['id']}/retry", environ_base=PHONE)
    assert rows(vapp)[0]["progress_pct"] == 0
    fp = FakePopen()
    work(vapp, fp)
    assert fp.cmds[0][fp.cmds[0].index("-ss") + 1] == "6.000" and rows(vapp)[0]["state"] == "ready"


def test_only_upload_page_allows_blob_media(vapp, paired):
    up = paired.get("/admin/upload", environ_base=PHONE)
    assert "media-src 'self' blob:;" in up.headers["Content-Security-Policy"]
    assert 'id="trim"' in up.get_data(as_text=True) and "trim.js" in up.get_data(as_text=True)
    lib = paired.get("/admin/library", environ_base=PHONE)
    assert "blob:" not in lib.headers["Content-Security-Policy"]
