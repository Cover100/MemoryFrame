"""#162: deploy/labwc-autostart.py merges ONE marked block (runs session-start.sh) at the top of the user's
~/.config/labwc/autostart: missing file created, existing bytes kept, twice = once, remove restores, backup, mode kept."""
import importlib.util
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "deploy" / "labwc-autostart.py"
CMD = "/opt/memoryframe/deploy/session-start.sh"
MARK = "memoryframe-managed boot-splash"


def run(*args):
    return subprocess.run(["python3", str(HELPER), *args], capture_output=True, text=True, timeout=30)


def test_missing_file_is_created_and_removed_again(tmp_path):
    f = tmp_path / "labwc" / "autostart"
    assert run("add", str(f), CMD).returncode == 0
    text = f.read_text()
    assert f"{CMD} &  # {MARK}" in text and text.count(f"&  # {MARK}") == 1
    assert oct(f.stat().st_mode & 0o777) == "0o644"
    assert run("add", str(f), CMD).returncode == 0 and f.read_text() == text     # idempotent
    assert run("remove", str(f)).returncode == 0
    assert not f.exists()                                                         # it was ours and is empty again


def test_existing_file_keeps_every_byte_and_gets_the_block_first(tmp_path):
    f = tmp_path / "autostart"
    before = "# my stuff\n/usr/bin/foo --bar &\n\nexport X=1"                    # no final newline
    f.write_text(before)
    f.chmod(0o750)
    r = run("add", str(f), CMD)
    assert r.returncode == 0 and "backup autostart.mf-bak" in r.stdout
    got = f.read_text()
    assert got.endswith(before) and got.splitlines()[1] == f"{CMD} &  # {MARK}"  # first command of the file
    assert oct(f.stat().st_mode & 0o777) == "0o750"
    assert (tmp_path / "autostart.mf-bak").read_text() == before
    mtime = f.stat().st_mtime_ns
    assert run("add", str(f), CMD).returncode == 0
    assert f.read_text() == got and f.stat().st_mtime_ns == mtime                  # not rewritten
    assert run("remove", str(f)).returncode == 0
    assert f.read_text() == before and oct(f.stat().st_mode & 0o777) == "0o750"  # byte-identical
    assert sorted(p.name for p in tmp_path.iterdir()) == ["autostart", "autostart.mf-bak"]   # no temp files


def test_new_path_updates_the_command_line_in_place(tmp_path):
    f = tmp_path / "autostart"
    f.write_text("/usr/bin/foo &\n")
    run("add", str(f), CMD)
    run("add", str(f), "/srv/mf/deploy/session-start.sh")
    got = f.read_text()
    assert got.count(MARK) == 2 and "/srv/mf/deploy/session-start.sh &" in got and CMD not in got
    run("remove", str(f))
    assert f.read_text() == "/usr/bin/foo &\n"


def test_remove_without_block_or_file_is_a_noop(tmp_path):
    f = tmp_path / "autostart"
    assert run("remove", str(f)).returncode == 0 and not f.exists()
    f.write_text("/usr/bin/foo &\n")
    assert run("remove", str(f)).returncode == 0 and f.read_text() == "/usr/bin/foo &\n"


def test_command_is_shell_quoted(tmp_path):
    spec = importlib.util.spec_from_file_location("la", HELPER)
    la = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(la)
    assert "'/opt/my frame/deploy/session-start.sh' &" in la.block("/opt/my frame/deploy/session-start.sh")


def test_block_is_ignored_by_the_autostart_audit(tmp_path):
    """audit-autostart.sh --disable must never comment our own line out."""
    home = tmp_path / "home"
    f = home / ".config" / "labwc" / "autostart"
    f.parent.mkdir(parents=True)
    run("add", str(f), CMD)
    before = f.read_text()
    e = dict(os.environ, AUDIT_HOME=str(home), AUDIT_ROOT=str(tmp_path / "root"), SUDO="", CRONTAB="false")
    r = subprocess.run(["bash", str(ROOT / "deploy" / "audit-autostart.sh"), "--disable"], env=e,
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0 and f.read_text() == before
