"""#102: short videos. Upload -> queued row -> one background conversion at a time -> ready.

Design: #101 (Sage's recommendations) and the feasibility doc. The frame never plays what the phone
sent: every clip is re-encoded on the Pi with ffmpeg to a silent H.264 MP4 at 800x480 (30 fps cap,
30 s max), framed like photos (aspect >= 1.30 centre-cropped, narrower clips get the #86/#87 "B2"
blur-fill). The rotation tag is honoured BEFORE framing, so a portrait phone clip is portrait.

Files (all on the SD card under DATA_DIR, never /run or a tmpfs):
    tmp/video-in/<stem>          the upload as sent (fsynced before the row is queued; kept until ready,
                                 so Retry works after a failure; removed with the row)
    tmp/video-out/<stem>.mp4.part  ffmpeg output while converting
    originals/<stem>.mp4         the converted clip (renamed in after fsync)
    display/<stem>.jpg           poster (first frame), also the thumbnail source
    thumbs/<stem>.jpg            square thumbnail made from the poster
The media row's sha256 is the hash of the upload as sent (duplicate detection).

Rows: state queued -> processing -> ready | failed. Only ready rows reach the slideshow, /uploads,
show-now and the backfill. A power cut at any step is recovered by recover() at start-up.
"""
import json
import math
import logging
import os
import shutil
import subprocess
import threading
import time
import uuid

from . import imaging

log = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 350 * 1024 * 1024      # Sage on #101
MAX_SECONDS = 30                         # clips play for up to 30 s (longer ones are cut)
MAX_INPUT_SECONDS = 600                  # refuse anything longer than 10 min outright
MAX_LONG, MAX_SHORT = 3840, 2160         # 4K UHD in either orientation; above it is allowed but slow ("high_res")
LIMIT_LONG, LIMIT_SHORT = 8192, 4320     # HEVC's own ceiling (level 6.2); anything bigger is not a phone clip
HIRES_RATE = 23                          # Pi software decode, over-4K HEVC: ~23x realtime (Hale, #115). Rough.
PRESCALE = 1920                          # over-4K clips are scaled to this long side before anything else
HIRES_TIMEOUT_RATE = 30                  # timeout for over-4K: 30x the clip (30 s -> 15 min; 23x = 11.5 min)
MAX_FPS = 120
OUT_W, OUT_H = 800, 480
OUT_FPS = 30
OUT_MAX_BYTES = "60M"
COVER_AT = imaging.BLURFILL_BELOW        # 1.30: same rule as photos
RLIMIT_AS = 1536 * 1024 * 1024           # 1.5 GiB address space: 4K HEVC software path peaks ~1.16 GiB (PR #115)
COPIES = 3                               # #96: free space must cover 3x the incoming file above the reserve
STATES = ("queued", "processing", "ready", "failed")
FORMAT_WHITELIST = "mov,mp4,m4a,3gp,3g2,mj2,matroska,webm"
CODECS = {"h264", "hevc", "vp8", "vp9", "av1", "mpeg4"}
CODEC_WHITELIST = "h264,hevc,vp8,vp9,av1,libdav1d,mpeg4"   # decoders ffmpeg may open (Sage: enforced, not just probed)
MAX_PENDING = 5                          # queued + preparing + failed clips (each keeps up to 350 MB on the card)
OUT_MAX_SECONDS = MAX_SECONDS + 0.5
NICE = ["nice", "-n", "19", "ionice", "-c", "3"]
TASKSET = ["taskset", "-c", "2,3"]

NOT_A_VIDEO = "This video can't be played on the frame. Please send an MP4 or MOV from your phone's camera."
TOO_LONG = "This video is longer than 10 minutes. Please send a shorter clip."
TOO_LARGE = "This video is too large for the frame (the limit is 350 MB). Please send a shorter clip."
FAILED = "The frame couldn't prepare this video."
QUEUE_FULL = ("The frame is still holding {n} videos it hasn't added yet. Wait for them to finish, "
              "or remove ones that couldn't be added, then try again.").format(n=MAX_PENDING)


