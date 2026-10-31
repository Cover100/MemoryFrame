"""#62: run the root network helper without sudo. The app service keeps NoNewPrivileges; each verb is a
root oneshot unit (deploy/units) that polkit lets only the app user start (deploy/memoryframe-polkit.rules).
`systemctl start` on a oneshot waits for it and returns non-zero if it failed; its stdout is in
/run/memoryframe/<verb>.out. Tests set NET_HELPER (an argv the verb is appended to) instead."""
import logging
import os
import subprocess

log = logging.getLogger(__name__)

UNITS = {"dev-temp": "memoryframe-devtemp.service", "wifi-apply": "memoryframe-wifi-apply.service",
         "wifi-commit": "memoryframe-wifi-commit.service", "wifi-rollback": "memoryframe-wifi-rollback.service",
         "home-wifi": "memoryframe-home-wifi.service"}
OUT_DIR = "/run/memoryframe"
INPUT_DIR = "/run/memoryframe-app"          # the app's own RuntimeDirectory (0700)
POWEROFF_CMD = ["systemctl", "poweroff"]     # logind, allowed by the polkit rule


def run(config, verb, stdin=None, timeout=120):
    """Return (returncode, stdout). Raises nothing; 99 = could not run."""
    if verb not in UNITS:
        raise ValueError(verb)
    helper = config.get("NET_HELPER")
    try:
        if helper:
            r = subprocess.run(list(helper) + [verb], input=stdin, capture_output=True, text=True,
                               timeout=timeout)
            return r.returncode, (r.stdout or "") + (r.stderr or "")
        if stdin is not None:
            path = os.path.join(config.get("PRIV_INPUT_DIR", INPUT_DIR), verb)
            try:                         # #68: never write through a symlink or into a stale file
                os.unlink(path)
            except FileNotFoundError:
                pass
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(stdin)
        r = subprocess.run(["systemctl", "start", UNITS[verb]], capture_output=True, text=True, timeout=timeout)
        out = ""
        try:
            with open(os.path.join(config.get("PRIV_OUT_DIR", OUT_DIR), verb + ".out")) as f:
                out = f.read(4096)
        except OSError:
            pass
        return r.returncode, out + (r.stderr or "")
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning("network helper %s failed to run: %s", verb, type(e).__name__)
        return (124 if isinstance(e, subprocess.TimeoutExpired) else 99), ""
