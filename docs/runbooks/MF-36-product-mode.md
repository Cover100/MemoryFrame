# MF-36 runbook: product mode, developer mode, recovery

Product mode is the default. At boot `memoryframe-netmode.service` runs `deploy/netmode.sh boot`:
the hotspot (`MemoryFrame-hotspot`, SSID `MemoryFrame-AP`) comes up and every Wi-Fi client profile
gets `autoconnect=no`. Phones reach `http://10.42.0.1/`.

There is **no** network-mode control in the frame menu or on the phone.

## Files on the boot partition (`/boot/firmware`, the "bootfs" drive on a PC)

| File | Content | Effect at next boot |
|---|---|---|
| `memoryframe-mode.txt` | `product` or `dev` (one word) | missing / anything else = product |
| `memoryframe-dev-wifi.txt` | optional, only `ssid=<name>` | which **existing** NetworkManager profile dev mode uses (default: the most recently used client profile) |
| `memoryframe-reset` | empty | recreate the default hotspot profile (`deploy/hotspot.sh`), force product; the file is deleted |

**Never put a password in any of these files.** Dev Wi-Fi credentials live only in the root-only
NetworkManager profile (`/etc/NetworkManager/system-connections/`, 0600), set up over SSH or with a
keyboard, e.g. `sudo nmcli connection add type wifi ifname wlan0 con-name Home ssid Home wifi-sec.key-mgmt wpa-psk wifi-sec.psk '…' connection.autoconnect no`.
A `password=` (or `psk=`, `key=`…) line in `memoryframe-dev-wifi.txt` is ignored and a warning is
logged without its value.

## Shell (SSH, keyboard, or Ethernet)

    sudo deploy/netmode.sh status     # requested mode, applied mode, active connection, notes
    sudo deploy/netmode.sh dev        # switch now AND write memoryframe-mode.txt = dev
    sudo deploy/netmode.sh product    # switch now AND write memoryframe-mode.txt = product
    journalctl -t memoryframe-netmode -b

`dev` over SSH from the home Wi-Fi is fine; `product` over SSH from the home Wi-Fi drops the session
(the Pi 4 radio can't be client and hotspot at once): reconnect via the hotspot.
Switches are refused (exit 3) while another switch runs or an MF-18 Wi-Fi change is pending.

## What the frame shows

- Dev mode: bottom-left `Developer mode · <LAN IP>` (the only place a LAN IP appears).
- Dev Wi-Fi missing / wrong password: within ~60 s (45 s dev timeout + hotspot start) the hotspot
  comes up and the toast `Developer Wi-Fi not found, hotspot started` shows for 30 s, once per boot.
- Hotspot fails to start: full-screen error with the PC recovery steps; the slideshow keeps
  running; Close hides it for 10 minutes. The service never fails the boot and never restarts,
  so there is no reboot loop.

## Rollback / no lock-out

From any PC, without network: open "bootfs", delete `memoryframe-mode.txt` (or write `product`),
optionally create `memoryframe-reset`, boot. The hotspot is back after one boot.
To remove MF-36 completely: `sudo systemctl disable memoryframe-netmode.service` (the hotspot then
no longer starts at boot; NetworkManager behaves as before MF-36, but client profiles keep
`autoconnect=no` until changed with `nmcli connection modify <name> connection.autoconnect yes`).

## Hardware test (blocked until U6; Tester)

Product boot; boot file → dev; dev → product via boot file and via shell; wrong dev password →
hotspot fallback + toast; simulated hotspot failure (e.g. `nmcli connection modify MemoryFrame-hotspot
802-11-wireless.channel 200`) → error screen; `grep -ri password /boot/firmware/memoryframe-*` is empty.

## MF-18: changing the hotspot name/password from a phone

Phone: admin home → `Frame Wi-Fi`. The app never touches the network itself; it calls the
root-owned helper through one sudoers rule (`/etc/sudoers.d/memoryframe`, from
`deploy/memoryframe-sudoers`): `sudo -l -U <app user>` must list only
`/usr/local/sbin/memoryframe-netmode wifi-apply|wifi-commit|wifi-rollback`. The new password goes
to the helper on stdin (never argv/logs), into a root-only keyfile `MemoryFrame-AP-pending`.

- Pending: old profile kept, frame shows the new Wi-Fi QR and a 5:00 countdown.
- Keep: a paired phone on the new hotspot (10.42.0.0/24) taps `Keep new Wi-Fi` → pending becomes
  `MemoryFrame-hotspot`, old profile deleted. The app stores the new name/password (for the QR)
  in its database; `deploy/hotspot.conf` is not changed.
- Rollback: timeout, activation failure, app restart or reboot while pending → old hotspot back,
  pending profile deleted. Mode switches (`netmode.sh product|dev`) are refused while pending, and
  Wi-Fi changes are refused (409) in dev mode or during a switch.
- `memoryframe-reset` recreates the hotspot from `deploy/hotspot.conf`; the app then drops its stored
  name/password so the QR matches again.
- After updating the code, re-run `deploy/install.sh` so the root-owned helper copy is refreshed.