class VideoError(Exception):
    pass


def _dirs(data_dir):
    i = os.path.join(data_dir, "tmp", "video-in")
    o = os.path.join(data_dir, "tmp", "video-out")
    return i, o


def ensure_dirs(data_dir):
    for d in _dirs(data_dir):
        os.makedirs(d, exist_ok=True)


def input_path(data_dir, stored_name):
    return os.path.join(_dirs(data_dir)[0], os.path.splitext(stored_name)[0])


def part_path(data_dir, stored_name):
    return os.path.join(_dirs(data_dir)[1], os.path.splitext(stored_name)[0] + ".mp4.part")


def poster_path(data_dir, stored_name):
    return os.path.join(data_dir, "display", os.path.splitext(stored_name)[0] + ".jpg")


def available():
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def space_ok(data_dir, min_free, incoming):
    """#96: the reserve (min_free) must stay free after COPIES x the incoming file."""
    free = shutil.disk_usage(data_dir).free
    return free >= min_free and free - min_free >= COPIES * max(0, int(incoming or 0))


# ---------------- probe ----------------

def _rotation(stream):
    """Degrees clockwise the picture must turn to be upright (0, 90, 180, 270)."""
    theta = None
    for sd in stream.get("side_data_list") or []:
        if "rotation" in sd:                       # display matrix: ffprobe prints the counter-clockwise angle
            theta = -float(sd["rotation"])
            break
    if theta is None:
        rot = (stream.get("tags") or {}).get("rotate")   # older ffmpeg: clockwise
        theta = float(rot) if rot not in (None, "") else 0.0
    return int(round(theta / 90.0)) % 4 * 90


def _fps(rate):
    try:
        n, d = (rate or "0/1").split("/")
        return float(n) / float(d) if float(d) else 0.0
    except (ValueError, ZeroDivisionError):
        return 0.0


def probe(path, runner=None):
    """ffprobe the upload. Returns {codec, width, height, rotation, duration, fps} (width/height as
    displayed, i.e. after rotation). Raises VideoError(message for the phone) if it must be refused."""
    cmd = ["ffprobe", "-v", "error", "-probesize", "5M", "-analyzeduration", "5M",
           "-format_whitelist", FORMAT_WHITELIST, "-protocol_whitelist", "file",
           "-codec_whitelist", CODEC_WHITELIST, "-print_format", "json", "-show_streams", "-show_format", "file:" + path]
    runner = runner or subprocess.run
    try:
        r = runner(cmd, capture_output=True, timeout=30, check=False)
        info = json.loads(r.stdout or b"{}") if r.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired, ValueError):
        info = None
    if not info:
        raise VideoError(NOT_A_VIDEO)
    streams = info.get("streams") or []
    video = [s for s in streams if s.get("codec_type") == "video"
             and not (s.get("disposition") or {}).get("attached_pic")]
    other = [s for s in streams if s.get("codec_type") in ("data", "attachment", "subtitle")]
    if len(video) != 1 or other:
        raise VideoError(NOT_A_VIDEO)
    v = video[0]
    if v.get("codec_name") not in CODECS:
        raise VideoError(NOT_A_VIDEO)
    w, h = int(v.get("width") or 0), int(v.get("height") or 0)
    if w <= 0 or h <= 0:
        raise VideoError(NOT_A_VIDEO)
    if max(w, h) > LIMIT_LONG or min(w, h) > LIMIT_SHORT:
        raise VideoError(NOT_A_VIDEO)
    hires = max(w, h) > MAX_LONG or min(w, h) > MAX_SHORT    # orientation-free, so rotated clips are judged the same
    fps = _fps(v.get("avg_frame_rate")) or _fps(v.get("r_frame_rate"))
    if fps > MAX_FPS:
        raise VideoError(NOT_A_VIDEO)
    try:
        duration = float(v.get("duration") or (info.get("format") or {}).get("duration") or 0)
    except ValueError:
        duration = 0.0
    if duration <= 0:
        raise VideoError(NOT_A_VIDEO)
    if duration > MAX_INPUT_SECONDS:
        raise VideoError(TOO_LONG)
    rot = _rotation(v)
    if rot in (90, 270):
        w, h = h, w
    return {"codec": v["codec_name"], "width": w, "height": h, "rotation": rot,
            "duration": duration, "fps": fps, "high_res": hires}


