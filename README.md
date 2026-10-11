# Memory Frame

A Flask-based digital photo/video frame. The display app serves a full-screen
kiosk slideshow (shuffle, Ken Burns, fade, swipe next/previous, hold-and-drag
brightness) of everything in `uploads/`.

Target hardware: **Raspberry Pi 4 Model B**, **Raspberry Pi OS 64-bit**,
5" DSI IPS display (800x480). Windows/other Linux is supported for development.

### Upload limits

- Photos over 64 megapixels (`MEMORYFRAME_MAX_MEGAPIXELS`) are refused with 415 and a clear message. This is checked from the image header before any decoding.
- If the disk fills up while a file is being written, the upload returns 507 and partial temp files are removed.
- Display labels keep Unicode letters and digits (for example `Été_à_Paris.jpg`). Path parts and control characters are dropped, and the label falls back to `photo.jpg`.

## Setup — Raspberry Pi (Raspberry Pi OS 64-bit)

```bash
sudo apt update && sudo apt install -y python3-venv git chromium-browser
git clone https://github.com/Cover100/MemoryFrame.git
cd MemoryFrame
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Then open http://localhost:5000. For start-on-boot kiosk mode see "Run on boot" below.

## Setup — Windows (development)

```powershell
git clone https://github.com/Cover100/MemoryFrame.git
cd MemoryFrame
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

The default browser opens http://localhost:5000.

## Media and data directory (MF-11)

Photos and the database live in the **data directory**:

| Where | What |
|---|---|
| `/var/lib/memoryframe` | Pi (created by `deploy/install.sh`; the service sets `MEMORYFRAME_DATA_DIR`) |
| `./data` | development fallback when `/var/lib/memoryframe` isn't writable (git-ignored) |

```
<data dir>/memoryframe.db   SQLite: media, devices, sessions, settings
<data dir>/originals/       photos exactly as imported/uploaded, server-generated names
<data dir>/display/         display copies (MF-15)   thumbs/  thumbnails (MF-15)
<data dir>/tmp/             in-progress files        backups/ migration backups
```

**One-time migration of the old `uploads/` folder.** On first start (or `python -m memoryframe migrate`):
1. A tar backup of every photo in `uploads/` is written to `<data dir>/backups/legacy-uploads-<time>.tar`
   with a SHA-256 manifest, and verified by reading it back. If there isn't room for backup + copies,
   it stops and changes nothing.
2. Each photo is **copied** into `originals/` (checksum-verified) and added to the library.
   `uploads/` is never changed or deleted. Identical files are imported once.
3. The import is marked done; running it again imports nothing.

`python -m memoryframe status` shows the data dir, media count and backup path. Copy the backup tar
off the Pi as well (it is on the same SD card). Supported types: `.jpg .jpeg .png .webp .gif`, and
`.mp4 .mov .webm` (video upload comes later, D5).

URL layout: display `/`, `/media`, `/uploads/<name>`, `/brightness`, `/healthz`; management site
`/admin/` (MF-12+); API `/api/v1/` (MF-12+). Display write routes (`POST /brightness`) only accept
requests from the frame itself (127.0.0.1 / ::1); from the network they return 403.

## Known limitations

- **Brightness** control uses the Raspberry Pi DSI backlight via sysfs
  (`/sys/class/backlight/...`). On Windows and non-Pi Linux it is not available.

## Run on boot (Raspberry Pi) — service + kiosk

`python app.py` serves the app with **waitress** (production WSGI server) on port 5000.
On the Pi, install it as a service plus a kiosk that only opens once the
server is healthy:

```bash
cd ~/MemoryFrame
./deploy/install.sh     # run as the desktop user; uses sudo where needed
sudo reboot
```

What it does:

- `deploy/memoryframe.service` → `/etc/systemd/system/` (`Restart=always`, so a crash or
  `kill -9` restarts the app within ~2 s; the open slideshow page recovers on its own).
- `deploy/memoryframe-kiosk.desktop` → `~/.config/autostart/`, which runs
  `deploy/kiosk.sh`: waits for `GET /healthz`, then runs one Chromium kiosk
  (restarted if it exits; a lock prevents a second launcher).
  Chromium runs with its own profile (`~/.cache/memoryframe/chromium-profile`),
  `--password-store=basic` (no keyring prompt), no first-run/restore bubbles, and
  `--ozone-platform=wayland` when a Wayland session is detected (`WAYLAND_DISPLAY` or `XDG_SESSION_TYPE=wayland`; the exact command line is logged as `launch: ...`). The launcher and Chromium's
  stdout/stderr go to `~/.cache/memoryframe-kiosk.log` (rotated at 1 MB). Extra flags:
  `MEMORYFRAME_CHROMIUM_FLAGS`; browser override: `MEMORYFRAME_BROWSER`.
  Debugging: `MEMORYFRAME_KIOSK_DEBUG=1` adds DevTools on **127.0.0.1:9222 only** plus Chromium
  logging; `python3 deploy/kiosk-console.py [--seconds N] [--screenshot file.png]` then prints the
  page's console messages, JS exceptions and slideshow state (standard library only).
