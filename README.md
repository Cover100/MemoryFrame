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

Then open http://localhost:5000 (the app also tries to launch Chromium in kiosk mode).

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
