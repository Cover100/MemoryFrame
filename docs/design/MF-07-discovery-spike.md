# MF-07 Discovery Spike: Hotspot, Phone Management Site, NFC, Access Control

- Ticket: MF-07 (Sprint 1) · Owner: Analyst · Status: Draft for review · Rev 2 (10 Oct 2026): updated with user decisions D1–D5 (section 6) · Rev 3: D6–D8
- Target: Raspberry Pi 4 Model B, Raspberry Pi OS 64-bit (Bookworm, NetworkManager), 5" DSI IPS 800x480
- Baseline: `app.py` (Flask, port 5000, unauthenticated routes `/`, `/media`, `/uploads/<f>`, `GET /brightness[/<int>]`), kiosk slideshow in `templates/index.html` + `static/script.js`. No phone-side UI exists.
- Builds on: MF-03 (brightness POST), MF-04 (systemd + WSGI), MF-09 (persistent NM hotspot profile).

## 0. Summary of recommendations
1. **Network:** keep NetworkManager. One saved AP profile (`MemoryFrame-AP`, WPA2-PSK, `ipv4.method shared`, 10.42.0.1/24) **Hotspot-only, permanently** (D1): the frame never joins home Wi-Fi, so there are no client profiles, fallback logic or concurrent AP+client.
2. **Name resolution:** fixed IP `10.42.0.1`, mDNS `memoryframe.local` (avahi), plus a dnsmasq drop-in resolving `frame.home`/`memoryframe.lan` to 10.42.0.1. **No captive-portal hijacking in v1**; instead tell the OS "no internet" cleanly and deep-link via QR/NFC. Captive portal is an optional later ticket.
3. **Phone site:** same Flask app, split into `/admin/*` (phone UI, auth) and display routes (localhost-only writes). Server-rendered Jinja + a little vanilla JS (no build step, no Node on the Pi), Pillow for 800x480 optimisation, SQLite for metadata/sessions, waitress behind systemd (MF-04).
4. **Access control:** WPA2 password is layer 1; layer 2 is device pairing: on-screen 6-digit PIN / QR with one-time token shown on the 800x480 display -> long-lived signed session cookie per phone, revocable. CSRF tokens, rate limiting, `SameSite=Strict`, admin-only state changes via POST.
5. **NFC (D7):** the user already has an NFC tag that opens the phone site; its role is the **site URL record**. Wi-Fi join is via the on-screen Wi-Fi QR (primary) or an optional Wi-Fi record added to a tag. No reader hardware; album cards / PN532 moved to backlog (BL-N1).
6. **Single owner (D6)** and **hardware RTC assumed (D8)**.

---

## 1. Wi-Fi hotspot (AP) on Bookworm

### 1.1 Facts / constraints
- Bookworm uses NetworkManager by default; `nmcli device wifi hotspot` creates a transient `Hotspot` connection (what the user does today). NM's `ipv4.method shared` spawns its own dnsmasq for DHCP/DNS (default subnet 10.42.0.0/24, gateway 10.42.0.1) and NATs to any upstream (eth0) if present.
- Pi 4 onboard Wi-Fi (CYW43455) supports 2.4 GHz and 5 GHz. Use **2.4 GHz, channel 6** by default (best phone compatibility, range), `wifi.band bg`. Set Wi-Fi country (`raspi-config nonint do_wifi_country AU`) or AP may not start / 5 GHz is blocked.
- AP + station on the same radio ("concurrent") is possible on brcmfmac only with a virtual interface on the same channel; it is fragile under NM. A USB Wi-Fi dongle with AP support (e.g. MT7612U/MT7601U-class) is the robust way to do both.

