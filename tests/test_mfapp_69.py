"""#69: the app runs as the system user mfapp from /opt/memoryframe. install.sh migrates (rollback pack, chown,
integrity check), writes units atomically, reloads once, waits for /healthz, and on failure runs ON_FAIL_CMD
(the migration rollback) before its one restart. deploy/memoryframe-migrate.sh is tested against stubs."""
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from test_install_never_arms_product import STUB, SUDO, run_install
from test_netmode_script import env  # noqa: F401  (fixture)

ROOT = Path(__file__).resolve().parents[1]
MIGRATE = ROOT / "deploy" / "memoryframe-migrate.sh"
RESTART = "sudo systemctl restart memoryframe.service"


def lines(log, prefix):
    return [ln for ln in log.splitlines() if ln.startswith(prefix)]


def fail_audit(app):
    (app / "deploy" / "audit-autostart.sh").write_text(STUB + "exit 1\n")


# ---------------- install.sh ----------------

def test_clean_install_order_one_reload_and_health_wait(env, tmp_path):
    r, log = run_install(tmp_path, env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert len(lines(log, "sudo systemctl daemon-reload")) == 1
    order = ["memoryframe-migrate save-pack", "memoryframe-migrate ensure-user", "systemctl stop memoryframe",
             "memoryframe-migrate chown-data", "memoryframe-cli check", "systemctl daemon-reload", RESTART, "curl -fsS"]
    pos = [log.index(o) for o in order]
    assert pos == sorted(pos), order
    assert "restarted and answering" in r.stdout
    assert "memoryframe-migrate rollback" not in log


def test_failure_after_chown_runs_rollback_before_the_one_restart(env, tmp_path):
    r, log = run_install(tmp_path, env, tweak=fail_audit)
    assert r.returncode == 1
    assert len(lines(log, RESTART)) == 1
    assert log.index("memoryframe-migrate chown-data") < log.index("memoryframe-migrate rollback") < log.index(RESTART)
    assert "rolled back" in r.stderr and "was restarted" in r.stderr


def test_unhealthy_after_normal_restart_rolls_back_and_restarts_once_more_then_stops(env, tmp_path):
    e = dict(env, env=dict(env["env"], STUB_CURL="7"))
    r, log = run_install(tmp_path, e)
    assert r.returncode == 1
    assert len(lines(log, RESTART)) == 2                     # normal + one from the trap, never a loop
    ls = log.splitlines()
    restarts = [i for i, l in enumerate(ls) if l.startswith(RESTART)]
    rb = [i for i, l in enumerate(ls) if "memoryframe-migrate rollback" in l]
    assert restarts[0] < rb[0] < restarts[1]                  # rollback before the trap's restart
    assert len(re.findall(r"memoryframe-migrate rollback", log)) == 1
    assert "not answering" in r.stderr and "rollback flag" in r.stderr
    assert "restarted and answering" not in r.stdout


def test_unhealthy_after_trap_restart_gets_exactly_one_extra_restart(env, tmp_path):
    e = dict(env, env=dict(env["env"], STUB_CURL="7"))
    r, log = run_install(tmp_path, e, tweak=fail_audit)
    assert r.returncode == 1
    assert len(lines(log, RESTART)) == 2 and len(re.findall(r"migrate rollback", log)) == 1
    assert "not answering" in r.stderr


def test_trap_never_daemon_reloads(env, tmp_path):
    r, log = run_install(tmp_path, env, tweak=fail_audit)
    assert r.returncode == 1 and not lines(log, "sudo systemctl daemon-reload")
    body = (ROOT / "deploy" / "install.sh").read_text().split("restart_if_stopped() {", 1)[1].split("\n}\n", 1)[0]
    assert "daemon-reload" not in body.replace("Never daemon-reload", "")


def test_reinstall_already_migrated_never_arms_rollback(env, tmp_path):
    # A data dir already owned by mfapp: a later failing install must not roll back to the pre-mfapp state.
    src = (ROOT / "deploy" / "install.sh").read_text()
    assert 'if [ "$(stat -c %U "$DATA_DIR" 2>/dev/null)" != "$APP_USER" ]' in src


def test_units_written_atomically_and_failed_render_keeps_old_file(tmp_path):
    src = (ROOT / "deploy" / "install.sh").read_text()
    assert "| sudo tee /etc/systemd" not in src and "tee /etc/polkit" not in src
    put = src.split("put_file() {", 1)[1].split("\n}\n", 1)[0]
    assert 'sudo install -o root -g "${4:-root}" -m "$3" "$1" "$new"' in put
    assert put.index("sync -f") < put.index("mv -f")
    # run render/put_file for real (sudo = passthrough) with a failing sed: the old unit stays byte-identical
    funcs = src.split("put_file() {", 1)[0][-1:] + "put_file() {" + src.split("put_file() {", 1)[1].split("# Bounded wait", 1)[0]
    unit = tmp_path / "memoryframe.service"
    unit.write_bytes(b"OLD UNIT\n")
    bin_ = tmp_path / "bin"; bin_.mkdir()
    (bin_ / "sudo").write_text('#!/bin/bash\n[ "$1" = install ] && set -- install -m "${@: -3:1}" "${@: -2}"\nexec "$@"\n')
    (bin_ / "sed").write_text("#!/bin/bash\necho partial; exit 4\n")
    for b in ("sudo", "sed"):
        (bin_ / b).chmod(0o755)
    script = f"set -euo pipefail\nAPP_USER=mfapp APP_DIR=/opt/memoryframe\n{funcs}\nrender /dev/null {unit} 0644\n"
    r = subprocess.run(["bash", "-c", script], env={"PATH": f"{bin_}:/usr/bin:/bin"}, capture_output=True, text=True)
    assert r.returncode != 0 and unit.read_bytes() == b"OLD UNIT\n"
    assert not list(tmp_path.glob(".memoryframe.service.new"))
    (bin_ / "sed").unlink()
    r = subprocess.run(["bash", "-c", script.replace("/dev/null", str(ROOT / "deploy" / "memoryframe.service"))],
                       env={"PATH": f"{bin_}:/usr/bin:/bin"}, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "User=mfapp" in unit.read_text() and "/opt/memoryframe/app.py" in unit.read_text()


def test_kiosk_runs_from_opt_and_stays_the_desktop_user():
    src = (ROOT / "deploy" / "install.sh").read_text()
    assert "APP_USER=mfapp" in src and 'pgrep}" -u "$DESKTOP_USER"' in src
    assert "--exclude=/deploy/hotspot.conf" in src and "--exclude=/.git" in src


def test_rollback_unit():
    u = (ROOT / "deploy" / "units" / "memoryframe-rollback.service").read_text()
    for want in ("DefaultDependencies=no", "Before=memoryframe.service", "Type=oneshot",
                 "ConditionPathExists=/boot/firmware/memoryframe-rollback.txt",
                 "ExecStart=/usr/local/sbin/memoryframe-migrate boot-flag"):
        assert want in u


def test_migrate_script_never_touches_forbidden_things():
    s = MIGRATE.read_text()
    code = "\n".join(l.split("#", 1)[0] for l in s.splitlines())
    for bad in ("memoryframe-mode.txt", "netplan", "cmdline.txt", "nmcli", "sshd", "rm -", "PASSWORD"):
        assert bad not in code, bad


# ---------------- memoryframe-migrate.sh against stubs ----------------

@pytest.fixture
def mig(tmp_path):
    bin_ = tmp_path / "bin"; bin_.mkdir()
    log = tmp_path / "log"
    for name in ("chown", "systemctl", "groupadd", "useradd"):
        (bin_ / name).write_text(f'#!/bin/bash\necho "{name} $*" >> "{log}"\n')
        (bin_ / name).chmod(0o755)
    # getent/id: mfapp exists once $T/user exists
    # (they print this test's own uid/gid, so the fchown in chown-data works unprivileged)
    (bin_ / "getent").write_text(f'#!/bin/bash\n[ -e "{tmp_path}/user" ] && echo "mfapp:x:{os.getgid()}:"\n')
    (bin_ / "id").write_text(f'#!/bin/bash\n[ -e "{tmp_path}/user" ] && echo {os.getuid()}\n')
    # find: the real one, but -user/-group mfapp would fail without the user: map to the current user
    me = subprocess.run(["id", "-un"], capture_output=True, text=True).stdout.strip()
    (bin_ / "find").write_text(f'#!/bin/bash\nargs=(); for a in "$@"; do [ "$a" = mfapp ] && a={me}; args+=("$a"); done\n'
                               f'[ "$*" == *chown* ] && {{ echo "find-chown" >> "{log}"; exit 0; }}\nexec /usr/bin/find "${{args[@]}}"\n')
    for b in ("getent", "id", "find"):
        (bin_ / b).chmod(0o755)
    d = {k: tmp_path / k for k in ("etc", "data", "units", "polkit", "boot", "backups")}
    for p in d.values():
        p.mkdir()
    (d["data"] / "originals").mkdir()
    (d["data"] / "originals" / "a.jpg").write_bytes(b"\xff\xd8photo\xff\xd9")
    (d["units"] / "memoryframe.service").write_text("User=memoryframe\n")
    (d["polkit"] / "50-memoryframe.rules").write_text("old rule\n")
    e = dict(os.environ, PATH=f"{bin_}:/usr/bin:/bin", MFM_ETC=str(d["etc"]), MFM_DATA=str(d["data"]),
             MFM_UNITS=str(d["units"]), MFM_POLKIT=str(d["polkit"]), MFM_BOOT=str(d["boot"]),
             MFM_BACKUPS=str(d["backups"]))

    def run(*args):
        r = subprocess.run(["bash", str(MIGRATE), *args], env=e, capture_output=True, text=True)
        return r, (log.read_text() if log.exists() else "")
    d["run"], d["tmp"] = run, tmp_path
    return d


def test_save_pack_once_with_photo_backup(mig):
    r, _ = mig["run"]("save-pack")
    assert r.returncode == 0, r.stderr
    pack = mig["etc"] / "rollback"
    assert (pack / "memoryframe.service").read_text() == "User=memoryframe\n"
    assert (pack / "50-memoryframe.rules").read_text() == "old rule\n"
    assert re.fullmatch(r"\d+:\d+\n", (pack / "previous-owner").read_text())
    tars = list(mig["backups"].glob("photos-before-mfapp-*.tar"))
    assert len(tars) == 1 and "originals/a.jpg" in subprocess.run(["tar", "-tf", tars[0]], capture_output=True, text=True).stdout
    assert oct(pack.stat().st_mode & 0o777) == "0o700"
    # second run (and after the unit changed): never overwritten, no second backup
    (mig["units"] / "memoryframe.service").write_text("User=mfapp\n")
    r, _ = mig["run"]("save-pack")
    assert "already saved" in r.stdout and (pack / "memoryframe.service").read_text() == "User=memoryframe\n"
    assert len(list(mig["backups"].glob("*.tar"))) == 1


def test_fresh_install_needs_no_pack(mig):
    (mig["units"] / "memoryframe.service").unlink()
    r, _ = mig["run"]("save-pack")
    assert r.returncode == 0 and "fresh install" in r.stdout and not (mig["etc"] / "rollback").exists()


def test_ensure_user_is_idempotent(mig):
    _, log = mig["run"]("ensure-user")
    assert "groupadd --system mfapp" in log
    assert "useradd --system --gid mfapp --no-create-home --home-dir" in log and "--shell /usr/sbin/nologin mfapp" in log
    (mig["tmp"] / "user").touch()
    open(mig["tmp"] / "log", "w").close()
    _, log = mig["run"]("ensure-user")
    assert log == ""


def test_chown_data_sets_modes_and_never_deletes(mig):
    (mig["tmp"] / "user").touch()
    (mig["data"] / "memoryframe.db-wal").write_bytes(b"wal")
    os.chmod(mig["data"] / "originals" / "a.jpg", 0o664)
    r, log = mig["run"]("chown-data")
    assert r.returncode == 0, r.stderr
    assert (mig["data"] / "memoryframe.db-wal").read_bytes() == b"wal"
    assert oct((mig["data"] / "originals" / "a.jpg").stat().st_mode & 0o777) == "0o640"
    assert oct((mig["data"] / "originals").stat().st_mode & 0o777) == "0o750"


def test_rollback_restores_unit_rule_owner_and_reloads(mig):
    mig["run"]("save-pack")
    (mig["units"] / "memoryframe.service").write_text("User=mfapp\n")
    (mig["polkit"] / "50-memoryframe.rules").write_text("new rule\n")
    r, log = mig["run"]("rollback", "--dry-run")
    assert r.returncode == 0 and "dry run" in r.stdout and "chown" not in log and "daemon-reload" not in log
    assert (mig["units"] / "memoryframe.service").read_text() == "User=mfapp\n"
    r, log = mig["run"]("rollback")
    assert r.returncode == 0, r.stderr
    assert (mig["units"] / "memoryframe.service").read_text() == "User=memoryframe\n"
    assert (mig["polkit"] / "50-memoryframe.rules").read_text() == "old rule\n"
    owner = (mig["etc"] / "rollback" / "previous-owner").read_text().strip()
    assert f"chown -R -h {owner} {mig['data']}" in log and "systemctl daemon-reload" in log
    assert "restart" not in log                                 # the caller restarts
    assert (mig["data"] / "originals" / "a.jpg").exists()


def test_rollback_without_pack_fails_cleanly(mig):
    r, log = mig["run"]("rollback")
    assert r.returncode == 1 and "no rollback pack" in r.stdout and log == ""


@pytest.mark.parametrize("content,real", [("rollback\n", True), ("DRY-RUN\r\n", False), ("yes please\n", False)])
def test_boot_flag_runs_once(mig, content, real):
    mig["run"]("save-pack")
    (mig["units"] / "memoryframe.service").write_text("User=mfapp\n")
    (mig["boot"] / "memoryframe-rollback.txt").write_text(content)
    r, log = mig["run"]("boot-flag")
    assert r.returncode == 0, r.stderr
    assert not (mig["boot"] / "memoryframe-rollback.txt").exists()
    assert len(list(mig["boot"].glob("memoryframe-rollback.done-*.txt"))) == 1
    assert ((mig["units"] / "memoryframe.service").read_text() == "User=memoryframe\n") == real
    assert ("systemctl restart --no-block memoryframe.service" in log) == real
    r, log2 = mig["run"]("boot-flag")                          # flag gone: nothing more
    assert r.returncode == 0 and log2 == log


def test_chown_data_never_follows_symlinks_and_tightens_before_chown(mig):
    (mig["tmp"] / "user").touch()
    outside = mig["tmp"] / "outside"; outside.write_text("x"); os.chmod(outside, 0o604)
    (mig["data"] / "link").symlink_to(outside)
    (mig["data"] / "dirlink").symlink_to(mig["tmp"] / "etc")
    r, _ = mig["run"]("chown-data")
    assert r.returncode == 0, r.stderr
    assert oct(outside.stat().st_mode & 0o777) == "0o604"            # target untouched
    assert oct((mig["tmp"] / "etc").stat().st_mode & 0o777) != "0o750"
    src = MIGRATE.read_text()
    assert "O_NOFOLLOW" in src and src.index("os.fchmod(fd") < src.index("os.fchown(fd")
    assert "chmod 0" not in src and "-exec chown" not in src


def test_boot_flag_with_quotes_is_renamed_and_ignored(mig):
    # Sage: xargs choked on a stray quote BEFORE the rename, so the unit failed on every boot.
    mig["run"]("save-pack")
    (mig["boot"] / "memoryframe-rollback.txt").write_text("rollback'\n")
    r, log = mig["run"]("boot-flag")
    assert r.returncode == 0, r.stderr
    assert not (mig["boot"] / "memoryframe-rollback.txt").exists() and "nothing done" in r.stdout
    assert "chown" not in log


def test_rollback_unit_verifies():
    u = ROOT / "deploy" / "units" / "memoryframe-rollback.service"
    directives = [l for l in u.read_text().splitlines() if l and not l.startswith("#")]
    for l in directives:                                            # no quoting in any directive
        assert '"' not in l and "'" not in l, l
    sa = shutil.which("systemd-analyze")
    if not sa:
        pytest.skip("systemd-analyze not installed (quoting check done)")
    r = subprocess.run([sa, "verify", "--man=no", str(u)], capture_output=True, text=True)
    # the ExecStart binary and memoryframe.service aren't installed here; any other complaint is a unit error
    bad = [l for l in (r.stdout + r.stderr).splitlines()
           if l.strip() and "memoryframe-migrate" not in l and "memoryframe.service" not in l]
    assert not bad, bad


# ---------------- app side ----------------

def test_cli_check(tmp_path):
    import sqlite3
    from memoryframe.__main__ import check
    db = tmp_path / "m.db"
    sqlite3.connect(db).execute("CREATE TABLE t(x)").connection.commit()
    assert check(str(db)) == 0
    db.write_bytes(b"SQLite format 3\x00" + b"\x00" * 200)
    assert check(str(db)) == 1
    assert check(str(tmp_path / "none.db")) == 0


def test_cli_wrapper_runs_as_mfapp():
    s = (ROOT / "deploy" / "memoryframe-cli.sh").read_text()
    assert "runuser -u mfapp --" in s and "/opt/memoryframe/.venv/bin/python -m memoryframe" in s


def test_version_from_file_and_hotspot_conf_env(tmp_path, monkeypatch):
    from memoryframe import frameinfo
    monkeypatch.setattr(frameinfo.storage, "REPO_DIR", str(tmp_path))
    (tmp_path / "VERSION").write_text("abc1234\n")
    assert frameinfo.version() == f"{frameinfo.VERSION} (abc1234)"
    conf = tmp_path / "hc"
    conf.write_text("SSID=Frame\nPASSWORD=pw-12345678\n")
    monkeypatch.setenv("MEMORYFRAME_HOTSPOT_CONF", str(conf))
    monkeypatch.delenv("MEMORYFRAME_SSID", raising=False)
    monkeypatch.delenv("MEMORYFRAME_WIFI_PASSWORD", raising=False)
    assert frameinfo.hotspot_conf() == ("Frame", "pw-12345678")