def prep_minutes(info):
    """Rough minutes the Pi needs for an over-4K clip (only what is kept: at most 30 s); None otherwise."""
    if not info.get("high_res"):
        return None
    return max(1, math.ceil(min(info["duration"], MAX_SECONDS) * HIRES_RATE / 60))


# ---------------- filter graph (rotation, then cover or B2 blur-fill) ----------------

def filter_graph(info, target=(OUT_W, OUT_H)):
    """-filter_complex for one clip. ffmpeg's autorotate turns the picture upright first (and drops the
    display matrix, so players do not turn it again; an explicit transpose with -noautorotate would leave
    the matrix in the output). info's width/height are already the upright size, so then the
    photo rules: aspect >= 1.30 -> centre cover crop; narrower -> imaging.blurfill's B2 look with the same
    constants (blurred dimmed cover background, sharp clip 15 % trimmed and at most 80 % wide, soft shadow).
    Output label [v], yuv420p, at most 30 fps."""
    cw, ch = target
    rot = ""
    a = info["width"] / info["height"]
    if info.get("high_res"):     # over 4K: shrink once, first, so split/blur never hold 8K frames (fits 1.5 GiB)
        f = PRESCALE / max(info["width"], info["height"])
        sw, sh = round(info["width"] * f) // 2 * 2, round(info["height"] * f) // 2 * 2
        turn = info.get("rotation", 0)
        if turn in (90, 270):        # the decoder's frames are still sideways: scale them as coded
            sw, sh = sh, sw
        # -noautorotate (convert_cmd): turn AFTER shrinking, so no full-size 8K transpose (Hale: 98 % of the cap)
        rot = f"scale={sw}:{sh}:flags=bilinear," + {90: "transpose=clock,", 270: "transpose=cclock,",
                                                    180: "hflip,vflip,"}.get(turn, "")
    tail = f"fps=fps='min(source_fps,{OUT_FPS})',format=yuv420p,setsar=1[v]"
    if a >= COVER_AT:
        return (f"[0:v:0]{rot}scale={cw}:{ch}:force_original_aspect_ratio=increase,"
                f"crop={cw}:{ch},{tail}")
    s = cw / 1000
    bw, bh = cw // imaging.BG_SCALE, ch // imaging.BG_SCALE
    sigma = imaging.BG_BLUR * s / imaging.BG_SCALE
    dim = 1 - imaging.BG_DIM
    k = max(1.0, min(imaging.TRIM, imaging.MAX_SHARP_W * cw / (a * ch)))
    fh = round(ch * k)
    fw, vis = max(2, round(fh * a) // 2 * 2), min(fh, ch)
    top = (fh - vis) // 2                                    # no face boxes for clips: centred trim
    x0, y0 = (cw - fw) // 2, (ch - vis) // 2
    r = round(imaging.SHADOW_BLUR * s)
    pad = 3 * r
    return (
        f"[0:v:0]{rot}split=2[bgsrc][fgsrc];"
        f"[bgsrc]scale={bw}:{bh}:force_original_aspect_ratio=increase,crop={bw}:{bh},"
        f"gblur=sigma={sigma:g},scale={cw}:{ch}:flags=bilinear,format=gbrp,"
        f"colorchannelmixer=rr={dim:g}:gg={dim:g}:bb={dim:g}[bg];"
        f"color=c=black@{imaging.SHADOW_ALPHA:g}:s={fw}x{vis},format=rgba,"
        f"pad={fw + 2 * pad}:{vis + 2 * pad}:{pad}:{pad}:color=black@0,gblur=sigma={r}[sh];"
        f"[bg][sh]overlay={x0 - pad}:{y0 - pad}:shortest=1[bgs];"
        f"[fgsrc]scale={fw}:{fh}:flags=lanczos,crop={fw}:{vis}:0:{top}[fg];"
        f"[bgs][fg]overlay={x0}:{y0},{tail}")


def convert_cmd(src, dst, info):
    """The ffmpeg argv (without the nice/ionice/taskset/timeout prefix)."""
    hires = bool(info.get("high_res"))
    # HEVC: Pi 4 hardware decode (~55 % faster), but only up to 4K: over 4K it is refused anyway (Hale), so go
    # straight to software, with one decoder thread so the frame buffers fit the 1.5 GiB address-space cap.
    hw = ["-hwaccel", "drm"] if info["codec"] == "hevc" and not hires else []
    return (["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-threads", "1" if hires else "2", *hw,
             *(["-noautorotate", "-display_rotation", "0"] if hires else []),   # turned in filter_graph instead
             "-format_whitelist", FORMAT_WHITELIST, "-protocol_whitelist", "file",
             "-codec_whitelist", CODEC_WHITELIST, "-probesize", "5M", "-analyzeduration", "5M", "-t", str(MAX_SECONDS), "-i", "file:" + src,
             "-filter_complex", filter_graph(info), "-map", "[v]", "-an", "-sn", "-dn",
             "-map_metadata", "-1", "-map_chapters", "-1", "-fflags", "+bitexact",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "26", "-maxrate", "2.5M", "-bufsize", "5M",
             "-profile:v", "high", "-g", "60", "-movflags", "+faststart", "-t", str(MAX_SECONDS),
             "-fs", OUT_MAX_BYTES, "-f", "mp4", "file:" + dst])


def poster_cmd(src, dst):
    return ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-protocol_whitelist", "file",
            "-codec_whitelist", "h264", "-i", "file:" + src, "-frames:v", "1", "-q:v", "3", "-f", "image2", "-c:v", "mjpeg", "file:" + dst]


