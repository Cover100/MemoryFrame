"""#62 (Juniper's review): the app service keeps NoNewPrivileges and more; privileged actions are root
oneshot units that polkit lets only the app user start, plus logind poweroff. No sudoers rules."""
import re
import subprocess
from pathlib import Path

import pytest

from memoryframe import privileged

ROOT = Path(__file__).resolve().parents[1]
UNITS = ["devtemp", "wifi-apply", "wifi-commit", "wifi-rollback"]
VERB = {"devtemp": "dev-temp"}


def directives(text):
    return [l.strip() for l in text.splitlines() if l.strip() and not l.lstrip().startswith(("#", ";"))]


def test_app_service_hardening_restored_and_extended():
    d = directives((ROOT / "deploy" / "memoryframe.service").read_text())
    for want in ("NoNewPrivileges=true", "CapabilityBoundingSet=CAP_NET_BIND_SERVICE",
                 "AmbientCapabilities=CAP_NET_BIND_SERVICE", "RestrictSUIDSGID=true", "PrivateTmp=true",
                 "ProtectSystem=strict", "RuntimeDirectory=memoryframe-app", "RuntimeDirectoryMode=0700"):
        assert want in d, want
    rw = [l for l in d if l.startswith("ReadWritePaths=")]
    assert len(rw) == 1 and "/var/lib/memoryframe" in rw[0] and "@APP_DIR@/uploads" in rw[0]


def test_app_service_has_a_memory_guard():
    """#82: a huge decode must not starve the kiosk Chromium."""
    d = directives((ROOT / "deploy" / "memoryframe.service").read_text())
    assert "MemoryHigh=2G" in d and "MemoryMax=2500M" in d and "Environment=MALLOC_ARENA_MAX=1" in d


@pytest.mark.parametrize("u", UNITS)
def test_oneshot_units(u):
    text = (ROOT / "deploy" / "units" / f"memoryframe-{u}.service").read_text()
    d = directives(text)
    verb = VERB.get(u, u)
    extra = " --request" if u == "wifi-apply" else ""
    assert "Type=oneshot" in d and f"ExecStart=/usr/local/sbin/memoryframe-netmode {verb}{extra}" in d
    assert "[Install]" not in d and not any(l.startswith(("User=", "Environment=")) for l in d)
    assert f"StandardOutput=truncate:/run/memoryframe/{verb}.out" in d
    assert privileged.UNITS[verb] == f"memoryframe-{u}.service"
    if u == "wifi-apply":
        assert not any(l.startswith("StandardInput=") for l in d)   # helper reads it itself (nofollow)
        assert "ExecStopPost=/bin/rm -f /run/memoryframe-app/wifi-apply" in d
    else:
        assert not any(l.startswith("StandardInput=") for l in d)


def test_polkit_rule_only_app_user_four_units_start_and_poweroff():
    js = (ROOT / "deploy" / "memoryframe-polkit.rules").read_text()
    assert 'subject.user !== "@USER@"' in js and "return polkit.Result.NOT_HANDLED" in js
    assert 'action.lookup("verb") === "start"' in js
    assert sorted(re.findall(r'"(memoryframe-[a-z-]+\.service)"', js)) == sorted(f"memoryframe-{u}.service" for u in UNITS)
    ids = set(re.findall(r'"(org\.freedesktop\.[\w.-]+)"', js))
    assert ids == {"org.freedesktop.systemd1.manage-units", "org.freedesktop.login1.power-off",
                   "org.freedesktop.login1.power-off-multiple-sessions"}
    assert "reboot" not in js and "*" not in js.replace("/*", "")


