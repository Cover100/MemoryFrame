"""Where to crop a photo for the frame (Designer's MF-26 framing rules, no-face part).

Two stages:
1. Choose the *visible window* W: the largest SCREEN_ASPECT (5:3, the 800x480 screen)
   rectangle that fits the image, placed by the rules below. Only moved, never zoomed.
2. Expand W to the output aspect (the display-copy target) around W's
   centre, clamped to the image. The frame shows copies with CSS object-fit: cover
   (centred), so on screen exactly the middle 5:3 band of the copy is visible - which is W
   unless clamping had to shift the box.
With the default 1000x600 (5:3) target, stage 2 is a no-op and the copy IS the window;
it stays generic for other sizes (e.g. a 4:3 1000x750 target).

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


def expand_to(window, w, h, out_aspect):
    """Stage 2: the largest out_aspect box centred on the window's centre, clamped to the image.
    Returns an int box (left, top, right, bottom)."""
    left, top, ww, wh = window
    cx, cy = left + ww / 2, top + wh / 2
    ow, oh = window_size(w, h, out_aspect)                # largest box of the output aspect
    ol = _clamp(cx - ow / 2, 0, w - ow)
    ot = _clamp(cy - oh / 2, 0, h - oh)
    box = (int(round(ol)), int(round(ot)), int(round(ol + ow)), int(round(ot + oh)))
    return (box[0], box[1], min(box[2], w), min(box[3], h))


def visible_band(box, screen_aspect=SCREEN_ASPECT):
    """The part of an output box that object-fit: cover (centred) shows on the screen."""
    l, t, r, b = box
    bw, bh = r - l, b - t
    vw, vh = window_size(bw, bh, screen_aspect)
    return (l + (bw - vw) / 2, t + (bh - vh) / 2, l + (bw + vw) / 2, t + (bh + vh) / 2)


def crop_box(size, target, faces=None):
    """Crop box (in the image) for a display copy of `size` aimed at `target` (w, h)."""
    w, h = size
    window = fallback_window(w, h)
    return expand_to(window, w, h, target[0] / target[1])
