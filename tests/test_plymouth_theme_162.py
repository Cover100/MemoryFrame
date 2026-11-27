"""#162 Change 2 (13.5, 13.8): the memoryframe Plymouth theme. Theme files and keys, size, Wren's PNG hashes, the
memoryframe-e4 message hook; deploy/boot-splash.sh plymouth on|off with fake plymouth-set-default-theme,
update-initramfs and lsinitramfs (a fake initramfs is a text listing derived from the active theme): rebuild once
per theme hash; backups (timestamped, hash checked) before the rebuild; every failed check (update-initramfs error,
no change, lost module, no script.so, size, theme missing, lsinitramfs error) puts the initramfs, theme and
plymouthd.conf back; refusals write nothing; `off` restores; df guard; cmdline.txt never changes."""
import hashlib
import importlib.util
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
THEME = ROOT / "deploy" / "plymouth" / "memoryframe"
KERNEL = "6.18.34+rpt-rpi-v8"
HALE_CMDLINE = ("console=serial0,115200 console=tty1 root=PARTUUID=00000000-02 rootfstype=ext4 fsck.repair=yes "
                "rootwait quiet splash plymouth.ignore-serial-consoles cfg80211.ieee80211_regdom=AU\n")
CONFIG = "# Automatically load initramfs files, if found\nauto_initramfs=1\n\ndtoverlay=vc4-kms-v3d\n[all]\n"
CONF_ORIG = "# Hale's plymouthd.conf\n[Daemon]\nTheme=pix\nShowDelay=0\n"
WREN_SHA = {"lockup-nodot.png": "7c11740e06876176a61ee49bdd24606abeb40668b568fa50a4b585c337b80de9",   # pulse revision, 20 Nov
            "dot.png": "f8d2d9fc54e09f0c96086a68bb9804506a0e8d24ca0d5ab1e23887d023f5ce8f",
            "error.png": "2ef02ebbb9778244d055189ce37275dd803d196084666c99d25a287978df7331"}

# ---------------- the fakes ----------------
PY = sys.executable
FAKE_SET = f"""#!{PY}
import os, sys, pathlib
open(os.environ["FAKE_LOG"], "a").write("plymouth-set-default-theme " + " ".join(sys.argv[1:]) + "\\n")
conf = pathlib.Path(os.environ["MEMORYFRAME_PLYMOUTH_CONF"])
if len(sys.argv) == 1:
    t = [l[6:] for l in (conf.read_text().splitlines() if conf.exists() else []) if l.startswith("Theme=")]
    print(t[-1] if t else "pix"); sys.exit(0)
if os.environ.get("FAKE_SET") == "fail": sys.exit(1)
name = sys.argv[-1]
if not (pathlib.Path(os.environ["MEMORYFRAME_PLYMOUTH_DIR"]) / name / (name + ".plymouth")).is_file():
    print(name + " not found", file=sys.stderr); sys.exit(1)
conf.parent.mkdir(parents=True, exist_ok=True)
# like the real one: only the Theme= line changes (inserted under [Daemon] if missing)
lines = conf.read_text().splitlines() if conf.exists() else ["[Daemon]"]
lines = [l for l in lines if not l.startswith("Theme=")]
i = lines.index("[Daemon]") + 1 if "[Daemon]" in lines else len(lines)
lines.insert(i, "Theme=" + name)
conf.write_text("\\n".join(lines) + "\\n")
"""
# the listing: header, base files, the active theme's files (with their hash), modules, filler
FAKE_UI = f"""#!{PY}
import hashlib, os, sys, pathlib
boot = pathlib.Path(os.environ["MEMORYFRAME_BOOT_DIR"])
log = open(os.environ["FAKE_LOG"], "a")
log.write("update-initramfs " + " ".join(sys.argv[1:]) + " backups=" +
          ",".join(sorted(p.name for p in boot.glob("*.memoryframe-bak.*"))) + "\\n")
mode = os.environ.get("FAKE_UI", "")
if mode == "fail": print("E: mkinitramfs failed", file=sys.stderr); sys.exit(1)
if mode == "noop": sys.exit(0)
conf = pathlib.Path(os.environ["MEMORYFRAME_PLYMOUTH_CONF"])
t = [l[6:] for l in (conf.read_text().splitlines() if conf.exists() else []) if l.startswith("Theme=")]
theme = t[-1] if t else "pix"
tdir = pathlib.Path(os.environ["MEMORYFRAME_PLYMOUTH_DIR"]) / theme
lines = ["usr/sbin/plymouthd", "usr/lib/aarch64-linux-gnu/plymouth/renderers/drm.so",
         "usr/lib/aarch64-linux-gnu/plymouth/label-pango.so"]
if mode != "noscript": lines.append("usr/lib/aarch64-linux-gnu/plymouth/script.so")
if mode != "notheme":
    for f in sorted(tdir.iterdir()):
        lines.append("usr/share/plymouth/themes/%s/%s #%s" % (theme, f.name, hashlib.sha256(f.read_bytes()).hexdigest()[:8]))
mods = ["vc4", "drm_kms_helper", "panel-raspberrypi-touchscreen", "edt-ft5x06"]
if mode == "nomodules": mods.remove("vc4")
lines += ["usr/lib/modules/%s/kernel/drivers/%s.ko.xz" % (sys.argv[-1], m) for m in mods]
filler = "#" * (6000 if mode == "huge" else 2000)
img = boot / ("initramfs8" if sys.argv[-1].endswith("-v8") else "initramfs_2712")
img.write_text("FAKEINITRD " + sys.argv[-1] + "\\n" + "\\n".join(lines) + "\\n" + filler + "\\n")
"""
FAKE_LS = f"""#!{PY}
import os, sys
if os.environ.get("FAKE_LS") == "fail": sys.exit(1)
for l in open(sys.argv[-1]):
    l = l.rstrip("\\n")
    if l and not l.startswith(("#", "FAKEINITRD")): print(l.split(" #")[0])
"""


