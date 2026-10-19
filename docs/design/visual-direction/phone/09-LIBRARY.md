# 09 Library redesign (#80)

Mocks: `09-library-grid.png`, `09b-library-select.png` (3 selected), `09c-library-delete-confirm.png`, `09-library-360-130.png`, `09-library-sheet.png`.
Source: `09-*.html` + `library.css` (on top of `phone.css` / tokens.css). Rendered with `render-10.py` (Playwright, 412x915; 360x915 for the 130% text check).
**Status: approved by the user 19 Oct 2026** (tap selects, Select all stays, no month grouping). Theme/font still open, see 10-theme-compare.png.
Follows the #70 phone direction and #79 (buttons only, no text links). This replaces the PR #77 "See all" 2-column `#grid` with filenames, Show now and Hide.

## Removed
Show now, Hide, the Hidden tag and filenames. Hidden state is not shown (see open question 3).

## Layout (normal mode, 09)
- Forest bar: Back button (`‹`, aria-label "Back to home"), title "Photos", Settings button. Walnut status strip as on home.
- Primary `btn btn-primary btn-big block` "＋ Upload photos" (56px, button).
- Header row: "Library" + "128 photos on the frame", and a visible **Select** button (`btn btn-neutral`, 48px).
- No month grouping (user decision): one continuous grid, newest first.
- Grid: full-bleed (`margin: 0 -16px`), 3 columns at 360 and 412, 3px gap, square tiles, `object-fit: cover`, no radius (Gallery style).
- Thumbnails: `<img loading="lazy" decoding="async" alt="">` inside a `<button class="tile">`; fixed aspect-ratio squares so lazy loading causes no layout shift.
- Missing thumbnail: `.tile.plain` = paper background, 1px line border, a small outlined picture glyph (CSS only), same as PR #77's plain tile.

