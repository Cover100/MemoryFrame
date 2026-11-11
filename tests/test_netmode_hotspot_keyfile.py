"""#137: the MemoryFrame-hotspot profile gets its own durable final keyfile (deploy/netmode.sh, fake nmcli).

After a hotspot name/password change (MF-18) the live hotspot used to be backed by a file with a *-pending-*
name (NetworkManager keeps a keyfile's name when a profile is renamed); before #135 that file was even removed,
leaving the hotspot in memory only, so it was gone after a reboot. Now wifi-commit (and boot, for a frame already
in that state) moves it to MemoryFrame-hotspot.nmconnection: temp in the same dir, fsync, rename, fsync dir,
`connection load`, verified on disk and live. The UUID is kept, because the app's reconcile (wifi.py) matches
the UUID wifi-apply printed against netmode.json's hotspot_uuid.
"""
import os
import subprocess
import re

import pytest

from test_netmode_keyfiles import backing, fake_sync
from test_netmode_script import HOT, NEWPW, PENDING, SCRIPT, active, keyfiles, on_hotspot, env, reboot, run, state  # noqa: F401

FINAL = "MemoryFrame-hotspot.nmconnection"


def p(env, name):
    return env["d"] / "p" / name


def change(env, ssid="Frame One"):
    r = run(env, "wifi-apply", input=f"{ssid}\n{NEWPW}\n")
    assert r.returncode == 0, r.stderr
    uuid = re.search(r"^uuid=(.+)$", r.stdout, re.M).group(1)    # what the app stores as new_uuid
    before = len((env["d"] / "log").read_text().splitlines())
    c = run(env, "wifi-commit")
    assert c.returncode == 0, c.stderr
    return uuid, c, (env["d"] / "log").read_text().splitlines()[before:]


def test_commit_saves_the_hotspot_in_its_own_durable_final_file(on_hotspot):
    env = on_hotspot
    log = fake_sync(env)
    uuid, c, calls = change(env)
    [kf] = backing(env, HOT)
    assert kf.name == FINAL and oct(kf.stat().st_mode & 0o777) == "0o600"
    body = kf.read_text()
    assert f"uuid={uuid}\n" in body and f"id={HOT}\n" in body and "psk=n3w pass\\\\word!" in body
    assert keyfiles(env, PENDING) == [] and list((env["tmp"] / "nm").glob(".*")) == []   # no old file, no temps
    assert (p(env, HOT) / "file").read_text().strip() == str(kf)          # live profile backed by the final file
    synced = log.read_text()
    assert re.search(r"sync -- .*/\.MemoryFrame-hotspot\.\w+$", synced, re.M)   # temp fsync-ed before the rename
    assert f"sync -- {kf.parent}" in synced                              # then the directory
    assert "hotspot: saved in MemoryFrame-hotspot.nmconnection" in c.stderr
    # no restart and no new waits: the phone stays connected through "Keep"
    assert active(env) == HOT
    assert not [l for l in calls if re.search(r" connection (up|down|delete uuid) ", l) or "--wait" in l]
    assert "n3w pass" not in c.stderr + (env["d"] / "log").read_text()
    reboot(env)
    assert run(env, "boot").returncode == 0 and active(env) == HOT
    assert (p(env, HOT) / "uuid").read_text().strip() == uuid


def test_uuid_stays_what_the_app_recorded(on_hotspot):   # wifi.py reconcile: hotspot_uuid == new_uuid
    env = on_hotspot
    from memoryframe.wifi import reconcile
    uuid, _, _ = change(env)
    assert state(env)["hotspot_uuid"] == uuid
    reboot(env)
    run(env, "boot")
    nm = state(env)
    assert nm["hotspot_uuid"] == uuid and nm["wifi_profiles"] is False
    from memoryframe import db as mfdb   # the app crashed before recording "kept": start-up finds it committed
    mfdb.init_db(str(env["tmp"] / "mf.db"))
    db = mfdb.connect(str(env["tmp"] / "mf.db"))
    db.execute("INSERT INTO wifi_change (id, state, old_ssid, new_ssid, new_password, old_profile, new_profile, deadline,"
               " updated, new_uuid) VALUES (1, 'pending', 'Old', 'Frame One', ?, ?, ?, 0, 0, ?)", (NEWPW, HOT, PENDING, uuid))
    db.commit()
    assert reconcile(db, nm) == "committed"