def sh_sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


@pytest.fixture
def pl(tmp_path):
    """A sandbox: boot dir, themes root with pix, plymouthd.conf, state, fakes; returns a helper object."""
    class P:
        pass
    p = P()
    p.tmp = tmp_path
    p.boot = tmp_path / "boot"
    p.boot.mkdir()
    (p.boot / "cmdline.txt").write_text(HALE_CMDLINE)
    (p.boot / "config.txt").write_text(CONFIG)
    (p.boot / "memoryframe-mode.txt").write_text("dev\n")
    p.themes = tmp_path / "themes"
    (p.themes / "pix").mkdir(parents=True)
    (p.themes / "pix" / "pix.plymouth").write_text("[Plymouth Theme]\nName=pix\nModuleName=script\n")
    (p.themes / "pix" / "pix.script").write_text("# pix\n")
    p.conf = tmp_path / "etc" / "plymouth" / "plymouthd.conf"
    p.conf.parent.mkdir(parents=True)
    p.conf.write_text(CONF_ORIG)
    p.state = tmp_path / "etc" / "memoryframe" / "boot-splash.state"
    p.log = tmp_path / "fake.log"
    bin_ = tmp_path / "fakebin"
    bin_.mkdir()
    for name, body in (("plymouth-set-default-theme", FAKE_SET), ("update-initramfs", FAKE_UI),
                       ("lsinitramfs", FAKE_LS)):
        (bin_ / name).write_text(body)
        (bin_ / name).chmod(0o755)
    p.env = dict(os.environ, MEMORYFRAME_BOOT_DIR=str(p.boot), MEMORYFRAME_BOOT_STATE=str(p.state),
                 MEMORYFRAME_PLYMOUTH_DIR=str(p.themes), MEMORYFRAME_PLYMOUTH_CONF=str(p.conf),
                 MEMORYFRAME_KERNEL=KERNEL, MEMORYFRAME_BOOT_FREE=str(10 ** 9), FAKE_LOG=str(p.log),
                 PLYMOUTH_SET=str(bin_ / "plymouth-set-default-theme"), UPDATE_INITRAMFS=str(bin_ / "update-initramfs"),
                 LSINITRAMFS=str(bin_ / "lsinitramfs"),
                 MEMORYFRAME_PROC_MODULES=str(tmp_path / "no-proc-modules"),
                 MEMORYFRAME_PROC_MOUNTS=str(tmp_path / "no-proc-mounts"))
    for k in ("FAKE_UI", "FAKE_LS", "FAKE_SET", "MEMORYFRAME_NO_PLYMOUTH"):
        p.env.pop(k, None)
    # the stock initramfs, as the fake builds it for pix
    subprocess.run([str(bin_ / "update-initramfs"), "-u", "-k", KERNEL], env=p.env, check=True)
    p.log.unlink()
    p.initrd = p.boot / "initramfs8"
    p.deploy = ROOT / "deploy"

    def run(*args, deploy=None, **env):
        e = dict(p.env, **env)
        return subprocess.run(["bash", str((deploy or p.deploy) / "boot-splash.sh"), *args], env=e,
                              capture_output=True, text=True, timeout=60)
    p.run = run
    p.calls = lambda: p.log.read_text().splitlines() if p.log.exists() else []
    p.rebuilds = lambda: [c for c in p.calls() if c.startswith("update-initramfs")]
    p.theme = lambda: subprocess.run([p.env["PLYMOUTH_SET"]], env=p.env, capture_output=True, text=True).stdout.strip()
    p.listing = lambda: subprocess.run([p.env["LSINITRAMFS"], str(p.initrd)], env=p.env, capture_output=True,
                                       text=True).stdout
    p.st = lambda: dict(l.split("=", 1) for l in p.state.read_text().splitlines()) if p.state.exists() else {}
    return p


