"""#135: saved Wi-Fi profiles survive a reboot (deploy/netmode.sh against the file-backed fake nmcli).

NetworkManager keeps a keyfile's NAME when a profile is renamed, so after a promotion the live
MemoryFrame-home / MemoryFrame-hotspot can be backed by a *-pending* file. netmode.sh used to `rm -f`
that path, which left the profile in NM's memory until the next reboot and then it was gone: every boot
found "no matching NetworkManager Wi-Fi profile" and fell back to the hotspot.
"""
import json
import os
import shutil
import threading

import pytest

from test_netmode_script import (HOME, HOT, OTHER, PENDING, NEWPW, active, env, keyfiles, on_hotspot,  # noqa: F401
                                 profile, reboot, run, state)

HCON = "MemoryFrame-home"
SENT = "S3ntinel-Pass"


def dev_fallback_on_hotspot(env):
    """Persistent dev, no saved home profile left (the #135 Pi): boot falls back to the hotspot."""
    shutil.rmtree(env["d"] / "p" / HOME)
    shutil.rmtree(env["d"] / "p" / OTHER)
    nm = env["d"] / "nmcli"   # the home-wifi test reads an IPv4 address once the pending profile is up
    nm.write_text(nm.read_text().replace('[ "$1" = -e ] && shift 2\n', '[ "$1" = -e ] && shift 2\n'
                  '[ "$1 $2" = "-g IP4.ADDRESS" ] && { [ "$(cat "$D/active")" = MemoryFrame-home-pending ] && echo 192.168.1.9/24; exit 0; }\n', 1))
    (env["boot"] / "memoryframe-mode.txt").write_text("dev\n")
    (env["d"] / "active").write_text("\n")
    r = run(env, "boot")
    assert r.returncode == 0 and active(env) == HOT
    assert "no matching NetworkManager Wi-Fi profile" in r.stderr
    s = state(env)
    assert s["mode"] == "product" and s["requested"] == "dev" and s["dev_fallback"] is True   # phone menu shows
    assert s["wifi_profiles"] is False


def save_from_phone(env, ssid="Oversclan_5G"):
    rq = env["tmp"] / "app" / "home-wifi"
    rq.parent.mkdir(mode=0o700, exist_ok=True)
    rq.write_text(f"v1\n{ssid.encode().hex()}\n{SENT}\n")
    env["env"]["MEMORYFRAME_HOME_REQ"] = str(rq)
    env["env"]["MEMORYFRAME_HOME_WAIT"] = "1"
    r = run(env, "home-wifi", "--request")
    assert r.returncode == 0, r.stderr
    assert json.loads((env["state"] / "home-wifi.json").read_text())["state"] == "saved"
    return r


def fake_sync(env):
    f = env["d"] / "sync"
    f.write_text('#!/bin/bash\necho "sync $*" >> "$STUB_DIR/synclog"\n')
    f.chmod(0o755)
    return env["d"] / "synclog"


def backing(env, name):
    return [f for f in keyfiles(env) if f"id={name}\n" in f.read_text()]


def test_phone_saved_home_wifi_survives_reboot_and_boot_joins_it(env):
    dev_fallback_on_hotspot(env)
    log = fake_sync(env)
    save_from_phone(env)
    [kf] = backing(env, HCON)                                    # its own final file, nothing else
    assert kf.name == "MemoryFrame-home.nmconnection" and kf.stat().st_size > 0
    assert oct(kf.stat().st_mode & 0o777) == "0o600"
    assert keyfiles(env, "MemoryFrame-home-pending") == [] and list((env["tmp"] / "nm").glob(".*")) == []
    synced = log.read_text()
    assert f"sync -- {kf.parent}" in synced                     # temp file and directory fsync-ed
    uuid = next(l[5:] for l in kf.read_text().splitlines() if l.startswith("uuid="))
    assert (env["d"] / "p" / HCON / "uuid").read_text().strip() == uuid   # live profile == file on disk
    assert not [l for l in (env["d"] / "log").read_text().splitlines()
                if l.startswith(f"nmcli connection modify {HCON} ")]   # never modified after the save
    reboot(env)
    r = run(env, "boot")
    assert r.returncode == 0 and active(env) == HCON, r.stderr
    s = state(env)
    assert s["mode"] == "dev" and s["requested"] == "dev" and s["dev_fallback"] is False


