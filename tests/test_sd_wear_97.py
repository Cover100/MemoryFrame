"""#97: kiosk Chromium profile + cache on a RAM-only tmpfs, merged --disable-features, and / mounted noatime."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.test_kiosk_script import env, launches, run_until  # noqa: F401  (env is a fixture)

ROOT = Path(__file__).resolve().parents[1]
MOUNT = ROOT / "deploy" / "run-mfkiosk.mount"
FSTAB_SH = ROOT / "deploy" / "fstab-noatime.sh"
INSTALL = (ROOT / "deploy" / "install.sh").read_text()
BASH = shutil.which("bash")
WEAR_FLAGS = ["--no-first-run", "--disable-background-networking", "--disable-component-update",
              "--disable-breakpad", "--disk-cache-size=33554432", "--media-cache-size=33554432"]


def first_launch(tmp_path, e):
    run_until(e, lambda: len(launches(tmp_path)) >= 1)
    return launches(tmp_path)[0]


def test_wear_flags_and_cache_dir(env):  # noqa: F811
    tmp_path, state, _, e = env
    args = first_launch(tmp_path, e)
    for f in WEAR_FLAGS + [f"--user-data-dir={state}/chromium-profile", f"--disk-cache-dir={state}/cache"]:
        assert f in args, f
    assert (state / "cache").is_dir()


def test_single_merged_disable_features(env):  # noqa: F811
    tmp_path, _, _, e = env
    e["MEMORYFRAME_CHROMIUM_FLAGS"] = "--disable-gpu --disable-features=Foo,Translate --disable-features=Bar"
    args = first_launch(tmp_path, e)
    df = [a for a in args if a.startswith("--disable-features")]
    assert df == ["--disable-features=Translate,OptimizationHints,MediaRouter,Foo,Bar"]
    assert "--disable-gpu" in args and args[-1] == "http://localhost:5000"


def test_default_state_dir_is_the_tmpfs(env):  # noqa: F811
    tmp_path, _, _, e = env
    tmpfs = tmp_path / "run-mfkiosk"
    tmpfs.mkdir(mode=0o700)
    del e["MEMORYFRAME_STATE_DIR"]
    e["MEMORYFRAME_KIOSK_TMPFS"] = str(tmpfs)
    args = first_launch(tmp_path, e)
    assert f"--user-data-dir={tmpfs}/chromium-profile" in args and f"--disk-cache-dir={tmpfs}/cache" in args
    assert not (tmp_path / ".cache" / "memoryframe").exists()          # nothing on the card


def test_missing_tmpfs_falls_back_and_warns(env):  # noqa: F811
    tmp_path, _, _, e = env
    del e["MEMORYFRAME_STATE_DIR"]
    e["MEMORYFRAME_KIOSK_TMPFS"] = str(tmp_path / "absent")
    args = first_launch(tmp_path, e)
    assert f"--user-data-dir={tmp_path}/.cache/memoryframe/chromium-profile" in args
    assert "missing or not writable" in (tmp_path / ".cache" / "memoryframe-kiosk.log").read_text()


def test_mount_unit_mode_owner_size():
    t = MOUNT.read_text()
    assert "Where=/run/mfkiosk" in t and "Type=tmpfs" in t and "WantedBy=local-fs.target" in t
    opts = dict((o.split("=", 1) + [""])[:2] for o in re.search(r"^Options=(.*)$", t, re.M).group(1).split(","))
    assert opts["size"] == "192M" and opts["mode"] == "0700"
    assert opts["uid"] == "@KIOSK_UID@" and opts["gid"] == "@KIOSK_GID@"
    assert {"nosuid", "nodev", "noexec"} <= set(opts)


def test_install_renders_owner_and_enables_mount():
    assert '"s#@KIOSK_UID@#$(id -u "$DESKTOP_USER")#g"' in INSTALL
    assert '"s#@KIOSK_GID@#$(id -g "$DESKTOP_USER")#g"' in INSTALL
    assert "put_file \"$mnt_tmp\" /etc/systemd/system/run-mfkiosk.mount 0644" in INSTALL
    assert INSTALL.index("run-mfkiosk.mount 0644") < INSTALL.index("enable --now run-mfkiosk.mount")


def test_kiosk_default_tmpfs_path_matches_unit():
    assert 'KIOSK_TMPFS="${MEMORYFRAME_KIOSK_TMPFS:-/run/mfkiosk}"' in (ROOT / "deploy/kiosk.sh").read_text()


def run_fstab(f):
    return subprocess.run([BASH, str(FSTAB_SH), str(f)], capture_output=True, text=True)


PI_FSTAB = ("proc            /proc           proc    defaults          0       0\n"
            "PARTUUID=abc-01  /boot/firmware  vfat    defaults          0       2\n"
            "# / below\n"
            "PARTUUID=abc-02  /               ext4    defaults,relatime  0       1\n")


def test_fstab_adds_noatime_with_backup_and_is_idempotent(tmp_path):
    f = tmp_path / "fstab"
    f.write_text(PI_FSTAB)
    r = run_fstab(f)
    assert r.returncode == 0, r.stderr
    lines = f.read_text().splitlines()
    root = [l.split() for l in lines if not l.startswith("#") and l.split()[1] == "/"][0]
    assert root[3] == "defaults,noatime" and root[4:] == ["0", "1"] and root[0] == "PARTUUID=abc-02"
    assert lines[:3] == PI_FSTAB.splitlines()[:3]                       # other lines untouched
    bak = tmp_path / "fstab.memoryframe-97.bak"
    assert bak.read_text() == PI_FSTAB
    once = f.read_text()
    r = run_fstab(f)
    assert r.returncode == 0 and "already noatime" in r.stdout
    assert f.read_text() == once and bak.read_text() == PI_FSTAB          # no change, backup kept
    assert "commit=" not in once


@pytest.mark.parametrize("opts,want", [("defaults", "defaults,noatime"), ("rw,strictatime,errors=remount-ro",
                                       "rw,errors=remount-ro,noatime")])
def test_fstab_option_variants(tmp_path, opts, want):
    f = tmp_path / "fstab"
    f.write_text(f"/dev/mmcblk0p2 / ext4 {opts} 0 1\n")
    assert run_fstab(f).returncode == 0
    assert f.read_text().split()[3] == want


def test_fstab_already_noatime_untouched_no_backup(tmp_path):
    f = tmp_path / "fstab"
    txt = "PARTUUID=x-02  /  ext4  defaults,noatime  0  1\n"
    f.write_text(txt)
    assert run_fstab(f).returncode == 0
    assert f.read_text() == txt and not (tmp_path / "fstab.memoryframe-97.bak").exists()


def test_fstab_without_root_line_fails_unchanged(tmp_path):
    f = tmp_path / "fstab"
    f.write_text("proc /proc proc defaults 0 0\n")
    assert run_fstab(f).returncode == 2 and f.read_text() == "proc /proc proc defaults 0 0\n"


def test_install_fstab_step_is_guarded_and_never_touches_commit_or_cmdline():
    assert 'sudo "$SRC_DIR/deploy/fstab-noatime.sh" /etc/fstab' in INSTALL
    assert "MEMORYFRAME_NO_FSTAB" in INSTALL and "findmnt --verify --tab-file /etc/fstab" in INSTALL
    for p in [INSTALL, FSTAB_SH.read_text()]:
        assert "commit=" not in p.replace("ext4 commit= ", "")
    assert "cmdline.txt" not in FSTAB_SH.read_text().replace("cmdline.txt is not used", "")