def snapshot(*roots):
    out = {}
    for root in roots:
        for f in sorted(Path(root).rglob("*")):
            if f.is_file():
                out[str(f)] = f.read_bytes()
    return out


# ---------------- the theme files ----------------

def test_theme_files_keys_and_hashes():
    names = sorted(p.name for p in THEME.iterdir())
    assert names == ["dot.png", "error.png", "lockup-nodot.png", "memoryframe.plymouth", "memoryframe.script"]
    for name, sha in WREN_SHA.items():
        assert sh_sha(THEME / name) == sha, name                                       # Wren's PNGs byte for byte
    assert sum(p.stat().st_size for p in THEME.iterdir()) < 200 * 1024
    ini = (THEME / "memoryframe.plymouth").read_text()
    assert re.search(r"^\[Plymouth Theme\]$", ini, re.M) and re.search(r"^ModuleName=script$", ini, re.M)
    assert re.search(r"^ImageDir=/usr/share/plymouth/themes/memoryframe$", ini, re.M)
    assert re.search(r"^ScriptFile=/usr/share/plymouth/themes/memoryframe/memoryframe\.script$", ini, re.M)


def test_png_sizes_and_alpha():
    from PIL import Image
    for name, size in (("lockup-nodot.png", (360, 172)), ("dot.png", (28, 28)), ("error.png", (480, 253))):
        im = Image.open(THEME / name)
        assert im.size == size and im.mode == "RGBA", name


def script_code():
    src = (THEME / "memoryframe.script").read_text()
    return "\n".join(l.split("#", 1)[0] for l in src.splitlines())


def test_installer_ships_exactly_the_theme_files():
    """boot-splash-plymouth.py FILES must list every file the script loads (a missing PNG = a blank splash at boot)."""
    spec = importlib.util.spec_from_file_location("bsp", ROOT / "deploy" / "boot-splash-plymouth.py")
    bsp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bsp)
    assert sorted(bsp.FILES) == sorted(p.name for p in THEME.iterdir())
    for img in re.findall(r'Image\("([^"]+)"\)', script_code()):
        assert img in bsp.FILES, img


def test_script_is_centred_ink_with_a_pulsing_dot_and_the_message_hooks():
    code = script_code()
    assert code.count("{") == code.count("}") and code.count("(") == code.count(")")
    assert code.count("Window.SetBackgroundTopColor(0.0549, 0.0745, 0.0667)") == 1           # #0E1311, flat
    assert code.count("Window.SetBackgroundBottomColor(0.0549, 0.0745, 0.0667)") == 1
    for img in ("lockup-nodot.png", "dot.png", "error.png"):
        assert f'Image("{img}")' in code
    assert "Window.GetWidth()" in code and "Window.GetHeight()" in code
    # the hook main shipped and Hale proved on the frame (E3/E4 pass on device): keep it, do not rename it
    assert "Plymouth.SetMessageFunction(message_callback)" in code and "SetDisplayMessageFunction" not in code
    assert "Plymouth.SetRefreshFunction(refresh_callback)" in code and "Plymouth.SetRefreshRate(FPS)" in code
    assert re.search(r'text == "memoryframe-e4"\) show_error\(\)', code)
    assert re.search(r'text == "memoryframe-ok"\) show_lockup\(\)', code)
    assert '"memoryframe-settle"' in code
    assert "Image.Text" not in code                                                         # no text rendering
    calls = set(re.findall(r"\b([A-Z][A-Za-z]*\.[A-Z][A-Za-z]*)\(", code))
    # everything used exists in plymouth 24.004.60's script libraries (checked against its source, and run in plymouthd's x11 renderer)
    assert calls <= {"Window.SetBackgroundTopColor", "Window.SetBackgroundBottomColor", "Window.GetWidth", "Window.GetHeight",
                     "Plymouth.SetMessageFunction", "Plymouth.SetDisplayNormalFunction", "Plymouth.SetRefreshFunction",
                     "Plymouth.SetRefreshRate", "Math.Int", "Math.Cos"}, calls
    assert "Math.Pi" in code                                                                  # defined by script-lib-math.script