def test_second_phone_save_replaces_the_first_and_survives_reboot(env):
    dev_fallback_on_hotspot(env)
    save_from_phone(env, "First")
    reboot(env)
    (env["d"] / "fail").write_text(HCON + "\n")                  # first network gone: fallback again
    run(env, "boot")
    assert state(env)["dev_fallback"] is True
    (env["d"] / "fail").unlink()
    save_from_phone(env, "Second")
    [kf] = backing(env, HCON)                                    # exactly one, the new one
    assert "ssid=83;101;99;111;110;100;" in kf.read_text()
    assert [n for n in os.listdir(env["d"] / "p") if n.startswith(HCON)] == [HCON]   # old one deleted by uuid
    assert keyfiles(env, "MemoryFrame-home-old") == [] and backing(env, "MemoryFrame-home-pending") == []
    reboot(env)
    assert run(env, "boot").returncode == 0 and active(env) == HCON


def test_boot_keeps_legacy_pending_named_file_that_backs_home(env):   # a Pi saved with the old code
    nm = env["tmp"] / "nm"
    nm.mkdir()
    f = nm / "MemoryFrame-home-pending.nmconnection"
    f.write_text("[connection]\nid=MemoryFrame-home\ntype=wifi\n\n[wifi]\nmode=infrastructure\n")
    profile(env["d"], HCON, ac="no")
    (env["d"] / "p" / HCON / "file").write_text(str(f) + "\n")
    (env["boot"] / "memoryframe-mode.txt").write_text("dev\n")
    (env["d"] / "active").write_text("\n")
    r = run(env, "boot")
    assert f.exists() and active(env) == HCON, r.stderr
    reboot(env)
    assert run(env, "boot").returncode == 0 and active(env) == HCON


@pytest.mark.parametrize("how", ["fails", "short"])
def test_failed_copy_never_replaces_the_saved_file(env, how):   # #135 review M1
    dev_fallback_on_hotspot(env)
    save_from_phone(env, "First")
    [kf] = backing(env, HCON)
    good = kf.read_bytes()
    real = shutil.which("sed")
    fake = env["d"] / "sed"      # only the copy into the .MemoryFrame-home.* temp file goes wrong
    fake.write_text(f"""#!/bin/bash
out="$(readlink /proc/$$/fd/1)"
if [[ "$out" == */.MemoryFrame-home.* ]]; then
  printf '[connection]\\nid=Memory'
  [ "{how}" = fails ] && exit 1 || exit 0
fi
exec {real} "$@"
""")
    fake.chmod(0o755)
    rq = env["tmp"] / "app" / "home-wifi"
    rq.write_text(f"v1\n{'Second'.encode().hex()}\n{SENT}\n")
    r = run(env, "home-wifi", "--request")
    assert r.returncode != 0 and "previous developer Wi-Fi kept" in r.stderr
    assert json.loads((env["state"] / "home-wifi.json").read_text())["state"] == "failed"
    assert kf.read_bytes() == good                                # byte-identical
    assert list((env["tmp"] / "nm").glob(".*")) == [] and backing(env, "MemoryFrame-home-pending") == []
    fake.unlink()
    reboot(env)
    assert run(env, "boot").returncode == 0 and active(env) == HCON


