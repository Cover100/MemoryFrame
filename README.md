# Memory Frame

A Flask-based digital photo/video frame. The display app serves a full-screen
kiosk slideshow (shuffle, Ken Burns, fade, swipe next/previous, hold-and-drag
brightness) of everything in `uploads/`.

Target hardware: **Raspberry Pi 4 Model B**, **Raspberry Pi OS 64-bit**,
5" DSI IPS display (800x480). Windows/other Linux is supported for development.

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

## Media

Put photos (`.jpg .jpeg .png .webp .gif`) and videos (`.mp4 .mov .webm`) in
`uploads/`. The folder is not tracked by git; the app creates it automatically. Its contents are
git-ignored: **never commit personal media** (photos can carry EXIF/GPS data).

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
- `deploy/audit-autostart.sh --disable` checks user/system XDG autostart, LXDE/labwc/wayfire
  autostart, shell profiles, crontab `@reboot`, `/etc/rc.local` and systemd units for anything
  else that starts the app or Chromium, and disables it (with backups).
  Run `./deploy/audit-autostart.sh` without arguments for a report only.

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
