"""#97 (#110 nit): the '## mounts' section of deploy/sd-wear-capture.sh lists every path (one findmnt per path)."""
import os
import shutil

import pytest
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASH = shutil.which("bash")


def _bin(tmp_path):
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    (bin_ / "findmnt").write_text('#!/bin/bash\nfor a in "$@"; do last="$a"; done\n'
                                  '[ "$last" = /var/log ] && exit 1\necho "$last fs opts"\n')
    for n in ["systemctl", "pgrep", "journalctl", "swapon"]:
        (bin_ / n).write_text("#!/bin/bash\nexit 0\n")
    for f in bin_.iterdir():
        f.chmod(0o755)
    return bin_


def cap(tmp_path, label, out, uid=None):
    env = {"PATH": f"{_bin(tmp_path)}:/usr/bin:/bin", "MEMORYFRAME_SD_DEV": "nosuchdev",
           "MEMORYFRAME_SD_WEAR_OWNER_UID": str(os.getuid() if uid is None else uid)}
    return subprocess.run([BASH, str(ROOT / "deploy/sd-wear-capture.sh"), label, str(out)],
                          env=env, capture_output=True, text=True)


def test_mounts_section_lists_each_path(tmp_path):
    r = cap(tmp_path, "T", tmp_path / "out")
    assert r.returncode == 0, r.stderr
    text = (tmp_path / "out" / "T.txt").read_text()
    mounts = text.split("## mounts\n", 1)[1].split("## swap", 1)[0].splitlines()
    assert mounts == ["/ fs opts", "/boot/firmware fs opts", "/tmp fs opts", "/var/log (not a mount point)",
                      "/run/mfkiosk fs opts"]


def test_default_out_is_root_sd_wear():
    s = (ROOT / "deploy/sd-wear-capture.sh").read_text()
    assert 'OUT="${2:-/root/sd-wear}"' in s and "/tmp/sd-wear" not in s


def test_missing_out_is_created_0700(tmp_path):
    out = tmp_path / "a" / "out"
    assert cap(tmp_path, "S1-t0", out).returncode == 0
    assert (out.stat().st_mode & 0o777) == 0o700 and (out / "S1-t0.txt").exists()


def test_refuses_symlinked_out(tmp_path):
    real = tmp_path / "real"; real.mkdir()
    link = tmp_path / "link"; link.symlink_to(real)
    r = cap(tmp_path, "T", link)
    assert r.returncode == 2 and "not a real directory" in r.stderr and not list(real.iterdir())


def test_refuses_dangling_symlink_out(tmp_path):
    link = tmp_path / "link"; link.symlink_to(tmp_path / "nowhere")
    r = cap(tmp_path, "T", link)
    assert r.returncode == 2 and not (tmp_path / "nowhere").exists()


def test_refuses_out_that_is_a_file(tmp_path):
    f = tmp_path / "f"; f.write_text("x")
    assert cap(tmp_path, "T", f).returncode == 2


def test_refuses_out_owned_by_someone_else(tmp_path):
    out = tmp_path / "out"; out.mkdir()
    r = cap(tmp_path, "T", out, uid=os.getuid() + 1)
    assert r.returncode == 2 and "not owned by" in r.stderr and not list(out.iterdir())


@pytest.mark.parametrize("label", ["../x", "a/b", "", "a b", ".", "..", "x;rm", "\u00e9"])
def test_bad_labels_refused(tmp_path, label):
    out = tmp_path / "out"
    r = cap(tmp_path, label, out)
    assert r.returncode != 0 and not (out.exists() and list(out.iterdir()))
    assert not (tmp_path / "x.txt").exists()


@pytest.mark.parametrize("label", ["S1-t0", "S2_t1.b", "A9"])
def test_good_labels(tmp_path, label):
    assert cap(tmp_path, label, tmp_path / "out").returncode == 0


def test_runbook_paths_and_names():
    s = (ROOT / "docs/runbooks/sd-wear-audit.md").read_text()
    assert "/tmp/" not in s and "/root/sd-wear/iw.log" in s
    assert "Juniper" not in s and "Rowan" not in s and "Sage" in s and "Reed" in s
