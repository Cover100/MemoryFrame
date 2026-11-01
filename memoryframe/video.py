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
# ffprobe opens a decoder for EVERY stream and exits 1 if one is not whitelisted, so phone clips with sound were
# all refused (Hale, Pi). Probe only (never convert: it runs -an) may also open these phone audio decoders (Sage's
# list; AMR because 3gp/3g2 are in FORMAT_WHITELIST). Any other audio codec -> ffprobe fails -> 415.
# -codec_whitelist matches DECODER names: MP3 is decoded by "mp3float" (ffmpeg's first choice) or "mp3",
# AMR by "amrnb"/"amrwb" (the codec ids amr_nb/amr_wb would never match).
AUDIO_WHITELIST = "aac,mp3float,mp3,opus,pcm_s16le,pcm_s16be,pcm_s24le,pcm_f32le,amrnb,amrwb"
MAX_STREAMS = 8
PROBE_WHITELIST = CODEC_WHITELIST + "," + AUDIO_WHITELIST
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


# #103 trim (picked on the phone, checked here against the PROBED duration, never the phone's).
MIN_KEEP_MS = 1000
TRIM_SLACK_MS = 250                      # the phone's <video>.duration may read a little past ffprobe's
TRIM_BAD = "The trim points for this video aren't valid. Please choose the part again."
TRIM_TOO_LONG = "That part is {len}. Choose 30 seconds or less."
TRIM_TOO_SHORT = "That part is under 1 second. Choose at least 1 second."
PROGRESS_WRITE_S = 1.0                   # write progress_pct at most once a second (SD card)
CANCEL_GRACE_S = 5                       # SIGTERM, then SIGKILL after this
MAX_PROGRESS_LINE = 1024


class Cancelled(Exception):
    """The clip was cancelled (or deleted) while ffmpeg ran: ffmpeg is stopped, nothing is kept."""


class TrimError(Exception):
    """Bad trim points: the phone gets 400 with this message."""


