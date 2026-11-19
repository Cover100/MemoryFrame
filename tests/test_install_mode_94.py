"""#94: the frame starts in product mode (hotspot) by default (Sage's product-default-scope.md).

install.sh decides the boot mode file (memoryframe-mode.txt on bootfs), read by netmode.sh at the NEXT boot:
  * an existing file is NEVER changed (with or without --mode);
  * no file + --mode dev|product: that value (an invalid value exits 2 before anything is changed);
  * no file, fresh install: "product";
  * no file, upgrade (an installed memoryframe.service, save-pack's signal): the LIVE mode is pinned, "dev" if
    wlan0 is on a home (non-hotspot) connection, else "product", so a deploy never changes mode.
install never runs netmode or switches the network (#40). Hermetic: install.sh with stubbed sudo/systemctl/nmcli
(test_install_never_arms_product.run_install), then a simulated reboot through the real netmode.sh."""
import os
import re
import shutil
import time

import pytest

from test_install_never_arms_product import MODE_WRITE, run_install
from test_netmode_script import HOME, HOT, active, env, mutations, run, state  # noqa: F401  (fixture)


def mode_file(env):
    return env["boot"] / "memoryframe-mode.txt"


def written(env):
    f = mode_file(env)
    return f.read_text() if f.exists() else None


def reboot(env):
    """/run is tmpfs: wipe the state dir and change the boot id, then run the boot service (netmode.sh boot)."""
    shutil.rmtree(env["state"], ignore_errors=True)
    (env["tmp"] / "boot_id").write_text(f"boot-{time.time()}\n")
    r = run(env, "boot")
    assert r.returncode == 0, r.stderr


def never_ran_netmode(log, env):
    assert not re.search(r"^netmode\.sh ", log, re.M)                  # the helper is copied, never executed
    assert not re.search(r"memoryframe-netmode (product|dev|dev-temp|boot)", log)
    assert not re.search(r"systemctl (start|restart|reload) memoryframe-netmode", log)
    assert mutations(env) == []                                         # nmcli: read-only during install


def go(tmp_path, env, *args, upgrade=False, wifi="home", ok=True, **kw):
    if wifi == "hotspot":
        (env["d"] / "active").write_text(HOT + "\n")
    r, log = run_install(tmp_path, env, args=args, upgrade=upgrade, **kw)
    if ok:
        assert r.returncode == 0, r.stdout + r.stderr
        never_ran_netmode(log, env)
    return r, log


# ---------- no file: fresh vs upgrade x home Wi-Fi vs hotspot ----------

@pytest.mark.parametrize("upgrade,wifi,expect", [
    (False, "home", "product"),       # #94: a fresh install is product even on home Wi-Fi (was: dev)
    (False, "hotspot", "product"),
    (True, "home", "dev"),            # upgrade pins the live dev mode: a deploy never flips a dev Pi
    (True, "hotspot", "product"),
])
def test_no_file_fresh_or_upgrade(env, tmp_path, upgrade, wifi, expect):
    r, log = go(tmp_path, env, upgrade=upgrade, wifi=wifi)
    assert written(env) == expect + "\n"
    assert re.search(MODE_WRITE, log)
    assert f"writing '{expect}' (" in r.stdout and ("fresh install" in r.stdout) == (not upgrade), r.stdout
    reboot(env)                                                          # next boot follows the file
    assert state(env)["mode"] == expect
    assert active(env) == (HOME if expect == "dev" else HOT)
    assert f"armed for next boot: {expect}" in run(env, "status").stdout


def test_stray_app_dir_without_unit_is_a_fresh_install(env, tmp_path):
    """A half-done install can leave /opt/memoryframe (code) without the unit: still fresh, so product."""
    opt = tmp_path / "opt"
    (opt / "memoryframe").mkdir(parents=True)
    (opt / "memoryframe" / "__init__.py").write_text("")
    (opt / "app.py").write_text("")
    r, _log = go(tmp_path, env, extra_env={"MEMORYFRAME_OPT_DIR": str(opt)})
    assert written(env) == "product\n" and "fresh install" in r.stdout


def test_upgrade_signal_is_the_installed_unit_not_the_code_dir():
    src = (os.path.dirname(__file__) + "/../deploy/install.sh")
    text = open(src).read()
    mig = open(os.path.dirname(__file__) + "/../deploy/memoryframe-migrate.sh").read()
    unit = 'UNIT="${MFM_UNITS:-/etc/systemd/system}/memoryframe.service"'
    assert unit in text and unit in mig                                  # the same path as save_pack's
    assert '[ -f "$UNIT" ] && IS_UPGRADE=1' in text
    assert text.index("IS_UPGRADE=1") < text.index('sudo "$MIGRATE" save-pack')   # decided before anything changes
    assert not re.search(r"IS_UPGRADE=1.*APP_DIR|APP_DIR.*IS_UPGRADE", text)


# ---------- --mode ----------

