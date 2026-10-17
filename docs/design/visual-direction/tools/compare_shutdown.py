"""Builds frame/14-shutdown-compare.png: the earlier shutdown cover and the restyled final screen side by side at 1:1,
with captions (not a mock itself)."""
import os
from PIL import Image, ImageDraw, ImageFont
root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FB = "/usr/share/fonts/truetype/sand-box/google/Atkinson Hyperlegible/AtkinsonHyperlegible-Bold.ttf"
FR = "/usr/share/fonts/truetype/sand-box/google/Atkinson Hyperlegible/AtkinsonHyperlegible-Regular.ttf"
if not os.path.exists(FB): FB = FR = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
cols = [("14-shutdown-progress", "1. Shutting down… (#shuttingDown, unchanged)",
         "Live while the frame syncs · dark ink screen · spinning brass arc · \"Don't unplug yet\""),
        ("14-shutdown-final-v2", "2. Final screen (#safeScreen, restyled)",
         "Last frame before power-off, stays frozen · walnut card, brass frame · static plug + stopwatch")]
pad, head, cap = 20, 72, 84
W = 2 * 800 + 3 * pad; H = head + 480 + cap + pad
out = Image.new("RGB", (W, H), "#0E1311"); d = ImageDraw.Draw(out)
d.text((pad, 20), "Memory Frame: shutdown, earlier screen vs final screen (800x480, 1:1, words unchanged)", fill="#F3EDE4", font=ImageFont.truetype(FB, 28))
for i, (name, t, sub) in enumerate(cols):
    x = pad + i * (800 + pad)
    out.paste(Image.open(os.path.join(root, "frame", name + ".png")).convert("RGB"), (x, head))
    d.rectangle([x - 1, head - 1, x + 800, head + 480], outline="#5A4535")
    d.text((x, head + 480 + 16), t, fill="#D6B36A", font=ImageFont.truetype(FB, 24))
    d.text((x, head + 480 + 50), sub, fill="#C9BBA8", font=ImageFont.truetype(FR, 18))
out.save(os.path.join(root, "frame", "14-shutdown-compare.png"), optimize=True)
print(out.size)
