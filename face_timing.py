#!/usr/bin/env python3
"""MF-26 face-detection timing on the Pi's real originals. READ-ONLY.

Opens each image in ORIGINALS (default /var/lib/memoryframe/originals) read-only, runs
memoryframe.faces.detect() exactly as copy generation would (EXIF transpose, RGB) and
prints per-image time, faces found, and whether the 1.5 s budget / no-face fallback hit.
Writes nothing anywhere (results go to stdout only).

Usage (from the PR #36 worktree, with its own venv):
    python face_timing.py [ORIGINALS_DIR] [--repeat N]
"""
import argparse, logging, os, statistics, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("MEMORYFRAME_FACE_DETECT", "1")

from PIL import Image, ImageOps  # noqa: E402
try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except Exception:  # noqa: BLE001
    pass
from memoryframe import faces, framing  # noqa: E402

EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".bmp", ".tif", ".tiff"}


class Catch(logging.Handler):
    def __init__(self):
        super().__init__(); self.msgs = []
    def emit(self, r):
        self.msgs.append(r.getMessage())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dir", nargs="?", default="/var/lib/memoryframe/originals")
    ap.add_argument("--repeat", type=int, default=1)
    a = ap.parse_args()
    h = Catch(); logging.getLogger("memoryframe").addHandler(h); logging.getLogger("memoryframe").setLevel(logging.INFO)
    files = sorted(f for f in os.listdir(a.dir) if os.path.splitext(f)[1].lower() in EXTS)
    print(f"budget {faces.BUDGET_S}s, model {os.path.basename(faces.MODEL)}, {len(files)} images in {a.dir}")
    print(f"{'file':40} {'size':>11} {'load_ms':>8} {'detect_ms':>10} {'faces':>5}  result")
    times = []; over = fb = 0
    for f in files:
        p = os.path.join(a.dir, f)
        try:
            t0 = time.monotonic()
            with open(p, "rb") as fh, Image.open(fh) as im0:
                im = ImageOps.exif_transpose(im0).convert("RGB")
            load = time.monotonic() - t0
        except Exception as e:  # noqa: BLE001
            print(f"{f[:40]:40} unreadable: {e}"); continue
        for _ in range(a.repeat):
            h.msgs.clear()
            t0 = time.monotonic(); found = faces.detect(im); dt = time.monotonic() - t0
            times.append(dt)
            timed_out = any("budget" in m for m in h.msgs)
            failed = [m for m in h.msgs if "budget" not in m]
            kept = framing.filter_faces(found, *im.size) if found else []
            if timed_out: res = "TIMEOUT -> no-face fallback"; over += 1
            elif failed: res = "ERROR -> no-face fallback: " + failed[0][:60]; fb += 1
            elif not kept: res = "no faces -> no-face fallback"; fb += 1
            else: res = f"face framing ({len(kept)} kept)"
            print(f"{f[:40]:40} {im.size[0]:>5}x{im.size[1]:<5} {load*1000:8.0f} {dt*1000:10.0f} {len(found):>5}  {res}")
    if times:
        print(f"\n{len(times)} detections: median {statistics.median(times)*1000:.0f} ms, "
              f"max {max(times)*1000:.0f} ms, over budget {over}, fallback (no face/error) {fb}")


if __name__ == "__main__":
    main()