@pytest.mark.parametrize("args,upgrade,wifi,expect", [
    (("--mode", "dev"), False, "home", "dev"),                 # a new dev Pi
    (("--mode=dev",), False, "hotspot", "dev"),
    (("--mode", "product"), False, "home", "product"),
    (("--mode", "product"), True, "home", "product"),          # overrides the upgrade pin (no file yet)
    (("--mode", "dev"), True, "hotspot", "dev"),
])
def test_mode_option_writes_that_mode_when_no_file(env, tmp_path, args, upgrade, wifi, expect):
    r, _log = go(tmp_path, env, *args, upgrade=upgrade, wifi=wifi)
    assert written(env) == expect + "\n" and f"(--mode {expect})" in r.stdout, r.stdout
    reboot(env)
    assert state(env)["mode"] == expect and active(env) == (HOME if expect == "dev" else HOT)


@pytest.mark.parametrize("args", [("--mode", "hotspot"), ("--mode", "Dev"), ("--mode", ""), ("--mode",),
                                  ("--mode=",), ("--mode=prod",)])
@pytest.mark.parametrize("upgrade", [False, True])
def test_invalid_mode_exits_before_any_change(env, tmp_path, args, upgrade):
    r, log = go(tmp_path, env, *args, upgrade=upgrade, ok=False)
    assert r.returncode == 2, (r.returncode, r.stdout, r.stderr)
    assert "Invalid --mode" in r.stderr and "use --mode dev or --mode product" in r.stderr
    assert log == ""                                                     # no sudo, apt, systemctl, nmcli call
    assert written(env) is None and mutations(env) == []


# ---------- an existing file is never touched ----------

@pytest.mark.parametrize("existing", ["product\n", "dev\n", "", "  Product \r\n", "hotspot\n"])
@pytest.mark.parametrize("args", [(), ("--mode", "dev"), ("--mode", "product")])
@pytest.mark.parametrize("upgrade,wifi", [(False, "home"), (True, "home"), (True, "hotspot")])
def test_existing_file_is_never_touched(env, tmp_path, existing, args, upgrade, wifi):
    f = mode_file(env)
    f.write_bytes(existing.encode())
    before = os.stat(f).st_mtime_ns
    r, log = go(tmp_path, env, *args, upgrade=upgrade, wifi=wifi)
    assert f.read_bytes() == existing.encode() and os.stat(f).st_mtime_ns == before
    assert "memoryframe-mode.txt" not in log                             # no mode-file write at all
    assert "left unchanged" in r.stdout and (("ignored" in r.stdout) == bool(args)), r.stdout


def test_missing_boot_partition_warns_and_writes_nothing(env, tmp_path):
    shutil.rmtree(env["boot"])
    r, log = go(tmp_path, env)
    assert "boot partition" in r.stderr and "memoryframe-mode.txt" not in log and not env["boot"].exists()


# ---------- PR152 nit (Hale): the upgrade's nmcli lookup is bounded ----------

def test_hung_nmcli_on_upgrade_times_out_into_product(env, tmp_path):
    """A hung `nmcli ... device show wlan0` must not stall install: after MEMORYFRAME_NMCLI_TIMEOUT it counts as
    "not on home Wi-Fi" (product), like a missing or failing nmcli. Other nmcli calls go to the normal fake."""
    hang = env["d"] / "nmcli-hang"
    hang.write_text('#!/bin/bash\nif [ "$1 $2 $3 $4" = "-g GENERAL.CONNECTION device show" ]; then\n'
                    f'  echo "nmcli-hang $*" >> "$STUB_DIR/log"; exec sleep 30\nfi\nexec "{env["env"]["NMCLI"]}" "$@"\n')
    hang.chmod(0o755)
    t0 = time.monotonic()
    r, log = go(tmp_path, env, upgrade=True, wifi="home",
                extra_env={"NMCLI": str(hang), "MEMORYFRAME_NMCLI_TIMEOUT": "1"})
    assert time.monotonic() - t0 < 25                                   # bounded: never waited for the 30 s sleep
    assert "nmcli-hang -g GENERAL.CONNECTION device show wlan0" in (env["d"] / "log").read_text()
    assert "WARNING: nmcli did not answer within 1s: treated as not on home Wi-Fi." in r.stderr
    assert written(env) == "product\n" and re.search(MODE_WRITE, log)


def test_mode_file_is_written_atomically_via_a_temp_in_the_boot_dir(env, tmp_path):
    r, log = go(tmp_path, env, upgrade=False)
    boot = env["boot"]
    assert written(env) == "product\n" and not (boot / ".memoryframe-mode.txt.new").exists()
    seq = [l for l in log.splitlines() if ".memoryframe-mode.txt.new" in l]
    assert [l.split()[1] for l in seq] == ["cp", "chown", "chmod", "sync", "mv"], seq   # temp, owner/mode, fsync, rename
    assert f"chown root:root {boot}/.memoryframe-mode.txt.new" in log and f"chmod 0644 {boot}/.memoryframe-mode.txt.new" in log