def test_load_failure_after_install_restores_previous_file(env):   # #135 QA N1
    dev_fallback_on_hotspot(env)
    save_from_phone(env, "First")
    [kf] = backing(env, HCON)
    good = kf.read_bytes()
    old_uuid = (env["d"] / "p" / HCON / "uuid").read_text().strip()
    (env["d"] / "loadfail").write_text("MemoryFrame-home.nmconnection 1\n")                 # NM refuses the new final file once
    rq = env["tmp"] / "app" / "home-wifi"
    rq.write_text(f"v1\n{'Second'.encode().hex()}\n{SENT}\n")
    r = run(env, "home-wifi", "--request")
    assert r.returncode != 0
    assert "did not load the new file; previous developer Wi-Fi restored" in r.stderr
    assert "test failed" not in r.stderr                         # truthful: the test itself passed
    assert kf.read_bytes() == good and (env["d"] / "p" / HCON / "uuid").read_text().strip() == old_uuid
    assert list((env["tmp"] / "nm").glob(".*")) == [] and backing(env, "MemoryFrame-home-pending") == []
    reboot(env)
    assert run(env, "boot").returncode == 0 and active(env) == HCON


def test_load_failure_with_no_previous_file_is_logged_truthfully(env):
    dev_fallback_on_hotspot(env)
    (env["d"] / "loadfail").write_text("MemoryFrame-home.nmconnection 1\n")
    rq = env["tmp"] / "app" / "home-wifi"
    rq.parent.mkdir(mode=0o700, exist_ok=True)
    rq.write_text(f"v1\n{'First'.encode().hex()}\n{SENT}\n")
    env["env"]["MEMORYFRAME_HOME_REQ"] = str(rq)
    env["env"]["MEMORYFRAME_HOME_WAIT"] = "1"
    r = run(env, "home-wifi", "--request")
    assert r.returncode != 0 and "kept" not in r.stderr and "next boot" not in r.stderr   # #137 N3
    assert "did not load the new file; nothing new saved" in r.stderr
    assert list((env["tmp"] / "nm").glob(".*")) == [] and backing(env, HCON) == []
    assert not (env["tmp"] / "nm" / "MemoryFrame-home.nmconnection").exists()
    reboot(env)
    r = run(env, "boot")                                          # consistent with the phone's "failed"
    assert active(env) == HOT and state(env)["dev_fallback"] is True


BAK_BODY = "[connection]\nid=MemoryFrame-home\nuuid=u-bak-1\ntype=wifi\n\n[wifi]\nmode=infrastructure\n"


def power_cut_mid_replace(env):
    """#137 R1: final file left empty, backup complete; NM (started before netmode) skipped the empty file."""
    shutil.rmtree(env["d"] / "p" / HOME)
    shutil.rmtree(env["d"] / "p" / OTHER)
    nm = env["tmp"] / "nm"
    nm.mkdir()
    (nm / "MemoryFrame-home.nmconnection").write_text("")
    (nm / ".MemoryFrame-home.bak").write_text(BAK_BODY)
    (env["boot"] / "memoryframe-mode.txt").write_text("dev\n")
    (env["d"] / "active").write_text("\n")
    return nm


def test_boot_restore_after_power_cut_is_loaded_and_used_this_boot(env):   # #137 R1
    nm = power_cut_mid_replace(env)
    r = run(env, "boot")
    assert r.returncode == 0 and active(env) == HCON, r.stderr
    assert "previous developer Wi-Fi file restored and loaded" in r.stderr
    assert (env["d"] / "p" / HCON / "uuid").read_text().strip() == "u-bak-1"
    s = state(env)
    assert s["mode"] == "dev" and s["dev_fallback"] is False
    assert (nm / "MemoryFrame-home.nmconnection").read_text() == BAK_BODY and list(nm.glob(".*")) == []


def test_boot_restore_load_failure_is_logged_truthfully(env):   # #137 R1
    nm = power_cut_mid_replace(env)
    (env["d"] / "loadfail").write_text("MemoryFrame-home.nmconnection 1\n")
    r = run(env, "boot")
    assert "restored, but NetworkManager did not load it" in r.stderr and "and loaded" not in r.stderr
    assert (nm / "MemoryFrame-home.nmconnection").read_text() == BAK_BODY   # still on disk for the next boot
    assert active(env) == HOT and state(env)["dev_fallback"] is True


