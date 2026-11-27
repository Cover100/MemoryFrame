"""#175: initramfs verification must not roll back when local update-initramfs
prunes unused generic modules bundled in a factory Raspberry Pi OS initramfs8
(ahci, aoe, aqc111, arizona*, etc.), while still failing and rolling back on
loss of critical boot/storage/filesystem/display modules, missing Plymouth or
theme assets, or empty/corrupt archives."""
import hashlib
import os
import sys
from pathlib import Path

import pytest

from test_plymouth_theme_162 import KERNEL, pl, sh_sha, snapshot  # noqa: F401

PY = sys.executable

FACTORY_GENERIC = [
    "ahci",
    "aoe",
    "aqc111",
    "arizona",
    "arizona-i2c",
    "arizona-ldo1",
    "arizona-micsupp",
    "arizona-spi",
]

BASE_CRITICAL = [
    "ext4",
    "jbd2",
    "mmc_core",
    "mmc_block",
    "sdhci_brcmstb",
    "overlay",
    "vc4",
    "drm_kms_helper",
    "panel-raspberrypi-touchscreen",
    "edt-ft5x06",
]


def _make_fake_ui(mods_after, *, drop_assets=(), drop_plymouthd=False, drop_script_so=False,
                  empty_file=False, corrupt=False):
    """Build a fake update-initramfs script for #175 test scenarios."""
    return f"""#!{PY}
import hashlib, os, sys, pathlib
boot = pathlib.Path(os.environ["MEMORYFRAME_BOOT_DIR"])
img = boot / ("initramfs8" if sys.argv[-1].endswith("-v8") else "initramfs_2712")
if {empty_file!r}:
    img.write_bytes(b"")
    sys.exit(0)
if {corrupt!r}:
    img.write_bytes(b"NOT_A_VALID_INITRAMFS_ARCHIVE" * 80)
    sys.exit(0)
conf = pathlib.Path(os.environ["MEMORYFRAME_PLYMOUTH_CONF"])
t = [l[6:] for l in (conf.read_text().splitlines() if conf.exists() else []) if l.startswith("Theme=")]
theme = t[-1] if t else "pix"
tdir = pathlib.Path(os.environ["MEMORYFRAME_PLYMOUTH_DIR"]) / theme
lines = []
if not {drop_plymouthd!r}:
    lines.append("usr/sbin/plymouthd")
lines += ["usr/lib/aarch64-linux-gnu/plymouth/renderers/drm.so",
          "usr/lib/aarch64-linux-gnu/plymouth/label-pango.so"]
if not {drop_script_so!r}:
    lines.append("usr/lib/aarch64-linux-gnu/plymouth/script.so")
drop_assets = set({list(drop_assets)!r})
for f in sorted(tdir.iterdir()):
    if f.name in drop_assets:
        continue
    lines.append("usr/share/plymouth/themes/%s/%s #%s" % (
        theme, f.name, hashlib.sha256(f.read_bytes()).hexdigest()[:8]))
for m in {list(mods_after)!r}:
    lines.append("usr/lib/modules/%s/kernel/drivers/%s.ko.xz" % (sys.argv[-1], m))
img.write_text("FAKEINITRD " + sys.argv[-1] + "\\n" + "\\n".join(lines) + "\\n" + ("#" * 2000) + "\\n")
"""


def _seed_factory_initrd(pl, mods):
    """Overwrite the sandbox's stock initramfs8 with a factory-like module list."""
    lines = [
        "usr/sbin/plymouthd",
        "usr/lib/aarch64-linux-gnu/plymouth/renderers/drm.so",
        "usr/lib/aarch64-linux-gnu/plymouth/script.so",
        "usr/share/plymouth/themes/pix/pix.plymouth",
        "usr/share/plymouth/themes/pix/pix.script",
    ]
    lines += [f"usr/lib/modules/{KERNEL}/kernel/drivers/{m}.ko.xz" for m in mods]
    pl.initrd.write_text(f"FAKEINITRD {KERNEL}\n" + "\n".join(lines) + "\n" + ("#" * 2000) + "\n")


def _install_fake_ui(pl, script_body):
    p = Path(pl.env["UPDATE_INITRAMFS"])
    p.write_text(script_body)
    p.chmod(0o755)


