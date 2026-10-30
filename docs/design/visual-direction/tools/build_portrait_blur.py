"""Build the MF #86 comparison mocks. usage: build.py SET OUTDIR   (SET = mixed | cc0)"""
import sys, os, json, glob
sys.path.insert(0, os.path.dirname(__file__))
from portrait_blur import *
from PIL import Image, ImageDraw

Image.MAX_IMAGE_PIXELS = None
SRC = os.environ.get("MF_CC0_DIR", "/workspace/portrait-blur/src")   # CC0 example photos (see README credits)
REAL = os.environ.get("MF_REAL_DIR", "/workspace/MemoryFrame-wt/mf-04-kiosk/uploads")   # the user's own photos: NOT committed
setname, out = sys.argv[1], sys.argv[2]
os.makedirs(out, exist_ok=True)
L = lambda p: Image.open(p)

def real(n): return L(f"{REAL}/{n}")
cc = lambda n: L(f"{SRC}/{n}")
CHILD = "Child_portrait_2016_(Unsplash).jpg"; KAREN = "Karen_Elder_(Unsplash).jpg"
ALP = "Alpine_village_landscape_(Unsplash).jpg"; ROD = "Rodanthe_Pier_beach_landscape_(Unsplash).jpg"
VB = "Virginia_Beach_Oceanfront_2024-08-31.jpg"

# (key, label, sub-label, image, focus_y)  focus_y = vertical placement of any cropped window
if setname == "mixed":
    rows = [
     ("p1", "Portrait 3:4", "REAL photo (your frame's photos)", crop_aspect(real("20260618_133956.jpg") if False else ImageOps.exif_transpose(real("20260618_133956.jpg")), 0.75), 0.5),
     ("p2", "Tall portrait 9:16", "real, cropped to 9:16", crop_aspect(real("20240425_145528.jpg"), 9/16, 0.62), 0.5),
     ("p3", "Portrait 2:3, face", "EXAMPLE photo (CC0)", cc(CHILD), 0.35),
     ("sq", "Square 1:1", "real, cropped to 1:1", crop_aspect(real("20240425_145447.jpg"), 1.0), 0.5),
     ("c54", "Near-square 5:4", "real, cropped to 5:4", crop_aspect(real("20240425_145514.jpg"), 1.25), 0.5),
     ("l43", "Landscape 4:3", "REAL photo", real("20240425_145431.jpg"), 0.5),
     ("n1610", "Near-5:3, 16:10", "real, cropped to 16:10", crop_aspect(real("20240425_145456.jpg"), 1.6), 0.5),
     ("l169", "Landscape 16:9", "real, cropped to 16:9", crop_aspect(real("20240425_145431.jpg"), 16/9, 0.5, 0.55), 0.5),
    ]
    tag = "Real photos from the frame's own sample set (4:3 Jeep and 3:4 coast shots) plus one CC0 example face portrait. Crops are marked."
else:
    rows = [
     ("p1", "Portrait 3:4", "EXAMPLE photo (CC0)", cc(VB), 0.5),
     ("p2", "Tall portrait 9:16", "example, cropped to 9:16", crop_aspect(cc(CHILD), 9/16, 0.55, 0.35), 0.35),
     ("p3", "Portrait 2:3, face", "EXAMPLE photo (CC0)", cc(CHILD), 0.35),
     ("sq", "Square 1:1", "example, cropped to 1:1", crop_aspect(cc(KAREN), 1.0, 0.5, 0.2), 0.3),
     ("c54", "Near-square 5:4", "example, cropped to 5:4", crop_aspect(cc(ALP), 1.25), 0.5),
     ("l43", "Landscape 4:3", "example, cropped to 4:3", crop_aspect(cc(ALP), 4/3), 0.5),
     ("n1610", "Near-5:3, 16:10", "example, cropped to 16:10", crop_aspect(cc(ALP), 1.6), 0.5),
     ("l169", "Landscape 16:9", "EXAMPLE photo (CC0)", cc(ROD), 0.5),
    ]
    tag = "Example photos only (all CC0). See README for credits."

rows = [(k, a, b, prep_stored(im), fy) for k, a, b, im, fy in rows]