def _mmss(ms):
    s = int(ms // 1000)
    return "%d:%02d" % (s // 60, s % 60)


def parse_trim(start, end):
    """Form values -> (start_ms, end_ms), or None if neither was sent (default: the first 30 s).
    Only plain non-negative integers (at most 9 digits) are accepted: no floats, signs, NaN or spaces."""
    if start is None and end is None:
        return None
    if not (isinstance(start, str) and isinstance(end, str)
            and 0 < len(start) <= 9 and 0 < len(end) <= 9 and start.isascii() and end.isascii()
            and start.isdigit() and end.isdigit()):
        raise TrimError(TRIM_BAD)
    return int(start), int(end)


def resolve_trim(trim, duration_s):
    """Check (start_ms, end_ms) against the probed duration; None -> the default first 30 s.
    Returns (start_ms, end_ms) with 1 s <= end - start <= 30 s. Raises TrimError."""
    dur_ms = int(duration_s * 1000)
    if trim is None:
        return 0, max(1, min(dur_ms, MAX_SECONDS * 1000))
    start, end = trim
    if end > dur_ms and end - dur_ms <= TRIM_SLACK_MS:
        end = dur_ms
    if not 0 <= start < end <= dur_ms:
        raise TrimError(TRIM_BAD)
    if end - start > MAX_SECONDS * 1000:
        raise TrimError(TRIM_TOO_LONG.format(len=_mmss(end - start)))
    if end - start < MIN_KEEP_MS:
        raise TrimError(TRIM_TOO_SHORT)
    return start, end


def keep_seconds(info):
    """Seconds the converted clip will last: the trimmed part (info["keep"]) or the default first 30 s."""
    return info.get("keep") or min(info["duration"], MAX_SECONDS)


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


def _ffprobe(path, runner, whitelist):
    cmd = ["ffprobe", "-v", "error", "-probesize", "5M", "-analyzeduration", "5M",
           "-format_whitelist", FORMAT_WHITELIST, "-protocol_whitelist", "file",
           "-codec_whitelist", whitelist, "-print_format", "json", "-show_streams", "-show_format", "file:" + path]
    try:
        r = runner(cmd, capture_output=True, timeout=30, check=False)
        return (json.loads(r.stdout or b"{}") if r.returncode == 0 else None), (r.stderr or b"")
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None, b""


def probe(path, runner=None):
    """ffprobe the upload. Returns {codec, width, height, rotation, duration, fps} (width/height as
    displayed, i.e. after rotation). Raises VideoError(message for the phone) if it must be refused."""
    runner = runner or subprocess.run
    info = _ffprobe(path, runner, PROBE_WHITELIST)[0]
    if not info:
        raise VideoError(NOT_A_VIDEO)
    streams = info.get("streams") or []
    video = [s for s in streams if s.get("codec_type") == "video"
             and not (s.get("disposition") or {}).get("attached_pic")]
    # Sage: data tracks (iPhone tmcd/mebx) are fine by type, no decoder needed, convert drops them (-dn). Any
    # attachment, unknown stream type or non-mov_text subtitle -> 415 here. mov_text passes this check but is in
    # practice refused too: no subtitle decoder is whitelisted, so ffprobe fails above -> 415. At most MAX_STREAMS.
    bad = [s for s in streams if s.get("codec_type") not in ("video", "audio", "data", "subtitle")
           or (s.get("codec_type") == "subtitle" and s.get("codec_name") != "mov_text")]
    if len(video) != 1 or bad or len(streams) > MAX_STREAMS:
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
    return max(1, math.ceil(keep_seconds(info) * HIRES_RATE / 60))


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


def _uses_hw(info):
    return info["codec"] == "hevc" and not info.get("high_res")


def convert_cmd(src, dst, info, hwaccel=True):
    """The ffmpeg argv (without the nice/ionice/taskset/timeout prefix). hwaccel=False forces software decode."""
    hires = bool(info.get("high_res"))
    # HEVC: Pi 4 hardware decode (~55 % faster), but only up to 4K: over 4K it is refused anyway (Hale), so go
    # straight to software, with one decoder thread so the frame buffers fit the 1.5 GiB address-space cap.
    hw = ["-hwaccel", "drm"] if hwaccel and _uses_hw(info) else []
    # #103: input seek + length (frame-accurate, as everything is re-encoded); output -t stays as a cap.
    # Only these two formatted numbers ever reach ffmpeg from the phone.
    start, keep = "%.3f" % info.get("start", 0), "%.3f" % keep_seconds(info)
    return (["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-nostats", "-progress", "pipe:1", "-y",
             "-threads", "1" if hires else "2", *hw,
             *(["-noautorotate", "-display_rotation", "0"] if hires else []),   # turned in filter_graph instead
             "-format_whitelist", FORMAT_WHITELIST, "-protocol_whitelist", "file",
             "-codec_whitelist", CODEC_WHITELIST, "-probesize", "5M", "-analyzeduration", "5M",
             "-ss", start, "-t", keep, "-i", "file:" + src,
             "-filter_complex", filter_graph(info), "-map", "[v]", "-an", "-sn", "-dn",
             "-map_metadata", "-1", "-map_chapters", "-1", "-fflags", "+bitexact",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "26", "-maxrate", "2.5M", "-bufsize", "5M",
             "-profile:v", "high", "-g", "60", "-movflags", "+faststart", "-t", keep,
             "-fs", OUT_MAX_BYTES, "-f", "mp4", "file:" + dst])


def poster_cmd(src, dst):
    return ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-protocol_whitelist", "file",
            "-codec_whitelist", "h264", "-i", "file:" + src, "-frames:v", "1", "-q:v", "3", "-f", "image2", "-c:v", "mjpeg", "file:" + dst]


def timeout_for(info):
    rate = HIRES_TIMEOUT_RATE if info.get("high_res") else 10
    return int(max(60, rate * keep_seconds(info)))


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


HW_OPEN_ERRORS = ("Device creation failed", "Hardware device setup failed", "No device available for decoder")


def _hw_open_failed(msg):
    return any(m in msg for m in HW_OPEN_ERRORS)


def _run(cmd, timeout_s, runner):
    r = runner(_prefix(timeout_s) + _limit() + cmd, capture_output=True, timeout=timeout_s + 30, check=False)
    if r.returncode != 0:
        raise VideoError("ffmpeg exit %s: %s" % (r.returncode, (r.stderr or b"")[-300:].decode("utf-8", "replace")))


def _stop(p):
    """Stop ffmpeg and its wrappers (timeout/nice/prlimit/env run in its own session): TERM, then KILL."""
    import signal
    for sig, wait in ((signal.SIGTERM, CANCEL_GRACE_S), (signal.SIGKILL, 5)):
        try:
            os.killpg(p.pid, sig)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            p.wait(timeout=wait)
            return
        except subprocess.TimeoutExpired:
            continue


def _run_progress(cmd, timeout_s, popen, on_time=None, cancelled=None):
    """#103: run the convert like _run (same timeout/nice/prlimit prefix), reading ffmpeg's -progress lines.
    Only out_time_us=<int> is used (clamped, passed to on_time); every other key is ignored. The pipe is
    polled with a 1 s timeout, so cancelled() and the deadline are checked even while ffmpeg prints nothing
    (e.g. stalled before its first frame). stderr goes to an unlinked temp file (no pipe that could fill up
    and stall ffmpeg); only its last 300 bytes are ever read."""
    import select
    import tempfile
    with tempfile.TemporaryFile() as err:
        p = popen(_prefix(timeout_s) + _limit() + cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                  stderr=err, start_new_session=True)
        deadline = time.monotonic() + timeout_s + 30
        fd, buf = p.stdout.fileno(), b""
        try:
            while True:
                ready = select.select([fd], [], [], 1.0)[0]
                if cancelled is not None and cancelled():
                    _stop(p)
                    raise Cancelled()
                if time.monotonic() > deadline:           # belt and braces: `timeout` should have fired
                    _stop(p)
                    raise VideoError("ffmpeg timed out")
                if not ready:
                    continue
                chunk = os.read(fd, 4096)
                if not chunk:
                    break
                *lines, buf = (buf + chunk).split(b"\n")
                if len(buf) > MAX_PROGRESS_LINE:          # an overlong line is dropped, never buffered
                    buf = b""
                for line in lines:
                    k, _, v = line[:MAX_PROGRESS_LINE].strip().partition(b"=")
                    if k == b"out_time_us" and on_time is not None and v.isdigit():
                        on_time(min(int(v), 10**12) / 1e6)
            rc = p.wait(timeout=max(1, deadline - time.monotonic()))
        except BaseException:
            if p.poll() is None:
                _stop(p)
            raise
        finally:
            p.stdout.close()
        if rc != 0:
            err.seek(0, os.SEEK_END)
            err.seek(max(0, err.tell() - 300))
            raise VideoError("ffmpeg exit %s: %s" % (rc, err.read().decode("utf-8", "replace")))


def convert(data_dir, stored_name, runner=None, trim=None, on_progress=None, cancelled=None, popen=None):
    """Probe + convert one queued clip into originals/<stem>.mp4 with its poster and thumbnail.
    trim: the row's stored (start_ms, end_ms) or None (first 30 s). on_progress(pct 0-99) and cancelled()
    are used when ffmpeg runs through popen (production; tests that mock `runner` alone skip them).
    Returns duration_ms of the stored clip. Raises VideoError / Cancelled (nothing half-made is left)."""
    use_popen = popen is not None or runner is None
    runner = runner or subprocess.run
    popen = popen or subprocess.Popen
    src = input_path(data_dir, stored_name)
    part = part_path(data_dir, stored_name)
    final = os.path.join(data_dir, "originals", stored_name)
    info = probe(src, runner)
    try:
        start_ms, end_ms = resolve_trim(trim, info["duration"])
    except TrimError as e:                 # stored trim no longer fits the file: fail, never guess
        raise VideoError(str(e)) from e
    info.update(start=start_ms / 1000, keep=(end_ms - start_ms) / 1000)
    keep = info["keep"]

    def on_time(t):
        if on_progress is not None:
            on_progress(max(0, min(99, int(t * 100 / keep))))

    def run(cmd, budget):
        if use_popen:
            _run_progress(cmd, budget, popen, on_time, cancelled)
        else:
            _run(cmd, budget, runner)
    try:
        budget, t0 = timeout_for(info), time.monotonic()
        try:
            run(convert_cmd(src, part, info), budget)
        except VideoError as e:
            # Under User=mfapp the DRM device may be unusable (no render group, no /dev/dri): ffmpeg then fails at
            # once opening the hwaccel. Only THAT failure is retried, once, in software, with the same command
            # otherwise (limits, whitelists, filters) and only the time left of the original budget (Sage).
            left = int(budget - (time.monotonic() - t0))
            if not (_uses_hw(info) and _hw_open_failed(str(e))) or left < 1:
                raise
            log.warning("video: DRM hwaccel unavailable, decoding %s in software", stored_name)
            run(convert_cmd(src, part, info, hwaccel=False), left)
        if not os.path.exists(part) or os.path.getsize(part) == 0:
            raise VideoError("ffmpeg made no output")
        out_s = check_output(part, runner, keep)
        if cancelled is not None and cancelled():
            raise Cancelled()
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

def accept(conn, data_dir, tmp_path, original_name, digest, trim=None):
    """Move an uploaded video (already on the card, already hashed) into tmp/video-in and queue it.
    trim: checked (start_ms, end_ms) or None; stored on the row so a retry or a restart cuts the same part.
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
                     " created_at, state, trim_start_ms, trim_end_ms)"
                     " VALUES (?, ?, 'video', ?, ?, 'upload', ?, 'queued', ?, ?)",
                     (stored, original_name, digest, os.path.getsize(dst), db.now(),
                      *(trim if trim else (None, None))))
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
    'queued' at 0 % with resumed=1 and its stored trim (its input is still there) or to 'failed' (input
    gone). #103: a clip whose Cancel was pending is finished as cancelled (row and files removed).
    Returns (requeued, failed, swept)."""
    din, dout = _dirs(data_dir)
    swept = 0
    for f in os.listdir(dout) if os.path.isdir(dout) else []:
        try:
            os.remove(os.path.join(dout, f))
            swept += 1
        except OSError:
            pass
    for r in conn.execute("SELECT stored_name FROM media WHERE kind = 'video' AND cancel_requested = 1"
                          " AND state <> 'ready'").fetchall():
        conn.execute("DELETE FROM media WHERE stored_name = ?", (r["stored_name"],))
        imaging.remove_all(data_dir, r["stored_name"])
        remove_files(data_dir, r["stored_name"])
        swept += 1
    conn.commit()
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
                    conn.execute("UPDATE media SET state = 'queued', progress_pct = 0, resumed = 1"
                                 " WHERE stored_name = ?", (r["stored_name"],))
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


def work_one(conn, data_dir, runner=None, popen=None, clock=time.monotonic):
    """Convert the oldest queued clip. Returns its stored name, or None if the queue is empty."""
    row = conn.execute("SELECT id, stored_name, trim_start_ms, trim_end_ms FROM media WHERE kind = 'video'"
                       " AND state = 'queued' ORDER BY created_at, id LIMIT 1").fetchone()
    if row is None:
        return None
    if conn.execute("UPDATE media SET state = 'processing', progress_pct = 0 WHERE id = ? AND state = 'queued'",
                    (row["id"],)).rowcount != 1:
        conn.commit()
        return row["stored_name"]
    conn.commit()
    name, mid = row["stored_name"], row["id"]
    trim = (row["trim_start_ms"], row["trim_end_ms"]) if row["trim_end_ms"] is not None else None
    last = {"pct": 0, "write": 0.0, "check": 0.0, "cancel": False}

    def on_progress(pct):          # at most one write a second, and only when the percent moved
        now = clock()
        if pct != last["pct"] and now - last["write"] >= PROGRESS_WRITE_S:
            conn.execute("UPDATE media SET progress_pct = ? WHERE id = ? AND state = 'processing'", (pct, mid))
            conn.commit()
            last.update(pct=pct, write=now)

    def cancelled():               # Cancel pressed, or the clip deleted, while converting
        now = clock()
        if not last["cancel"] and now - last["check"] >= 0.5:
            last["check"] = now
            r = conn.execute("SELECT cancel_requested FROM media WHERE id = ?", (mid,)).fetchone()
            last["cancel"] = r is None or r[0] == 1
        return last["cancel"]
    try:
        ms = convert(data_dir, name, runner, trim=trim, on_progress=on_progress, cancelled=cancelled, popen=popen)
    except Cancelled:
        log.info("video %s cancelled", name)
        _drop(conn, data_dir, mid, name)
        return name
    except Exception as e:  # noqa: BLE001 - any failure marks the row failed; Retry requeues it
        log.warning("video %s failed: %s", name, e)
        if conn.execute("UPDATE media SET state = 'failed', progress_pct = 0 WHERE id = ? AND state = 'processing'"
                        " AND cancel_requested = 0", (mid,)).rowcount == 0:
            _drop(conn, data_dir, mid, name)      # cancelled (or deleted) while it failed: just remove it
        conn.commit()
        return name
    final = os.path.join(data_dir, "originals", name)
    n = conn.execute("UPDATE media SET state = 'ready', duration_ms = ?, size_bytes = ?, progress_pct = 100,"
                     " resumed = 0 WHERE id = ? AND state = 'processing' AND cancel_requested = 0",
                     (ms, os.path.getsize(final), mid)).rowcount
    conn.commit()
    if n == 1:
        remove_files(data_dir, name)          # the input is only dropped once the clip is ready
    else:                                     # cancelled / removed while converting: its files go too
        _drop(conn, data_dir, mid, name)
    return name


def _drop(conn, data_dir, media_id, name):
    """Cancel finished: the row (if still there), the input, the .part and anything made from it go."""
    conn.execute("DELETE FROM media WHERE id = ? AND state <> 'ready'", (media_id,))
    conn.commit()
    imaging.remove_all(data_dir, name)
    remove_files(data_dir, name)


def status(conn, row):
    """#103: what the phone shows for one clip (no ffmpeg text ever reaches it, only the percent):
    state (queued/processing/cancelling/ready/failed), progress 0-99 (100 once ready), queue position
    (1 = next in line) while queued, resumed after a restart."""
    st = row["state"]
    out = {"id": row["id"], "state": st, "progress": 100 if st == "ready" else int(row["progress_pct"] or 0),
           "resumed": bool(row["resumed"]) and st in ("queued", "processing")}
    if st == "ready":
        out["duration_ms"] = row["duration_ms"]            # the Library turns the tile into "▶ m:ss"
    if row["cancel_requested"] and st != "ready":
        out["state"] = "cancelling"
    elif st == "queued":
        out["queue_pos"] = conn.execute(
            "SELECT COUNT(*) FROM media WHERE kind = 'video' AND state = 'queued' AND cancel_requested = 0"
            " AND (created_at < ? OR (created_at = ? AND id <= ?))",
            (row["created_at"], row["created_at"], row["id"])).fetchone()[0]
    return out


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