def test_factory_generic_modules_pruned_passes(pl):
    """Fresh Raspberry Pi OS factory initramfs8 losing generic modules
    (ahci, aoe, aqc111, arizona*) must pass verification and NOT roll back."""
    _seed_factory_initrd(pl, BASE_CRITICAL + FACTORY_GENERIC)
    before_sha = sh_sha(pl.initrd)
    _install_fake_ui(pl, _make_fake_ui(BASE_CRITICAL))

    r = pl.run("plymouth", "on")
    assert r.returncode == 0, r.stderr + r.stdout
    assert "lost modules" not in r.stderr
    assert sh_sha(pl.initrd) != before_sha
    assert pl.theme() == "memoryframe"
    st = pl.st()
    assert st["plymouth_sha_before"] == before_sha
    assert (pl.boot / st["plymouth_initramfs_bak"]).is_file()


def test_hyphen_underscore_module_normalization_passes(pl):
    """panel-raspberrypi-touchscreen vs panel_raspberrypi_touchscreen are the same module."""
    _seed_factory_initrd(pl, BASE_CRITICAL + FACTORY_GENERIC)
    normalized_after = [m.replace("-", "_") for m in BASE_CRITICAL]
    _install_fake_ui(pl, _make_fake_ui(normalized_after))

    r = pl.run("plymouth", "on")
    assert r.returncode == 0, r.stderr + r.stdout
    assert pl.theme() == "memoryframe"


@pytest.mark.parametrize("dropped", [
    "ext4",
    "mmc_block",
    "sdhci_brcmstb",
    "overlay",
    "vc4",
    "panel-raspberrypi-touchscreen",
])
def test_losing_critical_module_fails_and_rolls_back(pl, dropped):
    """Losing any critical boot/storage/filesystem/display module present in the
    prior initramfs must fail verification and roll back cleanly."""
    _seed_factory_initrd(pl, BASE_CRITICAL + FACTORY_GENERIC)
    snap = (snapshot(pl.boot), snapshot(pl.themes), snapshot(pl.conf.parent))
    after = [m for m in BASE_CRITICAL if m != dropped]
    _install_fake_ui(pl, _make_fake_ui(after))

    r = pl.run("plymouth", "on")
    assert r.returncode == 1
    assert f"lost modules: {dropped}" in r.stderr
    assert "rolling back" in r.stderr
    assert (snapshot(pl.boot), snapshot(pl.themes), snapshot(pl.conf.parent)) == snap


@pytest.mark.parametrize("label,ui_kwargs,extra_env,expect", [
    ("missing-dot-png", {"drop_assets": ("dot.png",)}, {}, "missing memoryframe theme assets: dot.png"),
    ("missing-lockup-png", {"drop_assets": ("lockup-nodot.png",)}, {}, "missing memoryframe theme assets: lockup-nodot.png"),
    ("missing-plymouthd", {"drop_plymouthd": True}, {}, "no plymouthd binary"),
    ("missing-script-so", {"drop_script_so": True}, {}, "no plymouth script.so"),
    ("empty-archive", {"empty_file": True}, {}, "empty (0 bytes)"),
    ("corrupt-archive", {"corrupt": True}, {"FAKE_LS_CORRUPT": "1"}, "lsinitramfs"),
])
def test_missing_assets_or_bad_archive_fails_and_rolls_back(pl, label, ui_kwargs, extra_env, expect):
    """Missing Plymouth/theme assets or corrupt/empty archive must fail and roll back."""
    _seed_factory_initrd(pl, BASE_CRITICAL + FACTORY_GENERIC)
    snap = (snapshot(pl.boot), snapshot(pl.themes), snapshot(pl.conf.parent))
    _install_fake_ui(pl, _make_fake_ui(BASE_CRITICAL, **ui_kwargs))

    if extra_env.get("FAKE_LS_CORRUPT"):
        # Make fake lsinitramfs fail only on the corrupt marker so initial listing succeeds
        ls_path = Path(pl.env["LSINITRAMFS"])
        ls_path.write_text(f"""#!{PY}
import sys
data = open(sys.argv[-1], "rb").read()
if b"NOT_A_VALID_INITRAMFS_ARCHIVE" in data:
    sys.exit(1)
for l in data.decode("utf-8", errors="replace").splitlines():
    if l and not l.startswith(("#", "FAKEINITRD")):
        print(l.split(" #")[0])
""")
        ls_path.chmod(0o755)

    r = pl.run("plymouth", "on")
    assert r.returncode == 1, f"{label}: {r.stdout}\n{r.stderr}"
    assert expect in r.stderr, f"{label}: expected {expect!r} in {r.stderr!r}"
    assert "rolling back" in r.stderr
    assert (snapshot(pl.boot), snapshot(pl.themes), snapshot(pl.conf.parent)) == snap


# ---------------- /proc/modules intersection (#175 follow-up) ----------------

