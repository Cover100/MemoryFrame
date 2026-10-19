# Phone theme: Dark (decided)

**Decision (user, 19 Oct 2026):** the whole phone site uses the frame's dark theme with Atkinson Hyperlegible (Variant B in `phone/10-theme-compare.png`, kept as the historical record). This **replaces the paper (light) phone theme** in DIRECTION.md and phone mocks 01-08; where those mocks differ in colour, this file wins. Layout, copy and components are unchanged. Reference implementation for mocks: `phone/theme-dark.css`; reference screens: `phone/09*` (dark).

## Font
`--mf-font-phone: var(--mf-font-frame)`. Link `/frame-fonts.css` (self-hosted Atkinson 400/700 woff2, `font-display: block`) on every phone page, before tokens.css. No CDN, no preload needed on phone.

## Token mapping (phone semantic token → frame token)
| Phone token | Was (paper) | Now (dark) | Use |
|---|---|---|---|
| `--mf-paper` | #F6F1E9 | `--mf-ink` #0E1311 | page background |
| `--mf-card` | #FFFFFF | `--mf-forest-800` #24382C | cards, rows, inputs, sheets |
| `--mf-line` | #E2D8CA | `--mf-forest-700` #2F4A3A | hairlines, dividers |
| `--mf-text` | #1F2A24 | `--mf-linen` #F3EDE4 | body text |
| `--mf-text-2` | #5E5146 | `--mf-oat` #C9BBA8 | secondary text |
| `--mf-green` | #2A6140 | `--mf-forest-600` #2A6140 | primary **fills only** (white text) |
| `--mf-accent-text` (new) | n/a | `--mf-leaf` #8FD1A4 | green text, success, selected border |
| `--mf-green-50` | #E3EEE6 | `--mf-forest-900` #16261D | selected/success background |
| `--mf-red` → split | #A8352A | fills: `--mf-brick` #A8352A; text: `--mf-brick-text` #E7A097 | destructive |
| `--mf-red-50` | #F8E4E1 | #3A1F1B | error box background (text brick-text, 6.0+) |
| `--mf-scrim-phone` | rgba(31,42,36,.55) | rgba(0,0,0,.6) | flat scrim, no blur |
| focus ring | green | `--mf-brass` #D6B36A, 3px | all focus-visible |
| bar | forest-900 | forest-900 + 1px forest-700 bottom | header |
| status strip | walnut-900 / oat | unchanged | |
| warn box | brass tint | walnut-900 bg, brass border, linen text | warnings |

## Component rules
- Outlined / neutral buttons: transparent (or forest-800), linen text, **1.5px oat border** (≥6.7:1 on every dark surface).
- Primary: forest-600 fill, white text (7.3). Destructive: brick fill, white text (6.5). Destructive text rows: brick-text.
- Disabled: forest-800 fill, oat text, no border.
- Inputs: forest-800 fill, oat border, linen text, brass focus.
- Bottom sheets: forest-800, forest-700 top border, linen title, oat body; Cancel outlined (default, focused), destructive brick.
- Tiles: plain = forest-900 + forest-700 border; selected = leaf 3px border + forest-600 tick circle with white tick + 0.88 inset.
- Hard-coded `#fff`/white surfaces must go (white text on fills is fine). No blur, shadows or gradients.

## Contrast summary
All text ≥ 5.9:1 (lowest brick-text on forest-800), all borders/focus/selection ≥ 3:1. Never use forest-600 as text (2.6 on ink). Full table: `phone/09-LIBRARY.md`.

## #77 dark fidelity checklist
1. No paper/white surfaces left on any page (home, upload, library, settings, Wi-Fi, keep Wi-Fi, pair, offline, About).
2. Atkinson renders on every page, served from `/frame-fonts.css`, works offline, no flash of fallback (font-display: block); test that only settings links it is updated.
3. Every token per the table; `--mf-accent-text` for green text, `--mf-green` only as fill; brick vs brick-text used correctly.
4. Outlined buttons have the oat border; disabled states forest-800/oat.
5. Focus rings brass, visible on every control (keyboard tab-through).
6. Forms: inputs forest-800 with oat border; placeholder oat; pair-code field readable; error box #3A1F1B + brick-text; warn box walnut/brass.
7. Wi-Fi bottom sheet and delete sheet: forest-800, scrim rgba(0,0,0,.6), Cancel focused.
8. Upload picker/progress: no raw file input visible; progress track forest-700, fill forest-600 or leaf.
9. Keep ring and status dot readable on ink.
10. 412x915 and 360 at 130% text: nothing clipped; contrast spot-check with a picker against the table.
11. No blur/backdrop-filter/shadows/gradients; transform/opacity only.