- `deploy/audit-autostart.sh --disable` checks user/system XDG autostart, LXDE/labwc/wayfire
  autostart, shell profiles, crontab `@reboot`, `/etc/rc.local` and systemd units for anything
  else that starts the app or Chromium, and disables it (with backups).
  Run `./deploy/audit-autostart.sh` without arguments for a report only.
  It only flags commands that actually launch Memory Frame or a kiosk browser
  (`.../MemoryFrame/app.py`, `cd .../MemoryFrame && python app.py`, `localhost:5000` / `127.0.0.1:5000`,
  `chromium --kiosk`, a duplicate `deploy/kiosk.sh`); a file that only contains the home path
  (e.g. `/home/memoryframe/pi-apps/updater`) is not flagged. For `.desktop` files only `Exec=` is checked.
  Lines containing `memoryframe-managed` are always left alone. Crontab backups go to `$HOME`.

Check after reboot (expect exactly one of each):

```bash
systemctl status memoryframe
pgrep -fc "app.py"            # 1
pgrep -f deploy/kiosk.sh      # 1 launcher
journalctl -u memoryframe -b
```

Rollback: `./deploy/uninstall.sh` (backups of anything the audit disabled are kept next to the
original files as `*.disabled-by-memoryframe.*` / `*.bak-memoryframe.*`).

Development: on Windows `python app.py` also opens the default browser; on Linux set
`MEMORYFRAME_OPEN_BROWSER=1` to do the same. `MEMORYFRAME_HOST` / `MEMORYFRAME_PORT` override the bind address.

## Wi-Fi hotspot (Raspberry Pi) - manual only

`deploy/hotspot.sh` manages a saved NetworkManager profile (`MemoryFrame-hotspot`, AP mode on
`wlan0`). **Hotspot use is manual only:** the profile always has `autoconnect=no` (re-running the
script resets it to `no` if it was changed), so it never starts at boot or on its own. Starting at
boot is not supported (`--enable-at-boot` is rejected). The script only touches its own profile
and never modifies or deletes others (home Wi-Fi, old `Hotspot`).

> **Warning:** on a Pi 4, NetworkManager cannot run `wlan0` as a Wi-Fi client and an access
> point at the same time. Activating the hotspot **drops the current Wi-Fi and any remote access
> over it** (SSH, Raspberry Pi Connect). The first hotspot test must be done with the user
> **physically at the Pi** (keyboard + screen).

```bash
cp deploy/hotspot.conf.example deploy/hotspot.conf   # optional; git-ignored
nano deploy/hotspot.conf                             # set SSID and PASSWORD
./deploy/hotspot.sh                  # create/update only: autoconnect=no, NOT activated (safe remotely)
./deploy/hotspot.sh --test 120       # at the Pi: up for 120 s, then a systemd timer switches back
./deploy/hotspot.sh --rollback       # down + delete the profile, let NetworkManager reconnect wlan0
```

- Security: WPA2-PSK (AES/CCMP). Password must be 8-63 characters, 12+ recommended; the old
  default `MemoryFrame123` is refused. Never commit `deploy/hotspot.conf`.
- Address: while active, the frame is **10.42.0.1**: open **http://10.42.0.1/** (or **http://memoryframe.lan/**) from a phone
  (no internet needed).
- `--test SECONDS` (30-1800) arms the revert first (`systemd-run --on-active=SECONDS`, runs as
  root and survives the session dropping), then brings the hotspot up. The revert takes the
  hotspot down and brings back the Wi-Fi profile that was active before.
- **Change password/SSID:** edit `deploy/hotspot.conf` and re-run `./deploy/hotspot.sh`
  (same profile, no duplicates, autoconnect stays off).
- **Rollback:** `./deploy/hotspot.sh --rollback`. If wlan0 does not come back on its own:
  `sudo nmcli connection up <your Wi-Fi profile>`.
- On-device runbook: `docs/runbooks/MF-04-MF-09-pi-runbook.md`.

## Frame address: port 80, memoryframe.lan / .local (MF-10)

