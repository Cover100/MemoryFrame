"""MF-19: information for the on-device onboarding screens and long-press menu."""
import os
import shutil
import socket
import subprocess

from . import storage

VERSION = "0.2.0"     # Sprint 2


def hotspot_conf():
    """SSID/PASSWORD from deploy/hotspot.conf (git-ignored), overridable by env."""
    out = {}
    try:
        with open(os.path.join(storage.REPO_DIR, "deploy", "hotspot.conf")) as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    out[k.strip()] = v.strip().strip('"\'')
    except OSError:
        pass
    ssid = os.environ.get("MEMORYFRAME_SSID") or out.get("SSID")
    password = os.environ.get("MEMORYFRAME_WIFI_PASSWORD") or out.get("PASSWORD")
    return ssid, password


def _wifi_escape(s):
    out = []
    for ch in s:
        if ch in '\\;,:"':
            out.append("\\")
        out.append(ch)
    return "".join(out)


def wifi_qr_payload(ssid, password):
    """Standard Wi-Fi QR (ZXing format) understood by the Android camera."""
    return f"WIFI:T:WPA;S:{_wifi_escape(ssid)};P:{_wifi_escape(password)};;"


def show_wifi_qr(conn):
    """D3: on by default; disable with MEMORYFRAME_SHOW_WIFI_QR=0 or settings key show_wifi_qr=0."""
    from . import db
    env = os.environ.get("MEMORYFRAME_SHOW_WIFI_QR")
    if env is not None:
        return env.strip().lower() not in ("0", "false", "no", "off")
    return db.get_setting(conn, "show_wifi_qr", "1") != "0"


def ip_addresses():
    """IPv4 addresses of this machine (excluding loopback)."""
    try:
        out = subprocess.run(["hostname", "-I"], capture_output=True, text=True, timeout=2).stdout
        ips = [a for a in out.split() if "." in a and not a.startswith("127.")]
        if ips:
            return ips
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        return [a for a in socket.gethostbyname_ex(socket.gethostname())[2] if not a.startswith("127.")]
    except OSError:
        return []


def version():
    sha = None
    try:
        sha = subprocess.run(["git", "-C", storage.REPO_DIR, "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=2).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        pass
    return f"{VERSION} ({sha})" if sha else VERSION


def free_storage(data_dir):
    du = shutil.disk_usage(data_dir)
    return {"free": du.free, "total": du.total}
