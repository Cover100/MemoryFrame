# MF-07 Discovery Spike: Hotspot, Phone Management Site, NFC, Access Control

- Ticket: MF-07 (Sprint 1) · Owner: Analyst · Status: Draft for review
- Target: Raspberry Pi 4 Model B, Raspberry Pi OS 64-bit (Bookworm, NetworkManager), 5" DSI IPS 800x480
- Baseline: `app.py` (Flask, port 5000, unauthenticated routes `/`, `/media`, `/uploads/<f>`, `GET /brightness[/<int>]`), kiosk slideshow in `templates/index.html` + `static/script.js`. No phone-side UI exists.
- Builds on: MF-03 (brightness POST), MF-04 (systemd + WSGI), MF-09 (persistent NM hotspot profile).

## 0. Summary of recommendations
1. **Network:** keep NetworkManager. One saved AP profile (`MemoryFrame-AP`, WPA2-PSK, `ipv4.method shared`, 10.42.0.1/24) plus optional saved home-Wi-Fi client profiles. Default AP-only; "client with AP fallback" via a small watchdog. Do not run AP+client concurrently on the single onboard radio in v1 (supported only on same channel, fragile).
2. **Name resolution:** fixed IP `10.42.0.1`, mDNS `memoryframe.local` (avahi), plus a dnsmasq drop-in resolving `frame.home`/`memoryframe.lan` to 10.42.0.1. **No captive-portal hijacking in v1**; instead tell the OS "no internet" cleanly and deep-link via QR/NFC. Captive portal is an optional later ticket.
3. **Phone site:** same Flask app, split into `/admin/*` (phone UI, auth) and display routes (localhost-only writes). Server-rendered Jinja + a little vanilla JS (no build step, no Node on the Pi), Pillow for 800x480 optimisation, SQLite for metadata/sessions, waitress behind systemd (MF-04).
4. **Access control:** WPA2 password is layer 1; layer 2 is device pairing: on-screen 6-digit PIN / QR with one-time token shown on the 800x480 display -> long-lived signed session cookie per phone, revocable. CSRF tokens, rate limiting, `SameSite=Strict`, admin-only state changes via POST.
5. **NFC:** phase 2 / optional. Passive NTAG215 sticker on the frame holding (a) Wi-Fi record (Android) + (b) URL record `http://10.42.0.1/` . No reader hardware needed for v1. A PN532 reader (I2C) is only needed for "tap a tag to trigger an album" features — defer.

---

## 1. Wi-Fi hotspot (AP) on Bookworm

### 1.1 Facts / constraints
- Bookworm uses NetworkManager by default; `nmcli device wifi hotspot` creates a transient `Hotspot` connection (what the user does today). NM's `ipv4.method shared` spawns its own dnsmasq for DHCP/DNS (default subnet 10.42.0.0/24, gateway 10.42.0.1) and NATs to any upstream (eth0) if present.
- Pi 4 onboard Wi-Fi (CYW43455) supports 2.4 GHz and 5 GHz. Use **2.4 GHz, channel 6** by default (best phone compatibility, range), `wifi.band bg`. Set Wi-Fi country (`raspi-config nonint do_wifi_country AU`) or AP may not start / 5 GHz is blocked.
- AP + station on the same radio ("concurrent") is possible on brcmfmac only with a virtual interface on the same channel; it is fragile under NM. A USB Wi-Fi dongle with AP support (e.g. MT7612U/MT7601U-class) is the robust way to do both.

