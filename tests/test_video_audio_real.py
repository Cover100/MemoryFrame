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


@pytest.mark.parametrize("fname,acodec", [("ac3.mp4", "ac3"), ("alaw.mov", "pcm_alaw"), ("flac.mp4", "flac")])
def test_unlisted_audio_is_refused_by_probe(tmp_path, fname, acodec):
    with pytest.raises(video.VideoError) as e:
        video.probe(make(str(tmp_path / fname), acodec=acodec))
    assert str(e.value) == video.NOT_A_VIDEO


def upload(mf_app, paired, path):
    import io
    mf_app.config["VIDEOS"] = True
    data = open(path, "rb").read()
    return paired.post("/api/v1/media", data={"file": (io.BytesIO(data), "clip.mp4")},
                                  content_type="multipart/form-data", environ_base={"REMOTE_ADDR": "10.42.0.23"})


BASE = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=320x240:r=30:d=2", "-f", "lavfi", "-i", "sine=d=2"]
X264 = ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-shortest"]


def build(path, extra_in, maps_codecs):
    subprocess.run(BASE + extra_in + maps_codecs + [path], check=True)
    return path


def srt(tmp_path):
    p = str(tmp_path / "s.srt")
    open(p, "w").write("1\n00:00:00,000 --> 00:00:01,000\nhi\n")
    return p


def refused(mf_app, paired, path):
    r = upload(mf_app, paired, path)
    assert r.status_code == 415, r.get_data(as_text=True)
    assert video.NOT_A_VIDEO in r.get_data(as_text=True)


@pytest.mark.parametrize("acodec", ["ac3", "pcm_alaw"])
def test_unlisted_audio_upload_is_415_not_500(mf_app, paired, tmp_path, acodec):
    refused(mf_app, paired, make(str(tmp_path / ("a." + ("mp4" if acodec == "ac3" else "mov"))), acodec=acodec))


def test_iphone_style_mov_with_tmcd_and_aac_converts_to_video_only(mf_app, paired, tmp_path):
    src = build(str(tmp_path / "iphone.mov"), [],
                X264 + ["-c:a", "aac", "-timecode", "01:00:00:00", "-metadata:s:v", "rotate=0"])
    kinds = [s["codec_type"] for s in streams(src)]
    assert "data" in kinds and "audio" in kinds
    assert video.probe(src)["codec"] == "h264"
    ms, out, poster = convert(tmp_path, src)
    assert [s["codec_type"] for s in streams(out)] == ["video"] and os.path.getsize(poster) > 0
    assert upload(mf_app, paired, src).status_code == 202


def test_mov_text_subtitle_outcome(tmp_path):
    """Sage: mov_text allowed by name with NO subtitle decoder on the whitelist; refused if ffprobe then errors."""
    src = build(str(tmp_path / "sub.mp4"), ["-i", srt(tmp_path)],
                ["-map", "0", "-map", "1", "-map", "2"] + X264 + ["-c:a", "aac", "-c:s", "mov_text"])
    assert "subtitle" in [s["codec_type"] for s in streams(src)]
    try:
        video.probe(src)
        outcome = "accepted"
    except video.VideoError:
        outcome = "refused"
    # ffmpeg 7: ffprobe opens the mov_text decoder, which is not whitelisted -> refused (415 at upload).
    assert outcome == "refused"


@pytest.mark.parametrize("scodec", ["srt", "ass"])
def test_srt_ass_subtitles_are_415(mf_app, paired, tmp_path, scodec):
    refused(mf_app, paired, build(str(tmp_path / "s.mkv"), ["-i", srt(tmp_path)],
                                  ["-map", "0", "-map", "1", "-map", "2"] + X264 + ["-c:a", "aac", "-c:s", scodec]))


def test_attachment_is_415(mf_app, paired, tmp_path):
    font = str(tmp_path / "f.ttf")
    open(font, "wb").write(b"\0" * 64)
    refused(mf_app, paired, build(str(tmp_path / "att.mkv"), [], X264 + ["-c:a", "aac", "-attach", font,
                                  "-metadata:s:t", "mimetype=application/x-truetype-font"]))


def test_more_than_eight_streams_is_415(mf_app, paired, tmp_path):
    maps = ["-map", "0:v"] + ["-map", "1:a"] * 8           # 1 video + 8 AAC = 9 streams
    refused(mf_app, paired, build(str(tmp_path / "many.mp4"), [], maps + X264 + ["-c:a", "aac"]))


def test_unlisted_video_codec_with_audio_is_still_refused(tmp_path):
    src = make(str(tmp_path / "mjpeg.mov"), vcodec="mjpeg", acodec="pcm_alaw")
    with pytest.raises(video.VideoError):
        video.probe(src)


def test_amr_3gp_is_accepted(tmp_path):
    if " amr_nb " not in subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout \
            and "libopencore_amrnb" not in subprocess.run(["ffmpeg", "-hide_banner", "-encoders"],
                                                          capture_output=True, text=True).stdout:
        pytest.skip("no AMR encoder")
    src = str(tmp_path / "a.3gp")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=176x144:r=15:d=2", "-f", "lavfi",
                    "-i", "sine=d=2", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-ar", "8000", "-ac", "1",
                    "-c:a", "libopencore_amrnb", "-b:a", "12.2k", "-shortest", src], check=True)
    assert video.probe(src)["width"] == 176
