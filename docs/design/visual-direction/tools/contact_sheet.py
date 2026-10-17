"""Build contact sheets (frame/_contact-sheet.png, phone/_contact-sheet.png) from the rendered PNGs."""
import glob, os
from PIL import Image, ImageDraw, ImageFont
root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONT = "/usr/share/fonts/truetype/sand-box/google/Atkinson Hyperlegible/AtkinsonHyperlegible-Bold.ttf"
if not os.path.exists(FONT): FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
def sheet(d, cols, scale, title):
    files = sorted(glob.glob(os.path.join(root, d, "[0-9]*.png")))
    ims = [Image.open(f).convert("RGB") for f in files]
    w, h = int(ims[0].width * scale), int(ims[0].height * scale)
    pad, lab, head = 24, 34, 70
    rows = (len(ims) + cols - 1) // cols
    W = cols * w + (cols + 1) * pad; H = head + rows * (h + lab + pad) + pad
    out = Image.new("RGB", (W, H), "#0E1311"); dr = ImageDraw.Draw(out)
    f1 = ImageFont.truetype(FONT, 30); f2 = ImageFont.truetype(FONT, 18)
    dr.text((pad, 20), title, fill="#F3EDE4", font=f1)
    for i, (im, f) in enumerate(zip(ims, files)):
        x = pad + (i % cols) * (w + pad); y = head + (i // cols) * (h + lab + pad)
        dr.text((x, y + 4), os.path.basename(f)[:-4], fill="#D6B36A", font=f2)
        out.paste(im.resize((w, h), Image.LANCZOS), (x, y + lab))
        dr.rectangle([x - 1, y + lab - 1, x + w, y + lab + h], outline="#5A4535")
    out.save(os.path.join(root, d, "_contact-sheet.png"), optimize=True)
    print(d, out.size)
sheet("frame", 3, 0.5, "Memory Frame: frame screens (800x480, shown at 50%)")
if glob.glob(os.path.join(root, "phone", "[0-9]*.png")):
    sheet("phone", 4, 0.6, "Memory Frame: phone site (412x915, shown at 60%)")
