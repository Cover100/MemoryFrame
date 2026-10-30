"""Generate neutral, photo-like placeholder images (procedural, no external photos) and the QR codes
used by the mocks. Run from docs/design/visual-direction with Python 3 + Pillow + segno."""
import math, random
from PIL import Image, ImageDraw, ImageFilter
import segno

def lerp(a, b, t): return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))

def scene(w, h, seed, sky_top, sky_bot, hill_cols, sun=None, trees=8):
    rnd = random.Random(seed)
    im = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(im)
    for y in range(h):
        d.line([(0, y), (w, y)], fill=lerp(sky_top, sky_bot, y / h))
    if sun:
        x, y, r, c = sun
        glow = Image.new("L", (w, h), 0)
        ImageDraw.Draw(glow).ellipse([x - r * 1.6, y - r * 1.6, x + r * 1.6, y + r * 1.6], fill=150)
        glow = glow.filter(ImageFilter.GaussianBlur(r * 0.9))
        im = Image.composite(Image.new("RGB", (w, h), c), im, glow)
        d = ImageDraw.Draw(im)
        d.ellipse([x - r / 2, y - r / 2, x + r / 2, y + r / 2], fill=c)
    base = h * 0.55
    for i, col in enumerate(hill_cols):
        amp = h * (0.06 + 0.03 * i); off = rnd.random() * 10; y0 = base + i * h * 0.12
        pts = [(0, h)]
        for x in range(0, w + 8, 8):
            pts.append((x, y0 + amp * math.sin(x / w * (2.2 + i) + off) + amp * 0.4 * math.sin(x / w * 9 + off * 2)))
        pts.append((w, h)); d.polygon(pts, fill=col)
        if i == len(hill_cols) - 2:
            for _ in range(trees):
                tx = rnd.randint(0, w); ty = y0 + amp * math.sin(tx / w * (2.2 + i) + off) + 4
                tr = rnd.randint(int(h * 0.05), int(h * 0.11))
                tc = lerp(col, (10, 30, 15), 0.45)
                d.rectangle([tx - 3, ty - tr * 0.3, tx + 3, ty + 6], fill=(60, 45, 32))
                d.ellipse([tx - tr * 0.7, ty - tr * 1.6, tx + tr * 0.7, ty - tr * 0.2], fill=tc)
    im = im.filter(ImageFilter.GaussianBlur(1.2))
    # film grain so it reads as a photo, not a flat illustration
    noise = Image.effect_noise((w, h), 18).convert("RGB")
    return Image.blend(im, noise, 0.06)

S = [
    ((96, 150, 205), (200, 220, 235), [(118, 150, 92), (84, 124, 66), (62, 100, 50)], (620, 110, 70, (255, 246, 220))),
    ((230, 150, 110), (250, 210, 160), [(140, 110, 90), (100, 80, 66), (70, 58, 48)], (200, 230, 90, (255, 230, 170))),
    ((70, 110, 160), (170, 200, 220), [(90, 120, 130), (60, 90, 96), (40, 66, 70)], None),
    ((150, 190, 220), (225, 235, 240), [(170, 160, 120), (130, 140, 90), (96, 110, 70)], (150, 90, 60, (255, 250, 235))),
    ((40, 50, 90), (210, 120, 100), [(70, 60, 80), (50, 45, 60), (30, 28, 40)], (400, 300, 120, (255, 200, 140))),
    ((120, 170, 200), (210, 230, 220), [(110, 140, 80), (150, 120, 70), (90, 80, 50)], None),
]
scene(800, 480, 1, *S[0]).save("assets/photo-landscape.jpg", quality=88)
for i, s in enumerate(S):
    sx = list(s)
    if sx[3]: sx[3] = (sx[3][0] * 0.4, sx[3][1] * 0.6, sx[3][2] * 0.5, sx[3][3])
    scene(320, 320, i + 10, *sx, trees=4).save(f"assets/thumb-{i + 1}.jpg", quality=82)

# QR codes: real payloads in the frame's formats (dummy password / token)
segno.make("WIFI:T:WPA;S:MemoryFrame-4F2A;P:walnut-forest-27;;", error="m").save(
    "assets/qr-wifi.svg", scale=10, border=4, dark="#000000", light="#FFFFFF")
segno.make("http://10.42.0.1/pair?t=Kq7vR2mX9pLs", error="m").save(
    "assets/qr-site.svg", scale=10, border=4, dark="#000000", light="#FFFFFF")
segno.make("WIFI:T:WPA;S:Garden Frame;P:new-password-88;;", error="m").save(
    "assets/qr-wifi-new.svg", scale=10, border=4, dark="#000000", light="#FFFFFF")
print("ok")
