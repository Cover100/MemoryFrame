"""Where to crop a photo for the frame (Designer's MF-26 framing rules, target 1000x600).

The display copy IS the visible window W: the largest 5:3 rectangle that fits the image,
placed by the rules below (faces if found, otherwise the no-face fallback), cropped and then
scaled down to 1000x600 (never up). The 800x480 screen is also 5:3, so CSS object-fit: cover
only scales it - what we choose here is exactly what is shown.

All coordinates are original-image pixels after EXIF transpose. Boxes are (left, top, right, bottom).
"""

SCREEN_ASPECT = 5 / 3          # the 800x480 display; flip here if the screen changes
TALL_AR = 0.56                 # aspect <= this: "very tall" (9:16 portraits, screenshots)
PANO_AR = 2.2                  # aspect >= this: panorama
FALLBACK_PORTRAIT_Y = 0.30     # no face, portrait: window centre at 30 % of the height
FALLBACK_LANDSCAPE_TOP = 0.35  # no face, landscape narrower than 5:3: 35 % of the slack on top
FALLBACK_TALL_TOP = 0.15       # no face, very tall: window starts 15 % down


def _clamp(v, lo, hi):
    return max(lo, min(v, hi))


def window_size(w, h, aspect=SCREEN_ASPECT):
    """Largest aspect-ratio rectangle that fits in w x h (floats)."""
    ww = min(w, h * aspect)
    return ww, ww / aspect


def fallback_window(w, h, aspect=SCREEN_ASPECT):
    """No-face (or no-detector) placement of the visible window. Returns (left, top, ww, wh)."""
    ww, wh = window_size(w, h, aspect)
    ar = w / h
    left = (w - ww) / 2                                   # always centred sideways
    if ar >= PANO_AR:
        top = (h - wh) / 2                                # window is full height anyway
    elif ar <= TALL_AR:
        top = FALLBACK_TALL_TOP * h
    elif h > w:
        top = FALLBACK_PORTRAIT_Y * h - wh / 2
    elif ar < aspect:
        top = FALLBACK_LANDSCAPE_TOP * (h - wh)
    else:
        top = (h - wh) / 2
    return _clamp(left, 0, w - ww), _clamp(top, 0, h - wh), ww, wh


# ---------------- faces (Designer sections 1-5, 8) ----------------

FACE_MIN_FRAC = 0.04
FACE_MIN_PX = 24
SMALL_REL = 0.35
EDGE_CUT_MAX = 0.40
PAD_X, PAD_TOP, PAD_BOTTOM = 0.30, 0.60, 0.50
EYE_LINE = 0.36
HEADROOM_MIN = 0.05
CLOSEUP = 0.85


def eye_y(f):
    return f.eye_y if f.eye_y is not None else f.y + 0.40 * f.h


def padded(f):
    return (f.x - PAD_X * f.w, f.y - PAD_TOP * f.h, f.x + f.w + PAD_X * f.w, f.y + f.h + PAD_BOTTOM * f.h)


def _union(boxes):
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


def _outside_frac(f, w, h):
    ix = max(0.0, min(f.x + f.w, w) - max(f.x, 0))
    iy = max(0.0, min(f.y + f.h, h) - max(f.y, 0))
    area = f.w * f.h
    return 1 - (ix * iy) / area if area > 0 else 1


def filter_faces(faces, w, h):
    short = min(w, h)
    keep = [f for f in faces or []
            if f.h >= FACE_MIN_FRAC * short and (f.det_h is None or f.det_h >= FACE_MIN_PX)
            and _outside_frac(f, w, h) <= EDGE_CUT_MAX]
    if keep:
        biggest = max(f.h for f in keep)
        keep = [f for f in keep if f.h >= SMALL_REL * biggest]
    return keep


def _order(faces, w, h):
    """Designer 4 + 8.1: score = area_norm*0.6 + centrality*0.4; ties: larger, more central, leftmost."""
    import math
    amax = max(f.w * f.h for f in faces)
    def key(f):
        cx, cy = f.x + f.w / 2, f.y + f.h / 2
        cen = 1 - math.hypot(cx - w / 2, cy - h / 2) / (math.hypot(w, h) / 2)
        score = (f.w * f.h / amax) * 0.6 + cen * 0.4
        return (-round(score, 9), -f.w * f.h, -cen, f.x)
    return sorted(faces, key=key)


