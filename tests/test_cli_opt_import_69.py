"""#69 Pi regression: `memoryframe-cli check` failed with "No module named memoryframe" because the
wrapper ran `python -m memoryframe` from /var/lib/memoryframe. Build a real /opt-like tree (copied code +
real venv) and run the wrapper's exact command, with only the root/runuser bits stripped, from another cwd."""
import os, shutil, subprocess, sys, venv
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]


def test_cli_wrapper_imports_package_from_opt_tree(tmp_path):
    opt, data, elsewhere = tmp_path / "opt", tmp_path / "data", tmp_path / "elsewhere"
    for d in (data, elsewhere):
        d.mkdir()
    shutil.copytree(ROOT / "memoryframe", opt / "memoryframe", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(ROOT / "app.py", opt / "app.py")
    venv.EnvBuilder(system_site_packages=True, with_pip=False).create(opt / ".venv")
    s = (ROOT / "deploy" / "memoryframe-cli.sh").read_text()
    s = s.replace('[ "$EUID" = 0 ] || exec sudo "$0" "$@"\n', "").replace("runuser -u mfapp -- ", "")
    s = s.replace("/opt/memoryframe", str(opt)).replace("/var/lib/memoryframe", str(data))
    assert "exec sudo" not in s and "runuser -u" not in s
    (tmp_path / "cli").write_text(s)
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME")}
    r = subprocess.run(["bash", str(tmp_path / "cli"), "check"], cwd=elsewhere, env=env,
                       capture_output=True, text=True, timeout=60)
    assert "No module named" not in r.stderr, r.stderr
    assert "database not found" in r.stderr                    # reached memoryframe's own check()
    assert not (data / "memoryframe.db").exists()