def test_backup_is_created_0600_even_if_final_was_looser(env):   # #137 R2
    dev_fallback_on_hotspot(env)
    save_from_phone(env, "First")
    [kf] = backing(env, HCON)
    kf.chmod(0o644)
    f = env["d"] / "sync"      # record the backup's mode when it is fsync-ed
    f.write_text('#!/bin/bash\nfor a; do case "$a" in *.bak) stat -c %a "$a" >> "$STUB_DIR/bakmodes";; esac; done\n')
    f.chmod(0o755)
    (env["d"] / "loadfail").write_text("MemoryFrame-home.nmconnection 1\n")   # keep the backup in play
    rq = env["tmp"] / "app" / "home-wifi"
    rq.write_text(f"v1\n{'Second'.encode().hex()}\n{SENT}\n")
    run(env, "home-wifi", "--request")
    modes = (env["d"] / "bakmodes").read_text().split()
    assert modes and set(modes) == {"600"}
    assert oct(kf.stat().st_mode & 0o777) == "0o600"              # restored file is 0600 too


def test_uuid_mismatch_restore_drops_the_bad_profile_and_reloads(env):   # #137 N4
    dev_fallback_on_hotspot(env)
    save_from_phone(env, "First")
    [kf] = backing(env, HCON)
    good = kf.read_bytes()
    old_uuid = (env["d"] / "p" / HCON / "uuid").read_text().strip()
    (env["d"] / "loadbadid").write_text("MemoryFrame-home.nmconnection")      # NM loads it, wrong id
    rq = env["tmp"] / "app" / "home-wifi"
    rq.write_text(f"v1\n{'Second'.encode().hex()}\n{SENT}\n")
    r = run(env, "home-wifi", "--request")
    assert r.returncode != 0 and "previous developer Wi-Fi restored" in r.stderr
    assert not (env["d"] / "p" / f"{HCON}~bad").exists()          # the bad in-memory profile is gone
    assert kf.read_bytes() == good
    assert (env["d"] / "p" / HCON / "uuid").read_text().strip() == old_uuid  # in memory == disk
    reboot(env)
    assert run(env, "boot").returncode == 0 and active(env) == HCON


def test_boot_removes_stray_hidden_temp_and_restores_interrupted_backup(env):   # #135 QA: PSK never left
    nm = env["tmp"] / "nm"
    nm.mkdir()
    stray = nm / ".MemoryFrame-home.Ab12Cd"
    stray.write_text("[connection]\nid=MemoryFrame-home\n[wifi-security]\npsk=secret\n")
    (env["boot"] / "memoryframe-mode.txt").write_text("dev\n")
    assert run(env, "boot").returncode == 0
    assert list(nm.glob(".*")) == []
    # power cut after the backup, with the final file gone/empty: the backup is put back at boot
    final = nm / "MemoryFrame-home.nmconnection"
    final.write_text("")
    (nm / ".MemoryFrame-home.bak").write_text("[connection]\nid=MemoryFrame-home\nuuid=u-1\n")
    assert run(env, "boot").returncode == 0
    assert final.read_text().startswith("[connection]\nid=MemoryFrame-home") and list(nm.glob(".*")) == []


def test_pending_keyfile_is_fsynced_before_rename(env):   # #135 QA (g)
    dev_fallback_on_hotspot(env)
    log = fake_sync(env)
    save_from_phone(env)
    assert any(l.startswith("sync -- ") and "MemoryFrame-home-pending-" in l and l.endswith(".tmp")
               for l in log.read_text().splitlines())


