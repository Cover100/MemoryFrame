"""#90: install.sh stops memoryframe.service early on. A failure before the normal restart must still
leave the service restarted, say what failed, and exit non-zero; a clean install restarts exactly once.
Runs install.sh hermetically (same stubs as test_install_never_arms_product)."""
import os
import re
import subprocess

import pytest

from test_install_never_arms_product import STUB, SUDO, run_install
from test_netmode_script import ac, active, env, mutations, run, state  # noqa: F401  (fixtures)

RESTART = "sudo systemctl restart memoryframe.service"


def fail_audit(app):
    """Force a failure after the stop and before the normal restart."""
    (app / "deploy" / "audit-autostart.sh").write_text(STUB + "exit 1\n")


def kill_self(sig):
    """Force a signal mid-install: the audit step signals its parent (install.sh), then succeeds."""
    def tweak(app):
        (app / "deploy" / "audit-autostart.sh").write_text(STUB + f"kill -{sig} $PPID\nexit 0\n")
    return tweak


def calls(log, what):
    return [ln for ln in log.splitlines() if ln.startswith(f"sudo systemctl {what} memoryframe.service")]


def test_clean_install_restarts_once_and_prints_no_trap_output(env, tmp_path):
    r, log = run_install(tmp_path, env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert len(calls(log, "stop")) == 1 and len(calls(log, "restart")) == 1
    assert "ERROR" not in r.stderr and "Restarting memoryframe.service" not in r.stderr


def test_failure_between_stop_and_restart_restarts_service_and_exits_nonzero(env, tmp_path):
    r, log = run_install(tmp_path, env, tweak=fail_audit)
    assert r.returncode == 1                                   # the original failure is not hidden
    assert len(calls(log, "stop")) == 1 and len(calls(log, "restart")) == 1
    assert log.index("systemctl stop") < log.index("systemctl restart")
    assert re.search(r"ERROR: install\.sh failed \(exit 1\) at line \d+: .*audit-autostart\.sh", r.stderr)
    assert "was restarted" in r.stderr
    assert "Done." not in r.stdout                             # the install did not carry on
    assert mutations(env) == []                                # never touches the mode


def test_failure_of_the_restart_itself_is_reported_without_looping(env, tmp_path):
    # The normal restart fails (set -e exits), then the trap's own restart fails too: one extra attempt, no loop.
    sudo = SUDO.replace('exit 0\n', '[[ " $* " == *" restart "* ]] && exit 7\nexit 0\n')
    r, log = run_install(tmp_path, env, sudo=sudo)
    assert r.returncode == 7                                   # the original failure's status
    assert len(calls(log, "restart")) == 2
    assert "ERROR: could not restart memoryframe.service" in r.stderr
    assert re.search(r"failed \(exit 7\) at line \d+: sudo systemctl restart", r.stderr)


def test_failure_before_the_stop_has_no_trap_output(env, tmp_path):
    # A failure before the service was stopped must not touch the service.
    sudo = SUDO.replace('exit 0\n', '[[ " $* " == *" install -d "* ]] && exit 5\nexit 0\n')
    r, log = run_install(tmp_path, env, sudo=sudo)
    assert r.returncode == 5
    assert not calls(log, "stop") and not calls(log, "restart")
    assert "Restarting memoryframe.service" not in r.stderr


def test_failure_after_the_restart_does_not_restart_again(env, tmp_path):
    sudo = SUDO.replace('exit 0\n', '[[ " $* " == *" rm -rf "* ]] && exit 9\nexit 0\n')
    r, log = run_install(tmp_path, env, sudo=sudo)
    assert r.returncode == 9
    assert len(calls(log, "restart")) == 1
    assert "Restarting memoryframe.service" not in r.stderr


def test_service_not_active_before_install_is_never_restarted_by_the_trap(env, tmp_path):
    # Fresh install or a service stopped on purpose: a failure must not start it, nor print "restarted".
    r, log = run_install(tmp_path, env, tweak=fail_audit, active=False)
    assert r.returncode == 1
    assert "systemctl is-active" in log and not calls(log, "restart")
    assert "restarted" not in r.stderr and "Restarting" not in r.stderr and "ERROR: install.sh failed" not in r.stderr


def test_clean_install_with_inactive_service_still_restarts_once_as_before(env, tmp_path):
    r, log = run_install(tmp_path, env, active=False)
    assert r.returncode == 0, r.stdout + r.stderr
    assert len(calls(log, "restart")) == 1 and "ERROR" not in r.stderr


@pytest.mark.parametrize("sig,code", [("INT", 130), ("TERM", 143), ("HUP", 129)])
def test_signal_mid_install_restarts_service_once(env, tmp_path, sig, code):
    r, log = run_install(tmp_path, env, tweak=kill_self(sig))
    assert r.returncode == code
    assert len(calls(log, "stop")) == 1 and len(calls(log, "restart")) == 1
    assert f"ERROR: install.sh was interrupted by a signal (exit {code})" in r.stderr
    assert "was restarted" in r.stderr
    assert "Done." not in r.stdout


def test_explicit_exit_without_err_record_says_unknown_step(env, tmp_path):
    # An ERR record from a different exit status must not be reported for this exit (no stale line/command).
    def tweak(app):
        script = app / "deploy" / "install.sh"
        script.write_text(script.read_text().replace(
            '"$APP_DIR/deploy/audit-autostart.sh" --disable', 'false || true; exit 4', 1))
    r, log = run_install(tmp_path, env, tweak=tweak)
    assert r.returncode == 4 and len(calls(log, "restart")) == 1
    assert "failed (exit 4) at an unknown step" in r.stderr


def test_trap_restart_is_capped_by_timeout(env, tmp_path):
    # `timeout 60` must wrap the trap's restart: stub timeout records its arguments and passes through.
    (tmp_path / "bin").mkdir(exist_ok=True)
    (tmp_path / "bin" / "timeout").write_text(STUB + 'shift; exec "$@"\n')
    (tmp_path / "bin" / "timeout").chmod(0o755)
    r, log = run_install(tmp_path, env, tweak=fail_audit)
    assert r.returncode == 1
    assert "timeout 60 sudo systemctl restart memoryframe.service" in log


def broken_pipe():
    """A stderr whose reader has gone: any write raises SIGPIPE/EPIPE (a dropped terminal or ssh session)."""
    rd, wr = os.pipe()
    os.close(rd)
    return wr


@pytest.mark.parametrize("how", ["dev-full", "closed-pipe"])
def test_unwritable_stderr_still_restarts_exactly_once(env, tmp_path, how):
    # F1: the trap used to die on its first message when stderr was gone, leaving the frame stopped.
    if how == "dev-full":
        r, log = run_install(tmp_path, env, tweak=fail_audit, stderr=open("/dev/full", "w"))
    else:
        r, log = run_install(tmp_path, env, tweak=fail_audit, stderr=broken_pipe())
    assert r.returncode == 1
    assert len(calls(log, "restart")) == 1


def test_sighup_with_broken_stderr_restarts_exactly_once(env, tmp_path):
    # The Pi Connect shell drops mid-install: SIGHUP, and nothing can be printed any more.
    r, log = run_install(tmp_path, env, tweak=kill_self("HUP"), stderr=broken_pipe())
    assert r.returncode == 129
    assert len(calls(log, "restart")) == 1


def test_sighup_while_the_trap_runs_does_not_stop_the_restart(env, tmp_path):
    # A hang-up delivered to the whole process group during the trap's restart: still one restart.
    sudo = SUDO.replace('exit 0\n', '[[ " $* " == *" restart "* ]] && kill -HUP 0\nexit 0\n')
    r, log = run_install(tmp_path, env, sudo=sudo, tweak=fail_audit, start_new_session=True)
    assert r.returncode == 1
    assert len(calls(log, "restart")) == 1
