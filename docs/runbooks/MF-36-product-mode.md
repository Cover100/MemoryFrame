# MF-36 / MF-51 runbook: product mode, Developer Wi-Fi, recovery

> **Scope.** Product mode by default and Developer Wi-Fi until reboot, as shipped in **#52 (MF-51)**.

## ⚠️ Development Pi: keep home Wi-Fi across reboots

The default flips to product (hotspot). A dev Pi **without a mode file** would boot into the hotspot
on its next reboot and lose home Wi-Fi and Pi Connect. Before pulling/deploying this code on a dev Pi:

    echo dev | sudo tee /boot/firmware/memoryframe-mode.txt
    sudo deploy/netmode.sh status        # expect: armed for next boot: dev (memoryframe-mode.txt = dev)

(`install.sh` also writes `dev` when there is no mode file and wlan0 is on a home Wi-Fi
connection, but do it by hand first: the guard only runs when install.sh runs.)

## Behaviour

- **Every boot is product mode** unless `/boot/firmware/memoryframe-mode.txt` says `dev`.
  `memoryframe-netmode.service` runs `deploy/netmode.sh boot`: the hotspot (`MemoryFrame-hotspot`)
  is brought up and **verified first**, and only then made the preferred boot connection
  (`autoconnect=yes`, `autoconnect-priority=100`). Phones reach `http://10.42.0.1/`.
- **The hotspot always stays on** in product mode, whether or not a phone is connected. There is
  **no** "no phone joined → back to home Wi-Fi" fallback (removed in MF-51; no
  `MEMORYFRAME_PRODUCT_CONFIRM_MIN`).
- **Home Wi-Fi profiles are never modified** (they may be netplan-generated); only `MemoryFrame-*`
  profiles are touched. If the hotspot fails to start, the previous connection is brought back and
  the frame shows an error screen. The boot service never fails the boot and never reboots.
- **Developer Wi-Fi (until reboot):**
  - On the frame: a still **3-second hold anywhere** on the screen (moving < 20 px; a faint ring
    fills after 1 s) opens the confirm **"Join developer Wi-Fi?"** / "Phones on the frame's Wi-Fi
    will disconnect until you restart the frame." with **Cancel** (default) and **Join**. After Join:
    "Joining developer Wi-Fi…" / "Restart the frame to go back to its own Wi-Fi."; on failure
    "Couldn't switch. Try again." Lifting early or moving (swipe, brightness drag) cancels silently.
  - The double-tap menu has **no** developer item: only **Pair new device**, **Sign out all phones**,
    **Shut down** and **Close**. The 3 s hold no longer opens the menu (double tap only).
  - From the shell: `sudo deploy/netmode.sh dev-temp`.
  - Either one runs `netmode dev-temp`: hotspot down, home Wi-Fi up **until the next reboot**.
    Nothing is written to the boot partition (state only in `/run`, a tmpfs), and the hotspot
    profile keeps its autoconnect, so **the next boot is product mode again**.
  - If home Wi-Fi doesn't connect (default wait 45 s, `MEMORYFRAME_DEV_WAIT`), the hotspot comes back.
  - The frame route `POST /frame/dev-mode` is localhost-only and needs the kiosk token; the sudoers
    rule allows only `memoryframe-netmode dev-temp` for this.