def test_polkit_rule_logic_with_node():
    if subprocess.run(["which", "node"], capture_output=True).returncode:
        pytest.skip("node not installed")
    js = (ROOT / "deploy" / "memoryframe-polkit.rules").read_text().replace("@USER@", "mf")
    prog = ("var rule; var polkit = {Result: {YES: 'yes', NOT_HANDLED: 'nh'}, addRule: function(f){ rule = f; }};\n"
            + js + "\n"
            "function a(id, verb, unit){ return {id: id, lookup: function(k){ return k === 'verb' ? verb : unit; }}; }\n"
            "var M = 'org.freedesktop.systemd1.manage-units';\n"
            "var out = [rule(a(M,'start','memoryframe-devtemp.service'),{user:'mf'}),\n"
            "  rule(a(M,'stop','memoryframe-devtemp.service'),{user:'mf'}),\n"
            "  rule(a(M,'start','ssh.service'),{user:'mf'}),\n"
            "  rule(a(M,'start','memoryframe-devtemp.service'),{user:'eve'}),\n"
            "  rule(a('org.freedesktop.login1.power-off'),{user:'mf'}),\n"
            "  rule(a('org.freedesktop.login1.reboot'),{user:'mf'})];\n"
            "console.log(out.join(','));\n")
    r = subprocess.run(["node", "-e", prog], capture_output=True, text=True)
    assert r.stdout.strip() == "yes,nh,nh,nh,yes,nh", r.stderr


def test_no_sudo_left_in_the_app_or_deploy():
    assert not (ROOT / "deploy" / "memoryframe-sudoers").exists()
    assert not (ROOT / "deploy" / "memoryframe-poweroff-sudoers").exists()
    for f in (ROOT / "memoryframe").glob("*.py"):
        code = "\n".join(l.split("#", 1)[0] for l in f.read_text().splitlines())
        assert '"sudo"' not in code, f.name
    inst = (ROOT / "deploy" / "install.sh").read_text()
    assert "visudo" not in inst
    assert "sudo rm -f /etc/sudoers.d/memoryframe /etc/sudoers.d/memoryframe-poweroff" in inst
    assert "/etc/polkit-1/rules.d/50-memoryframe.rules" in inst
    for u in UNITS:
        assert f"memoryframe-{u}.service" in (ROOT / "deploy" / "uninstall.sh").read_text() or "$u" in inst
    assert "50-memoryframe.rules" in (ROOT / "deploy" / "uninstall.sh").read_text()


def test_poweroff_is_logind_without_sudo():
    from memoryframe import display
    assert display.POWEROFF_CMD == ["systemctl", "poweroff"]


def _fake_systemctl(tmp_path, monkeypatch, rc=0, out="uuid=abc-123\n"):
    bin_ = tmp_path / "bin"; bin_.mkdir()
    log = tmp_path / "calls"
    (bin_ / "systemctl").write_text(f"#!/bin/sh\necho \"$@\" >> {log}\n"
                                    f"cat {tmp_path}/in/wifi-apply >> {tmp_path}/seen 2>/dev/null\nexit {rc}\n")
    (bin_ / "systemctl").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_}:/usr/bin:/bin")
    (tmp_path / "in").mkdir(); (tmp_path / "out").mkdir()
    (tmp_path / "out" / "wifi-apply.out").write_text(out)
    return {"PRIV_INPUT_DIR": str(tmp_path / "in"), "PRIV_OUT_DIR": str(tmp_path / "out")}, log


def test_run_starts_unit_and_passes_input_via_private_file(tmp_path, monkeypatch):
    cfg, log = _fake_systemctl(tmp_path, monkeypatch)
    rc, text = privileged.run(cfg, "wifi-apply", stdin="Net\nsecret123\n")
    assert rc == 0 and "uuid=abc-123" in text
    assert log.read_text().split() == ["start", "memoryframe-wifi-apply.service"]   # no --no-block: wait for result
    assert (tmp_path / "seen").read_text() == "Net\nsecret123\n"
    assert (tmp_path / "in" / "wifi-apply").stat().st_mode & 0o077 == 0
    assert "secret123" not in log.read_text()


def test_run_reports_failure_and_rejects_unknown_verbs(tmp_path, monkeypatch):
    cfg, _ = _fake_systemctl(tmp_path, monkeypatch, rc=1)
    assert privileged.run(cfg, "wifi-commit")[0] == 1
    with pytest.raises(ValueError):
        privileged.run(cfg, "product")
