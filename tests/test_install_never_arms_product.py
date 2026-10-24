"""SAFETY (#40): install / re-install never arms or switches to product mode; a reboot after
install on a dev Pi keeps home Wi-Fi. install.sh runs hermetically (sudo, apt, systemctl,
python, chromium stubbed); the boot service is then simulated with the fake nmcli."""
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from test_netmode_script import HOME, SCRIPT, ac, active, env, mutations, run, state  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]

STUB = '#!/bin/bash\necho "$(basename "$0") $*" >> "$INSTALL_LOG"\n'
SUDO = STUB + '[[ " $* " == *" tee "* ]] && cat > /dev/null\nexit 0\n'
# `systemctl is-active` (unprivileged, called without sudo) answers from $STUB_ACTIVE; the log line is
# "systemctl is-active ..." so it is never counted as a privileged "sudo systemctl ..." call.
SYSTEMCTL = STUB + '[ "$1" = is-active ] && exit "${STUB_ACTIVE:-0}"\nexit 0\n'
PY3 = STUB + '''if [ "$1 $2" = "-m venv" ]; then mkdir -p "$3/bin"
  for b in pip python; do printf '#!/bin/bash\\necho "%s $*" >> "$INSTALL_LOG"\\n' "$b" > "$3/bin/$b"; chmod +x "$3/bin/$b"; done
fi
exit 0
'''


def run_install(tmp_path, envd, sudo=SUDO, tweak=None, active=True, **popen_kw):
    """Run the sandboxed install.sh; returns (CompletedProcess, stub call log). `tweak(app)` may edit the
    sandbox copy of the repo (used to force a failure); `sudo` replaces the sudo stub; `popen_kw` goes to subprocess.run (e.g. stderr); `active` is what the
    systemctl stub answers to `is-active` (was the service running before install.sh stopped it?)."""
    app = tmp_path / "app"
    if not app.exists():
        shutil.copytree(ROOT / "deploy", app / "deploy")
        shutil.copy(ROOT / "requirements.txt", app / "requirements.txt")
        # if install.sh ever ran the helper, we'd see it
        (app / "deploy" / "netmode.sh").write_text(STUB + "exit 0\n")
        if tweak:
            tweak(app)
    bin_ = tmp_path / "bin"
    bin_.mkdir(exist_ok=True)
    for name, body in (("sudo", sudo), ("systemctl", SYSTEMCTL), ("python3", PY3), ("chromium", STUB + "exit 0\n"),
                       ("crontab", STUB + "exit 1\n"), ("nmcli", STUB + "exit 0\n"), ("pkaction", STUB + "exit 0\n"),
                       ("curl", STUB + 'exit "${STUB_CURL:-0}"\n')):
        (bin_ / name).write_text(body)
        (bin_ / name).chmod(0o755)
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    log = tmp_path / "install.log"
    e = dict(envd["env"], PATH=f"{bin_}:/usr/bin:/bin", HOME=str(home), INSTALL_LOG=str(log),
             AUDIT_HOME=str(home), AUDIT_ROOT=str(tmp_path / "root"), SUDO="", CRONTAB=str(bin_ / "crontab"),
             USER="pi", XDG_SESSION_TYPE="wayland", STUB_ACTIVE="0" if active else "3",
             MEMORYFRAME_HEALTH_WAIT="2")
    e.pop("SUDO_USER", None)
    popen_kw.setdefault("stderr", subprocess.PIPE)
    r = subprocess.run(["bash", str(app / "deploy" / "install.sh")], env=e, stdout=subprocess.PIPE,
                       text=True, timeout=60, **popen_kw)
    return r, (log.read_text() if log.exists() else "")


def install(tmp_path, envd):
    r, log = run_install(tmp_path, envd)
    assert r.returncode == 0, r.stdout + r.stderr
    return log


def test_install_text_never_arms_product():
    src = (ROOT / "deploy" / "install.sh").read_text()
    # MF-51: the only mode-file write is the dev-Pi guard, and it only ever writes "dev"
    writes = re.findall(r"printf '([a-z]+)\\n' \| sudo tee \"\$MODE_FILE\"", src)
    assert writes == ["dev"] and 'if [ ! -e "$MODE_FILE" ]' in src
    assert not re.search(r"netmode(\.sh)?\s+(product|dev|boot)", src)
    assert not re.search(r"systemctl\s+(start|restart)\s+memoryframe-netmode", src)
    unit = (ROOT / "deploy" / "memoryframe-netmode.service").read_text()
    assert re.search(r"^ExecStart=\S+ boot$", unit, re.M)


def test_install_then_reboot_on_dev_pi_keeps_home_wifi(env, tmp_path):
    log = install(tmp_path, env)
    assert not re.search(r"^netmode\.sh ", log, re.M)        # helper never EXECUTED by install (copying is fine)
    assert not re.search(r"memoryframe-netmode (product|dev|boot)", log)
    assert "systemctl enable memoryframe-netmode.service" in log
    assert not re.search(r"systemctl (start|restart) memoryframe-netmode", log)
    assert re.search(r"tee \S*/memoryframe-mode\.txt", log)   # MF-51 guard (sudo is stubbed)
    assert mutations(env) == []
    (env["boot"] / "memoryframe-mode.txt").write_text("dev\n")  # what the guard writes
    # simulated reboot: the boot service runs `netmode.sh boot`
    r = run(env, "boot")
    assert r.returncode == 0, r.stderr
    assert active(env) == HOME and ac(env, HOME) == "yes" and mutations(env) == []
    assert state(env)["mode"] == "dev"
    assert "armed for next boot: dev" in run(env, "status").stdout


@pytest.mark.parametrize("existing", ["product\n", "dev\n", "", "  Product \r\n"])
def test_reinstall_never_changes_existing_mode_file(env, tmp_path, existing):
    f = env["boot"] / "memoryframe-mode.txt"
    f.write_bytes(existing.encode())
    before = os.stat(f).st_mtime_ns
    install(tmp_path, env)
    install(tmp_path, env)                                    # re-install (upgrade)
    assert f.read_bytes() == existing.encode() and os.stat(f).st_mtime_ns == before