def test_message_callback_handles_e4_ok_and_settle():
    """E4 must keep working (proved on the frame): e4 shows the error image and stops the pulse, ok returns to the lockup and
    restarts the pulse, settle (only while pulsing) eases the dot to rest; the refresh callback does nothing in error mode."""
    code = script_code()
    m = re.search(r"fun message_callback\(text\) \{(.*?)\n\}", code, re.S)
    assert m
    body = m.group(1)
    assert re.search(r'text == "memoryframe-e4"\) show_error\(\);', body)
    assert re.search(r'text == "memoryframe-ok"\) show_lockup\(\);', body)
    assert re.search(r'text == "memoryframe-settle" && mode == "pulse"\)', body)
    err = re.search(r"fun show_error\(\) \{(.*?)\n\}", code, re.S).group(1)
    assert 'mode = "error"' in err and "dot.SetOpacity(0)" in err and "lockup.SetImage(error_image)" in err
    lk = re.search(r"fun show_lockup\(\) \{(.*?)\n\}", code, re.S).group(1)
    assert 'mode = "pulse"' in lk and "lockup.SetImage(lockup_image)" in lk and "place_dot(1, 1)" in lk
    assert re.search(r'fun refresh_callback\(\) \{\s*if \(mode == "error"', code)           # the pulse never touches the E4 screen
    assert "SetDisplayNormalFunction(normal_callback)" in code


def const(name):
    m = re.search(rf"^{name}\s*=\s*([0-9.]+);", script_code(), re.M)
    assert m, name
    return float(m.group(1))


def pulse(frames):
    """The script's pulse_dip/place_dot maths in Python: (opacity, size in px) after `frames` refreshes."""
    import math
    phase = frames / (const("FPS") * const("PERIOD"))
    frac = phase - int(phase)
    d = (1 - math.cos(2 * math.pi * frac)) / 2
    scale = 1 - (1 - const("S_MIN")) * d
    return 1 - (1 - const("O_MIN")) * d, int(const("DOT_PX") * scale + 0.5)


