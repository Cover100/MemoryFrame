"""Builds frame/07-menu-AvsB.png: menu option A and option B side by side at 1:1, with captions (not a mock itself)."""
import os
from PIL import Image, ImageDraw, ImageFont
root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FB = "/usr/share/fonts/truetype/sand-box/google/Atkinson Hyperlegible/AtkinsonHyperlegible-Bold.ttf"
FR = "/usr/share/fonts/truetype/sand-box/google/Atkinson Hyperlegible/AtkinsonHyperlegible-Regular.ttf"
if not os.path.exists(FB): FB = FR = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
cols = [("07-menu-optA", "A: Details menu (your reference layout)",
         "Info table (IP, Wi-Fi, Site, storage, phones, version) · message strip · Close / Shut down along the bottom"),
        ("07-menu-optB", "B: Simple menu (basic info left, buttons right)",
         "Title, gift message, free storage · Shut down, Pair, Sign out, Close on the right · details move to the phone")]
pad, head, cap = 32, 76, 84
W = 2 * 800 + 3 * pad; H = head + 480 + cap + pad
out = Image.new("RGB", (W, H), "#0E1311"); d = ImageDraw.Draw(out)
d.text((pad, 22), "Memory Frame: double-tap menu, option A vs option B (800x480, 1:1)", fill="#F3EDE4", font=ImageFont.truetype(FB, 30))
for i, (name, t, sub) in enumerate(cols):
    x = pad + i * (800 + pad)
    out.paste(Image.open(os.path.join(root, "frame", name + ".png")).convert("RGB"), (x, head))
    d.rectangle([x - 1, head - 1, x + 800, head + 480], outline="#5A4535")
    d.text((x, head + 480 + 16), t, fill="#D6B36A", font=ImageFont.truetype(FB, 24))
    d.text((x, head + 480 + 50), sub, fill="#C9BBA8", font=ImageFont.truetype(FR, 17))
out.save(os.path.join(root, "frame", "07-menu-AvsB.png"), optimize=True)
print(out.size)