def render(img, fy, opt):
    if opt == "today":
        return cover(img, CANVAS, fy), dict(mode="cover", coverage=1.0, kept=None)
    p = {k: v for k, v in OPTIONS[opt].items() if k != "name"}
    return make_display_copy_blurfill(img, focus_y=fy, **p)

COLS = ["today", "A1", "A2", "B1", "B2"]
info = {}
tiles = {}
for key, lab, sub, img, fy in rows:
    base, _ = render(img, fy, "today")
    for c in COLS:
        im, inf = render(img, fy, c)
        inf = dict(inf); inf["aspect"] = round(img.width / img.height, 3)
        inf["same_as_today"] = bool(ImageChops.difference(im, base).getbbox() is None)
        inf["size"] = im.size
        info[f"{key}:{c}"] = inf
        tiles[(key, c)] = to_screen(im)

# ---- per-option full-size 800x480 PNGs for portrait 1 and the face example
for key in ("p1", "p3"):
    for c in COLS:
        tiles[(key, c)].save(f"{out}/{key}-{c if c!='today' else 'today'}-800x480.png")

BG = (14, 19, 17); FG = (240, 236, 228); MUT = (160, 168, 160); ACC = (143, 209, 164)
captions = {
 "today": "TODAY: centred crop, fills screen, loses most of a portrait",
 "A1": "A1: whole photo kept, light blur (r15)",
 "A2": "A2: whole photo kept, strong blur r40 + 35% dim",
 "B1": "B1: 15% trim, light blur (r15)",
 "B2": "B2: 15% trim, strong blur r40 + 35% dim",
}
# ---- comparison.png  (1700 wide)
W = 1700; LABW = 190; G = 10
tw = (W - LABW - G * 6) // 5; th = round(tw * 3 / 5)
HEAD = 150; ROWH = th + 34 + G
Himg = HEAD + len(rows) * ROWH + 70
sheet = Image.new("RGB", (W, Himg), BG); d = ImageDraw.Draw(sheet)
d.text((24, 16), "Portrait photos on the frame: blurred-sides options (#86)", font=font(34, True), fill=FG)
d.text((24, 62), "Each tile is the real 800x480 (5:3) screen, scaled down. " + tag, font=font(18), fill=MUT)
d.text((24, 88), "Photos under 1.30 aspect get blur-fill; 1.30 and wider stay full-bleed exactly as today (marked 'same as today').", font=font(18), fill=MUT)
for ci, c in enumerate(COLS):
    x = LABW + G + ci * (tw + G)
    d.text((x, HEAD - 30), c.upper() if c == "today" else c, font=font(26, True), fill=ACC if c != "today" else FG)
for ri, (key, lab, sub, img, fy) in enumerate(rows):
    y = HEAD + ri * ROWH
    d.text((24, y + 4), lab, font=font(20, True), fill=FG)
    d.text((24, y + 30), sub, font=font(14), fill=MUT)
    d.text((24, y + 52), f"aspect {img.width/img.height:.2f}", font=font(14), fill=MUT)
    for ci, c in enumerate(COLS):
        x = LABW + G + ci * (tw + G)
        t = tiles[(key, c)].resize((tw, th), Image.LANCZOS)
        sheet.paste(t, (x, y))
        inf = info[f"{key}:{c}"]
        if c == "today":
            cap = "fills screen"
        elif inf["mode"] == "cover":
            cap = "same as today (full-bleed)"
        else:
            cap = f"photo uses {inf['coverage']*100:.0f}% of screen"
            if inf["kept"] and inf["kept"] < 0.999: cap += f", {inf['kept']*100:.0f}% of height kept"
        d.text((x, y + th + 6), cap, font=font(14), fill=MUT)
yb = HEAD + len(rows) * ROWH + 4
for i, c in enumerate(COLS[1:]):
    pass
d.text((24, yb), "A1/A2 keep the whole photo.  B1/B2 trim 15% of the photo's height (6.5% top and bottom) so the photo is bigger and the side bars narrower.  Light = blur r15, no dim.  Strong = blur r40 + 35% dim (radii at 1000x600).", font=font(15), fill=MUT)
d.text((24, yb + 24), "Soft shadow on the sharp photo's edge in all options (no border). " + ("Photos from the frame's sample set are marked REAL; CC0 examples are marked EXAMPLE." if setname == "mixed" else "All photos are CC0 examples, not the user's own."), font=font(15), fill=MUT)
sheet.save(f"{out}/comparison.png")