def test_pulse_numbers_are_wrens_spec():
    """wren-notes.md section 4: starts and ends at rest (opacity 1, 28 px), bottom opacity .3 at 21 px, every 2.8 s, only the dot."""
    assert (const("PERIOD"), const("O_MIN"), const("S_MIN"), const("DOT_PX"), const("FPS")) == (2.8, 0.3, 0.75, 28, 50)
    period = int(const("FPS") * const("PERIOD"))
    assert pulse(0) == (1.0, 28) and pulse(period) == (1.0, 28)                          # rest at both ends
    op, px = pulse(period // 2)
    assert abs(op - 0.3) < 1e-9 and px == 21                                              # the bottom
    sizes = {pulse(f)[1] for f in range(period)}
    assert sizes == set(range(21, 29))                                                    # every pre-scaled size is used, none else
    code = script_code()
    assert "dots[DOT_PX] = dot_image;" in code and "dot_image.Scale(s, s)" in code           # pre-scaled once; 28 is the PNG itself
    assert code.count(".Scale(") == 1 and "min_px" in code


def test_opacity_only_fallback_is_one_constant():
    """S_MIN = 1 makes min_px = 28, so no scaled image is ever made or used (only SetOpacity changes)."""
    src = (THEME / "memoryframe.script").read_text()
    assert re.search(r"^S_MIN\s*=\s*0\.75;.*OPACITY-ONLY", src, re.M)
    assert "for (s = min_px; s < DOT_PX; s++)" in script_code()
    assert int(28 * 1 + 0.5) == 28


def test_fast_settle_is_about_half_a_second():
    assert const("SETTLE_S") == 0.5
    code = script_code()
    assert "settle_frames = Math.Int(SETTLE_S * FPS + 0.5);" in code
    assert re.search(r'text == "memoryframe-settle" && mode == "pulse"\) \{ settle_from = cur_d;', code)


def test_bootwatch_sends_the_message_the_theme_listens_for():
    bw = (ROOT / "deploy" / "bootwatch.sh").read_text()
    assert "plymouth display-message --text=memoryframe-e4" in bw and '"$theme" = memoryframe' in bw
    assert '"memoryframe-e4"' in (THEME / "memoryframe.script").read_text()
    # the handoff sends settle, then quits keeping the splash (deploy/plymouth-handoff.sh)
    h = (ROOT / "deploy" / "plymouth-handoff.sh").read_text()
    assert "display-message --text=memoryframe-settle" in h and "quit --retain-splash" in h
    assert '"memoryframe-settle"' in (THEME / "memoryframe.script").read_text()


# ---------------- on ----------------

def test_on_backs_up_then_rebuilds_once_and_records_the_hashes(pl):
    orig = pl.initrd.read_bytes()
    cmd = (pl.boot / "cmdline.txt").read_bytes()
    r = pl.run("plymouth", "on")
    assert r.returncode == 0, r.stdout + r.stderr
    assert pl.theme() == "memoryframe"
    assert "usr/share/plymouth/themes/memoryframe/memoryframe.script" in pl.listing()
    baks = sorted(pl.boot.glob("initramfs8.memoryframe-bak.*"))
    assert len(baks) == 1 and baks[0].read_bytes() == orig                             # timestamped, the old bytes
    assert re.fullmatch(r"initramfs8\.memoryframe-bak\.\d{14}(-\d+)?", baks[0].name)
    cbaks = list(pl.conf.parent.glob("plymouthd.conf.memoryframe-bak.*"))
    assert len(cbaks) == 1 and cbaks[0].read_text() == CONF_ORIG
    rb = pl.rebuilds()
    assert rb == [f"update-initramfs -u -k {KERNEL} backups={baks[0].name}"]           # backup existed before it
    calls = pl.calls()
    assert calls.index("plymouth-set-default-theme memoryframe") < calls.index(rb[0])
    inst = pl.themes / "memoryframe"
    assert sorted(f.name for f in inst.iterdir()) == sorted(f.name for f in THEME.iterdir())
    for f in inst.iterdir():
        assert f.read_bytes() == (THEME / f.name).read_bytes() and (f.stat().st_mode & 0o777) == 0o644
    st = pl.st()
    assert st["plymouth_prev_theme"] == "pix" and st["plymouth_initramfs"] == "initramfs8"
    assert st["plymouth_kernel"] == KERNEL and st["plymouth_initramfs_bak"] == baks[0].name
    assert st["plymouth_sha_before"] == hashlib.sha256(orig).hexdigest()
    assert st["plymouth_sha_after"] == sh_sha(pl.initrd) != st["plymouth_sha_before"]
    assert st["plymouth_conf_bak"] == cbaks[0].name
    assert (pl.boot / "cmdline.txt").read_bytes() == cmd                                # cmdline.txt untouched
    assert "reboot" not in (r.stdout + r.stderr).lower().replace("next boot", "")


def test_second_on_does_not_rebuild_or_write(pl):
    assert pl.run("plymouth", "on").returncode == 0
    once = snapshot(pl.boot, pl.themes, pl.conf.parent, pl.state.parent)
    pl.log.unlink()
    r = pl.run("plymouth", "on")
    assert r.returncode == 0 and "no rebuild" in r.stdout
    assert pl.rebuilds() == [] and not any(" memoryframe" in c for c in pl.calls())
    assert snapshot(pl.boot, pl.themes, pl.conf.parent, pl.state.parent) == once


def test_changed_theme_files_rebuild_once_and_keep_the_first_backup(pl, tmp_path):
    assert pl.run("plymouth", "on").returncode == 0
    first = pl.st()
    dep = tmp_path / "repo" / "deploy"
    shutil.copytree(ROOT / "deploy", dep)
    s = dep / "plymouth" / "memoryframe" / "memoryframe.script"
    s.write_text(s.read_text() + "# v2\n")
    pl.log.unlink()
    r = pl.run("plymouth", "on", deploy=dep)
    assert r.returncode == 0, r.stdout + r.stderr
    assert len(pl.rebuilds()) == 1
    st = pl.st()
    assert st["plymouth_theme_sha"] != first["plymouth_theme_sha"]
    assert st["plymouth_prev_theme"] == "pix" and st["plymouth_initramfs_bak"] == first["plymouth_initramfs_bak"]
    assert st["plymouth_sha_before"] == first["plymouth_sha_before"]
    assert [b.name for b in pl.boot.glob("initramfs8.memoryframe-bak.*")] == [first["plymouth_initramfs_bak"]]
    assert (pl.themes / "memoryframe" / "memoryframe.script").read_text().endswith("# v2\n")
    pl.log.unlink()
    assert pl.run("plymouth", "on", deploy=dep).returncode == 0 and pl.rebuilds() == []   # once per hash


def test_a_kernel_update_that_dropped_the_theme_rebuilds(pl):
    assert pl.run("plymouth", "on").returncode == 0
    pl.initrd.write_text(pl.initrd.read_text().replace("themes/memoryframe/", "themes/gone/"))
    pl.log.unlink()
    r = pl.run("plymouth", "on")
    assert r.returncode == 0 and "rebuilding" in r.stdout and len(pl.rebuilds()) == 1
    assert "themes/memoryframe/memoryframe.script" in pl.listing()


FAILS = [("FAKE_UI", "fail", "update-initramfs failed"), ("FAKE_UI", "noop", "did not change"),
         ("FAKE_UI", "nomodules", "lost modules: vc4"), ("FAKE_UI", "noscript", "no plymouth script.so"),
         ("FAKE_UI", "huge", "more than 30 %"), ("FAKE_UI", "notheme", "does not list the memoryframe theme")]


@pytest.mark.parametrize("var,val,why", FAILS)
@pytest.mark.parametrize("conf", ["present", "absent"])
def test_every_failed_check_rolls_back_to_the_exact_old_state(pl, var, val, why, conf):
    if conf == "absent":
        pl.conf.unlink()
    before = snapshot(pl.boot, pl.themes, pl.conf.parent)
    r = pl.run("plymouth", "on", **{var: val})
    assert r.returncode == 1 and why in r.stderr and "rolling back" in r.stderr, r.stdout + r.stderr
    assert "rolled back" in r.stdout
    assert snapshot(pl.boot, pl.themes, pl.conf.parent) == before                    # initramfs, theme, conf, no baks
    assert pl.theme() == "pix" and not pl.state.exists()


def test_an_old_theme_update_failure_keeps_the_working_one(pl, tmp_path):
    assert pl.run("plymouth", "on").returncode == 0
    dep = tmp_path / "repo" / "deploy"
    shutil.copytree(ROOT / "deploy", dep)
    s = dep / "plymouth" / "memoryframe" / "memoryframe.script"
    s.write_text(s.read_text() + "# v2\n")
    before = snapshot(pl.boot, pl.themes, pl.conf.parent, pl.state.parent)
    r = pl.run("plymouth", "on", deploy=dep, FAKE_UI="nomodules")
    assert r.returncode == 1
    assert snapshot(pl.boot, pl.themes, pl.conf.parent, pl.state.parent) == before
    assert pl.theme() == "memoryframe"


def test_a_failed_backup_copy_check_changes_nothing(pl, tmp_path):
    dep = tmp_path / "repo" / "deploy"
    shutil.copytree(ROOT / "deploy", dep)
    h = dep / "boot-splash-plymouth.py"
    h.write_text(h.read_text().replace('    copy_synced(initrd, bak)\n',
                                       '    copy_synced(initrd, bak)\n    bak.write_text("torn")\n', 1))
    before = snapshot(pl.boot, pl.themes, pl.conf.parent)
    r = pl.run("plymouth", "on", deploy=dep)
    assert r.returncode == 1 and "does not match" in r.stderr
    assert snapshot(pl.boot, pl.themes, pl.conf.parent) == before and pl.rebuilds() == []


def _no_auto(p):
    (p.boot / "config.txt").write_text(CONFIG.replace("auto_initramfs=1", "#auto_initramfs=1"))


def _no_initrd(p):
    p.initrd.unlink()


def _foreign(p):
    (p.themes / "memoryframe").mkdir()
    (p.themes / "memoryframe" / "memoryframe.plymouth").write_text("[Plymouth Theme]\nName=someone else\n")


REFUSALS = [(_no_auto, {}, "no auto_initramfs=1"), (_no_initrd, {}, "no "),
            (None, {"MEMORYFRAME_KERNEL": "6.18.34+rpt-rpi-x86"}, "unknown kernel flavour"),
            (None, {"MEMORYFRAME_BOOT_FREE": "1000"}, "free on"),
            (None, {"PLYMOUTH_SET": "/nonexistent/plymouth-set-default-theme"}, "not installed"),
            (_foreign, {}, "not installed by this script"),
            (None, {"MEMORYFRAME_NO_PLYMOUTH": "1"}, "MEMORYFRAME_NO_PLYMOUTH=1")]


@pytest.mark.parametrize("prep,env,why", REFUSALS)
def test_refusals_write_nothing(pl, prep, env, why):
    if prep:
        prep(pl)
    before = snapshot(pl.boot, pl.themes, pl.conf.parent)
    r = pl.run("plymouth", "on", **env)
    assert r.returncode == 0 and why in r.stdout, r.stdout + r.stderr
    assert snapshot(pl.boot, pl.themes, pl.conf.parent) == before
    assert pl.rebuilds() == [] and not pl.state.exists()


def test_df_guard_is_three_times_the_initramfs(pl):
    size = pl.initrd.stat().st_size
    assert "free on" in pl.run("plymouth", "on", MEMORYFRAME_BOOT_FREE=str(3 * size - 1)).stdout
    assert pl.run("plymouth", "on", MEMORYFRAME_BOOT_FREE=str(3 * size)).returncode == 0
    assert pl.theme() == "memoryframe"


def test_a_theme_chosen_by_someone_else_later_is_left_alone(pl):
    assert pl.run("plymouth", "on").returncode == 0
    subprocess.run([pl.env["PLYMOUTH_SET"], "pix"], env=pl.env, check=True)
    pl.log.unlink()
    r = pl.run("plymouth", "on")
    assert r.returncode == 0 and "someone changed it" in r.stdout and pl.rebuilds() == []
    assert pl.theme() == "pix"


def test_missing_splash_is_reported_never_added(pl):
    line = HALE_CMDLINE.replace(" splash", "")
    (pl.boot / "cmdline.txt").write_text(line)
    r = pl.run("plymouth", "on")
    assert r.returncode == 0 and "has no `splash`" in r.stdout
    assert (pl.boot / "cmdline.txt").read_text() == line


def test_not_root_without_the_sandbox_hooks_is_refused(pl):
    env = dict(pl.env)
    env.pop("MEMORYFRAME_PLYMOUTH_DIR")
    if os.geteuid() == 0:
        pytest.skip("running as root")
    r = subprocess.run([sys.executable, str(ROOT / "deploy" / "boot-splash-plymouth.py"), "on"], env=env,
                       capture_output=True, text=True)
    assert r.returncode == 0 and "not root" in r.stdout


# ---------------- off ----------------

def test_off_restores_the_theme_conf_and_initramfs_and_removes_ours(pl):
    orig_initrd = pl.initrd.read_bytes()
    before = snapshot(pl.boot, pl.themes, pl.conf.parent)
    assert pl.run("plymouth", "on").returncode == 0
    r = pl.run("plymouth", "off")
    assert r.returncode == 0, r.stdout + r.stderr
    assert pl.theme() == "pix" and "themes/memoryframe" not in pl.listing()
    assert pl.initrd.read_bytes() == orig_initrd                                    # the fake rebuild is deterministic
    assert snapshot(pl.boot, pl.themes, pl.conf.parent) == before                   # no theme dir, no backups
    assert not pl.state.exists()
    pl.log.unlink()
    r2 = pl.run("plymouth", "off")
    assert r2.returncode == 0 and "nothing" not in r2.stderr and pl.rebuilds() == []


def test_off_keeps_other_state_keys(pl):
    pl.state.parent.mkdir(parents=True)
    pl.state.write_text("cmdline_added=quiet\n")
    assert pl.run("plymouth", "on").returncode == 0
    assert pl.run("plymouth", "off").returncode == 0
    assert pl.state.read_text() == "cmdline_added=quiet\n"


@pytest.mark.parametrize("mode", ["fail", "nomodules", "noop"])
def test_off_rebuild_failure_puts_ours_back_and_keeps_the_state(pl, mode):
    # #168 review: `off` is a transaction too: a failed rebuild puts back the initramfs from before the off (ours,
    # for this kernel) and our theme, keeps the state; a retry works
    orig = pl.initrd.read_bytes()
    assert pl.run("plymouth", "on").returncode == 0
    built = pl.initrd.read_bytes()
    conf = pl.conf.read_bytes()
    st = pl.st()
    r = pl.run("plymouth", "off", FAKE_UI=mode)
    assert r.returncode == 1 and "state kept" in r.stderr and "Traceback" not in r.stderr
    assert pl.initrd.read_bytes() == built and pl.theme() == "memoryframe" and pl.conf.read_bytes() == conf
    assert pl.st() == st and (pl.boot / st["plymouth_initramfs_bak"]).exists()
    assert sorted(b.name for b in pl.boot.glob("*.memoryframe-bak.*")) == [st["plymouth_initramfs_bak"]]
    assert pl.run("plymouth", "off").returncode == 0 and pl.initrd.read_bytes() == orig     # a retry works


def test_off_never_copies_the_first_backup_after_a_kernel_update(pl, tmp_path):
    # N1 (Hale B): on with K1, kernel update + theme change rebuilds for K2 (the first backup stays K1's), then a
    # failed off must not put the K1 initramfs over the K2 one
    assert pl.run("plymouth", "on").returncode == 0
    k1_bak = pl.st()["plymouth_initramfs_bak"]
    dep = tmp_path / "repo" / "deploy"
    shutil.copytree(ROOT / "deploy", dep)
    (dep / "plymouth" / "memoryframe" / "dot.png").write_bytes(b"v2")
    k2 = "6.18.40+rpt-rpi-v8"
    assert pl.run("plymouth", "on", deploy=dep, MEMORYFRAME_KERNEL=k2).returncode == 0
    st = pl.st()
    assert st["plymouth_initramfs_bak"] == k1_bak and st["plymouth_bak_kernel"] == KERNEL
    assert st["plymouth_kernel"] == k2
    r = pl.run("plymouth", "off", deploy=dep, MEMORYFRAME_KERNEL=k2, FAKE_UI="fail")
    assert r.returncode == 1
    assert pl.initrd.read_text().splitlines()[0] == f"FAKEINITRD {k2}"                # never the K1 image
    assert f"(kernel {KERNEL})" in pl.run("plymouth", "status").stdout


def test_rollback_refuses_a_backup_from_another_kernel(pl):
    # the pending record carries the backup's kernel: undoing it under another kernel never copies it
    assert pl.run("plymouth", "on").returncode == 0
    st = pl.st()
    pl.initrd.write_text("FAKEINITRD broken\n")
    st.update(plymouth_pending="on", plymouth_run_initramfs="initramfs8", plymouth_run_kernel="6.12.1+rpt-rpi-v8",
              plymouth_run_sha=st["plymouth_sha_before"], plymouth_run_bak=st["plymouth_initramfs_bak"],
              plymouth_run_prev="pix", plymouth_run_old_dir="keep", plymouth_run_first="0")
    pl.state.write_text("".join(f"{k}={v}\n" for k, v in sorted(st.items())))
    r = pl.run("plymouth", "on")
    assert r.returncode == 1 and "is for kernel 6.12.1+rpt-rpi-v8" in r.stderr and "Traceback" not in r.stderr
    assert pl.initrd.read_text() == "FAKEINITRD broken\n" and "plymouth_pending" in pl.st()


def test_off_with_someone_elses_rebuilt_theme_does_not_rebuild(pl):
    assert pl.run("plymouth", "on").returncode == 0
    subprocess.run([pl.env["PLYMOUTH_SET"], "pix"], env=pl.env, check=True)
    subprocess.run([pl.env["UPDATE_INITRAMFS"], "-u", "-k", KERNEL], env=pl.env, check=True)   # like -R pix
    pl.log.unlink()
    r = pl.run("plymouth", "off")
    assert r.returncode == 0 and pl.rebuilds() == []
    assert not (pl.themes / "memoryframe").exists() and not list(pl.boot.glob("*.memoryframe-bak.*"))


def test_off_rebuilds_when_the_initramfs_still_lists_ours(pl):
    # N2 (Hale E2): the theme is already pix (a failed or half-done off, or someone ran plymouth-set-default-theme
    # without -R) but initramfs8 still holds ours: `off` rebuilds with that theme before it forgets anything
    orig = pl.initrd.read_bytes()
    assert pl.run("plymouth", "on").returncode == 0
    subprocess.run([pl.env["PLYMOUTH_SET"], "pix"], env=pl.env, check=True)
    pl.log.unlink()
    r = pl.run("plymouth", "off")
    assert r.returncode == 0 and "still lists ours" in r.stdout and len(pl.rebuilds()) == 1
    assert "themes/memoryframe" not in pl.listing() and pl.initrd.read_bytes() == orig and not pl.state.exists()


def test_boot_splash_off_runs_plymouth_off_and_still_undoes_cmdline_on_failure(pl):
    assert pl.run("on").returncode == 0
    assert pl.run("plymouth", "on").returncode == 0
    assert "logo.nologo" in (pl.boot / "cmdline.txt").read_text()
    r = pl.run("off", FAKE_UI="fail")
    assert r.returncode == 1
    assert (pl.boot / "cmdline.txt").read_text() == HALE_CMDLINE                    # cmdline/config still undone
    assert pl.run("off").returncode == 0 and pl.theme() == "pix"


def test_status_lines(pl):
    assert pl.run("plymouth", "on").returncode == 0
    out = pl.run("plymouth", "status").stdout
    assert "plymouth theme: memoryframe (ours" in out and "cmdline splash: yes" in out


def test_helper_never_touches_the_mode_file_netplan_or_cmdline_and_never_reboots(pl):
    src = (ROOT / "deploy" / "boot-splash-plymouth.py").read_text()
    code = "\n".join(l for l in src.split('"""', 2)[2].splitlines())                    # after the docstring
    code = re.sub(r'"[^"\n]*"|\'[^\'\n]*\'', '""', code)
    for bad in ("reboot", "memoryframe-mode", "netplan", "nmcli"):
        assert bad not in code, bad
    uses = [l.strip() for l in src.split('"""', 2)[2].splitlines() if "cmdline.txt" in l]
    assert len(uses) == 2 and all(u.startswith(("return \"splash\" in (BOOT / \"cmdline.txt\").read_text",
                                                  "say(")) for u in uses), uses        # read once, mentioned once
    assert re.search(r'\(BOOT / "cmdline\.txt"\)\.read_text', src)
    mode = (pl.boot / "memoryframe-mode.txt").read_bytes()
    assert pl.run("plymouth", "on").returncode == 0 and pl.run("plymouth", "off").returncode == 0
    assert (pl.boot / "memoryframe-mode.txt").read_bytes() == mode
