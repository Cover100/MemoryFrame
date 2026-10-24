# Reset the frame (#99)

**On the frame:** hold a finger still anywhere for 3 s, then tap **Frame tools**, then **Reset the frame…**.

- **Keep my photos:** all phones sign out, and settings and the frame message reset. Photos stay. This is one database transaction, done by the app.
- **Erase everything** (decision A1): you must hold Erase for 2 s. It deletes:
  - the photos (`originals display thumbs tmp backups`);
  - the database (media, pairings, sessions, settings, a pending Wi-Fi change);
  - `frame_message.txt`.

  The hotspot name and password stay the same, so the printed card stays valid. The frame then shows the welcome screen (#98).
- Both are refused while Shut down or a Wi-Fi change is in progress.
- A reset that changes the hotspot name or password is a PC script only. It is never on the touch menu.

## How Erase works

The root helper is `deploy/factory-reset.sh`. It is installed as `/usr/local/sbin/memoryframe-factory-reset` and run by `memoryframe-factory-reset.service`.

1. The app writes `/run/memoryframe-app/factory-reset` (`erase`) and starts the unit with `--no-block`. The helper rejects symlinks, non-regular files, files with the wrong owner, and any other contents.
2. The helper writes the marker `/var/lib/memoryframe/.factory-reset`, then stops the app.
3. It moves the targets into `.erasing-<id>` (atomic renames) and syncs.
4. It deletes `.erasing-*`, runs `fstrim` where supported, and recreates the empty folders (data dir owner, 0750).
5. It removes the marker **last**, syncs, and starts the app.

The unit also runs at every boot, before the app. If the marker is there, it finishes the reset (roll forward, never back). With no marker, it does nothing.

The helper never touches `/opt`, the app user, units, polkit, `/etc/memoryframe`, `/boot/firmware`, NetworkManager, ssh or the admin account.

**Not a secure wipe.** Files are unlinked and trimmed, not overwritten. Wear levelling on SD cards makes overwriting pointless. To hand a frame to someone else, reflash the card.

## Check on the Pi (Hale)

1. Back up first: `sudo cp -a /var/lib/memoryframe ~/backup`.
2. Run each option, then check `sudo find /var/lib/memoryframe -mindepth 1 -maxdepth 1`. Check that `nmcli -g 802-11-wireless.ssid connection show MemoryFrame-hotspot` is unchanged.
3. Pull the power about 1 s and 5 s after Erase starts. After the next boot, the marker should be gone, the folders empty, the welcome screen showing, and ssh and admin still working.
4. Check whether `sudo fstrim -v /var/lib/memoryframe` works on the card. The helper only logs it if not.
5. Test the hold menu offline (hotspot only, no internet).
