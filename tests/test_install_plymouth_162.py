"""#162 Change 2 install/uninstall dry run: install.sh (stubbed sudo/apt/systemctl, sandbox boot dir and HOME) with
fake plymouth-set-default-theme / update-initramfs / lsinitramfs sets the memoryframe theme and rebuilds the
initramfs once; a second install does not rebuild and changes nothing; MEMORYFRAME_NO_PLYMOUTH=1 skips it; a failed
rebuild only warns and leaves everything as it was; uninstall puts the previous theme, plymouthd.conf and initramfs
back and removes the theme and the backups. cmdline.txt gets only PR A's tokens; the mode file is never touched."""
import hashlib
import os
import subprocess
from pathlib import Path

from test_boot_splash_cmdline import HALE
from test_install_boot_splash_162 import SUDO_BS, prepare
from test_plymouth_theme_162 import snapshot
from test_install_never_arms_product import STUB, run_install
from test_netmode_script import env  # noqa: F401  (fixture)
from test_plymouth_theme_162 import CONF_ORIG, FAKE_LS, FAKE_SET, FAKE_UI, KERNEL

ROOT = Path(__file__).resolve().parents[1]


def plymouth_sandbox(tmp_path, boot):
    themes = tmp_path / "themes"
    (themes / "pix").mkdir(parents=True)
    (themes / "pix" / "pix.plymouth").write_text("[Plymouth Theme]\nName=pix\n")
    conf = tmp_path / "etcp" / "plymouthd.conf"
    conf.parent.mkdir()
    conf.write_text(CONF_ORIG)
    fb = tmp_path / "fakebin"
    fb.mkdir()
    for name, body in (("plymouth-set-default-theme", FAKE_SET), ("update-initramfs", FAKE_UI),
                       ("lsinitramfs", FAKE_LS)):
        (fb / name).write_text(body)
        (fb / name).chmod(0o755)
    extra = {"MEMORYFRAME_PLYMOUTH_DIR": str(themes), "MEMORYFRAME_PLYMOUTH_CONF": str(conf),
             "MEMORYFRAME_KERNEL": KERNEL, "MEMORYFRAME_BOOT_FREE": str(10 ** 9), "FAKE_LOG": str(tmp_path / "f.log"),
             "PLYMOUTH_SET": str(fb / "plymouth-set-default-theme"), "UPDATE_INITRAMFS": str(fb / "update-initramfs"),
             "LSINITRAMFS": str(fb / "lsinitramfs"),
             "MEMORYFRAME_PROC_MODULES": str(tmp_path / "no-proc-modules"),
             "MEMORYFRAME_PROC_MOUNTS": str(tmp_path / "no-proc-mounts")}
    e = dict(os.environ, MEMORYFRAME_BOOT_DIR=str(boot), **extra)
    e.pop("FAKE_UI", None)
    subprocess.run([str(fb / "update-initramfs"), "-u", "-k", KERNEL], env=e, check=True)    # the stock pix image
    (tmp_path / "f.log").unlink()
    return themes, conf, extra


def rebuilds(tmp_path):
    f = tmp_path / "f.log"
    return [l for l in f.read_text().splitlines() if l.startswith("update-initramfs")] if f.exists() else []


def theme(extra):
    return subprocess.run([extra["PLYMOUTH_SET"]], env=dict(os.environ, **extra), capture_output=True,
                          text=True).stdout.strip()


def uninstall(tmp_path, envd, home, extra, **more):
    bin_ = tmp_path / "ubin"
    bin_.mkdir(exist_ok=True)
    for name in ("systemctl", "pkill", "pgrep", "labwc"):
        (bin_ / name).write_text(STUB + "exit 1\n")
    (bin_ / "sudo").write_text(SUDO_BS)
    for f in bin_.iterdir():
        f.chmod(0o755)
    e = dict(envd["env"], PATH=f"{bin_}:/usr/bin:/bin", HOME=str(home), INSTALL_LOG=str(tmp_path / "u.log"),
             **extra, **more)
    e.pop("XDG_CONFIG_HOME", None)
    return subprocess.run(["bash", str(tmp_path / "app" / "deploy" / "uninstall.sh")], env=e, capture_output=True,
                          text=True, timeout=120)