# ---- big readable sheet per featured photo: 3x2 grid of 560x336
def bigsheet(key, fname, title):
    tw2 = 540; th2 = round(tw2 * 3 / 5); Wb = tw2 * 3 + G * 4; Hb = 80 + 2 * (th2 + 52) + 20
    s = Image.new("RGB", (Wb, Hb), BG); dd = ImageDraw.Draw(s)
    dd.text((G + 6, 14), title, font=font(30, True), fill=FG)
    for i, c in enumerate(COLS):
        x = G + (i % 3) * (tw2 + G); y = 80 + (i // 3) * (th2 + 52)
        s.paste(tiles[(key, c)].resize((tw2, th2), Image.LANCZOS), (x, y + 6))
        dd.text((x, y + th2 + 12), captions[c], font=font(16, True), fill=ACC if c != "today" else FG)
        inf = info[f"{key}:{c}"]
        sub = "fills the screen (cropped)" if c == "today" else f"photo uses {inf['coverage']*100:.0f}% of the screen" + (f", bars {inf['bar']*100:.0f}% each side" if 'bar' in inf else "")
        dd.text((x, y + th2 + 32), sub, font=font(14), fill=MUT)
    i = 5; x = G + 2 * (tw2 + G); y = 80 + (th2 + 52)
    dd.text((x, y + 10), "Soft shadow edge on the sharp photo,\nno border. 800x480 PNGs of each tile\nare in this folder to zoom.", font=font(16), fill=MUT)
    s.save(f"{out}/{fname}")
bigsheet("p1", "p1-all-options.png", "Portrait 3:4: Today and options A1, A2, B1, B2")
bigsheet("p3", "p3-all-options.png", "Portrait 2:3 with a face (example photo, CC0)")

# ---- edge treatment (decision 4): none / soft shadow / hairline, on A2, portrait 1
key = "p1"; img = [r for r in rows if r[0] == key][0]
p = {k: v for k, v in OPTIONS["A2"].items() if k != "name"}
edge = []
for lab, sh, hair in (("No edge treatment", None, False), ("Soft shadow (chosen)", SHADOW, False), ("Hairline 2px, 25% white", None, True)):
    im, inf = make_display_copy_blurfill(img[3], focus_y=img[4], shadow=sh, **p)
    if hair:
        d2 = ImageDraw.Draw(im); x0 = (1000 - inf["sharp_w"]) // 2
        ov = Image.new("RGBA", im.size, (0, 0, 0, 0)); od = ImageDraw.Draw(ov)
        od.rectangle((x0 - 2, 0, x0 + inf["sharp_w"] + 1, 599), outline=(255, 255, 255, 64), width=2)
        im = Image.alpha_composite(im.convert("RGBA"), ov).convert("RGB")
    edge.append((lab, to_screen(im)))
tw3 = 540; th3 = 324; s = Image.new("RGB", (tw3 * 3 + G * 4, 70 + th3 + 50), BG); dd = ImageDraw.Draw(s)
dd.text((16, 14), "Edge of the sharp photo against the blur (shown on A2)", font=font(28, True), fill=FG)
for i, (lab, t) in enumerate(edge):
    x = G + i * (tw3 + G); s.paste(t.resize((tw3, th3), Image.LANCZOS), (x, 66)); dd.text((x, 66 + th3 + 10), lab, font=font(18, True), fill=FG if i != 1 else ACC)
s.save(f"{out}/edge-options.png")

# ---- numbers
cov = {c: info[f"p1:{c}"] for c in COLS}
json.dump({k: {kk: (round(vv, 4) if isinstance(vv, float) else vv) for kk, vv in v.items()} for k, v in info.items()}, open(f"{out}/numbers.json", "w"), indent=1)
for k, v in info.items():
    print(k, v["mode"], f"cov={v['coverage']:.3f}", f"kept={v['kept']}", "same_as_today" if v["same_as_today"] else "")
