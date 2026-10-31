"""#115 (Hale, Pi): phone clips WITH SOUND were refused, because ffprobe opens every stream's decoder and the
codec whitelist had no audio codecs. These use the real ffmpeg (skipped where it is not installed, e.g. CI):
small clips are generated per test, probed, converted and checked (no audio in the output, poster made)."""
import json
import os
import shutil
import subprocess

import pytest

from memoryframe import video

pytestmark = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="needs ffmpeg")


def make(path, vcodec="libx264", acodec="aac", rotate=None, size="320x240"):
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s={size}:r=30:d=2",
           "-f", "lavfi", "-i", "sine=f=440:d=2", "-c:v", vcodec, "-pix_fmt", "yuv420p", "-c:a", acodec, "-shortest"]
    if vcodec == "libx265":
        cmd += ["-x265-params", "log-level=error"]
    tmp = path + ".tmp" + os.path.splitext(path)[1]
    subprocess.run(cmd + [tmp], check=True)
    if rotate is None:
        os.replace(tmp, path)
    else:   # phone style: coded landscape + display matrix (portrait when shown)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-display_rotation", str(rotate), "-i", tmp,
                        "-c", "copy", path], check=True)
        os.remove(tmp)
    return path


def streams(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", path],
                       capture_output=True, check=True)
    return json.loads(r.stdout)["streams"]


def convert(tmp_path, src):
    d = str(tmp_path / "data")
    name = "e" * 32 + ".mp4"
    dst = video.input_path(d, name)
    for p in (dst, video.part_path(d, name), video.poster_path(d, name)):
        os.makedirs(os.path.dirname(p), exist_ok=True)
    for sub in ("originals", "thumbs", "display"):
        os.makedirs(os.path.join(d, sub), exist_ok=True)
    shutil.copy(src, dst)
    ms = video.convert(d, name)
    out = os.path.join(d, "originals", name)
    return ms, out, video.poster_path(d, name)


CASES = [
    ("h264_aac.mp4", dict(acodec="aac"), (320, 240)),
    ("h264_aac_portrait.mp4", dict(acodec="aac", rotate=-90), (240, 320)),
    ("hevc_aac.mov", dict(vcodec="libx265", acodec="aac"), (320, 240)),
    ("h264_opus.mp4", dict(acodec="libopus"), (320, 240)),
    ("h264_mp3.mp4", dict(acodec="libmp3lame"), (320, 240)),
    ("h264_alaw.mov", dict(acodec="pcm_alaw"), (320, 240)),   # NOT on the audio list: video-only fallback
]


@pytest.mark.parametrize("fname,kw,size", CASES, ids=[c[0] for c in CASES])
def test_clip_with_audio_is_probed_and_converted_without_audio(tmp_path, fname, kw, size):
    src = make(str(tmp_path / fname), **kw)
    assert any(s["codec_type"] == "audio" for s in streams(src))
    info = video.probe(src)
    assert (info["width"], info["height"]) == size
    assert info["rotation"] == (90 if kw.get("rotate") else 0)
    ms, out, poster = convert(tmp_path, src)
    assert 1500 <= ms <= 2100
    out_streams = streams(out)
    assert [s["codec_type"] for s in out_streams] == ["video"]
    assert (out_streams[0]["width"], out_streams[0]["height"]) == (800, 480)
    assert os.path.getsize(poster) > 0
    assert not os.path.exists(src + ".probe.mov")


def test_unlisted_video_codec_with_audio_is_still_refused(tmp_path):
    src = make(str(tmp_path / "mjpeg.mov"), vcodec="mjpeg", acodec="pcm_alaw")
    with pytest.raises(video.VideoError):
        video.probe(src)
    assert not os.path.exists(src + ".probe.mov")