- The service listens on **port 80 and 5000** (`MEMORYFRAME_PORTS=80,5000` in `deploy/memoryframe.service`). It still runs as the normal user; only `CAP_NET_BIND_SERVICE` is granted. `MEMORYFRAME_PORT` (single port) still works for development.
- `sudo deploy/network-setup.sh` (idempotent; `--remove` undoes its files) sets:
  - the hostname `memoryframe` and avahi, so `http://memoryframe.local/` works on the home network and on Android 12+.
  - a NetworkManager `dnsmasq-shared.d` drop-in for the hotspot: `memoryframe.lan` → `10.42.0.1`, and every other name gets an **immediate NXDOMAIN**. There is no upstream DNS and no captive-portal redirect.
  - Wi-Fi power save off (`wifi.powersave = 2`) and the Wi-Fi country (`AU` by default).
- The script never brings a connection up or down. It only reloads NetworkManager's config, so it is safe to run over Pi Connect. The hotspot is still manual only (MF-09).

## Display copies (MF-15)

The slideshow never loads the original files. For every image, a JPEG **display copy** is written to `<data dir>/display/<id>.jpg` with these rules:
- **Covers** the target size: both sides are at least the target and the aspect ratio is kept, so the CSS `object-fit: cover` crop stays sharp. Copies are never upscaled.
- EXIF orientation is applied, and all metadata is stripped, including GPS.
- Each copy is **at most 300 KB**.

Originals in `originals/` are only read, never changed. Animated GIFs are served as originals.

- **Target size:** `MEMORYFRAME_DISPLAY_SIZE`, default **`800x600`**. The other candidate is `1000x750`; the choice is still open (U3). After changing it, run `python -m memoryframe display-copies --force`.
- **New photos:** the copy is made when the photo is added, or on its first request.
- **Existing photos:** `python -m memoryframe display-copies` creates the missing copies (`install.sh` runs it).
- **HEIC/HEIF** from phones is accepted through `pillow-heif` (wheels exist for Pi aarch64 / Python 3.13). The original `.heic` is kept, and the display gets a JPEG copy.

## Pairing a phone (MF-12)

There is one owner (D6), who may pair several phones.
- **Overlay:** with no paired phone, the frame shows a pairing overlay: a QR code, a 6-digit code, and the Wi-Fi name (from `MEMORYFRAME_SSID` or `deploy/hotspot.conf`). A still long-press of **3 s or more** on the screen shows it at any time.
- **Pairing:** scan the QR (it opens `http://10.42.0.1/pair?t=…`; tap "Pair this phone"), or open `http://10.42.0.1/pair` and type the code. Either lands on `/admin/`.
- **Code rules:** codes and QR links are single-use and expire after 120 s. 5 wrong codes rotate the code. More than 10 attempts per minute from one phone get HTTP 429.
- **Session:** the phone gets an HttpOnly, SameSite=Strict cookie (`mf_session`, valid 1 year). Only its SHA-256 is stored in the database.
- **Access rules:** every `/admin/*` route redirects to `/pair` and every `/api/v1/*` route returns 401 without a session. The frame's own pairing routes (`/pairing/*`) are localhost only.
- **Sign out all phones:** available on the site, or on the frame's overlay (long-press). It revokes every session; phones then re-pair.

## Web security (MF-13)

- **Host allow-list:** requests are answered only for `10.42.0.1`, `memoryframe`, `memoryframe.lan`, `memoryframe.local`, `localhost`, `127.0.0.1` and `::1`. A trailing dot (e.g. `memoryframe.lan.`) is accepted, and any other Host gets **400**, which blocks DNS rebinding.
- **`MEMORYFRAME_EXTRA_HOSTS`:** a comma list of extra names or IPs, e.g. the home-network IP for testing over Pi Connect. On the Pi, set it with `./deploy/install.sh --extra-hosts 192.168.50.124`, or `deploy/env-file.sh MEMORYFRAME_EXTRA_HOSTS 192.168.50.124` followed by `sudo systemctl restart memoryframe`. Either writes `/etc/default/memoryframe`, which the service reads via `EnvironmentFile=`. Pass `""` to remove it. Values are limited to `A-Z a-z 0-9 . : , _ -`.
- **Origin check:** a non-GET request with an `Origin`/`Referer` from another host gets **403**.
- **CSRF:** the `mf_csrf` cookie (HttpOnly, SameSite=Strict) must be echoed in the `X-CSRF-Token` header (read it from `<meta name="csrf-token">`) or in a `csrf_token` form field. Otherwise the request gets **403**. The localhost-only kiosk routes are exempt.
- **Headers:** `Content-Security-Policy: default-src 'self'; ... frame-ancestors 'none'`, `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`. Templates contain no inline scripts or styles and no external URLs (enforced by a test).

## Uploading photos from the phone (MF-14)

