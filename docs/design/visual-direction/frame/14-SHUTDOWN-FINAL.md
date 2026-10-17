# 14 · Final shutdown screen restyle (`#safeScreen`)

Wren (Designer), 17 Oct 2026. For Kit: a small follow-up PR after #74. **Styling and markup only. The words do not change.**
They still come from `display.SAFE_SCREEN_COPY` (title / sub / note), so `tests/test_shutdown.py` is unaffected.

| Mock | File |
|---|---|
| Earlier screen, `#shuttingDown` (unchanged) | `14-shutdown-progress.png` / `.html` |
| **Final screen, `#safeScreen` (new)** | `14-shutdown-final-v2.png` / `.html` |
| Side by side | `14-shutdown-compare.png` (built by `tools/compare_shutdown.py`) |

## Why
Built as #74, the final screen looks like the earlier one: dark ink, a spinner ring, centred text. From across the room the two
read as the same "still busy" state. The final screen now has its own look:

- **Walnut card in a brass frame.** Brown fills the panel and echoes the wooden frame; the earlier screen stays near-black.
  You can tell them apart by colour alone.
- **The spinner is replaced by a static brass plug in a ring.** A spinner says "busy". A plug says "this is about unplugging",
  and a frozen copy of it doesn't look stuck.
- **Static stopwatch with a 10-second (60°) wedge** in front of the body text. It is a fixed cue, not a countdown.
- **Horizontal layout** (icon left, text left-aligned), where the earlier screen is stacked and centred, so the shape differs too.
- Nothing moves, so any frozen frame looks exactly like the mock.

## Markup: `templates/index.html` (replace the `#safeScreen` block)
```html
<div id="safeScreen" role="status" hidden>
    <div class="safeCard">
        <svg class="safePlug" viewBox="0 0 168 168" aria-hidden="true" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round">
            <circle cx="84" cy="84" r="78" stroke-width="6"/>
            <path d="M70 40v20M98 40v20" stroke-width="8"/>
            <path d="M58 60h52v18a26 26 0 0 1-52 0z" fill="currentColor" stroke-width="6"/>
            <path d="M84 104v14a12 12 0 0 0 12 12h6" stroke-width="7"/>
        </svg>
        <div class="safeText">
            <div class="safeTitle">{{ safe_copy.title }}</div>
            <div class="safeRule" aria-hidden="true"></div>
            <div class="safeSub">
                <svg class="safeClock" viewBox="0 0 40 40" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round">
                    <path d="M20 23V10A13 13 0 0 1 31.26 16.5Z" fill="currentColor" stroke="none"/>
                    <circle cx="20" cy="23" r="14.5"/>
                    <path d="M16 3h8M20 3v5M32 9l2.5-2.5"/>
                </svg>
                <span>{{ safe_copy.sub }}</span>
            </div>
            <div class="safeNote">{{ safe_copy.note }}</div>
        </div>
    </div>
</div>
```
Removed: `<div class="mf-spinner safeSpin">`. Leave `#shuttingDown` and its spinner exactly as they are.
Keep `role="status"` if it is already there; it's harmless if not.

## CSS: `static/style.css` (replace the `#65 B safe screen` block, currently 7 rules)
```css
/* #70 mock 14: final screen. Walnut card, brass frame, static plug + stopwatch. Fully static (frozen after halt). */
#safeScreen{position:fixed;inset:0;z-index:100000;background:var(--mf-ink);color:var(--mf-linen);font-family:var(--mf-font-frame);
  touch-action:none;padding:20px;box-sizing:border-box}
#safeScreen[hidden]{display:none}
#safeScreen .safeCard{height:100%;box-sizing:border-box;display:flex;align-items:center;justify-content:center;gap:32px;padding:0 32px;
  background:var(--mf-walnut-900);border:3px solid var(--mf-brass);border-radius:var(--mf-r-lg)}
#safeScreen .safePlug{flex:none;color:var(--mf-brass);width:144px;height:144px;display:block}
#safeScreen .safeText{flex:none;text-align:left}
#safeScreen .safeTitle{font-size:44px;font-weight:700;line-height:1.1;color:var(--mf-linen)}
#safeScreen .safeRule{height:3px;width:72px;background:var(--mf-brass);margin:18px 0 20px;border-radius:2px}
#safeScreen .safeSub{display:flex;align-items:center;gap:12px;font-size:30px;font-weight:700;line-height:1.15;white-space:nowrap;color:var(--mf-brass)}
#safeScreen .safeClock{flex:none;color:var(--mf-brass);width:38px;height:38px;display:block}
#safeScreen .safeNote{font-size:20px;color:var(--mf-oat);margin-top:16px}
```
Tokens used: `--mf-ink`, `--mf-walnut-900`, `--mf-brass`, `--mf-linen`, `--mf-oat`, `--mf-r-lg`, `--mf-font-frame`. No new colours;
the SVGs use `currentColor`. Delete the old `.safeSpin` rule. Keep the `body:has(... #safeScreen:not([hidden]) ...) .dev-banner{opacity:0}` rule.

Geometry at 800x480: the card is 760x440 (20 px ink margin). The content group is about 594 px wide (144 icon + 32 gap + about 418 text)
and centred. The widest line is the sub row, about 418 px, and it fits on one line with room to spare.

## Contrast (WCAG 2.x)
| Pair | Ratio | Level |
|---|---|---|
| Title: linen #F3EDE4 on walnut-900 #3A2C22 | **11.54:1** | AAA |
| Sub: brass #D6B36A on walnut-900 (30 px bold = large) | **6.73:1** | AAA large / AA normal |
| Note: oat #C9BBA8 on walnut-900 (20 px) | **7.14:1** | AAA |
| Icons + card border: brass on walnut-900 / on ink | 6.73:1 / 9.39:1 | > 3:1 non-text |
| Earlier screen for reference: linen on ink / brass on ink | 16.11:1 / 9.39:1 | AAA |

## Acceptance criteria
1. Wording is byte-identical: "Shutting down" / "Wait 10 seconds, then unplug" / "Photos are saved on the frame", still from `SAFE_SCREEN_COPY`.
2. `#safeScreen` has **no** animation or transition: `document.getAnimations().length === 0` while it is shown, and no `.mf-spinner` inside it.
3. No `filter`, `backdrop-filter`, `box-shadow`, `text-shadow` or gradient anywhere in the new rules.
4. At 800x480 the screen matches `14-shutdown-final-v2.png`: a walnut card with a 3 px brass border, 20 px ink margin, the plug left and the text
   left-aligned to its right, the sub on **one line**, nothing clipped.
5. Side by side with `#shuttingDown` (`14-shutdown-compare.png`), the two are obviously different from about 3 m away (brown card vs black screen).
6. On device: after power-off the frozen panel shows the full final screen, with no half-painted frame and no spinner.
7. The dev banner stays hidden while `#safeScreen` is shown (existing `:has()` rule; cosmetic only if the kiosk's Chromium lacks `:has()`).
8. Atkinson Hyperlegible (`--mf-font-frame`, `font-display:block`) is used, and the font is loaded before the screen paints (it is already used on every overlay).