### 1.2 Options
| Option | How | Pros | Cons |
|---|---|---|---|
| A. AP-only (recommended v1) | Saved NM profile, autoconnect, priority high | Simple, deterministic, what MF-09 delivers | Frame has no internet (no NTP: time from hardware RTC, D8; no OTA updates) |
| ~~B. Client preferred, AP fallback~~ (rejected, D1) | Saved home-Wi-Fi profiles; watchdog (systemd timer, ~30s after boot) brings up AP if no client connection | Internet when at home, still reachable when away | Phone must find frame on home LAN (mDNS); switching modes drops connections; more logic to test |
| ~~C. Concurrent AP + client~~ (rejected, D1) | `iw dev wlan0 interface add uap0 type __ap` or second USB adapter | Both at once | Single-radio: same channel forced, flaky; dongle: extra hardware/driver risk |
| D. hostapd + dnsmasq (classic) | Disable NM on wlan0 | Fine control, captive tricks easy | Fights Bookworm defaults; two network stacks; not recommended |

**Decision (D1):** Option A only, permanently. Consequences: no internet ever -> time comes from the user's hardware RTC (D8, assumed fitted and configured; no software time-sync work), no OS updates over the air (updates via SD/USB or temporary ethernet), and no LAN exposure.

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
- DNS: NM shared mode reads drop-ins from `/etc/NetworkManager/dnsmasq-shared.d/`. Add `address=/memoryframe.lan/10.42.0.1`. Avoid `.local` in dnsmasq (that's mDNS); use avahi for `memoryframe.local` (works on recent Android, 12+).
- Canonical URL printed everywhere: `http://10.42.0.1/` (IP always works; names are convenience).

### 1.4 Captive portal behaviour (Android; iOS out of scope, D2)
- On joining, phones probe `captive.apple.com`, `connectivitycheck.gstatic.com/generate_204`, etc.
- If probes **fail (no DNS answer / no route)**: Android may show "Wi-Fi has no internet access" and *may switch to mobile data*, routing `10.42.0.1` requests poorly unless the user taps "stay connected". Accessing a local IP generally still works on Android because the route is on-link, but some OEMs prefer cellular — risk.
- If probes are **hijacked** (wildcard DNS to 10.42.0.1 + redirect): OS opens a captive mini-browser (CNA). CNA sheets have limited cookie persistence, restrict file pickers, and close when "Done" tapped -> **bad for photo upload and persistent sessions.**
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
3. **Upload** `/admin/upload` — `<input type=file multiple accept="image/*">` (images only in Sprint 2; video is backlog, D5), per-file progress bars (XHR/fetch), resumes as separate requests per file, shows "optimising…" state.
4. **Item detail** — preview, hide/show from slideshow, rotate, delete (confirm), show next on frame.
5. **Display settings** `/admin/settings` — slide duration, transition (fade/Ken Burns), shuffle, brightness slider (uses MF-03 POST), schedule (night off / dim), clock overlay toggle (#14).
6. **Network** `/admin/network` — change SSID/password (warn: will disconnect), show IP/hostname.
7. **Devices** (section on System page, D6) — the single owner's paired phones (name, last seen), "sign out all phones", and "pair another phone" (puts code on screen). No per-user roles.
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
| GET | /api/v1/devices · POST /api/v1/devices/revoke-all | list owner's phones; sign out all (D6) |
| PUT | /api/v1/network/ap | change SSID/PSK (applies after response, 5s delay) |
| GET | /api/v1/system | storage, version, uptime |
| POST | /api/v1/system/{restart,reboot,shutdown} | power actions |
Display side: `GET /media` (existing, now returns optimised `display/` URLs), `GET /events` (SSE: `media-changed`, `settings-changed`, `show`, `pairing-code`).

### 2.5 Upload & image pipeline (backlog #12, #13)
- Limits: `MAX_CONTENT_LENGTH` 50 MB/request (configurable); max 50 files per batch; allow-list by **magic bytes** (Pillow open / ffprobe), not extension; random server-side filenames (UUID), never user-supplied paths.
- On upload: save to `tmp/`, verify, apply EXIF orientation, **strip EXIF/GPS** from derivatives, produce **display version fitting 800x480** (`ImageOps.contain`, keep aspect; generate at **1.0x = 800x480 max** — display is native 800x480, 1.25x headroom only if Ken Burns zoom looks soft: decide in testing, suggested 1000x600), JPEG q85 / WebP; thumbnail 240px. **Originals are always kept** (D4), unmodified, in `originals/`; derivatives in `display/` and `thumbs/`.
- HEIC: `pillow-heif` (some Android phones, e.g. Samsung with "high efficiency" on, save HEIC).
- Storage budget (D4): typical phone photo 3–8 MB original + ~150 KB display + ~20 KB thumb ≈ 5 MB/photo avg. 32 GB card (~22 GB free after OS) ≈ 4,000 photos; 64 GB ≈ 10,000. Recommend ≥ 64 GB A1/A2 card (or USB SSD for heavy use). Show usage + estimated remaining photo count in UI.
- Video (D5): **not in Sprint 2.** New uploads of video are rejected with 415 and a friendly message. Existing playback of videos already in the library keeps working. Seamless video integration is backlog item BL-V1 (section 7).
- Storage safeguard: refuse uploads when free space < 1 GB (507); show usage.
- Processing in a background thread queue (single worker) so requests return quickly.

### 2.6 On-device UX (800x480 DSI)
- **Pairing overlay** (on slideshow): large 6-digit code (≥ 72 px), QR code ≈ 300x300 px containing `http://10.42.0.1/admin/pair?t=<one-time token>`, SSID shown, 2-minute countdown. Triggered by: first boot with no paired devices, long-press (≥ 3 s) on screen, or "pair new device" from an existing phone.
- **Wi-Fi join QR** (separate screen, shown first if no devices paired): `WIFI:T:WPA;S:<ssid>;P:<psk>;;` — Android camera / Google Lens joins network, then second QR opens site. Showing the PSK as QR is **approved** (D3): physical access = trust; enabled by default.
- **Status strip** on long-press: IP, SSID, storage free, version.
- Empty-library screen: "Scan to add photos" + QR (replaces blank screen; ties into MF-02).
- Touch targets ≥ 48 px; DSI touch is single-touch-ish; no on-screen keyboard needed anywhere.

---

## 3. NFC

### 3.1 Use cases
| # | Use case | Needs reader? | Value |
|---|---|---|---|
| N1 | Tap phone on sticker -> join Wi-Fi + open site | No (passive tag) | High, cheap |
| N2 | ~~Tap "album card" to switch album~~ | Yes | Moved to backlog BL-N1 (D7) |
| N3 | ~~Tap phone to pair~~ | Yes, card emulation | Dropped; QR/PIN covers it |

**Decision (D7):** the user already has an NFC tag that opens the phone site. Its role is the **site URL record** (`http://10.42.0.1/` -> redirects to `/admin/pair` when not signed in). Wi-Fi join: the **on-screen Wi-Fi QR** is primary (D3); a Wi-Fi (WSC) record on the tag is optional.

### 3.2 Tags (N1) — user's existing tag
- Existing tag: verify its URL is `http://10.42.0.1/` (or `memoryframe.lan`) and rewrite if not. If adding a Wi-Fi record, check capacity: **NTAG215** (504 B) fits both; NTAG213 (144 B) may be too small. Sticker on the back/bezel; on-metal variant if the case is metal.
- NDEF content: record 1 URI `http://10.42.0.1/admin/pair` (no secret token — a static tag can't hold a one-time token); record 2 Wi-Fi WSC record (`application/vnd.wfa.wsc`) with SSID/PSK.
- Platform (D2, Android only): URL-only tag opens the site in Chrome when the phone is on the frame's Wi-Fi. Optional Wi-Fi record: Android 10+ prompts to join; combined Wi-Fi + URL behaviour varies by vendor, so if used, test on the user's phone, and keep QR as the reliable path.
- Writing tags: NFC Tools app on the user's phone (no hardware needed); doc a step. Lock the tag (read-only) after writing **or** don't include PSK if tag could be read by strangers (the tag is a password leak to anyone who taps it — acceptable for home use, document it).
- If the PSK changes, a tag carrying a Wi-Fi record must be rewritten (a URL-only tag is unaffected).

### 3.3 Reader hardware
Not needed (D7). Album cards with a reader (PN532 over I2C recommended if ever revived) are backlog item BL-N1.

---

## 4. Access control & security

### 4.1 Threat model (home device)
- Single owner manages the frame (D6). Threats: neighbours/guests in Wi-Fi range; anyone who joined the AP (password shown as QR, so visitors can join); physical access to frame (trusted by design); no internet or home-LAN exposure (hotspot-only, D1). App auth is still required because the Wi-Fi password is shared and shown as QR (D3).

### 4.2 Layers
1. **Network:** WPA2-PSK ≥ 12 chars generated randomly at install (MF-09), shown on frame's Wi-Fi QR. Optional "hide SSID" adds nothing; skip.
2. **Device pairing (recommended)**:
   - Frame shows 6-digit code + QR token (valid 2 min, single use, 5 failed attempts -> code rotates, global rate limit 10/min).
   - On success: server creates `device` row (id, name, created, last_seen, hashed session secret), sets cookie `mf_session` = random 256-bit token (stored hashed), `HttpOnly; SameSite=Strict; Path=/; Max-Age=1y` (no `Secure` since HTTP — see 4.4).
   - Single owner (D6): all paired phones belong to the owner, same full rights; no user accounts, roles or per-device permissions. Revocation is simple: "sign out all phones" (from the site or the on-device long-press menu), then re-pair.
   - Alternative considered: shared admin PIN/password — simpler but must be typed, guessable, can't revoke one phone. Option: support an admin PIN as fallback for pairing when screen is unavailable (no, keep scope small; recovery file on boot partition instead).
3. **Request protection:** CSRF token (synchronizer, per-session) on all non-GET; check `Origin`/`Host` header against allow-list (`10.42.0.1`, `memoryframe.lan`, `memoryframe.local`) to block DNS rebinding; no state-changing GETs (remove `GET /brightness/<int>`); security headers (CSP `default-src 'self'`, `X-Content-Type-Options`, `frame-ancestors 'none'`).
4. **Display/admin split:** display routes read-only; any write routes for kiosk (e.g. brightness via touch) accept only `remote_addr == 127.0.0.1`.
5. **Uploads:** see 2.5 (magic bytes, size caps, UUID names, serve with `Content-Disposition`/correct type, `send_from_directory` path-safe).
6. **OS:** app runs as unprivileged `memoryframe` user; network/power changes via a narrow sudoers rule or polkit rule for specific `nmcli` / `systemctl` commands only; SSH password login off or key-only (recommend; user decision).

### 4.3 Sessions
- Server-side session table in SQLite (token hash -> device). Sliding `last_seen`. No JWT needed.
### 4.4 HTTPS?
- Self-signed cert on an offline AP triggers scary warnings and breaks PWA install; Let's Encrypt impossible offline. **Decision: HTTP on the WPA2 AP** (link-layer encrypted). Revisit if remote access is ever requested.

---

## 5. Risks
| Risk | Impact | Mitigation |
|---|---|---|
| Android drops to mobile data on no-internet AP | Site unreachable | Onboarding copy: "Stay connected"; test on user's Android phone(s) |
| Network change locks out headless Pi | No access | Boot-partition reset file, on-screen status, delayed apply + auto-revert if no phone reconnects in 2 min |
| HEIC / oversized photos | Upload failures | pillow-heif, clear error messages, limits |
| SD fills up (originals kept, D4) | Uploads fail / OS instability | ≥ 64 GB card, 1 GB reserve -> 507, usage + remaining estimate in UI |
| SD card wear / power loss during write | Corrupt library | Write to tmp + atomic rename, SQLite WAL, optional backup export |
| Static NFC tag leaks PSK | Neighbour can join | Document; optional URL-only tag |
| No internet -> no OS updates (D1) | Unpatched OS | Offline update procedure; time from hardware RTC (D8, assumption: RTC fitted and set) |
| Pi 4 CPU for image processing | Slow uploads | Single worker queue, Pillow `draft()` for JPEG downscale; ~1-2 s/12 MP photo expected (verify) |

## 6. Questions and decisions
| # | Question | Status / decision (user, 10 Oct 2026) |
|---|---|---|
| Q-A | Should the frame ever join home Wi-Fi? | **Resolved — D1:** No. Standalone, hotspot-only permanently. Options B/C and MF-21 dropped. |
| Q-B | Which phones must be supported? | **Resolved — D2:** Android only for now; iOS out of scope. NFC Wi-Fi record now viable. |
| Q-H | Show Wi-Fi password as QR on frame? | **Resolved — D3:** Yes, OK (on by default). |
| Q-D | Keep originals or only optimised copies? | **Resolved — D4:** Keep originals plus 800x480 display renditions (and thumbs). Storage sizing in 2.5. |
| Q-E | Video support? | **Resolved — D5:** Nice-to-have, seamless integration later -> backlog BL-V1, not Sprint 2. |
| Q-C | How many people manage the frame? | **Resolved — D6:** a single person. Single owner, pairing for their phone(s), simple "sign out all" revocation. |
| Q-F | NFC sticker and/or album cards? | **Resolved — D7:** user already has a tag that opens the site (URL record). Wi-Fi join via on-screen QR, optional Wi-Fi record. Album cards/PN532 -> backlog BL-N1. |
| Q-G | Clock time source? | **Resolved — D8:** handled by user's hardware RTC (assumed). No time-sync work. |

## 7. Proposed Sprint 2 tickets
Sizes S/M/L as in Sprint 1. IDs continue after MF-09.

### MF-10 Hostname, DNS and port 80 for the frame
- Depends: MF-04, MF-09 · Size: S
- Description: avahi `memoryframe.local`, dnsmasq-shared drop-in `memoryframe.lan`, waitress on port 80 via `CAP_NET_BIND_SERVICE`, country code set, Wi-Fi power save off. Non-local names return NXDOMAIN quickly.
- AC:
  - From a phone joined to the AP, `http://10.42.0.1/` and `http://memoryframe.lan/` load the app; `http://memoryframe.local/` loads on Android 12+.
  - `nslookup example.com 10.42.0.1` returns NXDOMAIN in < 1 s; no captive-portal sheet appears on the user's Android phone after joining.
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
  - "Sign out all phones" (site or on-device long-press menu) invalidates every session; each phone gets 401 on its next request and can re-pair (D6). No multi-user/role features exist.

### MF-13 CSRF, Host/Origin checks and security headers
- Depends: MF-11 · Size: S
- AC:
  - Non-GET API calls without a valid `X-CSRF-Token` or form token return 403 (tests).
  - Requests with a Host not in the allow-list return 400 (DNS rebinding test).
  - Responses include CSP `default-src 'self'`, `X-Content-Type-Options: nosniff`, `frame-ancestors 'none'`; no external CDN references in templates.

### MF-14 Phone upload page + upload API
- Depends: MF-12, MF-13 · Size: M
- AC:
  - On Android Chrome, selecting 20 photos uploads them with per-file progress; all appear on the frame within 30 s of completion without a page reload on the frame (SSE or polling).
  - Files are validated by content: a `.jpg`-renamed text file returns 415; a file over the limit returns 413 with a readable message.
  - Stored names are server-generated; path traversal names (`../x.jpg`) cannot escape the data dir (test).
  - Uploads are refused with 507 when free space < configured minimum (test with mocked disk usage).

### MF-15 Image optimisation pipeline (800x480)
- Depends: MF-11 · Size: M
- AC:
  - Each uploaded image gets a display derivative fitting within the agreed target (default 800x480 or 1000x600 after visual check) and a 240 px thumbnail; EXIF orientation applied.
  - Derivatives contain no EXIF GPS data (test with a GPS-tagged fixture).
  - The original file is stored byte-identical (checksum match) in `originals/` and is never served to the display; deleting an item removes original + derivatives.
  - HEIC from Android is accepted and converted.
  - Uploading 20 photos increases used storage by ≈ sum of originals + < 250 KB per photo for derivatives (measured); the UI shows used/free space and an estimated remaining photo count.
  - Video uploads return 415 with a "video coming later" message (D5).
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

### MF-18 Paired phones & network settings (change SSID/password safely)
- Depends: MF-12, MF-09 · Size: M
- AC:
  - System page lists the owner's paired phones with last-seen and offers "sign out all phones" (see MF-12).
  - Changing SSID/password from the phone applies after a warning; if no paired device reconnects within 2 min the previous profile is restored automatically.
  - Creating `/boot/firmware/memoryframe-reset` and rebooting restores the default AP profile and revokes all sessions; documented in README.
  - App performs network changes via a restricted sudoers/polkit rule only (reviewed); app user has no general sudo.

### MF-19 On-device onboarding screens (Wi-Fi QR, empty library, status)
- Depends: MF-12 · Size: S
- AC:
  - With no media and no paired devices, the frame shows Wi-Fi join QR then site QR; scanning with the Android camera joins the network and opens the site (verified on hardware).
  - Long-press menu shows IP, SSID, free storage, version and "pair new device"; all text legible at 800x480 from 1 m.
  - Wi-Fi QR is shown by default (D3); it can be disabled in settings.

### MF-20 Configure user's existing NFC tag to open the site
- Depends: MF-10 · Size: S
- AC:
  - The URL on the user's existing tag is checked and, if needed, rewritten to the canonical site URL (`http://10.42.0.1/`); README documents how (free Android app) and the record content.
  - With the user's Android phone already on the frame's Wi-Fi, tapping the tag opens the site; not signed in -> pairing page, signed in -> library (hardware verified).
  - Optional: if a Wi-Fi record is added, tapping prompts to join and the PSK-leak caveat and "rewrite after password change" step are documented; otherwise README states Wi-Fi join is via the on-screen QR.

### MF-21 ~~Spike: client Wi-Fi with AP fallback~~ — WON'T DO
- Dropped per D1 (frame is hotspot-only permanently). ID retained to avoid renumbering.

### MF-22 Tests & hardware checklist for Sprint 2 features
- Owner: Tester · Depends: MF-10…MF-19 · Size: M
- AC:
  - pytest covers auth, CSRF, upload validation, settings validation; coverage of new modules ≥ 80%; CI green.
  - Hardware checklist executed on Pi 4B + 800x480 DSI with the user's Android phone(s): join, pair, upload 20 photos, settings, NFC tag tap, sign out all, network reset; results recorded.

### Suggested sequencing
MF-10, MF-11 -> MF-12, MF-13, MF-15 -> MF-14, MF-17, MF-19 -> MF-16, MF-18 -> MF-20, MF-22.

## 8. Backlog additions (not Sprint 2)
### BL-V1 Seamless video integration (D5)
- Description: Upload, validate (ffprobe), optionally transcode (ffmpeg, H.264 ≤ 1080p) and play video in the slideshow seamlessly alongside photos (poster frame thumbnail, mute default, max length setting, skip on error).
- Draft AC: Android-recorded MP4 (≤ configured length) uploads, appears in library with thumbnail, plays to end then advances without gap > 1 s; unsupported codec is transcoded or rejected with a message; storage accounting includes video.

### BL-N1 NFC album cards (D7, backlog)
- Description: A reader (PN532 over I2C recommended) on the frame; tapping a programmed card switches album/playlist. Not planned; revisit if the user asks.