def test_phone_save_replaces_a_legacy_in_memory_home_profile(env):   # the Pi today: file already lost
    dev_fallback_on_hotspot(env)
    profile(env["d"], HCON, ssid="Oversclan_5G", ac="no")       # in NM's memory only, no file
    (env["d"] / "p" / HCON / "uuid").write_text("uuid-legacy\n")
    save_from_phone(env)
    assert [n for n in os.listdir(env["d"] / "p") if n.startswith(HCON)] == [HCON]
    assert (env["d"] / "p" / HCON / "uuid").read_text().strip() != "uuid-legacy"
    reboot(env)
    assert run(env, "boot").returncode == 0 and active(env) == HCON


def test_boot_warns_about_empty_netplan_files_and_never_touches_them(env):   # #135 cause, read-only
    npd = env["tmp"] / "netplan"
    npd.mkdir()
    empty, full = npd / "90-NM-aae1e05a.yaml", npd / "90-NM-75a1216a.yaml"
    empty.write_text("")
    full.write_text("network: {}\n")
    before = {f: (f.stat().st_size, f.stat().st_mtime_ns) for f in (empty, full)}
    env["env"]["MEMORYFRAME_NETPLAN_DIR"] = str(npd)
    r = run(env, "boot")
    assert r.returncode == 0
    assert "WARNING: 90-NM-aae1e05a.yaml is empty" in r.stderr and "75a1216a" not in r.stderr
    assert {f: (f.stat().st_size, f.stat().st_mtime_ns) for f in (empty, full)} == before
    assert sorted(os.listdir(npd)) == sorted([empty.name, full.name])


def test_boot_removes_a_stale_unloaded_pending_file(env):   # power cut before `connection load`
    nm = env["tmp"] / "nm"
    nm.mkdir()
    stale = nm / "MemoryFrame-home-pending-AbC123.nmconnection"
    stale.write_text("[connection]\nid=MemoryFrame-home-pending\n")
    empty = nm / "MemoryFrame-home-pending-XyZ789.nmconnection"
    empty.write_text("")
    (env["boot"] / "memoryframe-mode.txt").write_text("dev\n")
    assert run(env, "boot").returncode == 0
    assert not stale.exists() and not empty.exists()


def test_two_hotspot_changes_then_reboot_hotspot_still_exists(on_hotspot):   # MF-18, same bug class
    env = on_hotspot
    log = fake_sync(env)
    for name in ("First Frame", "Second Frame"):
        assert run(env, "wifi-apply", input=f"{name}\n{NEWPW}\n").returncode == 0
        assert run(env, "wifi-commit").returncode == 0
    [kf] = backing(env, HOT)
    assert "ssid=83;101;99;111;110;100;32;" in kf.read_text()     # "Second ..."
    assert backing(env, PENDING) == [] and kf.exists()
    assert log.read_text().count("sync -f ") >= 2                # after each commit
    reboot(env)
    assert (env["d"] / "p" / HOT).exists()
    assert run(env, "boot").returncode == 0 and active(env) == HOT


def test_no_profile_yet_at_boot_rechecks_within_deadline(env):   # #135: profiles still loading
    shutil.rmtree(env["d"] / "p" / HOME)
    shutil.rmtree(env["d"] / "p" / OTHER)
    (env["boot"] / "memoryframe-mode.txt").write_text("dev\n")
    (env["d"] / "active").write_text("\n")
    env["env"]["MEMORYFRAME_DEV_WAIT"] = "15"
    t = threading.Timer(1.0, profile, args=(env["d"], HOME), kwargs={"ssid": "Oversclan_5G"})
    t.start()
    try:
        r = run(env, "boot")
    finally:
        t.join()
    assert r.returncode == 0 and active(env) == HOME, r.stderr
    assert state(env)["dev_fallback"] is False and "no matching" not in r.stderr


def test_dev_temp_no_profile_falls_back_at_once(env):   # dev-temp keeps its old, quick behaviour
    shutil.rmtree(env["d"] / "p" / HOME)
    shutil.rmtree(env["d"] / "p" / OTHER)
    (env["d"] / "active").write_text(HOT + "\n")
    env["env"]["MEMORYFRAME_DEV_WAIT"] = "30"
    r = run(env, "dev-temp")
    assert r.returncode == 1 and active(env) == HOT
    s = state(env)
    assert s["requested"] == "dev-temp" and s["dev_fallback"] is True


