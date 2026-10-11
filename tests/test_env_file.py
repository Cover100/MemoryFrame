"""MF-13: deploy/env-file.sh writes MEMORYFRAME_EXTRA_HOSTS into /etc/default/memoryframe."""
import os
import pathlib
import subprocess

import pytest

ROOT_DIR = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = ROOT_DIR / "deploy" / "env-file.sh"


def run(root, *args):
    env = dict(os.environ, ROOT=str(root), SUDO="")
    return subprocess.run(["bash", str(SCRIPT), *args], env=env, capture_output=True, text=True)


def content(root):
    return (root / "etc/default/memoryframe").read_text()


def test_set_replace_remove(tmp_path):
    assert run(tmp_path, "MEMORYFRAME_EXTRA_HOSTS", "192.168.50.124").returncode == 0
    assert "MEMORYFRAME_EXTRA_HOSTS=192.168.50.124\n" in content(tmp_path)
    (tmp_path / "etc/default/memoryframe").write_text(content(tmp_path) + "OTHER=keep\n")
    assert run(tmp_path, "MEMORYFRAME_EXTRA_HOSTS", "pi.home,10.0.0.5").returncode == 0
    c = content(tmp_path)
    assert c.count("MEMORYFRAME_EXTRA_HOSTS=") == 1 and "=pi.home,10.0.0.5" in c and "OTHER=keep" in c
    assert run(tmp_path, "MEMORYFRAME_EXTRA_HOSTS", "").returncode == 0
    assert "MEMORYFRAME_EXTRA_HOSTS" not in content(tmp_path) and "OTHER=keep" in content(tmp_path)


@pytest.mark.parametrize("bad", ["a b", "x;rm -rf /", "$(id)", "h\nOTHER=1", "é"])
def test_rejects_unsafe_values(tmp_path, bad):
    r = run(tmp_path, "MEMORYFRAME_EXTRA_HOSTS", bad)
    assert r.returncode == 2 and not (tmp_path / "etc/default/memoryframe").exists()


def test_rejects_other_keys(tmp_path):
    assert run(tmp_path, "PATH", "/tmp").returncode == 2


def test_unit_reads_environment_file():
    unit = (ROOT_DIR / "deploy" / "memoryframe.service").read_text()
    assert "EnvironmentFile=-/etc/default/memoryframe" in unit
    assert "# Environment=MEMORYFRAME_EXTRA_HOSTS=" in unit


def test_install_accepts_flag():
    text = (ROOT_DIR / "deploy" / "install.sh").read_text()
    assert "--extra-hosts" in text and "env-file.sh\" MEMORYFRAME_EXTRA_HOSTS" in text


@pytest.mark.parametrize("good", ["http://memoryframe.local", "http://192.168.50.124:5000", "https://frame.home"])
def test_site_url_accepted(tmp_path, good):
    assert run(tmp_path, "MEMORYFRAME_SITE_URL", good).returncode == 0
    assert f"MEMORYFRAME_SITE_URL={good}\n" in content(tmp_path)


@pytest.mark.parametrize("bad", ["memoryframe.local", "http://x/pair", "javascript:alert(1)", "http://a b", "ftp://x"])
def test_site_url_rejected(tmp_path, bad):
    assert run(tmp_path, "MEMORYFRAME_SITE_URL", bad).returncode == 2


def test_max_megapixels_key(tmp_path):
    assert run(tmp_path, "MEMORYFRAME_MAX_MEGAPIXELS", "48").returncode == 0
    assert "MEMORYFRAME_MAX_MEGAPIXELS=48\n" in content(tmp_path)
    for bad in ("abc", "0", "-5", "64 ", "1e3", "99999"):
        assert run(tmp_path, "MEMORYFRAME_MAX_MEGAPIXELS", bad).returncode == 2
    assert run(tmp_path, "MEMORYFRAME_MAX_MEGAPIXELS", "").returncode == 0
    assert "MAX_MEGAPIXELS" not in content(tmp_path)


def test_display_size_key(tmp_path):
    assert run(tmp_path, "MEMORYFRAME_DISPLAY_SIZE", "1024x600").returncode == 0
    assert "MEMORYFRAME_DISPLAY_SIZE=1024x600\n" in content(tmp_path)
    for bad in ("800", "800x", "x600", "800X600", "0x600", "800x600x2", "800 x600", "abc", "8x6", "800x600;id"):
        assert run(tmp_path, "MEMORYFRAME_DISPLAY_SIZE", bad).returncode == 2, bad
    assert run(tmp_path, "MEMORYFRAME_DISPLAY_SIZE", "").returncode == 0
    assert "DISPLAY_SIZE" not in content(tmp_path)