## Interactions
- **Single tap in normal mode = enter selection mode with that photo selected (approved).** Rationale: the user's brief says "on photo press show it has been pressed … multiple are selectable"; it needs no extra preview screen, no larger image download and no new server route, so it stays simple and offline.
- **Long press (500 ms)** also enters selection mode with that photo selected; call `navigator.vibrate?.(15)` if available. Cancel the long press if the pointer moves > 10px (that's a scroll). Set `-webkit-touch-callout: none; user-select: none` on tiles so Android doesn't show the image context menu.
- **Select** button enters selection mode with nothing selected (accessible, discoverable path).
- In selection mode, a tap toggles the tile. Selecting the last selected photo off keeps the mode on (N=0, Delete disabled); only Cancel, Back, or a finished delete exits.
- Android Back button / Back in selection mode = Cancel (push a history state on entering selection mode).
- Delete (N) opens the confirm sheet. Confirm sends one bulk request; on success, removed tiles disappear (opacity fade 150ms), the mode exits and the header count updates. On failure, keep the selection and show the `.err` box "Couldn't delete the photos. Check you're connected to the frame and try again."

## Selection mode (09b)
- Top bar replaced: `.bar.selbar` with "**N selected**" (`aria-live="polite"`) and **Select all** (approved) (`.btn-onbar`, outlined on forest, 48px). When all are selected it reads "Unselect all".
- The Upload button and header row are hidden; a hint "Tap photos to select or unselect them." sits above the grid.
- Unselected tile: an empty white ring (26px, 2px white border on a 35% ink fill, for contrast over light photos) top-left.
- Selected tile (not colour alone, 3 cues): filled forest-green circle with a white tick (inline SVG data URI), image scaled to 0.88 with 6px radius, 3px green border, green-50 behind the inset.
- Bottom toolbar, fixed: Cancel (`btn btn-neutral`, outlined) and **Delete (N)** (`btn btn-danger`, brick). Delete is `disabled` at N=0 (line bg, text-2 text). Buttons 48px, padding includes `env(safe-area-inset-bottom)`; page bottom padding stops the toolbar covering the last row.

## Delete confirm (09c) = PR #77 Wi-Fi sheet
- `.scrim` (flat `--mf-scrim-phone`, no blur) + `.sheet` (white, 20px top radius), `role="alertdialog" aria-modal="true"`, `body.sheet-open` locks scroll.
- Title: "Delete 3 photos?" (singular: "Delete this photo?"). Body: "They'll be removed from the frame. This can't be undone." (singular: "It'll be removed from the frame. This can't be undone.")
- Buttons: **Cancel** (neutral, default, focused on open) then **Delete** (brick). Esc / scrim tap / Back = Cancel. Focus returns to the Delete (N) button on close.

## Exact CSS
See `library.css` (all values are tokens.css tokens, plus `--mf-lib-gap: 3px`, `--mf-tick: 26px`, `--mf-toolbar-h: 72px`). Key rules:
```css
.lib { list-style:none; margin:0 -16px; padding:0; display:grid; grid-template-columns:repeat(3,1fr); gap:3px; }
.lib li { aspect-ratio:1; position:relative; }
.tile { all:unset; box-sizing:border-box; display:block; width:100%; height:100%; position:relative; overflow:hidden;
  background:var(--mf-paper); border:1px solid var(--mf-line); -webkit-touch-callout:none; user-select:none; }
.tile:has(img) { border:0; }   /* or add .has-img from JS */
.tile img { width:100%; height:100%; object-fit:cover; display:block; transition:transform var(--mf-t-fast) var(--mf-ease); }
.selecting .tile::after { /* empty ring */ content:""; position:absolute; top:6px; left:6px; width:26px; height:26px;
  border-radius:50%; border:2px solid #fff; background:rgba(31,42,36,.35); }
.selecting .tile[aria-pressed="true"] { border:3px solid var(--mf-green); background:var(--mf-green-50); }
.selecting .tile[aria-pressed="true"] img { transform:scale(.88); border-radius:6px; }
.selecting .tile[aria-pressed="true"]::after { background:var(--mf-green) url(tick.svg) center/18px no-repeat; }
.toolbar { position:fixed; left:0; right:0; bottom:0; background:var(--mf-card); border-top:1px solid var(--mf-line);
  display:flex; gap:12px; padding:12px 16px calc(12px + env(safe-area-inset-bottom)); }
.btn:focus-visible, .tile:focus-visible { outline:3px solid var(--mf-green); outline-offset:2px; }
```
Use the PR #77/#78 `.mf-phone` prefixes when porting. Add `viewport-fit=cover` to the viewport meta for the safe-area inset.

## Accessibility
- Tiles are `<button>`s. aria-label: "Photo 4 of 128" (index; a date may be appended if stored; never the filename). In selection mode they get `aria-pressed="true|false"`; in normal mode no aria-pressed.
- Grid tile focus ring is a 3px green outline (on top tiles inset with `outline-offset:-3px` if clipped); bar buttons use brass.
- Contrast: white on green 7.3:1, white on brick 6.6:1, text on paper ≥ 7:1, text-2 on paper 6.9:1, linen on forest-900 13:1, the tick circle green on photo + white edge.
- 360 px with 130% text (09-library-360-130): bar, hint, and toolbar fit on one line each; the grid stays 3 columns; the sheet's buttons wrap (`flex: 1 1 8rem`) instead of truncating.
- Count change is announced via `aria-live`.

## Performance
No blur, backdrop-filter, shadows or gradients. Only `transform` (tile inset) and `opacity` (remove fade, sheet in) animate. Thumbnails lazy + async decode. No image previews beyond existing thumbnails.

## Acceptance criteria for Kit
1. No Show now, Hide, Hidden tag or filename anywhere in the library.
2. 3-column square grid, 3px gaps, full-bleed, at 360 and 412; lazy thumbnails; plain tile for missing ones.
3. Upload is a 56px primary button; Select is a visible 48px button; no text links (#79).
4. Tap or 500 ms long press (with vibrate if available, cancelled on scroll) enters selection with that photo selected; Select enters with none.
5. Selected = tick circle + 0.88 inset + green border; unselected in mode = empty ring; tiles have aria-pressed and index/date aria-labels.
6. Top bar shows "N selected" (live) and Select all/Unselect all.
7. Fixed bottom toolbar Cancel + Delete (N), 48px, safe-area inset; Delete disabled at 0; Back = Cancel.
8. Confirm sheet copy as above, Cancel focused by default, Esc/scrim/Back cancel; one bulk request; failure keeps selection and shows the error.
9. Server: bulk delete for paired clients only, CSRF protected, valid IDs only, size limit (Juniper reviews).
10. Matches mocks within the usual tolerance at 412x915 and 360 at 130% text; no animation other than transform/opacity.

## Decisions (user, 19 Oct 2026)
1. Tapping a photo starts selecting with it. 2. Select all stays. 3. No month grouping.

## Open questions for the user
1. **Theme and font:** the user wants the whole phone to keep the frame's look and feel, including Atkinson Hyperlegible. See `10-theme-compare.png`: **(a)** Light: paper theme + Atkinson, or **(b)** Dark: frame theme (`theme-dark.css`) + Atkinson. Either way Atkinson is self-hosted via `/frame-fonts.css` (font-display: block). The 09 mocks stay on the light/system-font look until this is picked.
2. Photos already hidden, now that Hide is gone: **(a)** they show again (simplest) or **(b)** stay hidden, not shown on the phone.