- **Page:** after pairing, open `/admin/upload` (linked from `/admin/`). Pick any number of photos; they upload **one request per file**, each with its own progress bar and result. The page also shows free space and roughly how many more photos fit.
- **API:** `POST /api/v1/media` (multipart, field `file`) needs a session and a CSRF token. It returns:
  - **201** added
  - **200** already on the frame (same SHA-256)
  - **400** no file
  - **413** too large (`MEMORYFRAME_MAX_UPLOAD_MB`, default 50)
  - **415** not a photo, or a video ("videos are coming later", D5)
  - **507** less than `MEMORYFRAME_MIN_FREE_MB` (default 500) free
- **Validation:** files are checked by **content**: Pillow must decode them as JPEG, PNG, WebP, GIF or HEIC. The stored extension comes from the content, and stored names are server-generated. The client's file name is only kept, sanitised, as a label.
- **Storage:** the original is stored byte-identical in `originals/`, and the display copy (MF-15) is made right away.
- **Frame refresh:** the frame now polls `/media` every 10 s, so new photos show up without a reload. `GET /api/v1/storage` returns the disk numbers.

## NFC tag (MF-24)

- Write the tag as an **http://** URI record: `http://10.42.0.1/` (or `http://memoryframe.lan/`). **Never use https://.** The frame serves plain HTTP only (ports 80/5000) and has no certificate, so an https:// tag fails on the phone (Samsung Internet shows "null is unreachable").
- With NFC Tools: Write → Add a record → **URL/URI** → choose the `http://` prefix → `10.42.0.1/`.
- The on-screen QR codes always encode http:// links. An https `MEMORYFRAME_SITE_URL` is downgraded to http and logged.
- The frame never sends HSTS, so a phone never learns to force https for the frame.

## On-device onboarding and menu (MF-19)

- **No photos and no phone paired:** full onboarding. On the hotspot it shows **1. Join Wi-Fi** (a standard `WIFI:` QR), then **2. Open site** (the pairing QR) and the 6-digit code.
- **Close:** every overlay has a Close button. It dismisses the onboarding and empty-library screens for the browser session, in every state including no photos, and a reboot or kiosk restart brings them back. While dismissed, only the small corner hint shows, and pairing stays in the long-press menu.
- **Photos but no phone paired:** the slideshow runs. Only a small "Hold 3 s for menu to pair a phone" hint shows in the corner.
- **Paired but no photos:** a "No photos yet" screen.
- **Still long-press of 3 s or more:** a menu with IP, Wi-Fi, site address, free storage, phones paired and version, plus **Pair new device** (always available), **Sign out all phones** and **Close**. Text is at least 24 px at 800x480.
- **Addresses follow the current network** (checked on every request):
  - **Hotspot** (the frame holds 10.42.0.1): `http://10.42.0.1/pair`, or `memoryframe.lan`.
  - **Home Wi-Fi:** `http://memoryframe.local/pair`, plus the frame's own IP as a fallback (e.g. `http://192.168.50.124/pair`).
  - `:5000` is added unless the app actually serves port 80 (`MEMORYFRAME_PORTS`).
  - The Host check accepts the frame's own current IP addresses automatically.
  - **Override:** `MEMORYFRAME_SITE_URL=http://frame.home` (no path) replaces the detected address. Set it on the Pi with `deploy/env-file.sh MEMORYFRAME_SITE_URL http://…` and restart the service. The `PAIR_BASE_URL` app config key still wins for tests and development.
- **Wi-Fi QR:** shown only on the hotspot, and **on by default** (D3). Turn it off with `MEMORYFRAME_SHOW_WIFI_QR=0` or the database setting `show_wifi_qr=0` (the settings UI comes with MF-17). SSID and password come from `deploy/hotspot.conf`, or `MEMORYFRAME_SSID` / `MEMORYFRAME_WIFI_PASSWORD`.
- **Privacy:** these screens use localhost-only routes (`/pairing/state`, `/frame/status`). The Wi-Fi password only leaves the frame as the on-screen QR.

## Library management (MF-16)

- **Page:** `/admin/library` (linked from `/admin/`) shows every photo as a lazily loaded **240 px thumbnail** (`thumbs/<id>.jpg`, made at upload or on first request), newest first, with these buttons:
  - **Show now:** the frame jumps to that photo within about 2 s.
  - **Hide / Unhide:** a hidden photo leaves the slideshow within about 2 s, even if it is on screen; it stays in the library.
  - **Delete:** after a confirmation, removes the original, the display copy, the thumbnail and the database row.
- **API** (session and CSRF): `GET /api/v1/media`, `GET /api/v1/media/<id>/thumb`, `PUT /api/v1/media/<id>/hidden {"hidden": bool}`, `DELETE /api/v1/media/<id>`, `POST /api/v1/media/<id>/show-now`.
- **Frame:** it polls `GET /show-now` every 2 s. That response carries the show-now request and a library revision, so uploads, hides and deletes reach the frame fast.