def timeout_for(info):
    rate = HIRES_TIMEOUT_RATE if info.get("high_res") else 10
    return int(max(60, rate * min(info["duration"], MAX_SECONDS)))


def _prefix(timeout_s):
    pre = ["timeout", "-k", "10", str(timeout_s)]
    if shutil.which("nice") and shutil.which("ionice"):
        pre += NICE
    if shutil.which("taskset") and (os.cpu_count() or 1) >= 4:
        pre += TASKSET                    # cores 0-1 stay free for the kiosk browser
    return pre


def _limit():
    """Address-space cap as a `prlimit` prefix (not preexec_fn, which can deadlock in threaded waitress),
    so a hostile file cannot eat the Pi's RAM."""
    # MALLOC_ARENA_MAX=2: glibc otherwise reserves a 64 MB arena per thread, which counts against --as but is
    # never used (8K rotated: VmPeak 1307 -> ~1080 MB, same RSS).
    env = ["env", "MALLOC_ARENA_MAX=2"]
    return (["prlimit", "--as=%d" % RLIMIT_AS, "--"] + env) if shutil.which("prlimit") else env


def _run(cmd, timeout_s, runner):
    r = runner(_prefix(timeout_s) + _limit() + cmd, capture_output=True, timeout=timeout_s + 30, check=False)
    if r.returncode != 0:
        raise VideoError("ffmpeg exit %s: %s" % (r.returncode, (r.stderr or b"")[-300:].decode("utf-8", "replace")))


def convert(data_dir, stored_name, runner=None):
    """Probe + convert one queued clip into originals/<stem>.mp4 with its poster and thumbnail.
    Returns duration_ms of the stored clip. Raises VideoError on any failure (nothing half-made is left)."""
    runner = runner or subprocess.run
    src = input_path(data_dir, stored_name)
    part = part_path(data_dir, stored_name)
    final = os.path.join(data_dir, "originals", stored_name)
    info = probe(src, runner)
    try:
        _run(convert_cmd(src, part, info), timeout_for(info), runner)
        if not os.path.exists(part) or os.path.getsize(part) == 0:
            raise VideoError("ffmpeg made no output")
        out_s = check_output(part, runner, min(info["duration"], MAX_SECONDS))
        imaging.fsync_path(part)
        os.replace(part, final)
        imaging.fsync_dir(os.path.dirname(final))
        poster = poster_path(data_dir, stored_name)
        tmp_poster = poster + ".tmp"
        _run(poster_cmd(final, tmp_poster), 60, runner)
        os.replace(tmp_poster, poster)
        make_video_thumb(data_dir, stored_name)
    except BaseException:
        for p in (part, final, poster_path(data_dir, stored_name), poster_path(data_dir, stored_name) + ".tmp",
                  imaging.thumb_path(data_dir, stored_name)):
            try:
                os.remove(p)
            except OSError:
                pass
        raise
    return int(round(min(out_s, MAX_SECONDS) * 1000))


