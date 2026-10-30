# 12 Home Wi-Fi from the phone (#68)

Follows the security/flow design on #68 (§10 UI constraints). Developer feature: the page exists only when the gate holds (dev mode, dev fallback, hotspot active). Phone dark theme, Atkinson, 360 px; styles in `homewifi.css`. Frame states in frame dark style, 800x480.

| Mock | State |
|---|---|
| `phone/12a-home-wifi-form.png` | Form: saved network (name only), Network name, Password (hidden) + Show |
| `phone/12b-home-wifi-error.png` | Password shown (Hide, aria-pressed=true) + fixed inline error |
| `phone/12c-home-wifi-confirm.png` | Confirm sheet; Cancel is default focus |
| `phone/12d-home-wifi-testing.png` | Testing, no countdown |
| `phone/12e-home-wifi-saved.png` | Saved |
| `phone/12f-home-wifi-failed.png` | Couldn't connect, back on frame Wi-Fi, next steps |
| `frame/17a-home-wifi-testing.png` | Frame: non-dismissable overlay while testing |
| `frame/17b-home-wifi-failed.png`, `frame/17c-home-wifi-saved.png` | Frame: 10 s result line |

Copy: the §10 strings verbatim, plus two short additions: form hint `The frame tests it, then goes back to its own Wi-Fi.` and the failure line `The frame is back on its own Wi-Fi. Your saved network did not change.` Rate-limit (429) uses `Try again in a few minutes.` in the 12f layout without the Try again button.

## Acceptance
- Inputs `autocomplete=off autocapitalize=off autocorrect=off spellcheck=false`; password `type=password` by default, never pre-filled or shown after submit; cleared on Cancel and error.
- Show/Hide is a ≥48 px button, `aria-label="Show password"`/`"Hide password"`, `aria-pressed` reflects state; focus ring brass.
- Inline errors use the exact fixed strings, brick-text, `role=alert`; field border brick-text.
- Confirm is a modal (`aria-modal`), names the network in `<bdi>`, says the phone disconnects for up to 90 s; Cancel is default.
- Testing has no countdown; polls status; survives the phone dropping off and shows the result when it returns.
- Success and failure as mocked; failure says what to do and gives `http://10.42.0.1/`; the SSID is always escaped in `<bdi>`.
- Frame overlay can't be dismissed while testing; result toast shows for 10 s.
- All targets ≥48 px; AA contrast; Atkinson from /frame-fonts.css; no outside requests; 360 px and 130 % text with no horizontal scroll.
