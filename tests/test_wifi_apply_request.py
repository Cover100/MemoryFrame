"""#62 re-review: the wifi-apply unit's helper reads the app's request file itself (no StandardInput=file:,
which PID 1 opens as root and follows symlinks): regular files owned by the app user only, nofollow/nonblock."""
import os
import time
from pathlib import Path

from test_netmode_script import NEWPW, PENDING, HOT, env, run, active  # noqa: F401
from test_netmode_script import on_hotspot  # noqa: F401  (fixture)

ROOT = Path(__file__).resolve().parents[1]


def _req_env(env, tmp):
    d = tmp / "app-run"; d.mkdir(mode=0o700)
    req = d / "wifi-apply"
    env["env"]["MEMORYFRAME_APPLY_REQ"] = str(req)
    return req


def test_unit_has_no_standard_input_file_and_uses_request_flag():
    u = (ROOT / "deploy" / "units" / "memoryframe-wifi-apply.service").read_text()
    lines = [l for l in u.splitlines() if not l.startswith("#")]
    assert not any(l.startswith("StandardInput=") for l in lines)
    assert "ExecStart=/usr/local/sbin/memoryframe-netmode wifi-apply --request" in lines
    assert "iflag=nofollow,nonblock" in (ROOT / "deploy" / "netmode.sh").read_text()


def test_regular_request_file_is_applied_and_removed(on_hotspot):
    env = on_hotspot
    req = _req_env(env, env["tmp"])
    req.write_text(f"New Frame\n{NEWPW}\n")
    r = run(env, "wifi-apply", "--request")
    assert r.returncode == 0, r.stderr
    assert active(env) == PENDING and not req.exists()
    assert "n3w pass" not in r.stdout                       # stdout lands in 0644 /run/memoryframe/*.out


def test_symlink_rejected(on_hotspot, tmp_path):
    env = on_hotspot
    req = _req_env(env, env["tmp"])
    secret = tmp_path / "root-only"; secret.write_text(f"Leaked\n{NEWPW}\n")
    req.symlink_to(secret)
    r = run(env, "wifi-apply", "--request")
    assert r.returncode == 2 and active(env) == HOT
    assert "not a regular file" in r.stderr and not os.path.lexists(req) and secret.exists()


def test_fifo_rejected_without_hanging(on_hotspot):
    env = on_hotspot
    req = _req_env(env, env["tmp"])
    os.mkfifo(req)
    t = time.monotonic()
    r = run(env, "wifi-apply", "--request")                  # run() has a 30 s timeout
    assert time.monotonic() - t < 10
    assert r.returncode == 2 and active(env) == HOT


def test_missing_request_rejected(on_hotspot):
    env = on_hotspot
    _req_env(env, env["tmp"])
    assert run(env, "wifi-apply", "--request").returncode == 2 and active(env) == HOT


def test_password_never_in_stdout_of_any_verb(on_hotspot):
    env = on_hotspot
    outs = [run(env, "wifi-apply", input=f"New\n{NEWPW}\n").stdout]
    for v in ("wifi-rollback", "status"):
        outs.append(run(env, v).stdout)
    assert not any("n3w pass" in o for o in outs)