### 1.2 Options
| Option | How | Pros | Cons |
|---|---|---|---|
| A. AP-only (recommended v1) | Saved NM profile, autoconnect, priority high | Simple, deterministic, what MF-09 delivers | Frame has no internet (no NTP time -> clock overlay #14 drifts; no updates) |
| B. Client preferred, AP fallback | Saved home-Wi-Fi profiles; watchdog (systemd timer, ~30s after boot) brings up AP if no client connection | Internet when at home, still reachable when away | Phone must find frame on home LAN (mDNS); switching modes drops connections; more logic to test |
| C. Concurrent AP + client | `iw dev wlan0 interface add uap0 type __ap` or second USB adapter | Both at once | Single-radio: same channel forced, flaky; dongle: extra hardware/driver risk |
| D. hostapd + dnsmasq (classic) | Disable NM on wlan0 | Fine control, captive tricks easy | Fights Bookworm defaults; two network stacks; not recommended |

**Recommendation:** A now (MF-09), design for B in Sprint 2 (stretch), C only with a USB dongle if the user wants internet + AP simultaneously.

Reference profile (MF-09 script already scoped):
```
nmcli con add type wifi ifname wlan0 con-name MemoryFrame-AP autoconnect yes ssid "$SSID" \
  802-11-wireless.mode ap 802-11-wireless.band bg 802-11-wireless.channel 6 \
  ipv4.method shared ipv4.addresses 10.42.0.1/24 ipv6.method disabled \
  wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$PSK" wifi-sec.proto rsn wifi-sec.pairwise ccmp wifi-sec.group ccmp \
  connection.autoconnect-priority 100
nmcli con modify MemoryFrame-AP 802-11-wireless-security.pmf disable   # some phones fail with PMF on brcmfmac; verify
```
Also disable Wi-Fi power save on wlan0 (`802-11-wireless.powersave 2`) — known cause of AP drop-outs on Pi.

### 1.3 Hostname, DNS, port
- Serve the admin site on **port 80** (phones type/scan a clean URL). Options: waitress binds 5000 and NM/nftables redirects 80->5000, or grant `CAP_NET_BIND_SERVICE` in the systemd unit (recommended; simplest).
- DNS: NM shared mode reads drop-ins from `/etc/NetworkManager/dnsmasq-shared.d/`. Add `address=/memoryframe.lan/10.42.0.1`. Avoid `.local` in dnsmasq (that's mDNS); use avahi for `memoryframe.local` (works on iOS, recent Android, and on home LAN in option B).
- Canonical URL printed everywhere: `http://10.42.0.1/` (IP always works; names are convenience).

### 1.4 Captive portal behaviour (iOS/Android)
- On joining, phones probe `captive.apple.com`, `connectivitycheck.gstatic.com/generate_204`, etc.
- If probes **fail (no DNS answer / no route)**: iOS shows "No Internet Connection", stays joined if user confirms; Android may show "Wi-Fi has no internet access" and *may switch to mobile data*, routing `10.42.0.1` requests poorly unless the user taps "stay connected". Accessing a local IP generally still works on Android because the route is on-link, but some OEMs prefer cellular — risk.
- If probes are **hijacked** (wildcard DNS to 10.42.0.1 + redirect): OS opens a captive mini-browser (CNA). CNA sheets have limited cookie persistence, block file pickers on some iOS versions, and close when "Done" tapped -> **bad for photo upload and persistent sessions.**
- **Recommendation:** no hijack. Return honest "no internet" (dnsmasq answers only our names; everything else NXDOMAIN fast so probes fail quickly). Onboarding via QR/NFC opens the real browser. Revisit an optional "captive welcome page that only shows 'Open in browser' link" later.

### 1.5 Risks
- Lock-out when changing network config headless -> keep a recovery: a GPIO/long-press or file on boot partition (`/boot/firmware/memoryframe-reset`) that restores the default AP profile; plus the on-screen display of current SSID/IP.
- Country code unset -> AP fails silently.
- PMF/WPA3 compatibility per phone; default WPA2 (rsn/ccmp).

---

## 2. Phone-side management website (from scratch)

### 2.1 Architecture
```
Phone browser --Wi-Fi(AP)--> :80 waitress (systemd: memoryframe.service, user memoryframe)
                                 Flask app
                                 ├── display blueprint  (/, /media, /uploads/*, SSE /events)  -> Chromium kiosk on localhost
                                 ├── admin blueprint    (/admin/*, HTML pages)                  -> phones, auth required
                                 ├── api blueprint      (/api/v1/*, JSON)                       -> admin JS, auth + CSRF
                                 └── services: media store, image pipeline (Pillow), settings, pairing/auth, SQLite
Storage: /var/lib/memoryframe/{originals,display,thumbs,tmp}, memoryframe.db, settings.json
```
- Display stays read-only and is served unauthenticated **to localhost only** (check `request.remote_addr`), or with a long-lived kiosk token in the kiosk URL. Phones never hit display write paths.
- Display learns about changes via **Server-Sent Events** `/events` (replaces 30s polling; fallback polling kept). Simple with waitress (one thread per stream; only one kiosk client).

### 2.2 Stack options
| Option | Pros | Cons |
|---|---|---|
| **Flask + Jinja + vanilla JS / htmx (recommended)** | Reuses existing app & skills, no build step, tiny footprint, fast on Pi 4 | Less "app-like" UI |
| Flask API + SPA (Vue/Svelte, built off-device) | Rich UI | Build toolchain, larger bundle over AP, two codebases |
| FastAPI rewrite | Async, OpenAPI docs | Rewrite for little gain at this scale |
Libraries: `waitress`, `Pillow` (+`pillow-heif` for iPhone HEIC), `Flask-WTF` (CSRF) or a ~20-line custom CSRF, `itsdangerous` (bundled), `sqlite3` stdlib, `qrcode` (for on-device QR). Serve htmx locally (no CDN — no internet on AP).

### 2.3 Pages (mobile-first, works offline, single CSS file)
1. **Pair** `/admin/pair` — enter 6-digit code shown on frame (or arrive via QR token link).
2. **Home / Library** `/admin/` — grid of thumbnails (lazy loaded), count, storage used/free; tap = detail.
3. **Upload** `/admin/upload` — `<input type=file multiple accept="image/*,video/*">`, per-file progress bars (XHR/fetch), resumes as separate requests per file, shows "optimising…" state.
4. **Item detail** — preview, hide/show from slideshow, rotate, delete (confirm), show next on frame.
5. **Display settings** `/admin/settings` — slide duration, transition (fade/Ken Burns), shuffle, brightness slider (uses MF-03 POST), schedule (night off / dim), clock overlay toggle (#14).
6. **Network** `/admin/network` — change SSID/password (warn: will disconnect), add home Wi-Fi (option B), show IP/hostname.
7. **Devices** `/admin/devices` — list paired phones (name, last seen), revoke; "pair new device" button (puts code on screen).
8. **System** `/admin/system` — storage, version, restart app, reboot/shutdown, download log bundle.

### 2.4 API (v1, JSON; all require session + CSRF header `X-CSRF-Token` for non-GET)
| Method | Path | Purpose |
|---|---|---|
| POST | /api/v1/pair/start | (from frame UI or authed device) show new pairing code on display |
| POST | /api/v1/pair | body {code or token, device_name} -> sets session cookie |
| POST | /api/v1/logout | revoke current session |
| GET | /api/v1/media?offset&limit | list items {id, type, w, h, size, hidden, created} |
| POST | /api/v1/media | multipart upload, one or more files -> 201 + ids; 413 if too large; 415 unsupported; 507 storage low |
| PATCH | /api/v1/media/{id} | {hidden, rotation} |
| DELETE | /api/v1/media/{id} | delete original + derivatives |
| POST | /api/v1/display/show/{id} | jump slideshow to item |
| GET/PUT | /api/v1/settings | display settings (validated schema) |
| GET/POST | /api/v1/brightness | (MF-03) |
| GET | /api/v1/devices · DELETE /api/v1/devices/{id} | manage paired phones |
| PUT | /api/v1/network/ap | change SSID/PSK (applies after response, 5s delay) |
| GET | /api/v1/system | storage, version, uptime |
| POST | /api/v1/system/{restart,reboot,shutdown} | power actions |
Display side: `GET /media` (existing, now returns optimised `display/` URLs), `GET /events` (SSE: `media-changed`, `settings-changed`, `show`, `pairing-code`).

### 2.5 Upload & image pipeline (backlog #12, #13)
- Limits: `MAX_CONTENT_LENGTH` 50 MB/request for images, 300 MB for video (configurable); max 50 files per batch; allow-list by **magic bytes** (Pillow open / ffprobe), not extension; random server-side filenames (UUID), never user-supplied paths.
- On upload: save to `tmp/`, verify, apply EXIF orientation, **strip EXIF/GPS** from derivatives, produce **display version fitting 800x480** (`ImageOps.contain`, keep aspect; generate at **1.0x = 800x480 max** — display is native 800x480, 1.25x headroom only if Ken Burns zoom looks soft: decide in testing, suggested 1000x600), JPEG q85 / WebP; thumbnail 240px. Keep original (optional setting "keep originals" for space).
- HEIC: `pillow-heif` (iPhones send JPEG via browser file picker usually, but not always).
- Video: no transcode in v1 (Pi 4 H.264 decode OK up to 1080p); reject >1080p or non-H.264 with a clear message; optional ffmpeg transcode later.
- Storage safeguard: refuse uploads when free space < 1 GB (507); show usage.
- Processing in a background thread queue (single worker) so requests return quickly.

### 2.6 On-device UX (800x480 DSI)
- **Pairing overlay** (on slideshow): large 6-digit code (≥ 72 px), QR code ≈ 300x300 px containing `http://10.42.0.1/admin/pair?t=<one-time token>`, SSID shown, 2-minute countdown. Triggered by: first boot with no paired devices, long-press (≥ 3 s) on screen, or "pair new device" from an existing phone.
- **Wi-Fi join QR** (separate screen, shown first if no devices paired): `WIFI:T:WPA;S:<ssid>;P:<psk>;;` — iOS/Android camera joins network, then second QR opens site. Showing PSK on screen is acceptable (physical access = trust) but make it optional.
- **Status strip** on long-press: IP, SSID, storage free, version.
- Empty-library screen: "Scan to add photos" + QR (replaces blank screen; ties into MF-02).
- Touch targets ≥ 48 px; DSI touch is single-touch-ish; no on-screen keyboard needed anywhere.

---

## 3. NFC

### 3.1 Use cases
| # | Use case | Needs reader? | Value |
|---|---|---|---|
| N1 | Tap phone on sticker -> join Wi-Fi + open site | No (passive tag) | High, cheap |
| N2 | Tap "album card" on frame to switch album/playlist | Yes | Nice-to-have |
| N3 | Tap phone to pair (NFC as proof of presence) | Yes, card emulation | Low; QR/PIN covers it |

### 3.2 Tags (N1)
- **NTAG215** (504 B user memory; NTAG213 144 B may be too small for Wi-Fi + URL). Sticker on the back/bezel; on-metal variant if the case is metal.
- NDEF content: record 1 URI `http://10.42.0.1/admin/pair` (no secret token — a static tag can't hold a one-time token); record 2 Wi-Fi WSC record (`application/vnd.wfa.wsc`) with SSID/PSK.
- Platform caveats: **Android** supports the Wi-Fi record natively (Android 10+ prompts to join). **iOS cannot join Wi-Fi from NFC**, and background tag reading opens only the first URL record (iPhone XS+). So on iOS: join via QR/settings, then tap tag opens site. -> Put URI record **first**.
- Writing tags: NFC Tools app on the user's phone (no hardware needed); doc a step. Lock the tag (read-only) after writing **or** don't include PSK if tag could be read by strangers (the tag is a password leak to anyone who taps it — acceptable for home use, document it).
- If the PSK changes, the tag must be rewritten (or N2 reader writes it).

### 3.3 Reader hardware (N2, deferred)
| Reader | Interface | Libraries | Notes |
|---|---|---|---|
| **PN532 breakout** (Elechouse/Adafruit) | I2C / SPI / UART | `adafruit-circuitpython-pn532` (Blinka), `nfcpy` (UART) | Recommended; cheap, well documented on Pi; I2C needs `dtparam=i2c_arm=on` — check DSI display doesn't conflict (DSI uses its own I2C, fine) |
| ACR122U | USB (PC/SC) | `pyscard` + `pcscd`, `nfcpy` | Plug-and-play but bulky, beeps, ACR122U known flaky with nfcpy |
| RC522 | SPI | `mfrc522`, `pi-rc522` | Very cheap; MIFARE Classic only reliable, poor NTAG/NDEF support |
| PN7150 | I2C | NXP libnfc-nci | Can do card emulation (N3); heavier integration |
A reader-based feature would run as a small systemd service posting to the local API (localhost-only token).

**Recommendation:** Sprint 2 = N1 only (tag + documentation + display copy). Reader work only if the user wants album cards.

---

## 4. Access control & security

### 4.1 Threat model (home device)
- Neighbours/guests in Wi-Fi range; anyone who joined the AP (password shared with family); physical access to frame (trusted by design); no internet exposure in option A. In option B the site is reachable by **everyone on the home LAN** -> app auth must not rely on WPA2 alone.

### 4.2 Layers
1. **Network:** WPA2-PSK ≥ 12 chars generated randomly at install (MF-09), shown on frame's Wi-Fi QR. Optional "hide SSID" adds nothing; skip.
2. **Device pairing (recommended)**:
   - Frame shows 6-digit code + QR token (valid 2 min, single use, 5 failed attempts -> code rotates, global rate limit 10/min).
   - On success: server creates `device` row (id, name, created, last_seen, hashed session secret), sets cookie `mf_session` = random 256-bit token (stored hashed), `HttpOnly; SameSite=Strict; Path=/; Max-Age=1y` (no `Secure` since HTTP — see 4.4).
   - Revoke via Devices page; "revoke all" from on-device long-press menu (physical recovery).
   - Alternative considered: shared admin PIN/password — simpler but must be typed, guessable, can't revoke one phone. Option: support an admin PIN as fallback for pairing when screen is unavailable (no, keep scope small; recovery file on boot partition instead).
3. **Request protection:** CSRF token (synchronizer, per-session) on all non-GET; check `Origin`/`Host` header against allow-list (`10.42.0.1`, `memoryframe.lan`, `memoryframe.local`) to block DNS rebinding; no state-changing GETs (remove `GET /brightness/<int>`); security headers (CSP `default-src 'self'`, `X-Content-Type-Options`, `frame-ancestors 'none'`).
4. **Display/admin split:** display routes read-only; any write routes for kiosk (e.g. brightness via touch) accept only `remote_addr == 127.0.0.1`.
5. **Uploads:** see 2.5 (magic bytes, size caps, UUID names, serve with `Content-Disposition`/correct type, `send_from_directory` path-safe).
6. **OS:** app runs as unprivileged `memoryframe` user; network/power changes via a narrow sudoers rule or polkit rule for specific `nmcli` / `systemctl` commands only; SSH password login off or key-only (recommend; user decision).

### 4.3 Sessions
- Server-side session table in SQLite (token hash -> device). Sliding `last_seen`. No JWT needed.
### 4.4 HTTPS?
- Self-signed cert on an offline AP triggers scary warnings and breaks PWA install; Let's Encrypt impossible offline. **Decision: HTTP on the WPA2 AP** (link-layer encrypted). In option B (home LAN) traffic is protected by home Wi-Fi encryption only — accepted risk, documented. Revisit if remote access is ever requested.

---

## 5. Risks
| Risk | Impact | Mitigation |
|---|---|---|
| Android drops to mobile data on no-internet AP | Site unreachable | Onboarding copy: "Stay connected"; test on user's phones (Q-B) |
| Network change locks out headless Pi | No access | Boot-partition reset file, on-screen status, delayed apply + auto-revert if no phone reconnects in 2 min |
| iOS HEIC / large videos | Upload failures | pillow-heif, clear error messages, limits |
| SD card wear / power loss during write | Corrupt library | Write to tmp + atomic rename, SQLite WAL, optional backup export |
| Static NFC tag leaks PSK | Neighbour can join | Document; optional URL-only tag |
| Concurrent AP+client fragility | Flaky network | Not in v1 |
| Pi 4 CPU for image processing | Slow uploads | Single worker queue, Pillow `draft()` for JPEG downscale; ~1-2 s/12 MP photo expected (verify) |

## 6. Open questions (for user via Delivery Lead)
- Q-A: Should the frame ever join home Wi-Fi (internet for clock/time sync, updates)? Determines option A vs B.
- Q-B: Which phones (iOS/Android, models) must be supported? Drives captive/NFC testing.
- Q-C: How many people will manage the frame? (pairing vs shared PIN)
- Q-D: Keep originals or only optimised copies? (storage)
- Q-E: Video support required, and max length?
- Q-F: Want NFC sticker (cheap, N1) and/or album cards (reader, N2)?
- Q-G: Is a real-time clock or internet time required for the clock overlay (#14)? (Pi 4 has no RTC.)
- Q-H: Is showing the Wi-Fi password as a QR on the frame acceptable?

## 7. Proposed Sprint 2 tickets
Sizes S/M/L as in Sprint 1. IDs continue after MF-09.

### MF-10 Hostname, DNS and port 80 for the frame
- Depends: MF-04, MF-09 · Size: S
- Description: avahi `memoryframe.local`, dnsmasq-shared drop-in `memoryframe.lan`, waitress on port 80 via `CAP_NET_BIND_SERVICE`, country code set, Wi-Fi power save off. Non-local names return NXDOMAIN quickly.
- AC:
  - From a phone joined to the AP, `http://10.42.0.1/` and `http://memoryframe.lan/` load the app; `http://memoryframe.local/` loads on iOS and on Android 12+.
  - `nslookup example.com 10.42.0.1` returns NXDOMAIN in < 1 s; no captive-portal sheet appears on iOS or Android after joining (verified on ≥ 1 device each).
  - After reboot, all of the above still work; `iw wlan0 get power_save` reports off.

### MF-11 App restructure: blueprints, storage layout, SQLite, display/admin split
- Depends: MF-03, MF-04 · Size: M
- Description: Split into display/admin/api blueprints; data dir `/var/lib/memoryframe`; SQLite schema (media, devices, sessions, settings); migrate existing `uploads/` files on first run; display write routes localhost-only.
- AC:
  - Existing slideshow behaviour unchanged (MF-05 tests pass).
  - Files in legacy `uploads/` are imported once and appear in `/media`; second run imports nothing.
  - A non-localhost request to any display write route returns 403; tests cover this.
  - No state-changing GET route exists (test enumerates `app.url_map`).

### MF-12 Pairing & session auth (on-screen code + QR)
- Depends: MF-11 · Size: M
- AC:
  - With no paired devices, the frame shows the pairing overlay (6-digit code ≥ 72 px, QR, SSID) readable at 800x480; long-press ≥ 3 s on the screen shows it on demand.
  - Scanning the QR or entering the code pairs the phone and lands on `/admin/`; the code/token is single-use and expires after 120 s (tests).
  - 5 wrong codes rotate the code; > 10 attempts/min returns 429.
  - Every `/admin/*` and `/api/v1/*` route (except pair) returns 302/401 without a valid session (test enumerates routes).
  - Cookie is HttpOnly, SameSite=Strict; tokens stored hashed in DB.
  - Revoking a device in Devices page logs that phone out on next request.

### MF-13 CSRF, Host/Origin checks and security headers
- Depends: MF-11 · Size: S
- AC:
  - Non-GET API calls without a valid `X-CSRF-Token` or form token return 403 (tests).
  - Requests with a Host not in the allow-list return 400 (DNS rebinding test).
  - Responses include CSP `default-src 'self'`, `X-Content-Type-Options: nosniff`, `frame-ancestors 'none'`; no external CDN references in templates.

### MF-14 Phone upload page + upload API
- Depends: MF-12, MF-13 · Size: M
- AC:
  - On iOS Safari and Android Chrome, selecting 20 photos uploads them with per-file progress; all appear on the frame within 30 s of completion without a page reload on the frame (SSE or polling).
  - Files are validated by content: a `.jpg`-renamed text file returns 415; a file over the limit returns 413 with a readable message.
  - Stored names are server-generated; path traversal names (`../x.jpg`) cannot escape the data dir (test).
  - Uploads are refused with 507 when free space < configured minimum (test with mocked disk usage).

### MF-15 Image optimisation pipeline (800x480)
- Depends: MF-11 · Size: M
- AC:
  - Each uploaded image gets a display derivative fitting within the agreed target (default 800x480 or 1000x600 after visual check) and a 240 px thumbnail; EXIF orientation applied.
  - Derivatives contain no EXIF GPS data (test with a GPS-tagged fixture).
  - HEIC from iPhone is accepted and converted.
  - Processing a 12 MP JPEG on the Pi 4 takes < 3 s (measured, recorded); the slideshow uses derivatives, not originals.

### MF-16 Library management page (grid, hide, delete, show now)
- Depends: MF-14, MF-15 · Size: M
- AC:
  - Grid loads thumbnails lazily; 500 items render without the page exceeding 5 MB transferred on first load.
  - Hide removes the item from the slideshow within 10 s; unhide restores it; delete removes original and derivatives (disk check) after confirmation.
  - "Show now" jumps the frame to that item within 5 s.

### MF-17 Display settings page
- Depends: MF-12, MF-03 · Size: M
- AC:
  - Slide duration, transition, shuffle and brightness can be changed from the phone and apply on the frame within 5 s without reload; values persist across reboot.
  - Invalid values (e.g. duration 0 or text) return 400 and do not change settings (tests).

### MF-18 Devices & network settings (change SSID/password safely)
- Depends: MF-12, MF-09 · Size: M
- AC:
  - Devices page lists paired phones with last-seen; revoke works (see MF-12).
  - Changing SSID/password from the phone applies after a warning; if no paired device reconnects within 2 min the previous profile is restored automatically.
  - Creating `/boot/firmware/memoryframe-reset` and rebooting restores the default AP profile and revokes all sessions; documented in README.
  - App performs network changes via a restricted sudoers/polkit rule only (reviewed); app user has no general sudo.

### MF-19 On-device onboarding screens (Wi-Fi QR, empty library, status)
- Depends: MF-12 · Size: S
- AC:
  - With no media and no paired devices, the frame shows Wi-Fi join QR then site QR; scanning with iOS Camera and Android Camera joins the network and opens the site (verified on hardware).
  - Long-press menu shows IP, SSID, free storage, version and "pair new device"; all text legible at 800x480 from 1 m.
  - Wi-Fi QR can be disabled in settings (Q-H).

### MF-20 NFC sticker (passive NTAG215) setup guide
- Depends: MF-10 · Size: S · Optional (Q-F)
- AC:
  - README section documents writing an NTAG215 with URI record first and optional Wi-Fi record, using a free phone app, and the PSK-leak caveat.
  - Tapping the tag opens the site on an iPhone (XS+) already on the AP, and joins + opens on an Android 10+ phone (hardware verified).

### MF-21 Spike: client Wi-Fi with AP fallback (option B)
- Depends: MF-09, answer to Q-A · Size: S (spike)
- AC:
  - Prototype watchdog brings up the AP within 60 s of boot when no known Wi-Fi is available, and stays on client when it is; result and recommendation (incl. USB dongle option) written up.

### MF-22 Tests & hardware checklist for Sprint 2 features
- Owner: Tester · Depends: MF-10…MF-19 · Size: M
- AC:
  - pytest covers auth, CSRF, upload validation, settings validation; coverage of new modules ≥ 80%; CI green.
  - Hardware checklist executed on Pi 4B + 800x480 DSI with ≥ 1 iOS and ≥ 1 Android phone: join, pair, upload 20 photos, settings, revoke, network reset; results recorded.

### Suggested sequencing
MF-10, MF-11 -> MF-12, MF-13, MF-15 -> MF-14, MF-17, MF-19 -> MF-16, MF-18 -> MF-20, MF-22. MF-21 any time after Q-A.