- **Persistent developer mode** (developer's Pi only): `dev` in `memoryframe-mode.txt`, written from
  a PC or with `sudo deploy/netmode.sh dev`. Every boot then joins home Wi-Fi; if it can't, the
  hotspot starts for that boot only and the next boot tries home Wi-Fi again.

## Files on the boot partition (`/boot/firmware`, the "bootfs" drive on a PC)

| File | Content | Effect at next boot |
|---|---|---|
| `memoryframe-mode.txt` | `dev` or `product` (one word) | `dev` = persistent developer mode (home Wi-Fi). `product`, missing, empty or anything else = **product** (hotspot) |
| `memoryframe-dev-wifi.txt` | optional, **only** `ssid=<name>` | which **existing** NetworkManager profile developer mode uses (default: the most recently used client profile) |
| `memoryframe-reset` | empty | recreate the default hotspot profile (`deploy/hotspot.sh`), write `product`; the file is deleted |

**Never put a password in any of these files.** `memoryframe-dev-wifi.txt` holds only `ssid=`; a
`password=` (or `psk=`, `key=`, `secret`) line is ignored and a warning is logged **without** its
value. Dev Wi-Fi credentials live only in the root-only NetworkManager profile
(`/etc/NetworkManager/system-connections/`, 0600), set up over SSH or with a keyboard, e.g.
`sudo nmcli connection add type wifi ifname wlan0 con-name Home ssid Home wifi-sec.key-mgmt wpa-psk wifi-sec.psk '…' connection.autoconnect no`.

## Shell (SSH, keyboard, or Ethernet)

    sudo deploy/netmode.sh status     # armed for next boot, current mode, active connection, notes
    sudo deploy/netmode.sh dev-temp   # home Wi-Fi now, until the next reboot (no file written)
    sudo deploy/netmode.sh dev        # home Wi-Fi now AND write memoryframe-mode.txt = dev
    sudo deploy/netmode.sh product    # hotspot now AND write memoryframe-mode.txt = product
    journalctl -t memoryframe-netmode -b

`dev`/`dev-temp` over SSH from the hotspot drops the session (the Pi 4 radio can't be client and
hotspot at once); `product` over SSH from home Wi-Fi drops it too: reconnect via the other network.
Switches are refused (exit 3) while another switch runs or an MF-18 Wi-Fi change is pending.

## What the frame shows

- Developer Wi-Fi until reboot: a small bottom-left label `Developer Wi-Fi · <LAN IP> · restart to exit`.
  Persistent developer mode (`dev` mode file): `Developer mode · <LAN IP>`. These are the only places a
  LAN IP appears.
- Developer Wi-Fi missing / wrong password: within ~60 s the hotspot comes up and the toast
  `Developer Wi-Fi not found, hotspot started` shows for 30 s, once per boot.
- Hotspot fails to start: full-screen error with the PC recovery steps; the slideshow keeps running;
  Close hides it for 10 minutes. No reboot loop.

## Recovery / no lock-out

| Situation | Network-free way back |
|---|---|
| Frame in Developer Wi-Fi (until reboot) and unreachable | Power-cycle or menu **Shut down**, then start: boots into product (hotspot) |
| Persistent `dev` and home Wi-Fi gone | Hotspot starts automatically for that boot; or on a PC write `product` in `memoryframe-mode.txt` (or delete it) and boot |
| Hotspot broken / wrong name or password | On a PC create an empty `memoryframe-reset` on bootfs and boot: default hotspot from `/etc/memoryframe/hotspot.conf` (the copy `install.sh` makes of `deploy/hotspot.conf`: re-run `install.sh` after editing it), product mode |
| Need home Wi-Fi permanently (dev Pi) | On a PC write `dev` in `memoryframe-mode.txt` and boot |
| Keyboard/SSH available | `sudo deploy/netmode.sh status`, then `product` / `dev` / `dev-temp` |

To remove MF-36 completely: `sudo systemctl disable memoryframe-netmode.service` and
`sudo deploy/netmode.sh dev` (the hotspot loses its autoconnect; client profiles were never changed).

## Hardware test (Sprint 4, MF-57; Tester)

Pre-merge step done (`dev` file) on the dev Pi; product boot with no mode file; `dev` file → home
Wi-Fi; `product` / deleted file → hotspot; 3 s hold anywhere → "Join developer Wi-Fi?" → Join → home Wi-Fi →
reboot → hotspot; dev-temp with wrong home password → hotspot back + toast; simulated hotspot failure
(e.g. `nmcli connection modify MemoryFrame-hotspot 802-11-wireless.channel 200`) → error screen;
`memoryframe-reset` → default hotspot; `grep -ri password /boot/firmware/memoryframe-*` is empty.

## MF-18: changing the hotspot name/password from a phone

Phone: admin home → `Frame Wi-Fi`. The app never touches the network itself, and it has no sudo rules
(#62: its service keeps `NoNewPrivileges`). It starts root oneshot units (`memoryframe-wifi-apply`,
`-wifi-commit`, `-wifi-rollback`, `-devtemp`), and power-off goes through logind. The polkit rule
`/etc/polkit-1/rules.d/50-memoryframe.rules` allows only the app user to start exactly those units and
power off. The new password goes in the app's 0700 `/run/memoryframe-app/wifi-apply`, which the root
helper reads itself (regular file owned by the app user only, no symlinks or FIFOs) and deletes, into a
root-only keyfile `MemoryFrame-AP-pending`. It never appears in argv, logs or `/run/memoryframe/*.out`.

**Note:** the app user is also the desktop/kiosk user (install.sh runs as the desktop user). So any
process in the desktop session can start those four fixed units or power off the frame. That is
accepted because the commands are fixed; a dedicated system user would be a later improvement.
`install.sh` installs `polkitd` and stops with an error if polkit is still missing.

- Pending: old profile kept, frame shows the new Wi-Fi QR and a 5:00 countdown.
- Keep: a paired phone on the new hotspot (10.42.0.0/24) taps `Keep new Wi-Fi` → pending becomes
  `MemoryFrame-hotspot`, old profile deleted. The app stores the new name/password (for the QR)
  in its database (it does not read the profile back); `deploy/hotspot.conf` is not changed, so it
  no longer matches the live hotspot.
- Rollback: timeout, activation failure, app restart or reboot while pending → old hotspot back,
  pending profile deleted. Mode switches (`netmode.sh product|dev|dev-temp`) are refused while pending, and
  Wi-Fi changes are refused (409) in developer mode (incl. until-reboot) or during a switch.
- `memoryframe-reset` recreates the `MemoryFrame-hotspot` profile from `/etc/memoryframe/hotspot.conf` (the `install.sh` copy of `deploy/hotspot.conf`); the app then drops its stored
  name/password so the QR matches again.
- After updating the code, re-run `deploy/install.sh` so the root-owned helper copy is refreshed.