UNUSED_OPTIONAL_CRITICAL = [
    "btrfs",
    "xfs",
    "squashfs",
    "panel-raspberrypi-touchscreen",
]


def _write_proc_files(pl, loaded_mods, mounts=None):
    pm = pl.tmp / "proc_modules"
    pm.write_text("\n".join(f"{m} 16384 0 - Live 0x0000000000000000" for m in loaded_mods) + "\n")
    pmounts = pl.tmp / "proc_mounts"
    if mounts is None:
        mounts = [
            "/dev/mmcblk0p2 / ext4 rw,noatime 0 0",
            "/dev/mmcblk0p1 /boot/firmware vfat rw,relatime 0 0",
        ]
    pmounts.write_text("\n".join(mounts) + "\n")
    return {"MEMORYFRAME_PROC_MODULES": str(pm), "MEMORYFRAME_PROC_MOUNTS": str(pmounts)}


def test_unused_optional_critical_modules_dropped_passes_when_not_loaded(pl):
    """Factory initramfs8 losing unused btrfs/xfs/squashfs/panel-raspberrypi-touchscreen
    (plus generic modules) when not loaded in /proc/modules must succeed (#175)."""
    _seed_factory_initrd(pl, BASE_CRITICAL + UNUSED_OPTIONAL_CRITICAL + FACTORY_GENERIC)
    # Keep only the loaded HDMI/ext4 Pi modules; drop btrfs, xfs, squashfs, DSI panel, and generic
    kept = [m for m in BASE_CRITICAL if m != "panel-raspberrypi-touchscreen"]
    _install_fake_ui(pl, _make_fake_ui(kept))
    proc_env = _write_proc_files(pl, ["vc4", "drm", "drm_kms_helper", "mmc_block", "sdhci_brcmstb", "ext4"])

    r = pl.run("plymouth", "on", **proc_env)
    assert r.returncode == 0, f"stdout={r.stdout}\nstderr={r.stderr}"
    assert "lost modules" not in r.stderr
    assert pl.theme() == "memoryframe"


@pytest.mark.parametrize("dropped,loaded_entry", [
    ("ext4", "ext4"),
    ("vc4", "vc4"),
    ("mmc_block", "mmc_block"),
    ("panel-raspberrypi-touchscreen", "panel_raspberrypi_touchscreen"),
])
def test_losing_loaded_critical_module_fails_and_rolls_back(pl, dropped, loaded_entry):
    """Losing a critical module that IS loaded in /proc/modules and in the prior initramfs
    must fail verification and roll back cleanly."""
    _seed_factory_initrd(pl, BASE_CRITICAL + UNUSED_OPTIONAL_CRITICAL + FACTORY_GENERIC)
    snap = (snapshot(pl.boot), snapshot(pl.themes), snapshot(pl.conf.parent))
    kept = [m for m in BASE_CRITICAL if m != dropped]
    _install_fake_ui(pl, _make_fake_ui(kept))
    proc_env = _write_proc_files(pl, ["vc4", "drm", "mmc_block", "ext4", loaded_entry])

    r = pl.run("plymouth", "on", **proc_env)
    assert r.returncode == 1, f"dropped={dropped}: {r.stdout}\n{r.stderr}"
    assert "lost modules:" in r.stderr
    assert dropped in r.stderr
    assert "rolling back" in r.stderr
    assert (snapshot(pl.boot), snapshot(pl.themes), snapshot(pl.conf.parent)) == snap


def test_losing_mounted_root_fs_module_fails_even_if_not_in_proc_modules(pl):
    """A root filesystem module listed in /proc/mounts and in the prior initramfs
    must still be protected even if omitted from /proc/modules."""
    _seed_factory_initrd(pl, BASE_CRITICAL + UNUSED_OPTIONAL_CRITICAL)
    snap = (snapshot(pl.boot), snapshot(pl.themes), snapshot(pl.conf.parent))
    kept = [m for m in BASE_CRITICAL if m != "ext4"]
    _install_fake_ui(pl, _make_fake_ui(kept))
    proc_env = _write_proc_files(pl, ["vc4", "drm"], mounts=["/dev/mmcblk0p2 / ext4 rw 0 0"])

    r = pl.run("plymouth", "on", **proc_env)
    assert r.returncode == 1, f"{r.stdout}\n{r.stderr}"
    assert "lost modules:" in r.stderr
    assert "ext4" in r.stderr
    assert "rolling back" in r.stderr
    assert (snapshot(pl.boot), snapshot(pl.themes), snapshot(pl.conf.parent)) == snap
