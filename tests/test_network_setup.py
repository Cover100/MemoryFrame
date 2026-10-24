"""deploy/network-setup.sh (MF-10) against stub system commands (no real changes)."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT_DIR = Path(__file__).resolve().parents[1]
SCRIPT = ROOT_DIR / "deploy" / "network-setup.sh"
BASH = shutil.which("bash")
TOOLS = ["bash", "cat", "cmp", "mkdir", "install", "rm", "mktemp", "grep", "sed", "dirname", "tee"]
STUBS = ["hostnamectl", "hostname", "avahi-daemon", "apt-get", "systemctl", "raspi-config", "iw", "nmcli"]


@pytest.fixture
def env(tmp_path):
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    log = tmp_path / "calls.log"
    for name in STUBS:
        body = f'#!/bin/bash\necho "{name} $*" >> "{log}"\n'
        if name == "hostnamectl":
            body += f'[ "$1" = --static ] && cat "{tmp_path}/hostname"\n'
        (bin_ / name).write_text(body + "exit 0\n")
        (bin_ / name).chmod(0o755)
    tools = tmp_path / "tools"
    tools.mkdir()
    for name in TOOLS:
        (tools / name).symlink_to(shutil.which(name))
    root = tmp_path / "root"
    (root / "etc").mkdir(parents=True)
    (root / "etc" / "hosts").write_text("127.0.0.1\tlocalhost\n127.0.1.1\traspberrypi\n")
    (tmp_path / "hostname").write_text("raspberrypi\n")
    e = {"PATH": f"{bin_}:{tools}", "ROOT": str(root), "SUDO": "", "HOME": str(tmp_path)}
    return tmp_path, root, log, e


def run(e, *args):
    return subprocess.run([BASH, str(SCRIPT), *args], env=e, capture_output=True, text=True)


def test_apply_writes_dns_and_powersave(env):
    tmp_path, root, log, e = env
    r = run(e)
    assert r.returncode == 0, r.stderr
    dns = (root / "etc/NetworkManager/dnsmasq-shared.d/memoryframe.conf").read_text()
    assert "address=/memoryframe.lan/10.42.0.1" in dns
    assert "address=/#/" in dns                     # everything else NXDOMAIN
    assert "no-resolv" in dns
    ps = (root / "etc/NetworkManager/conf.d/memoryframe-wifi-powersave.conf").read_text()
    assert "wifi.powersave = 2" in ps
    assert "127.0.1.1\tmemoryframe" in (root / "etc/hosts").read_text()
    calls = log.read_text()
    assert "hostnamectl set-hostname memoryframe" in calls
    assert "raspi-config nonint do_wifi_country AU" in calls
    assert "systemctl enable --now avahi-daemon" in calls
    assert "nmcli general reload conf" in calls


def test_never_touches_connections(env):
    _, _, log, e = env
    run(e)
    run(e, "--remove")
    for line in log.read_text().splitlines():
        if line.startswith("nmcli"):
            assert line in ("nmcli general reload conf",), line   # no con up/down/modify


def test_idempotent(env):
    tmp_path, root, log, e = env
    run(e)
    (tmp_path / "hostname").write_text("memoryframe\n")
    r = run(e)
    assert r.returncode == 0
    assert r.stdout.count("unchanged:") == 2
    assert "already memoryframe" in r.stdout
    assert (root / "etc/hosts").read_text().count("127.0.1.1") == 1


def test_remove(env):
    _, root, _, e = env
    run(e)
    r = run(e, "--remove")
    assert r.returncode == 0
    assert not (root / "etc/NetworkManager/dnsmasq-shared.d/memoryframe.conf").exists()
    assert not (root / "etc/NetworkManager/conf.d/memoryframe-wifi-powersave.conf").exists()


DNSMASQ = shutil.which("dnsmasq") or shutil.which("dnsmasq", path="/usr/sbin:/sbin")
needs_dnsmasq = pytest.mark.skipif(DNSMASQ is None, reason="dnsmasq not installed")


@needs_dnsmasq
def test_dnsmasq_accepts_config(env):
    _, root, _, e = env
    run(e)
    conf = root / "etc/NetworkManager/dnsmasq-shared.d/memoryframe.conf"
    r = subprocess.run([DNSMASQ, "--test", f"--conf-file={conf}"], capture_output=True, text=True)
    assert "syntax check OK" in (r.stdout + r.stderr)


def test_service_binds_port_80_without_root():
    unit = (ROOT_DIR / "deploy" / "memoryframe.service").read_text()
    assert "Environment=MEMORYFRAME_PORTS=80,5000" in unit
    assert "AmbientCapabilities=CAP_NET_BIND_SERVICE" in unit
    assert "CapabilityBoundingSet=CAP_NET_BIND_SERVICE" in unit and "NoNewPrivileges=true" in unit
    assert "User=@USER@" in unit


@needs_dnsmasq
@pytest.mark.skipif(shutil.which("dig") is None, reason="dig not installed")
def test_dns_answers_lan_names_and_fast_nxdomain(env, tmp_path):
    """Run the real dnsmasq with our drop-in on a high port (as NM's shared mode would).

    Deflaked: the port used to be probed with a UDP socket only, then closed, and dnsmasq given 0.5 s.
    dnsmasq also listens on TCP on that port, so a TCP user of that ephemeral port (or a race with
    another process) made it exit with "Address already in use" (CI, pytest 3.13 on main d106318/d1dd670).
    Now the port must be free for UDP and TCP, an address-in-use exit retries on a new port, and the test
    waits until dnsmasq answers instead of sleeping a fixed time. The checks below are unchanged."""
    import socket
    import time
    _, root, _, e = env
    run(e)
    conf = root / "etc/NetworkManager/dnsmasq-shared.d/memoryframe.conf"

    def free_port():
        while True:
            u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            u.bind(("127.0.0.1", 0))
            port = u.getsockname()[1]
            t = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                t.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
            finally:
                t.close()
                u.close()

    def dig(name, timeout=2):
        t = time.monotonic()
        r = subprocess.run(["dig", "+tries=1", f"+time={timeout}", "-p", str(port), "@127.0.0.1", name, "A"],
                           capture_output=True, text=True)
        return r.stdout, time.monotonic() - t

    d = None
    for _attempt in range(5):
        port = free_port()
        d = subprocess.Popen([DNSMASQ, "--keep-in-foreground", "--no-resolv", "--no-hosts",
                              "--bind-interfaces", "--listen-address=127.0.0.1", f"--port={port}",
                              f"--conf-file={conf}", "--user=" + __import__("getpass").getuser(),
                              f"--pid-file={tmp_path}/dnsmasq.pid"],
                             stderr=subprocess.PIPE, text=True)
        deadline = time.monotonic() + 10
        ready = False
        while time.monotonic() < deadline and d.poll() is None:
            if "status: NOERROR" in dig("memoryframe.lan", timeout=1)[0]:
                ready = True
                break
            time.sleep(0.05)
        if ready:
            break
        if d.poll() is None:
            d.terminate()
            d.wait(timeout=5)
            pytest.fail("dnsmasq started but never answered within 10 s")
        err = d.stderr.read()
        assert "Address already in use" in err, err      # any other start failure is a real failure
    else:
        pytest.fail("dnsmasq: port in use on 5 attempts")
    try:
        assert d.poll() is None, d.stderr.read()

        out, _ = dig("memoryframe.lan")
        assert "status: NOERROR" in out and "10.42.0.1" in out
        out, took = dig("example.com")
        assert "status: NXDOMAIN" in out and took < 1.0
        out, _ = dig("connectivitycheck.gstatic.com")   # Android captive check: no redirect
        assert "status: NXDOMAIN" in out and "10.42.0.1" not in out
    finally:
        d.terminate()
        d.wait(timeout=5)
