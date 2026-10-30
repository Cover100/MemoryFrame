# 11 Video on the phone (#102)

Phone dark theme (PHONE-DARK.md), rendered at 360 px. Builds on the 09 Library. New styles: `video.css`.

Mocks: `11a-library-video.png` (default: both chips on, badges, queued/converting/failed tiles), `11b-library-filter-one-on.png` (Photos off; Videos is the last one on and locked, with focus ring).

- **Upload:** `＋ Upload` replaces `＋ Upload photos` (same primary fill, 56 px). Note under it, oat text: `Photos and videos. Videos play silently, up to 30 seconds. The frame keeps its own copy, not your original.` The upload page (03) title becomes `Upload` with the same note; the picker reads `Choose photos or videos` with the hint `From your phone's gallery. Photos: JPEG, PNG, WebP, GIF or HEIC. Videos: MP4, MOV or WebM, up to 350 MB.`.
- **Filter chips** `Photos` / `Videos`: 48 px tall, pill. On = green fill, white text, ✓. Off = transparent, 1.5px oat border, linen text. Both on by default. When only one is on it gets `aria-disabled="true"`; tapping it does nothing except show `At least one stays on.` (role=status). Focus ring brass. Count line follows the filter (`96 photos · 14 videos` / `Showing 14 videos`). Filter is session-only.
- **Play badge:** bottom-left, `▶ m:ss`, 14 px bold linen on ink at 82% opacity, 6 px radius; readable on any thumbnail (white-sky test: thumb-1).
- **Tile states** (not selectable until ready): `Waiting` (queued) and `Preparing` with a static leaf spinner (converting) on an ink 72% scrim over the poster if one exists; `Couldn't add` failed tile on red-50: the brick-text label stays as visible text, with two 48x48 SVG icon buttons side by side below it (8 px gap): a retry arrow (oat 1.5px outline, linen icon, `aria-label="Retry video"`) and a remove cross (oat 1.5px outline, brick-text icon, `aria-label="Remove video"`; Remove follows 09c confirm rules). Both are `disabled` in select mode. Ready video tiles are labelled `Video N of M, m:ss` at all times (on load and when a tile becomes ready live). No runtime blur.

## Acceptance
- Button reads `＋ Upload` and accepts photos and videos; the note is visible without scrolling at 360 px.
- Chips ≥48 px tall, both on at load; the last one on can't be turned off; hint announced.
- Every video tile shows `▶ m:ss`; badge contrast ≥4.5:1 on a white thumbnail.
- Queued, converting and failed tiles render as mocked; failed shows `Couldn't add` plus two 48x48 SVG icon buttons (8 px gap) labelled `Retry video` / `Remove video`, remove icon in brick-text, both disabled in select mode, nothing clipped at 360 px and 130 %; ready video appears without reload; ready tiles always labelled `Video N of M, m:ss`.
- Upload picker reads `Choose photos or videos` with the format hint above.
- Green text only via --mf-accent-text; brick only as fill, brick-text for text; focus brass; Atkinson from /frame-fonts.css; no outside requests.
- 360 px and 130 % text: no horizontal scroll, nothing clipped.
