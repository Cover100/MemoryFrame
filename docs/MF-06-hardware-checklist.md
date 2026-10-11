# MF-06 Hardware Test Checklist & Soak Test — Memory Frame

**Status: PENDING — no on-device runs performed yet.** Execute on hardware after MF-02, MF-03, MF-04 and MF-09 are merged (may be run remotely if Raspberry Pi Connect is enabled; physical-touch steps still need someone at the frame).

## Test environment (record before starting)
| Item | Expected | Recorded |
|---|---|---|
| Board | Raspberry Pi 4 Model B | |
| OS | Raspberry Pi OS 64-bit — paste `cat /etc/os-release` | |
| Display | 5" DSI IPS 800x480 — `ls /sys/class/backlight/` node | |
| Session | X11 or Wayland — `echo $XDG_SESSION_TYPE` | |
| App commit | `git -C <repo> rev-parse --short HEAD` | |
| Phone(s) | model + OS used for hotspot test | |
| Tester / date (AEST) | | |

Result codes: PASS / FAIL / BLOCKED / N/A. Every FAIL gets a note and becomes a proposed Sprint 2 item (filed only after approval).

## Checklist
| # | Area (ticket) | Steps | Pass criterion | Result | Notes |
|---|---|---|---|---|---|
| H1 | Hotspot persistence (MF-09) | Power-cycle the Pi (pull power 10 s). On phone, scan Wi-Fi. | SSID visible and phone joins with configured password, no one runs nmcli. | PENDING | |
| H2 | Hotspot reachability (MF-09) | On joined phone, open `http://10.42.0.1/` (or `http://memoryframe.lan/`). | Page loads with no internet available. | PENDING | |
| H3 | Hotspot security (MF-09) | Try connecting with `MemoryFrame123`; check `nmcli -s connection show <profile>` key-mgmt. | Old password rejected; key-mgmt `wpa-psk`. | PENDING | |
| H4 | Boot to slideshow (MF-04) | Power-cycle with stopwatch from power-on. Repeat 3×. | Slideshow visible ≤ 60 s each time; never a connection-refused page. Record times. | PENDING | |
| H5 | Single instance (MF-04) | After boot: `pgrep -af "app.py\|waitress\|gunicorn"`; `pgrep -c chromium` main process. | Exactly one app instance and one Chromium kiosk. | PENDING | |
| H6 | Crash restart (MF-04) | `sudo kill -9 <app pid>`; watch screen. | Service restarts (`systemctl status`), display recovers without manual action; record seconds. | PENDING | |
| H7 | Empty-folder recovery (MF-02) | Stop app, empty `uploads/`, start; then copy one JPG in. | Image appears within 30 s, no reload/reboot. | PENDING | |
| H8 | Corrupt JPG skip (MF-02) | Add a file `bad.jpg` (e.g. `head -c 2000 /dev/urandom > bad.jpg`) alongside good files. | Skipped within ≤ IMAGE_DURATION; logged in console/journal. | PENDING | |
| H9 | Corrupt/unsupported MP4 skip (MF-02) | Add `bad.mp4` of random bytes. | Skipped within ≤ IMAGE_DURATION, no hang. | PENDING | |
| H10 | Swipe | Swipe left and right on the touchscreen 5× each. | Advances next / previous each time, no freeze. | PENDING | |
| H11 | Brightness drag (MF-03) | Drag brightness from min to max and back; read `/sys/class/backlight/<node>/brightness`. | Backlight changes visibly; floor 40 respected; final value matches last drag position. | PENDING | |
| H12 | Brightness API (MF-03) | `curl -X POST` set request with 0, 9999, `abc`; `curl` GET `/brightness/100`. | Values clamped (40..max), invalid rejected (4xx), old GET-set route gone. | PENDING | |
| H13 | Video playback | Add a valid short .mp4. | Plays to end, then advances. | PENDING | |
| H14 | Soak (1 h) | See soak procedure below. | All soak criteria met. | PENDING | |

## Soak test procedure (1 hour; MF-02 requires ≥ 10 min with a bad file — covered)
1. Load `uploads/` with ≥ 10 good images, 1 valid video, 1 `bad.jpg`, 1 `bad.mp4`.
2. Record baseline: `free -m`, `vcgencmd measure_temp`, `vcgencmd get_throttled`, `ps -o pid,rss,cmd -C chromium` and app process RSS.
3. Start a logger:
   ```bash
   while true; do echo "$(date +%T) $(vcgencmd measure_temp) $(vcgencmd get_throttled) $(free -m | awk '/Mem/{print $3}')MB"; sleep 60; done | tee ~/mf06-soak.log
   ```
4. At ~20 min, add a new good image (should appear) and remove one (slideshow must not break).
5. Check the screen at 0, 15, 30, 45, 60 min: still cycling, no frozen frame or error page.
6. At end: `journalctl -u <service> --since "-65 min"` for errors/restarts; repeat step 2.

**Soak pass criteria:** slideshow cycling at every check; zero unplanned service restarts; bad files skipped every cycle; memory growth < ~20% over baseline (note actual); temp < 80 °C and `get_throttled` = `0x0` (note if not); added file appears, removed file doesn't crash.

| Check | 0 min | 15 | 30 | 45 | 60 |
|---|---|---|---|---|---|
| Cycling OK | PENDING | | | | |
| Temp / throttled | | | | | |
| Used memory (MB) | | | | | |

## Sign-off
- Overall: PENDING
- Failures → proposed Sprint 2 items:
- Demo video/photo captured for sprint review (MF-08): ☐
