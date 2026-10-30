# 16 First-run welcome (#98)

Frame dark style, 800x480, no scroll, no animation. Data: `/pairing/state` only (Juniper's table on #98).

| State | Mock | Notes |
|---|---|---|
| W0, hotspot (Wi-Fi QR allowed) | `16-welcome-wifi.png` | 3 steps: Join the Wi-Fi (QR + name), Open the site (QR + URL), Type the code |
| W0, home Wi-Fi / no Wi-Fi QR | `16b-welcome-site-only.png` | 2 steps; URL shown is an example |
| W1, paired, empty | `16c-welcome-connected.png` | replaces "No photos yet"; keeps Close |
| Paired toast (3 s) | `05-paired-toast.png` (unchanged) | |
| Hotspot error | `06-hotspot-error.png` (unchanged) | wins over welcome |

Copy: title `Welcome to your Memory Frame`; sub `Send photos from your phone and they appear here.`; footer `Then tap Upload on your phone.` (says "Upload", not "Upload photos", to match #102). W1: `You're connected` / `Open {url} on your phone and tap Upload.` Code caption `New code every 2 minutes`.

Tokens: ink background, forest-900 cards with forest-700 border, brass step badges, linen text, oat secondary. QR 240x240 on white, 4-module quiet zone. Code: monospace, bold, ≥48 px, no space between digits.

## Acceptance
- W0 shows only when paired 0, media 0, not dev_temp, and no error overlay; W1 only when paired > 0, media 0.
- 800x480 with nothing clipped; QR boxes 240 px; text ≥20 px; titles 40–48 px; Close ≥48 px tall.
- Wi-Fi step only on the hotspot with the Wi-Fi QR allowed; otherwise the 2-step layout.
- Code only in W0 / on-demand pairing; never in W1, W2 or dev_temp. No Close in W0.
- No "SSID", "hotspot", "IP" or password text on screen.
- W0 → W1 on pairing (after the toast), → slideshow on first media, without reload.
- Contrast AA (linen/oat on ink/forest-900). Verified on the Pi: readable from 1 m, both QRs scan.
