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
    """SSID/PASSWORD from deploy/hotspot.conf (git-ignored), overridable by env. The env override is
    per key: MEMORYFRAME_SSID and MEMORYFRAME_WIFI_PASSWORD are each used on their own when set and
    non-empty, otherwise that key comes from the file. This is only the seed value for the hotspot:
    the NetworkManager profile MemoryFrame-hotspot holds what is broadcast."""
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
    """IPv4 addresses of this machine (excluding loopback), or None if they can't be read.

    `hostname -I` lists addresses in no particular order; in AP mode 10.42.0.1 may be the only
    one. Every token is validated, so garbage/IPv6/empty output never becomes an address.
    """
    import ipaddress
    try:
        res = subprocess.run(["hostname", "-I"], capture_output=True, text=True, timeout=2)
        if res.returncode == 0:
            ips = []
            for tok in (res.stdout or "").split():
                try:
                    a = ipaddress.ip_address(tok)
                except ValueError:
                    continue
                if a.version == 4 and not a.is_loopback and not a.is_unspecified and tok not in ips:
                    ips.append(tok)
            return ips
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        return [a for a in socket.gethostbyname_ex(socket.gethostname())[2] if not a.startswith("127.")]
    except OSError:
        return None


def version():
    sha = None
    try:
        sha = subprocess.run(["git", "-C", storage.REPO_DIR, "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=2).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        pass
    return f"{VERSION} ({sha})" if sha else VERSION


def free_storage(data_dir, min_free=None):
    du = shutil.disk_usage(data_dir)
    out = {"free": du.free, "total": du.total}
    if min_free is not None:
        out["almost_full"] = du.free < min_free          # #96: frame menu "Almost full"
    return out


# ---------------- network-aware addresses (Tester bug at cb270ce) ----------------

HOTSPOT_IP = "10.42.0.1"
_cache = {"at": None, "ips": []}
# MF-24: must be well under the 10 s the screen has to follow a hotspot switch.
CACHE_SECONDS = 5


def own_ips(max_age=None):
    """This machine's IPv4 addresses, cached briefly (the frame polls every 2 s and the Host
    check runs per request). A failed read is not cached: the last good value is returned and
    the next call tries again."""
    import time
    max_age = CACHE_SECONDS if max_age is None else max_age
    now = time.monotonic()
    if _cache["at"] is None or now - _cache["at"] >= max_age:
        ips = ip_addresses()
        if ips is not None:
            _cache["ips"] = ips
            _cache["at"] = now
    return list(_cache["ips"])


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


def _force_allowed():
    """MEMORYFRAME_FORCE_NETMODE is a test hook: only under pytest or a testing Flask app."""
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return True
    try:
        from flask import current_app
        return bool(current_app and current_app.testing)
    except RuntimeError:                                 # no app context
        return False


def _forced_network():
    """TESTS ONLY: MEMORYFRAME_FORCE_NETMODE=hotspot | lan:<IPv4> | unknown pins detection so
    browser tests (server in another thread) don't depend on the runner's own addresses.
    Ignored, with a warning, outside tests or when the value is invalid."""
    import ipaddress
    raw = os.environ.get("MEMORYFRAME_FORCE_NETMODE", "").strip()
    if not raw:
        return None
    if not _force_allowed():
        _log_once(("force-netmode", "not-test"), "MEMORYFRAME_FORCE_NETMODE is set but ignored (tests only)")
        return None
    if raw == "hotspot":
        return {"mode": "hotspot", "lan_ip": HOTSPOT_IP, "ips": [HOTSPOT_IP]}
    if raw == "unknown":
        return {"mode": "unknown", "lan_ip": None, "ips": []}
    if raw.startswith("lan:"):
        try:
            ip = ipaddress.IPv4Address(raw[4:])
            if not (ip.is_loopback or ip.is_unspecified or ip.is_multicast):
                return {"mode": "lan", "lan_ip": str(ip), "ips": [str(ip)]}
        except ValueError:
            pass
    _log_once(("force-netmode", raw), "ignoring invalid MEMORYFRAME_FORCE_NETMODE %r", raw)
    return None


def network():
    """{"mode": "hotspot"|"lan"|"unknown", "lan_ip": ...} detected at request time."""
    forced = _forced_network()
    if forced:
        return forced
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
    if url.startswith("https://"):
        # MF-24 (#31): the frame serves plain HTTP only; an https:// link fails on phones
        # ("null is unreachable" via NFC). QRs and links always use http://.
        _log_once(("https", url), "MEMORYFRAME_SITE_URL=%s uses https; the frame serves http only - using http", url)
        url = "http://" + url[len("https://"):]
    try:
        from urllib.parse import urlsplit
        from . import websec
        if not websec.allowed(urlsplit(url).netloc):
            _log_once(("host", url), "MEMORYFRAME_SITE_URL host %s is not in the Host allowlist; "
                      "add it with MEMORYFRAME_EXTRA_HOSTS or phones will get 400", urlsplit(url).hostname)
    except RuntimeError:                         # outside an app context
        pass
    return url


_BAD_HOSTS = {"", "null", "none", "undefined", "nan", "0.0.0.0"}


def _safe(url):
    """url if it is http(s)://<real host> - never 'null'/'None'/empty (MF-24) - else None."""
    from urllib.parse import urlsplit
    clean = validate_site_url(url) if isinstance(url, str) else None
    if clean is not None and clean.startswith("https://"):
        clean = "http://" + clean[len("https://"):]          # QRs/links are always http://
    if clean is None or (urlsplit(clean).hostname or "").lower() in _BAD_HOSTS:
        return None
    return clean


def default_base(mode):
    sfx = port_suffix()
    return f"http://{HOTSPOT_IP}{sfx}" if mode == "hotspot" else f"http://memoryframe.local{sfx}"


def site_urls(net=None):
    """Primary and fallback base URLs for phones on the frame's current network. Always returns
    a usable primary: hotspot -> http://10.42.0.1, otherwise http://memoryframe.local."""
    try:
        urls = _site_urls(net)
    except Exception:  # noqa: BLE001 - detection must never break the screen
        log.exception("network detection failed")
        urls = {"mode": "unknown", "primary": None, "fallback": None}
    mode = urls.get("mode") if urls.get("mode") in ("hotspot", "lan", "unknown") else "unknown"
    primary = _safe(urls.get("primary"))
    if primary is None:
        _log_once(("primary", repr(urls.get("primary"))), "unusable site URL %r; using default", urls.get("primary"))
        primary = default_base(mode)
    fallback = _safe(urls.get("fallback"))
    if fallback == primary:
        fallback = None
    return {"mode": mode, "primary": primary, "fallback": fallback}


def _site_urls(net=None):
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


# ---------------- MF-36: network mode (written by deploy/netmode.sh) ----------------

NETMODE_STATE = "/run/memoryframe/netmode.json"


def netmode_state():
    """What deploy/netmode.sh applied at boot: {"mode": "product"|"dev", "dev_fallback": bool,
    "hotspot_error": bool, "boot_id": str, "hotspot_ssid": str}. Missing/unreadable/unknown file -> DEV, no flags
    (safety: nothing assumes product mode unless the boot service applied it). Never contains secrets."""
    import json
    path = os.environ.get("MEMORYFRAME_NETMODE_STATE") or NETMODE_STATE
    out = {"mode": "dev", "dev_fallback": False, "hotspot_error": False, "boot_id": "", "reset": False,
           "hotspot_uuid": "", "wifi_profiles": False, "commit_recovered": False, "hotspot_ssid": ""}
    try:
        with open(path, "rb") as f:
            data = f.read(65536)
        # #89: one odd byte or control character in a field must not discard the whole state
        raw = json.loads(data.decode("utf-8", "replace"), strict=False)
    except (OSError, ValueError):
        return out
    if not isinstance(raw, dict):
        return out
    if raw.get("mode") == "product":
        out["mode"] = "product"
    out["dev_fallback"] = raw.get("dev_fallback") is True
    out["hotspot_error"] = raw.get("hotspot_error") is True and out["mode"] == "product"
    out["reset"] = raw.get("reset") is True
    out["temp"] = out["mode"] == "dev" and raw.get("requested") == "dev-temp"
    out["requested"] = raw.get("requested") if raw.get("requested") in ("product", "dev", "dev-temp") else ""   # MF-51 until reboot
    uid = raw.get("hotspot_uuid")
    out["hotspot_uuid"] = uid[:64] if isinstance(uid, str) else ""
    out["wifi_profiles"] = raw.get("wifi_profiles") is True
    out["commit_recovered"] = raw.get("commit_recovered") is True
    ssid = raw.get("hotspot_ssid")      # #89: SSID of the MemoryFrame-hotspot profile (not a secret)
    if isinstance(ssid, str) and 1 <= len(ssid.encode("utf-8", "replace")) <= 32 \
            and not any(ord(c) < 32 or 127 <= ord(c) < 160 for c in ssid):
        out["hotspot_ssid"] = ssid
    bid = raw.get("boot_id")
    out["boot_id"] = bid[:64] if isinstance(bid, str) else ""
    return out


def dev_mode():
    return netmode_state()["mode"] == "dev"
