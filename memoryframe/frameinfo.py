"""MF-19: information for the on-device onboarding screens and long-press menu."""
import logging
import os
import shutil
import socket
import subprocess

from . import storage

VERSION = "0.2.0"     # Sprint 2
log = logging.getLogger(__name__)


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


# ---------------- network-aware addresses (Tester bug at cb270ce) ----------------

HOTSPOT_IP = "10.42.0.1"
_cache = {"at": 0.0, "ips": []}
CACHE_SECONDS = 15


def own_ips(max_age=CACHE_SECONDS):
    """This machine's IPv4 addresses, cached briefly (used per request by the Host check)."""
    import time
    now = time.monotonic()
    if now - _cache["at"] > max_age:
        _cache["ips"] = ip_addresses()
        _cache["at"] = now
    return _cache["ips"]


def default_route_ip():
    """Source address the kernel would use for the default route (no packet is sent)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 9))          # TEST-NET-1, never actually contacted
        ip = s.getsockname()[0]
        return None if ip.startswith("127.") or ip == "0.0.0.0" else ip
    except OSError:
        return None
    finally:
        s.close()


def network():
    """{"mode": "hotspot"|"lan"|"unknown", "lan_ip": ...} detected at request time."""
    ips = own_ips()          # cached (15 s): the frame polls every 2 s, don't fork each time
    if HOTSPOT_IP in ips:
        return {"mode": "hotspot", "lan_ip": HOTSPOT_IP, "ips": ips}
    ip = default_route_ip() or (ips[0] if ips else None)
    return {"mode": "lan" if ip else "unknown", "lan_ip": ip, "ips": ips}


def served_ports():
    raw = os.environ.get("MEMORYFRAME_PORTS") or os.environ.get("MEMORYFRAME_PORT") or "5000"
    ports = []
    for p in raw.replace(" ", "").split(","):
        if p.isdigit():
            ports.append(int(p))
    return ports or [5000]


def port_suffix():
    ports = served_ports()
    return "" if 80 in ports else f":{ports[0]}"


_logged = set()


def _log_once(key, msg, *args):
    if key not in _logged:
        _logged.add(key)
        log.warning(msg, *args)


def validate_site_url(raw):
    """Return a clean "scheme://host[:port]" or None. http/https only, a real host, no
    whitespace/control characters, no credentials, query or fragment."""
    from urllib.parse import urlsplit
    raw = (raw or "").strip()
    if not raw or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in raw) or not raw.isascii():
        return None
    try:
        u = urlsplit(raw)
        u.port                                   # raises ValueError on a bad port
    except ValueError:
        return None
    if u.scheme.lower() not in ("http", "https") or not u.hostname or u.username or u.password \
            or u.query or u.fragment or u.path not in ("", "/"):
        return None
    return f"{u.scheme.lower()}://{u.netloc}"


def site_url_override():
    raw = os.environ.get("MEMORYFRAME_SITE_URL", "")
    if not raw.strip():
        return None
    url = validate_site_url(raw)
    if url is None:
        _log_once(("bad", raw), "MEMORYFRAME_SITE_URL=%r ignored: must be http(s)://host[:port]", raw)
        return None
    try:
        from urllib.parse import urlsplit
        from . import websec
        if not websec.allowed(urlsplit(url).netloc):
            _log_once(("host", url), "MEMORYFRAME_SITE_URL host %s is not in the Host allowlist; "
                      "add it with MEMORYFRAME_EXTRA_HOSTS or phones will get 400", urlsplit(url).hostname)
    except RuntimeError:                         # outside an app context
        pass
    return url


def site_urls(net=None):
    """Primary and fallback base URLs for phones on the frame's current network."""
    net = net or network()
    override = site_url_override()
    if override:          # e.g. http://frame.home - the network is still detected for the Wi-Fi QR
        return {"mode": net["mode"], "primary": override, "fallback": None}
    sfx = port_suffix()
    if net["mode"] == "hotspot":
        return {"mode": "hotspot", "primary": f"http://{HOTSPOT_IP}{sfx}", "fallback": f"http://memoryframe.lan{sfx}"}
    primary = f"http://memoryframe.local{sfx}"
    fallback = f"http://{net['lan_ip']}{sfx}" if net.get("lan_ip") else None
    return {"mode": net["mode"], "primary": primary, "fallback": fallback}
