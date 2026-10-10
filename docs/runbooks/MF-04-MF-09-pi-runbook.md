# Memory Frame: on-device runbooks (MF-04 deploy, MF-09 hotspot)

Written 10 Oct 2026 by Developer for Tester. Updated 17:50 AEST: hotspot manual only, Phase C removed, uploads backup mandatory. Based on the scripts as they are on GitHub:
- MF-04 scripts: `origin/main` (merged via #9). `deploy/install.sh`, `deploy/audit-autostart.sh`, `deploy/kiosk.sh`, `deploy/memoryframe.service`, `deploy/memoryframe-kiosk.desktop`, `deploy/uninstall.sh`.
- **Hotspot use is MANUAL ONLY (user decision, 10 Oct 2026).** The profile always has autoconnect=no; nothing starts it at boot. The first hotspot test happens only with the **user physically at the Pi**.
- **`uploads/` is ALWAYS backed up before any Pi update** (git reset, pull or install). See 1.1.
- MF-09 script: **use the `mf-09-hotspot` branch (PR #12), not `origin/main`.** The `hotspot.sh` merged to main in #8 activates the hotspot immediately and sets autoconnect=yes, priority 100. On this Pi that cuts off Pi Connect (see section 2.1). Until #12 is merged, **do not run `deploy/hotspot.sh` from main.**

Pi baseline (from Tester): Debian 13 trixie aarch64, user `memoryframe`, repo `/home/memoryframe/Documents/GitHub/MemoryFrame` on old main `6c1839d`, clean tree. Network: wlan0 on `Oversclan_5G` (192.168.50.124), NM profile `netplan-wlan0-Oversclan_5G`, an inactive `Hotspot` profile, eth0 down, Pi Connect is the only remote access. Backlight is `10-0045`. No services. The only launchers are Desktop `.desktop` files running `chromium --kiosk`.

Conventions: run everything as `memoryframe` in a terminal (Pi Connect remote shell is fine for section 1). `R=/home/memoryframe/Documents/GitHub/MemoryFrame`.

---

## 1. MF-04 deploy: service + kiosk

Remote access is not at risk in this section; nothing here touches networking.

### 1.1 Pre-checks and backups
```bash
R=/home/memoryframe/Documents/GitHub/MemoryFrame
cd "$R"
git status --porcelain            # MUST print nothing. If it prints anything, stop and report.
git rev-parse --short HEAD        # expect 6c1839d
git branch backup/old-main-6c1839d   # local pointer to the old history for rollback

# ALWAYS back up uploads/ before ANY update of the Pi checkout (every time, not just now).
# The old main TRACKED the sample photos in uploads/, so resetting to the new main
# (where they are untracked) DELETES them from disk.
B=~/memoryframe-uploads-backup-$(date +%Y%m%d-%H%M%S)
cp -a uploads "$B"
diff -r uploads "$B" && echo "backup OK: $B"     # must print backup OK
du -sh uploads "$B"

# Record what launches today (for the rollback):
ls -l ~/Desktop/*.desktop
grep -l "chromium" ~/Desktop/*.desktop
echo "$XDG_SESSION_TYPE"; ps -e | grep -E "labwc|wayfire|Xorg|lxsession"   # record X11/Wayland + compositor
```

### 1.2 Update the code (plain `git pull` won't work: history was rewritten)
```bash
cd "$R"
git fetch origin
git status --porcelain            # still empty
git reset --hard origin/main
git log --oneline -1              # should match origin/main on GitHub
# Restore the photos (uploads/ is now git-ignored, so they stay local):
mkdir -p uploads && cp -an "$B"/. uploads/
diff -r "$B" uploads && echo "photos restored"
git status --porcelain            # empty (uploads/ is ignored)
```

### 1.3 Python venv (trixie notes)
Trixie ships Python 3.13 and pip is "externally managed" (PEP 668), so always use the venv and never `sudo pip`. `python3 -m venv` needs the `python3-venv` package. `install.sh` installs it, but you can check first:
```bash
python3 --version
sudo apt-get install -y python3-venv curl
cd "$R"
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt      # Flask 3.1.3 stack + waitress 3.0.2 (pure Python, no compiler needed)
.venv/bin/python -c "import flask, waitress; print('ok')"
```
(`install.sh` repeats this idempotently. Doing it by hand first just surfaces errors earlier.)

### 1.4 Audit existing autostarts (report only, first)
```bash
cd "$R"
./deploy/audit-autostart.sh          # REPORT ONLY, changes nothing
```
What it checks, all with case-insensitive pattern `app\.py|flask|chromium|memoryframe|MemoryFrame|localhost:5000`:
- `~/.config/autostart/*.desktop`, `/etc/xdg/autostart/*.desktop`
- `~/.config/lxsession/*/autostart`, `/etc/xdg/lxsession/*/autostart`, `~/.config/labwc/autostart`, `/etc/xdg/labwc/autostart`, `~/.config/wayfire.ini`, `~/.xinitrc`, `~/.xsessionrc`, `~/.profile`, `~/.bash_profile`, `~/.bashrc`
- `crontab -l` for memoryframe and root, `/etc/crontab`, `/etc/cron.d/*` (report only)
- `/etc/rc.local`
- `/etc/systemd/system/*.service`, `~/.config/systemd/user/*.service`

Things to know before running `--disable` (it is run automatically by `install.sh`):
1. **The `~/Desktop/*.desktop` launchers are NOT scanned.** They are not autostarts, since they only run when clicked, so the audit will not find or disable them. Handle them by hand in 1.5.
2. **False-positive risk: the pattern includes `memoryframe`, which is also the username.** Any matching file that contains `/home/memoryframe/...` (e.g. a `PATH` line in `~/.bashrc`, an unrelated `.desktop` or systemd unit with `User=memoryframe`) would be reported, and in `--disable` mode its lines would be commented out or the file disabled. **Read the report. If anything listed is not a Memory Frame or Chromium launcher, do not run `install.sh`; use the manual install in 1.6b instead and report it to Developer.**
3. If it reports nothing, it ends with `No other autostart entries for Memory Frame or Chromium found.` That is the expected result for this Pi.

Where `--disable` puts backups (it always backs up first):
| Location type | Action | Backup |
|---|---|---|
| autostart `.desktop` files | `sudo mv` | same folder, `<file>.disabled-by-memoryframe.<YYYYmmddHHMMSS>` |
| session scripts / shell profiles | matching lines prefixed `# disabled by memoryframe <stamp>: ` | `<file>.bak-memoryframe.<stamp>` |
| crontabs | matching lines commented | `/tmp/crontab-<user>.bak-memoryframe.<stamp>`. **/tmp is wiped at reboot: copy it to ~ right away** |
| `/etc/rc.local` | lines commented | `/etc/rc.local.bak-memoryframe.<stamp>` |
| systemd units | `systemctl disable --now` | none needed (`systemctl enable` to undo) |
| `/etc/crontab`, `/etc/cron.d/*` | report only | edit manually |

### 1.5 Retire the Desktop launchers (manual)
They don't start at boot, but clicking one would start a second Chromium. Move them aside rather than deleting:
```bash
mkdir -p ~/memoryframe-launcher-backup
for f in ~/Desktop/*.desktop; do grep -q "chromium" "$f" && mv -v "$f" ~/memoryframe-launcher-backup/; done
ls ~/memoryframe-launcher-backup/
```

### 1.6a Install (normal path)
```bash
cd "$R"
./deploy/install.sh        # as memoryframe, NOT root; it uses sudo itself
```
What `install.sh` does, in order (`set -euo pipefail`, so it stops at the first error):
1. Refuses to run as root.
2. `sudo apt-get update` and `sudo apt-get install -y python3-venv curl`. If neither `chromium-browser` nor `chromium` is on PATH, it installs `chromium-browser`. On trixie `chromium` already exists, so nothing more is installed.
3. Creates `$R/.venv` if missing, runs `.venv/bin/pip install -r requirements.txt`, and runs `mkdir -p uploads`.
4. **Runs `deploy/audit-autostart.sh --disable`** (see 1.4).
5. Renders `deploy/memoryframe.service` with `@USER@=memoryframe` and `@APP_DIR@=$R` into **`/etc/systemd/system/memoryframe.service`**, then runs `systemctl daemon-reload`, `enable` and `restart`. The unit:
   - runs `ExecStart=$R/.venv/bin/python $R/app.py`, which starts waitress on `0.0.0.0:5000` with 4 threads
   - sets `Restart=always`, `RestartSec=2`, `StartLimitIntervalSec=0`, `WantedBy=multi-user.target`
6. Runs `chmod +x deploy/kiosk.sh` and writes **`~/.config/autostart/memoryframe-kiosk.desktop`** with `Exec=$R/deploy/kiosk.sh`.
7. Prints `XDG_SESSION_TYPE`. **Record it in the MF-04 results.**

What `kiosk.sh` does at login:
- takes the lock `$XDG_RUNTIME_DIR/memoryframe-kiosk.lock` (a second copy exits with `already running`)
- picks `chromium-browser` or `chromium`
- waits until `curl -fsS --max-time 2 http://localhost:5000/healthz` succeeds
- runs `xset s off -dpms s noblank` if X11
- launches the browser in a loop (restart 2 s after it exits) with these flags:
  `--kiosk --noerrdialogs --disable-infobars --disable-session-crashed-bubble --disable-features=Translate --autoplay-policy=no-user-gesture-required --ozone-platform-hint=auto --check-for-update-interval=31536000 http://localhost:5000`

### 1.6b Manual install (only if the audit report showed false positives)
```bash
cd "$R"
sed -e "s#@USER@#memoryframe#g" -e "s#@APP_DIR@#$R#g" deploy/memoryframe.service | sudo tee /etc/systemd/system/memoryframe.service
sudo systemctl daemon-reload && sudo systemctl enable --now memoryframe.service
chmod +x deploy/kiosk.sh && mkdir -p ~/.config/autostart
sed -e "s#@APP_DIR@#$R#g" deploy/memoryframe-kiosk.desktop > ~/.config/autostart/memoryframe-kiosk.desktop
```

### 1.7 Verify
```bash
systemctl status memoryframe --no-pager        # active (running)
curl -s http://localhost:5000/healthz           # {"status":"ok"}
curl -s http://localhost:5000/brightness        # supported:true, maximum from 10-0045 (MF-03)
journalctl -u memoryframe -b --no-pager | tail
```
**Reboot test** (AC: slideshow visible within 60 s with no connection-refused page):
```bash
sudo reboot
# Start a stopwatch at power-on / reboot. Watch the screen: no "This site can't be reached" page should appear.
# After login:
systemd-analyze; systemctl status memoryframe --no-pager
pgrep -fc "$R/app.py"                     # 1
pgrep -fc deploy/kiosk.sh                 # 1
pgrep -c -x chromium || pgrep -c -f chromium-browser   # 1 browser main process (child processes are expected; check: ps -ef --forest | grep -m3 chromium)
```
If the kiosk did not start, the session's XDG autostart probably isn't running. Check `cat /etc/xdg/labwc/autostart ~/.config/labwc/autostart 2>/dev/null`. As a fallback, append one line `$R/deploy/kiosk.sh &` (with `$R` expanded) to `~/.config/labwc/autostart`, record it, and report to Developer.

**`kill -9` recovery** (AC: restarts automatically and the display recovers):
```bash
PID=$(systemctl show -p MainPID --value memoryframe); echo $PID
sudo kill -9 "$PID"
sleep 5
systemctl show -p NRestarts --value memoryframe      # incremented
systemctl show -p MainPID --value memoryframe        # new PID
curl -s http://localhost:5000/healthz                # {"status":"ok"}
# Screen: the slideshow continues; any image that failed during the ~2 s gap is skipped (MF-02). No manual action.
```
Browser crash: `pkill -9 -f chromium`. `kiosk.sh` relaunches it within about 2 s once `/healthz` answers.

### 1.8 Roll back MF-04
```bash
cd "$R"
./deploy/uninstall.sh
# = systemctl disable --now memoryframe.service; rm /etc/systemd/system/memoryframe.service; daemon-reload;
#   rm ~/.config/autostart/memoryframe-kiosk.desktop; pkill -f deploy/kiosk.sh
pkill -f chromium   # if a kiosk browser is still open

# Restore the Desktop launchers:
mv -v ~/memoryframe-launcher-backup/*.desktop ~/Desktop/

# Restore anything the audit disabled (only if it found something), e.g.:
#   sudo mv <file>.disabled-by-memoryframe.<stamp> <file>
#   sudo cp -p <file>.bak-memoryframe.<stamp> <file>
#   crontab ~/crontab-memoryframe.bak-memoryframe.<stamp>   (if you copied it out of /tmp)
#   sudo systemctl enable --now <unit>

# Optional: back to the old code (the old history is still in this clone):
git reset --hard backup/old-main-6c1839d
cp -an ~/memoryframe-uploads-backup-*/. uploads/
```

---

## 2. MF-09 hotspot: read section 2.1 before touching anything

### 2.1 Risk analysis
**A. Activating the hotspot drops the home Wi-Fi and Pi Connect, immediately.**
- The profile is `802-11-wireless.mode ap` on `connection.interface-name wlan0`. NetworkManager runs one active connection per device, so `nmcli connection up MemoryFrame-hotspot` deactivates `netplan-wlan0-Oversclan_5G` on wlan0. The Pi leaves 192.168.50.124, and Pi Connect (the only remote path; eth0 is down) disconnects.
- **The version merged to main (#8) does exactly that at the end of every run** (`nm connection up "$CON_NAME"`). Running it remotely means losing the device until someone is physically at it.

**B. Concurrent AP+STA on the Pi 4 (brcmfmac / CYW43455): don't count on it.**
- The firmware advertises limited interface combinations. Check on the device with `iw list | sed -n '/valid interface combinations/,/^$/p'` (read-only).
- Even where AP+managed is listed, it requires a *second virtual interface* (e.g. `iw dev wlan0 interface add uap0 type __ap`) **and both on the same channel**. The home network is `Oversclan_5G` (5 GHz) while our profile is `band bg`, channel 6 (2.4 GHz), so concurrency is not possible with this config.
- NM with our single-`wlan0` profile will never do concurrency. Treat AP mode as exclusive.

**C. Boot-time takeover (fixed in #12: manual only).**
- In the version on main, the profile is saved with `connection.autoconnect yes` and `connection.autoconnect-priority 100`. Netplan-generated profiles default to priority 0. Check with:
  `nmcli -f connection.autoconnect,connection.autoconnect-priority connection show netplan-wlan0-Oversclan_5G`
- At boot NM activates the highest-priority available autoconnect profile, and an AP profile is always "available". So **the hotspot would take wlan0 at every boot**, and home Wi-Fi and Pi Connect would never come back on their own.

**D. Old rollback touched another profile.** The version on main's `--rollback` forced `connection.autoconnect yes` on the old `Hotspot` profile, even if it was `no` before. That could make the old weak-password hotspot grab wlan0 at the next boot. Neither version ever modified `netplan-wlan0-Oversclan_5G`.

Also note: netplan-generated profiles on trixie are backed by `/etc/netplan/*.yaml`. Editing them with `nmcli` rewrites netplan files. **Never modify `netplan-wlan0-Oversclan_5G`**; only `con up` it.

### 2.2 Fix (PR #12, branch `mf-09-hotspot`, commit 170d280): what the script does now
- `./deploy/hotspot.sh` (no flag): creates the profile with `autoconnect no` (set already on `connection add`), then `connection modify` with:
  - `connection.interface-name wlan0`, `connection.autoconnect no`, `connection.autoconnect-priority -10`
  - `802-11-wireless.ssid <SSID>`, `802-11-wireless.mode ap`, `802-11-wireless.band bg`, `802-11-wireless.channel 6`
  - `ipv4.method shared`, `ipv4.addresses 10.42.0.1/24`, `ipv6.method disabled`
  - `wifi-sec.key-mgmt wpa-psk`, `wifi-sec.proto rsn`, `wifi-sec.pairwise ccmp`, `wifi-sec.group ccmp`, `wifi-sec.psk <PASSWORD>`

  **It does not activate anything. Safe to run remotely.**
- `./deploy/hotspot.sh --test SECONDS` (30–1800): saves the profile (autoconnect stays `no`) and reads the currently active wlan0 profile via `nmcli -g GENERAL.CONNECTION device show wlan0`. It then **first** arms:
  `sudo systemd-run --on-active=SECONDS --unit=memoryframe-hotspot-revert-<pid> /bin/sh -c "nmcli connection down MemoryFrame-hotspot; nmcli connection up netplan-wlan0-Oversclan_5G || nmcli device connect wlan0"`
  and **then** runs `nmcli connection up MemoryFrame-hotspot`. The timer runs as root in systemd, so it fires even if the shell or Pi Connect session dies.
- `--enable-at-boot` is **rejected** (exit 2, "not supported: the hotspot is manual only"). Every run sets `connection.autoconnect no`, including when an existing profile is updated, so a profile left with autoconnect=yes by the old script is reset to `no`. `autoconnect-priority` is -10.
- `./deploy/hotspot.sh --rollback`: `nmcli connection down MemoryFrame-hotspot` (if present), `nmcli connection delete MemoryFrame-hotspot`, then `nmcli device connect wlan0` (NM picks its best autoconnect profile, i.e. the home Wi-Fi). It no longer touches `Hotspot` or any other profile.
- Password rules: 8–63 chars, `MemoryFrame123` (any case) refused, warning under 12. SSID 1–32 chars.
- Tested against stateful `nmcli` and `systemd-run` stubs (`tests/test_hotspot_script.py`, 18 tests, all passing, including: autoconnect always no, `--enable-at-boot` rejected, re-run resets autoconnect=yes to no):
  - the default run never runs `connection up`
  - re-running is idempotent
  - netplan and `Hotspot` never appear in any nmcli call
  - `--test` arms the revert before activation, and firing it restores `netplan-wlan0-Oversclan_5G`
  - **`--rollback` deletes only our profile and the home Wi-Fi is active again**

### 2.3 Safe procedure
**Phase A: remote is OK (creates the profile only, changes nothing live).**
```bash
R=/home/memoryframe/Documents/GitHub/MemoryFrame
cd "$R" && git fetch origin
# Until PR #12 is merged, run the fixed script from a separate detached worktree:
git worktree add ~/mf-hotspot origin/mf-09-hotspot
cd ~/mf-hotspot
grep -q "MANUAL ONLY" deploy/hotspot.sh && echo "fixed version"   # must print "fixed version"
cp deploy/hotspot.conf.example deploy/hotspot.conf && chmod 600 deploy/hotspot.conf
nano deploy/hotspot.conf          # SSID + a NEW password, 12+ chars (git-ignored, never commit)
nmcli -t -f NAME,DEVICE connection show --active > ~/nm-active-before.txt   # baseline
./deploy/hotspot.sh               # default: save only
nmcli -f connection.autoconnect,802-11-wireless.mode connection show MemoryFrame-hotspot   # autoconnect: no, mode: ap
nmcli -t -f NAME,DEVICE connection show --active | diff ~/nm-active-before.txt - && echo "unchanged"   # Wi-Fi still Oversclan
```

**Phase B: activation test. The USER must be physically at the Pi for the first test** (keyboard + screen attached, phone at hand). Pi Connect *will* drop for the test window.
```bash
cd ~/mf-hotspot
./deploy/hotspot.sh --test 180    # 3 minutes; the revert is armed before the switch
systemctl list-timers 'memoryframe-hotspot-revert-*'   # (on the local screen) shows the pending revert
```
On the phone: join the SSID with the new password, then open `http://10.42.0.1:5000`. The slideshow page should load (no internet needed). Note iOS/Android "no internet" prompts for MF-07.
After 180 s, check on the local screen:
```bash
nmcli -t -f NAME,DEVICE connection show --active    # netplan-wlan0-Oversclan_5G on wlan0 again
ip -4 addr show wlan0                               # 192.168.50.124
```
Then confirm Pi Connect reconnects from a remote machine.

Equivalent raw commands, if you prefer not to use `--test` (same safety: arm first, then switch):
```bash
sudo systemd-run --on-active=180 --unit=mf-hotspot-revert /bin/sh -c \
  'nmcli connection down MemoryFrame-hotspot; nmcli connection up netplan-wlan0-Oversclan_5G'
sudo nmcli connection up MemoryFrame-hotspot
```
(With `nohup` instead: `sudo nohup sh -c 'sleep 180; nmcli connection down MemoryFrame-hotspot; nmcli connection up netplan-wlan0-Oversclan_5G' >/tmp/revert.log 2>&1 &`, started **before** `con up`. `systemd-run` is preferred because it survives the session ending.)

Phase C (enable at boot) was **removed**: the user decided the hotspot is manual only.

### 2.4 Roll back MF-09
At the keyboard, or remotely if the Pi is still on the home Wi-Fi:
```bash
cd ~/mf-hotspot && ./deploy/hotspot.sh --rollback
#  = nmcli connection down MemoryFrame-hotspot; nmcli connection delete MemoryFrame-hotspot; nmcli device connect wlan0
nmcli -t -f NAME,DEVICE connection show --active   # netplan-wlan0-Oversclan_5G on wlan0
# If wlan0 did not reconnect on its own:
sudo nmcli connection up netplan-wlan0-Oversclan_5G
```
Emergency (script unavailable): `sudo nmcli connection down MemoryFrame-hotspot; sudo nmcli connection up netplan-wlan0-Oversclan_5G`.
The old `Hotspot` profile is never modified by the fixed script. Its state stays as Tester recorded it (inactive).
Clean up the helper worktree when PR #12 is merged: `cd "$R" && git worktree remove ~/mf-hotspot`. After that, use `$R/deploy/hotspot.sh`. Move `deploy/hotspot.conf` across first; it is git-ignored, so `worktree remove` refuses until you delete or move it.

### 2.5 Record for MF-06
- the `iw list` interface combinations
- the netplan profile's autoconnect and priority
- Phase A diff output
- Phase B: phone joined (y/n), page loaded (y/n), revert happened at T+180 s (y/n), Pi Connect back (y/n)
- after a reboot: `nmcli -f connection.autoconnect connection show MemoryFrame-hotspot` is `no` and wlan0 is on the home Wi-Fi
- the rollback result
