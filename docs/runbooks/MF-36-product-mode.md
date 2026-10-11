# MF-36 runbook: product mode, developer mode, recovery

> **Safety rule.** Product mode is armed ONLY by an explicit `sudo deploy/netmode.sh product` (or the
> `memoryframe-reset` recovery file). `install.sh`, upgrades, `git pull` + re-install and reboots never
> write the mode file and never switch to the hotspot. With no mode file the frame stays on home
> Wi-Fi. Switching to product cuts off Pi Connect: only with the user present and the way back tested.
> `deploy/netmode.sh status` shows `armed for next boot: …` and `current mode: …`.

Once armed (`sudo deploy/netmode.sh product`), `memoryframe-netmode.service` runs
`deploy/netmode.sh boot` at every boot: the hotspot (`MemoryFrame-hotspot`, SSID `MemoryFrame-AP`) is
brought up and verified, and only then made the preferred boot connection (its own
`autoconnect=yes`, `autoconnect-priority=100`). **Home Wi-Fi profiles are never modified**
(netplan-generated profiles would be rewritten in /etc/netplan). If the hotspot fails, the previous
connection is brought back. Phones reach `http://10.42.0.1/`.

**First product boot safety net:** after arming, if no phone joins the hotspot within
`MEMORYFRAME_PRODUCT_CONFIRM_MIN` minutes (default 10, `0` = off; set in the service's
environment), the frame reverts to developer mode and home Wi-Fi by itself and logs
`no phone joined the hotspot within … min` (`journalctl -t memoryframe-netmode`). Once a phone has
joined, later boots are not watched until product is armed again.

There is **no** network-mode control in the frame menu or on the phone.

## Files on the boot partition (`/boot/firmware`, the "bootfs" drive on a PC)

| File | Content | Effect at next boot |
|---|---|---|
| `memoryframe-mode.txt` | `product` or `dev` (one word) | missing / empty / anything else = developer default: the boot service changes nothing (home Wi-Fi stays) |
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

From any PC, without network: open "bootfs", write `product` in `memoryframe-mode.txt` (deleting the file means developer default),
optionally create `memoryframe-reset`, boot. The hotspot is back after one boot.
To remove MF-36 completely: `sudo systemctl disable memoryframe-netmode.service` and
`sudo deploy/netmode.sh dev` (the hotspot loses its autoconnect; client profiles were never changed).

## Hardware test (blocked until U6; Tester)

Product boot; boot file → dev; dev → product via boot file and via shell; wrong dev password →
hotspot fallback + toast; simulated hotspot failure (e.g. `nmcli connection modify MemoryFrame-hotspot
802-11-wireless.channel 200`) → error screen; `grep -ri password /boot/firmware/memoryframe-*` is empty.
