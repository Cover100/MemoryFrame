"""#90: install.sh stops memoryframe.service early on. A failure before the normal restart must still
leave the service restarted, say what failed, and exit non-zero; a clean install restarts exactly once.
Runs install.sh hermetically (same stubs as test_install_never_arms_product)."""
import re

from test_install_never_arms_product import STUB, SUDO, run_install
from test_netmode_script import ac, active, env, mutations, run, state  # noqa: F401  (fixtures)

RESTART = "sudo systemctl restart memoryframe.service"


def fail_audit(app):
    """Force a failure after the stop and before the normal restart."""
    (app / "deploy" / "audit-autostart.sh").write_text(STUB + "exit 1\n")


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
    assert "memoryframe.service restarted" in r.stderr
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
