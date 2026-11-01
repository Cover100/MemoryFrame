"""#97 (#110 nit): the '## mounts' section of deploy/sd-wear-capture.sh lists every path (one findmnt per path)."""
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASH = shutil.which("bash")


def test_mounts_section_lists_each_path(tmp_path):
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    (bin_ / "findmnt").write_text('#!/bin/bash\nfor a in "$@"; do last="$a"; done\n'
                                  '[ "$last" = /var/log ] && exit 1\necho "$last fs opts"\n')
    for n in ["systemctl", "pgrep", "journalctl", "swapon"]:
        (bin_ / n).write_text("#!/bin/bash\nexit 0\n")
    for f in bin_.iterdir():
        f.chmod(0o755)
    env = {"PATH": f"{bin_}:/usr/bin:/bin", "MEMORYFRAME_SD_DEV": "nosuchdev"}
    r = subprocess.run([BASH, str(ROOT / "deploy/sd-wear-capture.sh"), "T", str(tmp_path / "out")],
                       env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    text = (tmp_path / "out" / "T.txt").read_text()
    mounts = text.split("## mounts\n", 1)[1].split("## swap", 1)[0].splitlines()
    assert mounts == ["/ fs opts", "/boot/firmware fs opts", "/tmp fs opts", "/var/log (not a mount point)",
                      "/run/mfkiosk fs opts"]