def test_install_twice_then_uninstall_restores_theme_conf_and_initramfs(env, tmp_path):
    boot, home, extra = prepare(tmp_path, env)
    themes, conf, pextra = plymouth_sandbox(tmp_path, boot)
    extra.update(pextra)
    initrd = boot / "initramfs8"
    orig = initrd.read_bytes()
    mode = hashlib.md5((boot / "memoryframe-mode.txt").read_bytes()).hexdigest()
    before = snapshot(boot, themes, conf.parent)
    r, _ = run_install(tmp_path, env, sudo=SUDO_BS, extra_env=extra)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "memoryframe theme set; initramfs8 rebuilt" in r.stdout
    assert theme(extra) == "memoryframe" and len(rebuilds(tmp_path)) == 1
    assert len(list(boot.glob("initramfs8.memoryframe-bak.*"))) == 1
    assert (boot / "cmdline.txt").read_text() == HALE.rstrip("\n") + " logo.nologo vt.global_cursor_default=0 loglevel=3\n"
    once = snapshot(boot, themes, conf.parent)
    r2, _ = run_install(tmp_path, env, sudo=SUDO_BS, extra_env=extra)                 # second install: no rebuild
    assert r2.returncode == 0 and "no rebuild" in r2.stdout
    assert len(rebuilds(tmp_path)) == 1
    assert snapshot(boot, themes, conf.parent) == once
    u = uninstall(tmp_path, env, home, extra)
    assert u.returncode == 0, u.stdout + u.stderr
    assert theme(extra) == "pix" and initrd.read_bytes() == orig and len(rebuilds(tmp_path)) == 2
    after = snapshot(boot, themes, conf.parent)
    for k, v in before.items():
        assert after.get(k) == v, k                                                    # cmdline, config, conf, initrd
    assert not (themes / "memoryframe").exists() and not list(boot.glob("initramfs8.memoryframe-bak.*"))
    assert not list(conf.parent.glob("*.memoryframe-bak.*"))
    assert hashlib.md5((boot / "memoryframe-mode.txt").read_bytes()).hexdigest() == mode


def test_no_plymouth_variable_skips_it(env, tmp_path):
    boot, home, extra = prepare(tmp_path, env)
    themes, conf, pextra = plymouth_sandbox(tmp_path, boot)
    extra.update(pextra, MEMORYFRAME_NO_PLYMOUTH="1")
    r, _ = run_install(tmp_path, env, sudo=SUDO_BS, extra_env=extra)
    assert r.returncode == 0 and "skipped (MEMORYFRAME_NO_PLYMOUTH=1)" in r.stdout
    assert theme(extra) == "pix" and rebuilds(tmp_path) == []


def test_failed_rebuild_only_warns_and_the_install_goes_on(env, tmp_path):
    boot, home, extra = prepare(tmp_path, env)
    themes, conf, pextra = plymouth_sandbox(tmp_path, boot)
    extra.update(pextra, FAKE_UI="nomodules")
    initrd = (boot / "initramfs8").read_bytes()
    r, _ = run_install(tmp_path, env, sudo=SUDO_BS, extra_env=extra)
    assert r.returncode == 0 and "Done." in r.stdout
    assert "lost modules: vc4" in r.stderr and "the previous boot theme stays" in r.stderr
    assert (boot / "initramfs8").read_bytes() == initrd and theme(extra) == "pix"
    assert conf.read_text() == CONF_ORIG and not (themes / "memoryframe").exists()
    assert not list(boot.glob("initramfs8.memoryframe-bak.*"))


def test_without_plymouth_on_the_machine_install_and_uninstall_skip_it(env, tmp_path):
    boot, home, extra = prepare(tmp_path, env)
    extra["PLYMOUTH_SET"] = str(tmp_path / "missing" / "plymouth-set-default-theme")
    r, _ = run_install(tmp_path, env, sudo=SUDO_BS, extra_env=extra)
    assert r.returncode == 0 and "skipped: Plymouth or initramfs-tools is not installed" in r.stdout
    u = uninstall(tmp_path, env, home, extra)
    assert u.returncode == 0 and "no Memory Frame theme of ours to undo" in u.stdout


def test_install_runs_plymouth_on_once_and_never_reboots():
    src = (ROOT / "deploy" / "install.sh").read_text()
    code = "\n".join(l.split("#", 1)[0] for l in src.splitlines())
    assert src.count('deploy/boot-splash.sh" plymouth on') == 1 and "plymouth off" not in code
    assert 'MEMORYFRAME_NO_PLYMOUTH="${MEMORYFRAME_NO_PLYMOUTH:-}"' in src
    assert "update-initramfs" not in code and "plymouth-set-default-theme" not in code   # only via boot-splash.sh
    assert "systemctl reboot" not in code and "\nsudo reboot" not in code and "shutdown -r" not in code
