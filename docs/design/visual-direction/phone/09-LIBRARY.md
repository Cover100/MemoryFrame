# 09 Library redesign (#80)

Mocks: `09-library-grid.png`, `09b-library-select.png` (3 selected), `09c-library-delete-confirm.png`, `09-library-360-130.png`, `09-library-sheet.png`.
Source: `09-*.html` + `library.css` + `theme-dark.css` (on top of `phone.css` / tokens.css; Atkinson via `--mf-font-phone: var(--mf-font-frame)`). Rendered with `render-10.py` (Playwright, 412x915; 360x915 for the 130% text check).
**Status: approved by the user 19 Oct 2026** (tap selects, Select all stays, no month grouping). Theme: **B Dark + Atkinson Hyperlegible (user, 19 Oct 2026)**, see ../PHONE-DARK.md. 10-theme-compare.png is kept as the historical comparison.
Follows the #70 phone direction and #79 (buttons only, no text links). This replaces the PR #77 "See all" 2-column `#grid` with filenames, Show now and Hide.

## Removed
Show now, Hide, the Hidden tag and filenames. Previously hidden photos all show again (user decision).

## Layout (normal mode, 09)
- Page ink; forest-900 bar with a forest-700 hairline: Back button (`‹`, aria-label "Back to home"), title "Photos", Settings button. Walnut status strip as on home.
- Primary `btn btn-primary btn-big block` "＋ Upload photos" (56px, button).
- Header row: "Library" + "128 photos on the frame", and a visible **Select** button (`btn btn-neutral`, 48px).
- No month grouping (user decision): one continuous grid, newest first.
- Grid: full-bleed (`margin: 0 -16px`), 3 columns at 360 and 412, 3px gap, square tiles, `object-fit: cover`, no radius (Gallery style).
- Thumbnails: `<img loading="lazy" decoding="async" alt="">` inside a `<button class="tile">`; fixed aspect-ratio squares so lazy loading causes no layout shift.
- Missing thumbnail: `.tile.plain` = forest-900 background, 1px forest-700 border, a small oat outlined picture glyph at 60% opacity (decorative, CSS only).

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
- Unselected tile: an empty white ring (26px, 2px white border on a 35% ink fill, readable over light and dark photos) top-left.
- Selected tile (not colour alone, 3 cues): filled forest-green circle with a white tick (inline SVG data URI), image scaled to 0.88 with 6px radius, 3px **leaf** border, forest-900 behind the inset.
- Bottom toolbar, fixed: Cancel (`btn btn-neutral`, transparent with a 1.5px oat border) and **Delete (N)** (`btn btn-danger`, brick fill, white text). The toolbar is forest-900 with a forest-700 top border. Delete is `disabled` at N=0 (forest-800 fill, oat text). Buttons 48px, padding includes `env(safe-area-inset-bottom)`; page bottom padding stops the toolbar covering the last row.

## Delete confirm (09c) = PR #77 Wi-Fi sheet
- `.scrim` (flat rgba(0,0,0,.6), no blur) + `.sheet` (forest-800, 20px top radius, forest-700 top border; linen title, oat body), `role="alertdialog" aria-modal="true"`, `body.sheet-open` locks scroll.
- Title: "Delete 3 photos?" (singular: "Delete this photo?"). Body: "They'll be removed from the frame. This can't be undone." (singular: "It'll be removed from the frame. This can't be undone.")
- Buttons: **Cancel** (outlined, oat border, default, focused on open with the 3px brass ring) then **Delete** (brick). Esc / scrim tap / Back = Cancel. Focus returns to the Delete (N) button on close.

## Exact CSS (dark, decided)
`library.css` holds layout; `theme-dark.css` holds the dark mapping (full table in ../PHONE-DARK.md). Key rules:
```css
:root { --mf-paper:var(--mf-ink); --mf-card:var(--mf-forest-800); --mf-line:var(--mf-forest-700);
  --mf-text:var(--mf-linen); --mf-text-2:var(--mf-oat); --mf-green:var(--mf-forest-600); --mf-green-50:var(--mf-forest-900);
  --mf-accent-text:var(--mf-leaf); --mf-red-50:#3A1F1B; --mf-scrim-phone:rgba(0,0,0,.6); --mf-font-phone:var(--mf-font-frame); }
.lib { list-style:none; margin:0 -16px; padding:0; display:grid; grid-template-columns:repeat(3,1fr); gap:3px; }
.lib li { aspect-ratio:1; position:relative; }
.tile { all:unset; box-sizing:border-box; display:block; width:100%; height:100%; position:relative; overflow:hidden;
  background:var(--mf-forest-900); border:1px solid var(--mf-forest-700); -webkit-touch-callout:none; user-select:none; }
.tile:has(img) { border:0; }   /* or .has-img from JS */
.tile img { width:100%; height:100%; object-fit:cover; display:block; transition:transform var(--mf-t-fast) var(--mf-ease); }
.tile.plain::before { border:2px solid var(--mf-oat); opacity:.6; }   /* decorative glyph */
.selecting .tile::after { content:""; position:absolute; top:6px; left:6px; width:26px; height:26px; border-radius:50%;
  border:2px solid #fff; background:rgba(31,42,36,.35); }
.selecting .tile[aria-pressed="true"] { border:3px solid var(--mf-leaf); background:var(--mf-forest-900); }
.selecting .tile[aria-pressed="true"] img { transform:scale(.88); border-radius:6px; }
.selecting .tile[aria-pressed="true"]::after { background:var(--mf-forest-600) url(tick.svg) center/18px no-repeat; }
.btn-danger { background:var(--mf-brick); color:#fff; }                 /* brick = fills */
.btn-text-danger, .danger-text { color:var(--mf-brick-text); }        /* brick-text = text on dark */
.btn-neutral, .btn-onbar { background:transparent; color:var(--mf-linen); border:1.5px solid var(--mf-oat); }
.btn-danger:disabled { background:var(--mf-forest-800); color:var(--mf-oat); }
.toolbar { position:fixed; left:0; right:0; bottom:0; background:var(--mf-forest-900); border-top:1px solid var(--mf-forest-700);
  display:flex; gap:12px; padding:12px 16px calc(12px + env(safe-area-inset-bottom)); }
.sheet { background:var(--mf-forest-800); border-top:1px solid var(--mf-forest-700); }
.btn:focus-visible, .tile:focus-visible { outline:3px solid var(--mf-brass); outline-offset:2px; }
```
Use the PR #77/#78 `.mf-phone` prefixes when porting. Add `viewport-fit=cover` to the viewport meta. Link `/frame-fonts.css` on every phone page.