def _top_for(eye, group, wh):
    """Eye line at 36 %, headroom guard, then containment of the group wins (8.2)."""
    top = eye - EYE_LINE * wh
    guarded = min(top, group[1] - HEADROOM_MIN * wh)
    if group[3] <= guarded + wh:                     # guard only if chin+padding still fits
        top = guarded
    if group[3] - group[1] <= wh:                    # containment wins over the eye line
        top = min(max(top, group[3] - wh), group[1])
    return top


def _left_avoiding(group, ww, w, dropped):
    """Horizontal position: centred on the group, but prefer edges that don't cut dropped faces."""
    lo, hi = max(0.0, group[2] - ww), min(group[0], w - ww)
    centred = (group[0] + group[2]) / 2 - ww / 2
    if hi < lo:
        return centred
    cands = sorted({lo + (hi - lo) * i / 20 for i in range(21)} | {min(max(centred, lo), hi)},
                   key=lambda v: abs(v - centred))
    for left in cands:
        if not any(f.x < left < f.x + f.w or f.x < left + ww < f.x + f.w for f in dropped):
            return left
    return min(max(centred, lo), hi)


def face_window(w, h, faces, aspect=SCREEN_ASPECT):
    """Visible window for kept faces. Returns (left, top, ww, wh, kept)."""
    ww, wh = window_size(w, h, aspect)
    group = _union([padded(f) for f in faces])
    kept = list(faces)
    if len(faces) > 1 and (group[2] - group[0] > ww or group[3] - group[1] > wh):
        kept = []
        for f in _order(faces, w, h):                # greedy, best first
            g = _union([padded(k) for k in kept + [f]])
            if g[2] - g[0] <= ww and g[3] - g[1] <= wh:
                kept.append(f)
        if not kept:
            kept = [_order(faces, w, h)[0]]
        group = _union([padded(f) for f in kept])
    dropped = [f for f in faces if f not in kept]
    if len(kept) == 1:
        f = kept[0]
        pb = padded(f)
        if pb[3] - pb[1] > CLOSEUP * wh:             # close-up: headroom fixed at 5 %, chin may go
            top = pb[1] - HEADROOM_MIN * wh
        elif pb[2] - pb[0] > ww or pb[3] - pb[1] > wh:   # padded box too big: use the bare box
            top = min(max(eye_y(f) - EYE_LINE * wh, f.y + f.h - wh), f.y)
        else:
            top = _top_for(eye_y(f), pb, wh)
        left = _left_avoiding(pb, ww, w, dropped) if pb[2] - pb[0] <= ww else f.x + f.w / 2 - ww / 2
    else:
        area = sum(f.w * f.h for f in kept)
        eye = sum(eye_y(f) * f.w * f.h for f in kept) / area
        top = _top_for(eye, group, wh)
        left = _left_avoiding(group, ww, w, dropped)
    return _clamp(left, 0, w - ww), _clamp(top, 0, h - wh), ww, wh, kept


def _int_box(window, w, h):
    l, t, ww, wh = window
    left, top = int(round(l)), int(round(t))
    bw = min(w - left, int(round(ww)))
    bh = min(h - top, int(round(wh)))
    return (left, top, left + bw, top + bh)


def choose(size, faces=None, screen_aspect=SCREEN_ASPECT):
    """Returns (crop box, window (l, t, ww, wh), kept faces)."""
    w, h = size
    kept = filter_faces(faces, w, h)
    if kept:
        l, t, ww, wh, kept = face_window(w, h, kept, screen_aspect)
        window = (l, t, ww, wh)
    else:
        window = fallback_window(w, h, screen_aspect)
    return _int_box(window, w, h), window, kept


def crop_box(size, target=None, faces=None):
    """Crop box (in the image) for a display copy: the 5:3 window. `target` is only checked
    to have the screen's aspect (the copy is scaled to it afterwards)."""
    return choose(size, faces)[0]
