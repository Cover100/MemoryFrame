"""Portrait blur-fill display copy: reference implementation + mock renderer (MF #86).

make_display_copy_blurfill() is the Pillow reference Kit can port into imaging.py.
Everything is in 1000x600 canvas pixels (the real display copy). Mocks are made
at 1000x600 and scaled to 800x480 (what the screen does), so mock == pipeline.
"""
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps, ImageChops

CANVAS = (1000, 600)
R = CANVAS[0] / CANVAS[1]          # 5/3
ASPECT_BLURFILL_BELOW = 1.30       # a < 1.30 -> blur-fill; a >= 1.30 -> face-aware cover (as today)
BG_SCALE = 4                       # blur at 1/4 size (250x150), then upscale: cheap on the Pi

OPTIONS = {
    #        trim: max sharp height / canvas height; radius at 1000x600; dim = black fraction
    "A1": dict(trim=1.00, radius=15, dim=0.00, name="A1  whole photo, light blur"),
    "A2": dict(trim=1.00, radius=40, dim=0.35, name="A2  whole photo, strong blur + dim"),
    "B1": dict(trim=1.15, radius=15, dim=0.00, name="B1  slight trim, light blur"),
    "B2": dict(trim=1.15, radius=40, dim=0.35, name="B2  slight trim, strong blur + dim"),
}
SHADOW = dict(radius=12, alpha=0.35)   # soft edge on the sharp photo (decision 4)
MAX_SHARP_W = 0.80                     # sharp photo never wider than 80% of canvas (bars >= 10%/side)


def cover(im, size=CANVAS, focus_y=0.5):
    """Today's behaviour: scale to cover, crop. focus_y = vertical window position (0..1)."""
    cw, ch = size
    s = max(cw / im.width, ch / im.height)
    w, h = round(im.width * s), round(im.height * s)
    r = im.resize((w, h), Image.LANCZOS)
    x = (w - cw) // 2
    y = round((h - ch) * focus_y)
    return r.crop((x, y, x + cw, y + ch))


def make_display_copy_blurfill(src, trim=1.0, radius=15, dim=0.0, shadow=SHADOW,
                               focus_y=0.5, force=False, canvas=CANVAS):
    """src: upright RGB stored copy (any size). Returns (1000x600 RGB image, info dict)."""
    cw, ch = canvas
    a = src.width / src.height
    if a >= ASPECT_BLURFILL_BELOW and not force:
        return cover(src, canvas, focus_y), dict(mode="cover", coverage=1.0, kept=None)

    # 1. background: cover-sized window of the photo, blurred at 1/4 scale, upscaled
    bw, bh = cw // BG_SCALE, ch // BG_SCALE
    small = cover(src, (bw, bh), focus_y)
    small = small.filter(ImageFilter.GaussianBlur(radius / BG_SCALE))
    bg = small.resize(canvas, Image.BILINEAR)
    if dim:
        bg = Image.blend(bg, Image.new("RGB", canvas, (0, 0, 0)), dim)

    # 2. sharp photo: fit inside, optionally enlarged (trim) but never wider than MAX_SHARP_W
    k = max(1.0, min(trim, MAX_SHARP_W * cw / (a * ch)))   # sharp height / canvas height
    fh = round(ch * k)
    fw = round(fh * a)
    fg = src.resize((fw, fh), Image.LANCZOS)
    top = round((fh - ch) * focus_y) if fh > ch else 0   # trimmed rows are cut from top/bottom
    if fh > ch:
        fg = fg.crop((0, top, fw, top + ch))
    fh_vis = fg.height
    x0, y0 = (cw - fw) // 2, (ch - fh_vis) // 2

    # 3. soft edge: blurred rectangle shadow, then paste the sharp photo
    if shadow:
        m = Image.new("L", canvas, 0)
        ImageDraw.Draw(m).rectangle((x0, y0, x0 + fw - 1, y0 + fh_vis - 1), fill=int(255 * shadow["alpha"]))
        m = m.filter(ImageFilter.GaussianBlur(shadow["radius"]))
        bg = Image.composite(Image.new("RGB", canvas, (0, 0, 0)), bg, m)
    bg.paste(fg, (x0, y0))
    kept = (fh_vis / fh)            # fraction of the photo's height still visible
    return bg, dict(mode="blurfill", coverage=(fw * fh_vis) / (cw * ch), kept=kept,
                    sharp_w=fw, sharp_h=fh_vis, bar=(cw - fw) / 2 / cw, k=k)


def to_screen(im):
    """What the 800x480 screen does with the 1000x600 copy."""
    return im.resize((800, 480), Image.LANCZOS)


def prep_stored(im, max_w=1500, max_h=2000):
    """Simulate the #85 stored copy: upright, no EXIF, <=1500 wide / <=2000 tall."""
    im = ImageOps.exif_transpose(im).convert("RGB")
    s = min(1.0, max_w / im.width, max_h / im.height)
    return im.resize((round(im.width * s), round(im.height * s)), Image.LANCZOS) if s < 1 else im


def crop_aspect(im, a, cx=0.5, cy=0.5):
    """Crop to aspect ratio a (w/h), window centred at (cx, cy) fractions."""
    if im.width / im.height > a:
        w, h = round(im.height * a), im.height
    else:
        w, h = im.width, round(im.width / a)
    x = round((im.width - w) * cx)
    y = round((im.height - h) * cy)
    return im.crop((x, y, x + w, y + h))


def font(size, bold=False):
    base = "/usr/share/fonts/truetype/sand-box/google/Atkinson Hyperlegible/AtkinsonHyperlegible-"
    try:
        return ImageFont.truetype(base + ("Bold" if bold else "Regular") + ".ttf", size)
    except OSError:
        return ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans" + ("-Bold" if bold else "") + ".ttf", size)