def check_output(path, runner=None, expect=None):
    """ffprobe the converted file before it is marked ready: exactly one H.264 stream, 800x480,
    0 < duration <= 30.5 s. Returns the duration; raises VideoError otherwise (the row goes 'failed')."""
    runner = runner or subprocess.run
    cmd = ["ffprobe", "-v", "error", "-format_whitelist", "mov,mp4", "-protocol_whitelist", "file",
           "-codec_whitelist", "h264", "-print_format", "json", "-show_streams", "-show_format", "file:" + path]
    try:
        r = runner(_prefix(60) + _limit() + cmd, capture_output=True, timeout=90, check=False)
        info = json.loads(r.stdout or b"{}") if r.returncode == 0 else {}
        streams = info.get("streams") or []
        v = streams[0] if len(streams) == 1 else {}
        dur = float(v.get("duration") or (info.get("format") or {}).get("duration") or 0)
    except (OSError, subprocess.TimeoutExpired, ValueError, TypeError, AttributeError):
        v, dur = {}, 0.0
    if (v.get("codec_type") != "video" or v.get("codec_name") != "h264"
            or (v.get("width"), v.get("height")) != (OUT_W, OUT_H) or not 0 < dur <= OUT_MAX_SECONDS
            or (expect is not None and dur < expect - 1.0)):      # cut short (e.g. ffmpeg hit the memory cap)
        raise VideoError("converted output failed its check")
    return dur


def pending(conn):
    """Clips holding an input on the card: queued, preparing or failed."""
    return conn.execute("SELECT COUNT(*) FROM media WHERE kind = 'video' AND state <> 'ready'").fetchone()[0]


def make_video_thumb(data_dir, stored_name):
    poster = poster_path(data_dir, stored_name)
    dst = imaging.thumb_path(data_dir, stored_name)
    if os.path.exists(poster) and not os.path.exists(dst):
        imaging.make_thumb(poster, dst)
    return dst if os.path.exists(dst) else None


# ---------------- accept an upload (request thread) ----------------

def accept(conn, data_dir, tmp_path, original_name, digest):
    """Move an uploaded video (already on the card, already hashed) into tmp/video-in and queue it.
    Returns the stored name, or None for a duplicate. The caller has checked space and sniffed it."""
    from . import db
    if conn.execute("SELECT 1 FROM media WHERE sha256 = ? AND state <> 'failed'", (digest,)).fetchone():
        return None
    ensure_dirs(data_dir)
    stored = uuid.uuid4().hex + ".mp4"
    dst = input_path(data_dir, stored)
    imaging.fsync_path(tmp_path)
    os.replace(tmp_path, dst)
    imaging.fsync_dir(os.path.dirname(dst))
    try:
        conn.execute("INSERT INTO media (stored_name, original_name, kind, sha256, size_bytes, source,"
                     " created_at, state) VALUES (?, ?, 'video', ?, ?, 'upload', ?, 'queued')",
                     (stored, original_name, digest, os.path.getsize(dst), db.now()))
        conn.commit()
    except BaseException:
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001
            pass
        try:
            os.remove(dst)
        except OSError:
            pass
        raise
    wake()
    return stored


def remove_files(data_dir, stored_name):
    for p in (input_path(data_dir, stored_name), part_path(data_dir, stored_name)):
        try:
            os.remove(p)
        except OSError:
            pass


