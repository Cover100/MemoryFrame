# 15 Portrait photos: blurred sides (issue #86)

Status: design proposal for user sign-off. Mocks: `frame/15-portrait-blur/` (CC0 example photos only; the user's own photos are never committed). Reference code: `tools/portrait_blur.py`, mock builder: `tools/build_portrait_blur.py`.

## 1. What the user sees

A portrait is shown whole (or nearly), sharp and centred, on the 5:3 screen. The two side areas are filled with an enlarged, blurred copy of the same photo, slightly dimmed. Landscape and near-5:3 photos are unchanged.

| Option | Photo kept | Side blur (radius at 1000x600) | Side dim | Portrait 3:4 occupies |
|---|---|---|---|---|
| Today | centred crop, ~45% of the photo's height | none | none | 100% of the screen |
| A1 | whole photo | 15 | 0% | 45% |
| A2 | whole photo | 40 | 35% black | 45% |
| B1 | 15% trim (6.5% top and bottom) | 15 | 0% | 52% |
| B2 | 15% trim | 40 | 35% black | 52% |

Other portrait shapes (A / B): 9:16 34% / 39%, 2:3 40% / 46%, 1:1 60% / 69%, 5:4 75% / 80%.
Radii at the real 800x480 screen are 0.8x these (15 -> 12, 40 -> 32), which matches the 12-16 / 30-40 ranges in the brief.

**Recommendation: A2.** The whole picture stays; B only adds about 7 points of screen for a portrait and cuts 13% of the photo (feet, hands, the top of heads), so it is not worth it. A2 over A1: with a light blur the bars still show recognisable shapes (a second, smudged copy of the scene) that compete with the photo, and the dim keeps bright bars from glaring in a dark room. Dim 25-35% is tunable without a re-design.

## 2. Which photos get what (exact thresholds)

`a = width / height` of the upright stored copy (after EXIF rotation, #85). Display aspect `R = 5/3 = 1.667`.

| Aspect a | Examples | Treatment |
|---|---|---|
| **a >= 1.30** | 4:3 (1.33), 3:2 (1.5), 16:10 (1.6), 16:9 (1.78), panoramas | **Full-bleed face-aware cover, exactly as today** (framing.py, MF-26). No bars, no blur, byte-identical to today's copy. |
| **a < 1.30** | 5:4 (1.25), 1:1, 4:5, 3:4, 2:3, 9:16 and taller | **Blur-fill** (option A or B). |

Why 1.30:
- Blur-fill bars are `(1 - a/R)/2` of the width per side: 12.5% at 5:4, 20% at 1:1, 27.5% at 3:4. At a = 1.30 they are 11% (88 px at 800 wide). 4:3 would be exactly 10%: the thinnest bar we accept is therefore **10% per side**, and the threshold sits just above 4:3 so the most common phone landscape never gets bars.
- Cover on a 1.30 photo loses 22% of the height (20% for 4:3), which face-aware framing already handles well. Below 1.30 the loss grows fast (1:1 loses 40%, 3:4 loses 55%), which is where the user's complaint starts.
- No hysteresis needed (the copy is made once per photo).
- Safety net for option B: the sharp photo is never wider than 80% of the canvas (`MAX_SHARP_W = 0.80`), so even with trim a bar is never thinner than 10% per side (5:4 with B: 10%, trim shrinks to 6%).
- Near-5:3 (1.5 to 1.8) is always cover: proof row "Near-5:3, 16:10" in the mock is identical to Today in every column (the builder asserts pixel equality and prints `same_as_today`).
- Near-square: 1:1 and 5:4 are treated as portraits (blur-fill). Their bars (20% / 12.5%) are wide enough to look intentional, not like thin letterbox slivers.
- Open question for the user: should 4:3 photos (20% of their height cropped today) also be blur-filled? Bars would be 10% per side. Default: no.

## 3. Pipeline: baked into the 1000x600 display copy

The sharp photo and the blur are **one 1000x600 JPEG**. The 800x480 screen only scales it by 0.8 (`object-fit: cover`, same aspect), so **no CSS or kiosk change, no runtime blur, no `backdrop-filter`, no `filter: blur`**. The stored 1500xN copy (#85) is never modified: it keeps the full uncropped photo, and the backfill regenerates display copies from it.

Reference code is in `tools/portrait_blur.py` (`make_display_copy_blurfill`). Steps, all in 1000x600 canvas pixels:

1. `src` = stored copy, upright RGB. `a = w/h`. If `a >= 1.30`: existing cover path, stop.
2. Background: take the same 5:3 window as today's cover (face-aware window from framing.py, centre when no face). Resize it to **250x150** (canvas / 4), `GaussianBlur(radius / 4)` (A1/B1 radius 15 -> 3.75, A2/B2 radius 40 -> 10), upscale to 1000x600 with BILINEAR. Doing the blur at 1/4 size is about 2x cheaper than at full size and gives the same look.
3. Dim (A2/B2): `Image.blend(bg, black, 0.35)`.
4. Sharp photo: height `fh = 600 * k`, `k = max(1.0, min(trim, 0.80 * 1000 / (a * 600)))` with `trim = 1.00` (A) or `1.15` (B); width `fw = fh * a`; LANCZOS resize from the stored copy. For B, crop the extra rows to 600: centre by default; with a face, shift the crop so the padded face box (framing.py PAD_TOP / PAD_BOTTOM) stays inside, and if it cannot fit, fall back to `k = 1` (option A) for that photo, never cut a head.
5. Soft edge: a rectangle at 35% black blurred with radius 12 under the photo, then paste the photo at `((1000 - fw) / 2, (600 - fh_visible) / 2)`. No border (a 2 px hairline was tried and is not worth it; `edge-options.png`). The shadow is subtle; dropping it is also fine.
6. Save as JPEG with the existing display-copy quality. Measured on the 3:4 example: today 133 KB, A1 82 KB, A2 76 KB, B1 89 KB, B2 84 KB: under the existing cap (the blurred bars compress very well).

Constants for Kit (1000x600): `ASPECT_BLURFILL_BELOW = 1.30`, `MAX_SHARP_W = 0.80`, `BG_SCALE = 4`, `radius = 40`, `dim = 0.35`, `trim = 1.00` for the recommended A2 (B would be `1.15`, i.e. 6.5% of the photo's height trimmed top and bottom), `SHADOW = (radius 12, alpha 0.35)`.

## 4. Performance on the Pi 4

- **Slideshow: zero cost.** The frame shows one ready-made JPEG, so a transition costs the same as today. This is the whole reason to bake it in.
- **Generation: one-off per photo**, at upload (or in the backfill). On the x86 box used for the mocks the whole blur-fill copy takes about 60 ms (the quarter-size background about 25 ms, mostly the resize). A Pi 4 is probably 4-8x slower, so roughly 0.2-0.5 s per photo: not measured, Hollis to time it (AC7). The backfill should stay serial and low priority.
- **Memory:** no extra full-size decode. The inputs are the stored 1500xN copy (about 9 MB decoded at 1500x2000) plus a 250x150 buffer and a 1000x600 canvas (1.8 MB). Use `Image.reduce` / `thumbnail(..., reducing_gap=2.0)` for the background source so the full stored copy is not resampled twice.
- Do not do this in CSS: a large blurred layer re-rasterised on the Pi's GPU, plus `backdrop-filter`, is exactly what the Pi rules ban.
- Background must never contain sharp detail (AC8): it is blurred at 1/4 scale then upscaled, so there is none; the test can check mean horizontal gradient in a bar crop (the mock measured 0.17 grey levels per pixel on A1).

## 5. Test photos for AC8 (suggested)

3:4 portrait, 9:16 tall, 1:1, 5:4, 4:3, 16:10, 16:9 and an EXIF-rotated portrait (stored copy upright, orientation tag gone). Assert: `a >= 1.30` copies are pixel-identical to today's; blur-fill copies are 1000x600; the sharp column is centred within 1 px; bars >= 10% each; the bar crop has no sharp edges.

## 6. Decisions needed from the user

1. Whole photo (A) or slight trim (B)? Recommendation A.
2. Light blur (1) or strong blur with dim (2)? Recommendation 2.
3. Should 4:3 photos stay full-bleed (default, 1.30 threshold) or also be blur-filled (threshold 1.40)?

## 7. Photo credits for the committed mocks (all CC0, Wikimedia Commons, example photos only)
Child portrait 2016 by Laura Aziz (https://unsplash.com/photos/fn2EMPjPBHo); Karen Elder by zjtcpts (https://unsplash.com/photos/i9uIhSlOWYc); Alpine village landscape by Mira Bozhko (https://unsplash.com/photos/WCm0wjn1y04); Rodanthe Pier beach landscape by Khürt Williams (https://unsplash.com/photos/hLv41HgZv1Y); Virginia Beach Oceanfront 2024-08-31 by TheWxResearcher. Some rows are crops of these to get each aspect. The version shown to the user may use the frame's own sample photos instead; those are not committed.