@pytest.mark.parametrize("nm_fault", ["loadfail", "loadbadid"])
def test_failed_backup_restore_keeps_the_previous_file_and_logs_it(env, nm_fault):
    """#138 M1: the .bak cannot be put back (fsync EIO): it stays on disk and a home keyfile is never lost."""
    dev_fallback_on_hotspot(env)
    save_from_phone(env, "First")
    [kf] = backing(env, HCON)
    good = kf.read_bytes()
    sync = env["d"] / "sync"   # 1st fsync of the .bak (the backup) works, every later one (restore) fails
    sync.write_text('#!/bin/bash\nfor a; do case "$a" in *.bak) n=$(($(cat "$STUB_DIR/bakn" 2>/dev/null || echo 0) + 1))\n'
                    '  echo $n > "$STUB_DIR/bakn"; [ $n -ge 2 ] && exit 1;; esac; done\nexit 0\n')
    sync.chmod(0o755)
    (env["d"] / nm_fault).write_text("MemoryFrame-home.nmconnection" + (" 1\n" if nm_fault == "loadfail" else ""))
    rq = env["tmp"] / "app" / "home-wifi"
    rq.write_text(f"v1\n{'Second'.encode().hex()}\n{SENT}\n")
    r = run(env, "home-wifi", "--request")
    assert r.returncode != 0
    assert "could not put the previous developer Wi-Fi file back; new file removed, backup kept" in r.stderr
    assert "nothing new saved" not in r.stderr and "previous developer Wi-Fi restored" not in r.stderr
    bak = env["tmp"] / "nm" / ".MemoryFrame-home.bak"
    assert bak.read_bytes() == good and oct(bak.stat().st_mode & 0o777) == "0o600"   # the last copy is kept
    # #137 double fault: the untested new file is never left as the final file (loadfail: removed by us;
    # loadbadid: NM deleted it by UUID); only the .bak is on disk, so the next boot restores the previous Wi-Fi
    assert not kf.exists()
    assert not [f for f in keyfiles(env) if "ssid=83;101;99;111;110;100;" in f.read_text()]   # no "Second" anywhere
    sync.write_text("#!/bin/bash\nexit 0\n")   # the SD card works again
    reboot(env)
    r = run(env, "boot")
    assert r.returncode == 0 and active(env) == HCON and kf.stat().st_size > 0, r.stderr
    assert not bak.exists()
    assert "previous developer Wi-Fi file restored and loaded" in r.stderr and kf.read_bytes() == good   # both faults


def test_no_valid_new_uuid_changes_nothing_and_deletes_nothing(env):
    """#138 nit: an empty/invalid new UUID never reaches `nmcli connection delete uuid`."""
    dev_fallback_on_hotspot(env)
    save_from_phone(env, "First")
    [kf] = backing(env, HCON)
    good = kf.read_bytes()
    cat = env["d"] / "cat"   # reading /proc/sys/kernel/random/uuid gives nothing
    cat.write_text(f'#!/bin/bash\n[ "$1" = /proc/sys/kernel/random/uuid ] && exit 0\nexec {shutil.which("cat")} "$@"\n')
    cat.chmod(0o755)
    before = len((env["d"] / "log").read_text().splitlines())
    rq = env["tmp"] / "app" / "home-wifi"
    rq.write_text(f"v1\n{'Second'.encode().hex()}\n{SENT}\n")
    r = run(env, "home-wifi", "--request")
    assert r.returncode != 0 and "no valid new UUID; nothing changed" in r.stderr
    assert not [l for l in (env["d"] / "log").read_text().splitlines()[before:] if " delete uuid" in l]
    assert kf.read_bytes() == good and list((env["tmp"] / "nm").glob(".*")) == []
