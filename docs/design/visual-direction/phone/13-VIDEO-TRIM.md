# 13 · Video trim and conversion progress (#103, Phase 2)

Mocks: 13a trim, 13b trim too long, 13c upload progress, 13d Library preparing, 13e trim at 130% text. Styles: `video.css` + `trim.css`. Dark theme B tokens only (PHONE-DARK.md). Follows Sage's approach (issue comment 6552044756): the phone picks the points, the Pi trims within its single convert.

## a) Trim screen (13a, 13b, 13e)
- **Reached from:** (1) the upload flow, right after picking a video longer than 30 s, *before* upload (the phone previews the local file in `<video>` and sends `trim_start_ms` / `trim_end_ms` with it); (2) a ready video tile in the Library → `Trim` (see open question 4).
- **Layout:** bar `‹ Trim video` (back = Cancel). 16:9 preview with a 56 px round play/pause (`aria-label` "Play selection" / "Pause"), playing only the selected part, silent. Timecode badge bottom right.
- **Length line:** `0:30 selected` (22 px bold, tabular, `role="status"`) and muted `of 1:14 · up to 0:30`.
- **Timeline:** one track (forest-700), selection fill forest-600 (brick-text #E7A097 when invalid, for contrast on the track), linen playhead. Start/end handles: 18×44 visible grip inside a 48×48 touch target; track inset 24 px each side so handles at 0:00 / end never clip. Times `0:00` / total under the ends. **No frame-strip thumbnails** (costly on the Pi and on old phones).
- **Default:** first 30 s (or the whole clip if shorter, never under 1 s).
- **Limits:** selection 1–30 s. Handles stay at least 1 s apart (dragging pushes against the other handle, never crosses). Too long: `That part is 0:42. Choose 30 seconds or less.` Too short (only possible via typed/keyboard edge cases): `That part is under 1 second. Choose at least 1 second.` Both on red-50, brick-text, `role="alert"`; `Use this part` is disabled and `aria-describedby` the message (13b).
- **Actions:** `Use this part` primary 56 px full width; `Cancel` outlined (1.5px oat). Cancel from upload drops that video from the batch; from the Library, leaves the clip unchanged.
- **Keyboard / screen readers:** handles are `role="slider"` with `aria-label` Start/End, `aria-valuemin/max/now` in seconds, `aria-valuetext="Start 0:06"`. Arrow keys step 0.5 s, PageUp/Down 5 s, Home/End to the limits. Hint text under the actions says so.

## b) Conversion progress (13c, 13d)
- **Upload row:** after upload, `Added. The frame is preparing it.` + percent on the right, then a determinate bar (8 px, leaf on forest-700, `role="progressbar"`, `aria-valuetext="38%, about 3 minutes left"`). The over-4K note from #115 stays as a second muted line: `This video is very high resolution and may take about N minutes to prepare.` Queued clips show `Waiting to prepare · next in line` (queue position).
- **Library tile:** `Preparing` tile gets a 40 px ring (leaf on forest-700) plus `Preparing 38%`; tile `aria-label="Video preparing, 38%"`. Waiting tile unchanged.
- **Progress behaviour:** server polls ~every 2 s; percent caps at **99%** until the clip is ready, then the tile becomes a normal ready tile (`Video N of M, m:ss`). The bar steps to each new value with a short transform transition: no fake/indeterminate animation between polls. Announce politely (`aria-live="polite"`) only at 25/50/75% and when ready, not every tick.
- **Cancel:** preparing and waiting items have `Cancel` (upload row: 48 px outlined button; Library tile: 48×48 × button top right, `aria-label="Cancel preparing video"`; disabled in select mode). Tapping it shows `Cancelling…`: row/tile muted (oat text, ~0.6 opacity), button disabled, until the item disappears.
- **Power cut:** after a restart the clip resumes on its own from 0%; show a muted line `Resumed after restart` under the status until it is ready.
- **Failure:** becomes the existing 11a failed tile (`Couldn't add` + Retry / Remove icon buttons); the upload row shows `Couldn't add this video.` in brick-text with the same two actions. Retry reuses the stored trim.

## c) Acceptance (Kit)
- 360 px wide and 130% text (13e) with no horizontal scroll and nothing clipped; all targets ≥48 px (handles, play, Cancel, tile ×).
- Dark theme B tokens; Atkinson via `/frame-fonts.css`; brass focus rings, `outline-offset:-3px` inside overflow:hidden containers (preview, tiles).
- Only transform/opacity animate (bar scaleX, ring dashoffset is static per poll).
- Selection enforced 1–30 s with the two messages; default first 30 s; handles ≥1 s apart; arrow keys 0.5 s; aria-valuetext as above.
- Progress ≤99% until ready, updates ~2 s, polite announcements only at milestones; hi-res note kept; Cancel → `Cancelling…` disabled on both row and tile; failure lands on the 11a failed tile.

## Open questions (Sage, Kit)
1. Trim on phone or Pi: Sage proposes phone picks, Pi cuts inside the single convert. Design assumes this.
2. Progress source: Sage proposes `progress` on the status endpoint (ffmpeg `-progress`). Design assumes it; without it, fall back to the 11a spinner.
3. Trim before or after upload: design assumes before (only the picked points travel; whole file still uploads).
4. Library `Trim` on a ready video: the upload is removed once ready, so re-trimming would need the original. Keep it (re-pick from phone), or drop it from Phase 2?
5. Should "about N minutes left" in aria-valuetext come from the server, or only the static over-4K estimate?