def test_two_changes_each_end_in_the_final_file_and_the_old_one_is_gone(on_hotspot):
    env = on_hotspot
    u1, _, _ = change(env, "First Frame")
    u2, c, _ = change(env, "Second Frame")
    assert u1 != u2
    [kf] = backing(env, HOT)
    assert kf.name == FINAL and f"uuid={u2}\n" in kf.read_text() and "ssid=83;101;99;111;110;100;32;" in kf.read_text()
    assert sorted(n for n in os.listdir(env["d"] / "p") if n.startswith("MemoryFrame")) == [HOT]
    assert keyfiles(env, PENDING) == [] and len(keyfiles(env)) == 1
    reboot(env)
    assert run(env, "boot").returncode == 0 and active(env) == HOT and state(env)["hotspot_uuid"] == u2


def legacy_pending_backed(env, with_file=True, uuid="2b7c1e1a-0000-4000-8000-00000000c137"):
    """A frame changed with the current main: the live hotspot sits in a *-pending-* file (or, before #135, in
    memory only: its file was removed)."""
    nm = env["tmp"] / "nm"
    nm.mkdir(exist_ok=True)
    f = nm / f"{PENDING}-Ab12Cd.nmconnection"
    body = (f"[connection]\nid={HOT}\nuuid={uuid}\ntype=wifi\ninterface-name=wlan0\nautoconnect-priority=100\n\n"
            "[wifi]\nmode=ap\nssid=79;108;100;\n\n[wifi-security]\nkey-mgmt=wpa-psk\npsk=0ld-secret-pw\n")
    (p(env, HOT) / "file").write_text(f"{f}\n")
    (p(env, HOT) / "uuid").write_text(uuid + "\n")
    (p(env, HOT) / "keyfile").write_text(body)
    if with_file:
        f.write_text(body)
    return f, uuid


@pytest.mark.parametrize("with_file", [True, False], ids=["pending-named-file", "memory-only"])
def test_boot_migrates_an_existing_hotspot_profile(on_hotspot, with_file):
    env = on_hotspot
    old, uuid = legacy_pending_backed(env, with_file)
    r = run(env, "boot")
    assert r.returncode == 0 and active(env) == HOT, r.stderr
    [kf] = backing(env, HOT)
    assert kf.name == FINAL and f"uuid={uuid}\n" in kf.read_text() and "psk=0ld-secret-pw" in kf.read_text()
    assert not old.exists() and list((env["tmp"] / "nm").glob(".*")) == []
    assert ("profile was in memory only; written back" in r.stderr) == (not with_file)
    assert "hotspot: saved in MemoryFrame-hotspot.nmconnection" in r.stderr and state(env)["hotspot_uuid"] == uuid
    assert "0ld-secret-pw" not in r.stderr + (env["d"] / "log").read_text()
    reboot(env)
    assert run(env, "boot").returncode == 0 and active(env) == HOT
    assert (p(env, HOT) / "uuid").read_text().strip() == uuid


def test_boot_after_a_power_cut_mid_move_removes_the_shadowed_old_copy(on_hotspot):
    env = on_hotspot
    old, uuid = legacy_pending_backed(env)
    final = env["tmp"] / "nm" / FINAL
    final.write_text(old.read_text())                       # final written, old one not removed yet
    (p(env, HOT) / "file").write_text(f"{final}\n")         # NM prefers the newer file
    r = run(env, "boot")
    assert r.returncode == 0 and active(env) == HOT, r.stderr
    assert not old.exists() and final.exists() and backing(env, HOT) == [final]
    assert p(env, HOT).exists() and (p(env, HOT) / "uuid").read_text().strip() == uuid


