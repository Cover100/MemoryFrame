"""MF-49: late systemd-shutdown hook draws the committed 'Safe to unplug' image on fb0."""
import subprocess
from pathlib import Path

import pytest
from PIL import Image

D = Path(__file__).resolve().parents[1] / "deploy" / "safe-screen"
HOOK = D / "memoryframe-safe-screen"


def test_assets_match_spec():
    im = Image.open(D / "safe-to-unplug.png").convert("RGB")
    assert im.size == (800, 480)
    for xy in ((5, 5), (795, 475), (5, 475), (795, 5)):
        assert im.getpixel(xy) == (0x00, 0x6B, 0x2E)
    assert (255, 255, 255) in set(im.getdata())
    x32 = (D / "safe-to-unplug.xrgb8888").read_bytes()
    r16 = (D / "safe-to-unplug.rgb565").read_bytes()
    assert len(x32) == 800 * 480 * 4 and len(r16) == 800 * 480 * 2
    assert x32[:3] == bytes([0x2E, 0x6B, 0x00])                 # B, G, R
    v = (0x00 >> 3) << 11 | (0x6B >> 2) << 5 | (0x2E >> 3)
    assert r16[:2] == bytes([v & 0xFF, v >> 8])


def test_render_is_reproducible(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("render", D / "render.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    try:
        im = m.render()
    except OSError:
        pytest.skip("DejaVu fonts not installed")
    x32, _ = m.dumps(im)
    assert x32 == (D / "safe-to-unplug.xrgb8888").read_bytes()


def run(tmp_path, action, bpp="32", size="800,480"):
    sysd = tmp_path / "sys"
    sysd.mkdir(exist_ok=True)
    (sysd / "bits_per_pixel").write_text(bpp + "\n")
    (sysd / "virtual_size").write_text(size + "\n")
    fb = tmp_path / "fb0"
    fb.write_bytes(b"")
    r = subprocess.run(["sh", str(HOOK), action], env={"MF_FB": str(fb), "MF_FB_SYS": str(sysd),
                                                     "MF_ASSETS": str(D), "PATH": "/usr/bin:/bin"}, timeout=10)
    assert r.returncode == 0
    return fb.read_bytes()


@pytest.mark.parametrize("bpp,name", [("32", "safe-to-unplug.xrgb8888"), ("16", "safe-to-unplug.rgb565")])
def test_hook_writes_image_on_poweroff(tmp_path, bpp, name):
    assert run(tmp_path, "poweroff", bpp) == (D / name).read_bytes()
    assert run(tmp_path, "halt", bpp) == (D / name).read_bytes()


@pytest.mark.parametrize("action,bpp,size", [("reboot", "32", "800,480"), ("kexec", "32", "800,480"),
                                             ("poweroff", "24", "800,480"), ("poweroff", "32", "1024,600")])
def test_hook_skips_reboot_and_unknown_formats(tmp_path, action, bpp, size):
    assert run(tmp_path, action, bpp, size) == b""


def test_install_puts_hook_in_system_shutdown():
    s = (D.parent / "install.sh").read_text()
    assert "/usr/lib/systemd/system-shutdown/" in s and "memoryframe-safe-screen" in s
