"""#162 install/uninstall dry run (stubbed sudo/apt/systemctl, MEMORYFRAME_BOOT_DIR and a fake HOME): install adds the
quiet-boot tokens, the labwc autostart block, the bootwatch unit and moves the Pi-Apps updater aside (skipped with
MEMORYFRAME_KEEP_PI_APPS_UPDATER=1); a second install changes nothing; uninstall restores every file byte-identical;
the mode file is never touched."""
import hashlib
import os
import shutil
import subprocess
from pathlib import Path

from test_boot_splash_cmdline import HALE
from test_boot_splash_config import HALE_CONFIG
from test_install_never_arms_product import STUB, SUDO, run_install
from test_netmode_script import env  # noqa: F401  (fixture)
from test_pi_apps_updater_162 import ENTRY

ROOT = Path(__file__).resolve().parents[1]
# sudo stub: like the #94 one, but really runs deploy/boot-splash.sh (its writes go to the sandbox boot dir and state)
SUDO_BS = STUB + 'if [[ " $* " == *"/boot-splash.sh "* ]]; then exec "$@"; fi\n' + SUDO[len(STUB):]


def md5(p):
    return hashlib.md5(p.read_bytes()).hexdigest()


def prepare(tmp_path, envd):
    boot = envd["boot"]
    boot.mkdir(exist_ok=True)
    (boot / "cmdline.txt").write_text(HALE)
    (boot / "config.txt").write_text(HALE_CONFIG)
    (boot / "memoryframe-mode.txt").write_text("dev\n")
    home = tmp_path / "home"
    (home / ".config" / "autostart").mkdir(parents=True)
    (home / ".config" / "autostart" / "pi-apps-updater.desktop").write_text(ENTRY)
    return boot, home, {"MEMORYFRAME_BOOT_STATE": str(tmp_path / "etc" / "boot-splash.state")}


def snapshot(boot, home):
    files = {}
    for root in (boot, home):
        for f in sorted(root.rglob("*")):
            if f.is_file():
                files[str(f)] = f.read_bytes()
    return files


def test_install_twice_then_uninstall(env, tmp_path):
    boot, home, extra = prepare(tmp_path, env)
    mode = md5(boot / "memoryframe-mode.txt")
    before = snapshot(boot, home)
    r, log = run_install(tmp_path, env, sudo=SUDO_BS, extra_env=extra)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "sudo apt-get install -y swaybg" in log
    assert "/usr/local/sbin/memoryframe-bootwatch" in log and "systemctl enable memoryframe-bootwatch.service" in log
    assert "memoryframe-bootwatch.service" in log
    cmd = (boot / "cmdline.txt").read_text()
    assert cmd.split()[-3:] == ["logo.nologo", "vt.global_cursor_default=0", "loglevel=3"]
    assert "disable_splash=1" in (boot / "config.txt").read_text()
    auto = home / ".config" / "labwc" / "autostart"
    assert "/opt/memoryframe/deploy/session-start.sh &  # memoryframe-managed boot-splash" in auto.read_text()
    moved = list((home / ".config" / "autostart").glob("pi-apps-updater.desktop.disabled-by-memoryframe.*"))
    assert len(moved) == 1 and moved[0].read_text() == ENTRY
    assert not (home / ".config" / "autostart" / "pi-apps-updater.desktop").exists()
    once = snapshot(boot, home)
    r2, _ = run_install(tmp_path, env, sudo=SUDO_BS, extra_env=extra)              # second install: nothing changes
    assert r2.returncode == 0, r2.stdout + r2.stderr
    assert snapshot(boot, home) == once
    assert md5(boot / "memoryframe-mode.txt") == mode

    # uninstall (same sandbox: stub sudo runs boot-splash.sh, real python3 for the labwc helpers)
    bin_ = tmp_path / "ubin"
    bin_.mkdir()
    for name in ("systemctl", "pkill", "pgrep", "labwc"):
        (bin_ / name).write_text(STUB + "exit 1\n")
    (bin_ / "sudo").write_text(SUDO_BS)
    for f in bin_.iterdir():
        f.chmod(0o755)
    e = dict(env["env"], PATH=f"{bin_}:/usr/bin:/bin", HOME=str(home), INSTALL_LOG=str(tmp_path / "u.log"), **extra)
    e.pop("XDG_CONFIG_HOME", None)
    u = subprocess.run(["bash", str(tmp_path / "app" / "deploy" / "uninstall.sh")], env=e, capture_output=True,
                       text=True, timeout=60)
    assert u.returncode == 0, u.stdout + u.stderr
    after = snapshot(boot, home)
    # every original file is back byte-identical; only our backups are left (and the #155 rc.xml backup/file)
    for k, v in before.items():
        assert after.get(k) == v, k
    left = sorted(Path(k).name for k in after if k not in before)
    assert all(".memoryframe-bak." in n or n.startswith("rc.xml") or n.endswith(".mf-bak") for n in left), left
    assert not auto.exists()                                                       # it was ours
    assert not (tmp_path / "etc" / "boot-splash.state").exists()
    assert md5(boot / "memoryframe-mode.txt") == mode
    ulog = (tmp_path / "u.log").read_text()
    assert "systemctl disable memoryframe-bootwatch.service" in ulog


def test_keep_pi_apps_updater_variable(env, tmp_path):
    boot, home, extra = prepare(tmp_path, env)
    r, _ = run_install(tmp_path, env, sudo=SUDO_BS, extra_env=dict(extra, MEMORYFRAME_KEEP_PI_APPS_UPDATER="1"))
    assert r.returncode == 0, r.stdout + r.stderr
    assert (home / ".config" / "autostart" / "pi-apps-updater.desktop").read_text() == ENTRY
    assert "Pi-Apps updater kept" in r.stdout


def test_refused_cmdline_only_warns_and_the_install_goes_on(env, tmp_path):
    boot, home, extra = prepare(tmp_path, env)
    (boot / "cmdline.txt").write_text("console=tty1 rootwait\nquiet\n")             # two lines, no root=
    r, _ = run_install(tmp_path, env, sudo=SUDO_BS, extra_env=extra)
    assert r.returncode == 0, r.stdout + r.stderr
    assert (boot / "cmdline.txt").read_text() == "console=tty1 rootwait\nquiet\n"
    assert "not exactly one line" in r.stdout and "Done." in r.stdout


def test_install_never_reboots_or_runs_boot_splash_from_elsewhere():
    src = (ROOT / "deploy" / "install.sh").read_text()
    assert src.count("deploy/boot-splash.sh\" on") == 1 and "boot-splash.sh\" off" not in src
    code = "\n".join(l.split("#", 1)[0] for l in src.splitlines())
    assert "systemctl reboot" not in code and "\nsudo reboot" not in code