# ---------------- start-up recovery (#91 reconcile, extended) ----------------

def recover(conn, data_dir):
    """After a power cut: drop *.part files and inputs without a row; a row left 'processing' goes back to
    'queued' (its input is still there) or to 'failed' (input gone). Returns (requeued, failed, swept)."""
    din, dout = _dirs(data_dir)
    swept = 0
    for f in os.listdir(dout) if os.path.isdir(dout) else []:
        try:
            os.remove(os.path.join(dout, f))
            swept += 1
        except OSError:
            pass
    rows = conn.execute("SELECT stored_name, state FROM media WHERE kind = 'video'").fetchall()
    stems = {os.path.splitext(r["stored_name"])[0] for r in rows if r["state"] != "ready"}
    for f in os.listdir(din) if os.path.isdir(din) else []:
        p = os.path.join(din, f)
        if f not in stems and os.path.isfile(p):
            from . import storage
            if storage.settled(p):
                os.remove(p)
                swept += 1
    requeued = failed = 0
    for r in rows:
        if r["state"] in ("queued", "processing"):
            if os.path.exists(input_path(data_dir, r["stored_name"])):
                if r["state"] == "processing":
                    conn.execute("UPDATE media SET state = 'queued' WHERE stored_name = ?", (r["stored_name"],))
                    requeued += 1
            else:
                conn.execute("UPDATE media SET state = 'failed' WHERE stored_name = ?", (r["stored_name"],))
                failed += 1
    conn.commit()
    return requeued, failed, swept


# ---------------- the worker: one conversion at a time, low priority ----------------

_wake = threading.Event()
_started = threading.Lock()
_thread = None


def wake():
    _wake.set()


def work_one(conn, data_dir, runner=None):
    """Convert the oldest queued clip. Returns its stored name, or None if the queue is empty."""
    row = conn.execute("SELECT id, stored_name FROM media WHERE kind = 'video' AND state = 'queued' "
                       "ORDER BY created_at, id LIMIT 1").fetchone()
    if row is None:
        return None
    if conn.execute("UPDATE media SET state = 'processing' WHERE id = ? AND state = 'queued'",
                    (row["id"],)).rowcount != 1:
        conn.commit()
        return row["stored_name"]
    conn.commit()
    name = row["stored_name"]
    try:
        ms = convert(data_dir, name, runner)
    except Exception as e:  # noqa: BLE001 - any failure marks the row failed; Retry requeues it
        log.warning("video %s failed: %s", name, e)
        conn.execute("UPDATE media SET state = 'failed' WHERE id = ? AND state = 'processing'", (row["id"],))
        conn.commit()
        return name
    final = os.path.join(data_dir, "originals", name)
    n = conn.execute("UPDATE media SET state = 'ready', duration_ms = ?, size_bytes = ? "
                     "WHERE id = ? AND state = 'processing'", (ms, os.path.getsize(final), row["id"])).rowcount
    conn.commit()
    if n == 1:
        remove_files(data_dir, name)          # the input is only dropped once the clip is ready
    else:                                     # removed while converting: its files go too
        imaging.remove_all(data_dir, name)
        remove_files(data_dir, name)
    return name


def start_worker(app, on_ready=None):
    """One daemon thread per process (waitress runs one process): conversions never overlap."""
    global _thread
    from . import db
    with _started:
        if _thread is not None:
            return _thread
        data_dir, db_path = app.config["DATA_DIR"], app.config["DB_PATH"]

        def run():
            conn = db.connect(db_path)
            while True:
                try:
                    if not available():
                        _wake.wait(300)
                        _wake.clear()
                        continue
                    name = work_one(conn, data_dir)
                    if name is not None:
                        if on_ready:
                            on_ready()
                        time.sleep(1)
                        continue
                except Exception as e:  # noqa: BLE001 - never let the worker die
                    log.warning("video worker: %s", e)
                _wake.wait(30)
                _wake.clear()

        _thread = threading.Thread(target=run, name="mf-video", daemon=True)
        _thread.start()
        return _thread