def test_netplan_or_os_hotspot_profile_is_left_alone(on_hotspot):   # e.g. made by hotspot.sh via nmcli add
    env = on_hotspot
    (p(env, HOT) / "file").write_text("/run/NetworkManager/system-connections/netplan-NM-x.nmconnection\n")
    before = len((env["d"] / "log").read_text().splitlines())
    r = run(env, "boot")
    assert r.returncode == 0 and active(env) == HOT
    assert "(netplan/OS profile); left as it is" in r.stderr
    new = (env["d"] / "log").read_text().splitlines()[before:]
    assert not [l for l in new if " load " in l or f"modify {HOT} connection.autoconnect-priority" in l]
    assert keyfiles(env) == []


def test_failed_write_keeps_the_hotspot_where_it_was_and_logs_it(on_hotspot):
    env = on_hotspot
    run(env, "wifi-apply", input=f"Frame One\n{NEWPW}\n")
    sync = env["d"] / "sync"   # fsync of the hotspot temp file fails (SD write error)
    sync.write_text('#!/bin/bash\nfor a; do case "$a" in */.MemoryFrame-hotspot.*) exit 1;; esac; done\nexit 0\n')
    sync.chmod(0o755)
    c = run(env, "wifi-commit")
    assert c.returncode == 0 and active(env) == HOT, c.stderr   # the change itself is kept
    assert "hotspot: could not write MemoryFrame-hotspot.nmconnection; kept in MemoryFrame-AP-pending-" in c.stderr
    assert "saved in" not in c.stderr
    [kf] = backing(env, HOT)
    assert kf.name.startswith(PENDING + "-") and kf.stat().st_size > 0     # still on disk, under the old name
    assert not (env["tmp"] / "nm" / FINAL).exists() and list((env["tmp"] / "nm").glob(".*")) == []
    sync.unlink()
    reboot(env)
    r = run(env, "boot")                                   # boot finishes the move
    assert r.returncode == 0 and active(env) == HOT and backing(env, HOT)[0].name == FINAL, r.stderr


def test_nm_not_switching_is_logged_truthfully_and_the_file_is_used_after_reboot(on_hotspot):
    env = on_hotspot
    run(env, "wifi-apply", input=f"Frame One\n{NEWPW}\n")
    (env["d"] / "norebind").write_text("")   # an NM that keeps the profile on the old (removed) file
    c = run(env, "wifi-commit")
    assert c.returncode == 0
    assert "NetworkManager has not switched to it; it is used from the next boot" in c.stderr
    assert "hotspot: saved in MemoryFrame-hotspot.nmconnection\n" not in c.stderr + "\n"
    final = env["tmp"] / "nm" / FINAL
    assert final.stat().st_size > 0 and keyfiles(env, PENDING) == []      # on disk once, under the final name
    assert active(env) == HOT                                              # live profile untouched
    (env["d"] / "norebind").unlink()
    reboot(env)                                    # the profile pointed at the removed file: gone from memory
    assert not p(env, HOT).exists()
    subprocess.run([env["env"]["NMCLI"], "connection", "load", str(final)], env=env["env"], check=True)   # NM start-up scan
    assert run(env, "boot").returncode == 0 and active(env) == HOT and backing(env, HOT) == [final]


def test_final_file_of_another_profile_is_never_overwritten(on_hotspot):
    env = on_hotspot
    old, uuid = legacy_pending_backed(env)
    final = env["tmp"] / "nm" / FINAL
    final.write_text(f"[connection]\nid=MemoryFrame-hotspot-old\nuuid=someone-else\n")
    r = run(env, "boot")
    assert "still backs another profile; kept in" in r.stderr
    assert final.read_text().endswith("uuid=someone-else\n") and old.exists()


def test_save_hotspot_never_restarts_waits_or_deletes():   # boot budget and the "Keep" request stay unchanged
    src = SCRIPT.read_text()
    body = src[src.index("hotspot_file() {"):src.index("# ---------- #68:")]
    for bad in ("--wait", "sleep", "connection up", "connection down", "connection delete", "/etc/netplan",
                "memoryframe-mode", "MODE_FILE", "cmdline"):
        assert bad not in body, bad
    assert body.count("connection load") == 2
