"""MF-26: offline face detection for display-copy framing (OpenCV YuNet).

Runs only while display copies are generated (upload / `display-copies`), never at display
time. Model: models/face_detection_yunet_2023mar.onnx (~230 KB, vendored) from OpenCV Zoo,
MIT licence (c) 2020 Shiqi Yu - see models/LICENSE-YuNet. No network access.

Any problem (OpenCV not installed, model missing, detector error, too slow) -> [] and the
no-face framing rules apply. Never raises.
"""
import logging
import os
import threading
import time
from dataclasses import dataclass

log = logging.getLogger(__name__)

MODEL = os.path.join(os.path.dirname(__file__), "models", "face_detection_yunet_2023mar.onnx")
DET_MAX_SIDE = 640      # detect on a downscaled copy; boxes are mapped back
CONF_MIN = 0.80
BUDGET_S = 1.5          # slower than this -> treat as no faces (Designer 8.5)

_lock = threading.Lock()
_logged = set()


@dataclass(frozen=True)
class Face:
    x: float            # box in original-image pixels
    y: float
    w: float
    h: float
    score: float = 1.0
    eye_y: float = None  # mean eye y (YuNet landmarks), if known
    det_h: float = None  # box height in the detector image (FACE_MIN_PX rule)


def _once(key, msg, *a):
    if key not in _logged:
        _logged.add(key)
        log.warning(msg, *a)


def enabled():
    return os.environ.get("MEMORYFRAME_FACE_DETECT", "1").strip().lower() not in ("0", "false", "no", "off")


def _run_detector(arr):
    """YuNet on a BGR array. Replaced by tests (slow fake detector)."""
    import cv2
    det = cv2.FaceDetectorYN.create(MODEL, "", (arr.shape[1], arr.shape[0]), CONF_MIN, 0.3, 50)
    _, rows = det.detect(arr)
    return rows


def _run_with_budget(arr, budget):
    """Run the detector in a worker thread; give up after `budget` s (the worker finishes on
    its own and its result is discarded). Returns rows, or None on timeout/error/busy."""
    deadline = time.monotonic() + budget                # lock wait + detection share one budget
    if budget <= 0 or not _lock.acquire(timeout=budget):   # one detection at a time
        log.warning("face detection over its %.1f s budget; using no-face framing", BUDGET_S)
        return None
    box = {}

    def work():
        try:
            box["rows"] = _run_detector(arr)
        except Exception as e:  # noqa: BLE001
            box["err"] = e
        finally:
            _lock.release()

    th = threading.Thread(target=work, name="mf-face-detect", daemon=True)
    th.start()
    th.join(max(0.0, deadline - time.monotonic()))
    if th.is_alive():
        log.warning("face detection took longer than %.1f s; ignoring it (no-face framing)", BUDGET_S)
        return None
    if "err" in box:
        _once(("err", type(box["err"]).__name__), "face detection failed (%s); using no-face framing", box["err"])
        return None
    return box.get("rows")


def detect(im):
    """Faces in a PIL RGB image (already EXIF-transposed). Never raises."""
    if not enabled():
        return []
    try:
        import cv2
        import numpy as np
    except Exception as e:  # noqa: BLE001
        _once("import", "face detection unavailable (OpenCV): %s; using no-face framing", e)
        return []
    try:
        cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)   # quiet DNN backend notes
    except Exception:  # noqa: BLE001
        pass
    if not os.path.exists(MODEL):
        _once("model", "face model missing at %s; using no-face framing", MODEL)
        return []
    t0 = time.monotonic()
    try:
        w, h = im.size
        s = min(1.0, DET_MAX_SIDE / max(w, h))           # pre-downscale before detection
        small = im
        if s < 1.0:
            f = int(1 / s) // 2                                     # fast integer pre-reduce
            if f >= 2:
                small = small.reduce(f)
            small = small.resize((max(1, round(w * s)), max(1, round(h * s))))
        arr = cv2.cvtColor(np.asarray(small.convert("RGB")), cv2.COLOR_RGB2BGR)
    except Exception as e:  # noqa: BLE001
        _once(("err", type(e).__name__), "face detection failed (%s); using no-face framing", e)
        return []
    rows = _run_with_budget(arr, BUDGET_S - (time.monotonic() - t0))
    if rows is None:
        return []
    faces = []
    for r in rows if rows is not None else []:
        x, y, fw, fh = (float(v) / s for v in r[:4])
        eye_y = (float(r[5]) + float(r[7])) / 2 / s
        faces.append(Face(x, y, fw, fh, float(r[-1]), eye_y, float(r[3])))
    return faces