## Contrast (every pair used, WCAG 2.x)
| Pair | Ratio | Need |
|---|---|---|
| linen on ink (page text) | 16.1 | 4.5 |
| oat on ink (hint, secondary) | 10.0 | 4.5 |
| linen on forest-900 (bar title, "N selected") | 13.6 | 4.5 |
| oat on walnut-900 (status strip) | 7.1 | 4.5 |
| linen on forest-800 (sheet title, cards) | 10.8 | 4.5 |
| oat on forest-800 (sheet body) | 6.7 | 4.5 |
| white on forest-600 (Upload, tick) | 7.3 | 4.5 |
| white on brick (Delete) | 6.5 | 4.5 |
| brick-text on forest-800 (destructive text rows) | 5.9 | 4.5 |
| leaf on ink / forest-900 (accent text, status dot) | 10.6 / 8.9 | 4.5 |
| oat border on ink / forest-900 / forest-800 (outlined buttons) | 10.0 / 8.4 / 6.7 | 3 |
| leaf selected border on forest-900 | 8.9 | 3 |
| brass focus ring on ink / forest-900 / forest-800 | 9.4 / 7.9 / 6.3 | 3 |
| white ring over photos (on 35% ink fill) | photo-dependent, fill guarantees ≥3 | 3 |
| disabled Delete: oat on forest-800 | 6.7 | exempt |
No failures. Not used: forest-600 as text on ink (2.6). Filled button edges (forest-600 2.2, brick 2.4 against forest-900) are identified by their text label, so 1.4.11 does not apply.

## Accessibility
- Tiles are `<button>`s. aria-label: "Photo 4 of 128" (index; a date may be appended if stored; never the filename). In selection mode they get `aria-pressed="true|false"`; in normal mode no aria-pressed.
- Focus ring everywhere is a 3px brass outline (inset with `outline-offset:-3px` on edge tiles if clipped).
- Contrast: see the table above.
- 360 px with 130% text (09-library-360-130, dark, re-checked 19 Oct): bar and toolbar fit on one line, the hint wraps to two lines cleanly, nothing clips; the grid stays 3 columns; the sheet's buttons wrap (`flex: 1 1 8rem`) instead of truncating.
- Count change is announced via `aria-live`.

## Performance
No blur, backdrop-filter, shadows or gradients. Only `transform` (tile inset) and `opacity` (remove fade, sheet in) animate. Thumbnails lazy + async decode. No image previews beyond existing thumbnails.

## Acceptance criteria for Kit
1. No Show now, Hide, Hidden tag or filename anywhere in the library.
2. 3-column square grid, 3px gaps, full-bleed, at 360 and 412; lazy thumbnails; plain tile for missing ones.
3. Upload is a 56px primary button; Select is a visible 48px button; no text links (#79).
4. Tap or 500 ms long press (with vibrate if available, cancelled on scroll) enters selection with that photo selected; Select enters with none.
5. Selected = forest-600 tick circle + 0.88 inset + leaf border; unselected in mode = empty ring; tiles have aria-pressed and index/date aria-labels.
6. Top bar shows "N selected" (live) and Select all/Unselect all.
7. Fixed bottom toolbar Cancel + Delete (N), 48px, safe-area inset; Delete disabled at 0; Back = Cancel.
8. Confirm sheet copy as above, Cancel focused by default, Esc/scrim/Back cancel; one bulk request; failure keeps selection and shows the error.
9. Server: bulk delete for paired clients only, CSRF protected, valid IDs only, size limit (Juniper reviews).
10. Dark theme + Atkinson per ../PHONE-DARK.md; matches mocks within the usual tolerance at 412x915 and 360 at 130% text; no animation other than transform/opacity.
11. Existing hidden flags are cleared (migration) or ignored, so every photo appears in the grid and the slideshow; nothing on the phone can hide a photo.

## Decisions (user, 19 Oct 2026)
1. Tapping a photo starts selecting with it. 2. Select all stays. 3. No month grouping.
4. Photos already hidden all show again (resolved, recorded on #80).
5. Phone theme B Dark + Atkinson Hyperlegible for the whole phone (resolved).

## Open questions for the user
None. Theme and font decided: B Dark + Atkinson (user, 19 Oct 2026).
