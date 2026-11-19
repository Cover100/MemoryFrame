"""Pairing and session auth (MF-12, single owner, D6).

Pairing
  The frame (localhost only) starts a pairing round: a 6-digit code and a random
  token for the QR, <base>/pair?t=<token>. <base> is PAIR_BASE_URL if set, else the
  site's primary address (frameinfo.site_urls(), see pairing._state()): MEMORYFRAME_SITE_URL
  if set, else http://10.42.0.1 on the frame's hotspot and http://memoryframe.local
  otherwise (plus :port when not served on 80); in temporary Developer Wi-Fi (dev-temp)
  http://<LAN IP> (frameinfo.lan_base(), only an address the Host check accepts). Both are single-use and
  expire after PAIR_TTL seconds. They live only in this process's memory
  (one waitress process); a restart simply needs a new round.
  5 wrong codes rotate the code. More than RATE_LIMIT attempts per minute from
  one address -> 429.
Sessions
  A successful pair creates a device + session. The browser gets a random token
  in an HttpOnly, SameSite=Strict cookie; only its SHA-256 is stored.
  #153: if the pairing request still carries a valid session cookie, that phone's
  device row is reused (its old session revoked, a new secret issued) instead of
  adding a second row. The code / QR token is still required; the device id only
  ever comes from the presented cookie, never from the request body. Other cookie
  jars (iOS sign-in pop-up vs Safari) and other hosts (10.42.0.1 vs
  memoryframe.local) send no cookie, so they still get a new row.
  "Sign out all phones" revokes every session.
  #154: Settings -> Paired phones lists the devices with a live session and removes
  ONE of them: all of that device's sessions are revoked (its cookie gets 401 and it
  must pair again with a code); other devices are untouched. Removing the phone in
  use also clears its cookie. Rows are kept, as with sign-out-all.
  Audit (journal, INFO): sign-out-all, per-device remove and #153 re-pair reuse log the
  device ids, how many sessions/devices, and the source. Never a cookie, token, hash or code.
"""
import hashlib
import hmac
import logging
import secrets
import threading
import time
from collections import defaultdict, deque
from functools import wraps

from flask import current_app, g, jsonify, redirect, request

from . import db

log = logging.getLogger("memoryframe.auth")

COOKIE = "mf_session"
PAIR_TTL = 120
MAX_WRONG = 5
RATE_LIMIT = 10           # attempts per RATE_WINDOW per address
RATE_WINDOW = 60
MAX_TRACKED = 1024       # addresses the rate limiter remembers
LAST_SEEN_EVERY = 60     # seconds between last_seen writes
SESSION_DAYS = 365
LABEL_MAX = 200          # stored device label (User-Agent), chars

clock = time.time         # patched in tests


def hash_token(token):
    return hashlib.sha256(token.encode()).hexdigest()


class Pairing:
    def __init__(self):
        self.lock = threading.Lock()
        self.code = self.token = None
        self.expires = 0.0
        self.wrong = 0
        self.attempts = defaultdict(deque)

    def _new(self):
        self.code = f"{secrets.randbelow(10**6):06d}"
        self.token = secrets.token_urlsafe(24)
        self.expires = clock() + PAIR_TTL
        self.wrong = 0

    def _clear(self):
        self.code = self.token = None
        self.expires = 0.0
        self.wrong = 0

    def start(self):
        with self.lock:
            self._new()
            return self.current()

    def current(self):
        if self.code is None or clock() >= self.expires:
            return None
        return {"code": self.code, "token": self.token, "expires_in": round(self.expires - clock(), 1)}

    def rate_limited(self, addr):
        """Sliding window per address; locked, and memory is bounded (MAX_TRACKED addresses)."""
        now = clock()
        with self.lock:
            for a in [a for a, q in self.attempts.items() if not q or q[-1] <= now - RATE_WINDOW]:
                del self.attempts[a]                 # forget idle addresses
            if addr not in self.attempts and len(self.attempts) >= MAX_TRACKED:
                return True                          # flood of addresses: refuse until they age out
            q = self.attempts[addr]
            while q and q[0] <= now - RATE_WINDOW:
                q.popleft()
            q.append(now)
            if len(q) > RATE_LIMIT + 1:
                q.popleft()                          # never more than RATE_LIMIT+1 timestamps
            return len(q) > RATE_LIMIT

    def redeem(self, code=None, token=None):
        """True if code or token matches the live round (which is then consumed)."""
        with self.lock:
            if self.current() is None:
                return False
            ok = (_valid_token(token) and hmac.compare_digest(token.encode(), self.token.encode())) or \
                 (_valid_code(code) and hmac.compare_digest(code.strip().encode(), self.code.encode()))
            if ok:
                self._clear()
                return True
            self.wrong += 1
            if self.wrong >= MAX_WRONG:
                self._new()          # rotate; the frame picks up the new code
            return False


def _valid_code(code):
    """Exactly 6 ASCII digits (rejects ints, non-ASCII digits such as Arabic-Indic)."""
    if not isinstance(code, str):
        return False
    c = code.strip()
    return len(c) == 6 and c.isascii() and c.isdigit()


def _valid_token(token):
    return isinstance(token, str) and 0 < len(token) <= 100 and token.isascii()


def pairing(app=None):
    app = app or current_app
    return app.extensions.setdefault("mf_pairing", Pairing())


def device_label(user_agent):
    """Printable ASCII only, whitespace collapsed, at most LABEL_MAX chars (#154: 200, was 80, which cut a
    phone User-Agent before its browser token; device_display needs it). Display only, never an identity key."""
    text = "".join(ch if ch.isascii() and ch.isprintable() else " " for ch in str(user_agent or ""))
    text = " ".join(text.split())[:LABEL_MAX]
    return text or "phone"


_OS = (("iPhone", "iPhone"), ("iPad", "iPad"), ("Android", "Android"), ("CrOS", "Chromebook"),
       ("Windows", "Windows"), ("Macintosh", "Mac"), ("Linux", "Linux"))
_BROWSERS = (("SamsungBrowser/", "Samsung Internet"), ("Edg", "Edge"), ("Firefox/", "Firefox"),
             ("FxiOS/", "Firefox"), ("CriOS/", "Chrome"), ("Chrome/", "Chrome"), ("Safari/", "Safari"))


# Without a browser token (labels stored before #154 were cut at 80 chars): the OS version, where the UA has a
# reliable one. Android is omitted: Chrome's reduced UA freezes "Android 10; K" on every Android, so a version
# would be wrong; those rows show plain "Android".
_OS_VERSION = (("iPhone", r"iPhone OS (\d+)", "iPhone · iOS {}"), ("iPad", r"CPU OS (\d+)", "iPad · iPadOS {}"))
DEVICE_HINTS = {"ipad": "iPad"}   # the only hint the pair page sends (#154 follow-up)


def device_display(name):
    """#154: a short "iPhone · Safari" from the stored User-Agent label (display only, never an identity key)."""
    import re
    name = str(name or "")
    os_ = next((label for key, label in _OS if key in name), None)
    browser = next((label for key, label in _BROWSERS if key in name), None)
    if os_ and browser:
        return f"{os_} · {browser}"
    for label, pattern, fmt in _OS_VERSION:
        m = re.search(pattern, name) if os_ == label else None
        if m:
            return fmt.format(m.group(1))
    return os_ or "Phone"


def hinted_name(user_agent, hint):
    """#154 follow-up: iPadOS Safari sends a Mac User-Agent; the pair page adds "ipad" when the browser has a
    touch screen. Accepted only as that exact string and only with a Macintosh UA, and it only changes the
    stored display label (device_label caps it). It never picks a device or affects auth."""
    ua = str(user_agent or "")
    if isinstance(hint, str) and hint in DEVICE_HINTS and "Macintosh" in ua and "iPad" not in ua:
        return f"{DEVICE_HINTS[hint]}; {ua}"
    return ua


def _date(ts):
    t = time.localtime(ts)
    return f"{t.tm_mday} {time.strftime('%b', t)} {t.tm_year}"


def seen_ago(ts, now=None):
    if ts is None:
        return "never"
    d = max(0, (db.now() if now is None else now) - ts)
    if d < 120:
        return "just now"
    if d < 3600:
        return f"{int(d // 60)} min ago"
    if d < 86400:
        return f"{int(d // 3600)} h ago"
    if d < 7 * 86400:
        n = int(d // 86400)
        return f"{n} day{'s' if n > 1 else ''} ago"
    return "on " + _date(ts)


def paired_devices(conn, current=None):
    """#154: devices with a live session (the ones paired_count counts), oldest first."""
    rows = conn.execute(
        "SELECT d.id, d.name, d.created_at, d.last_seen FROM devices d WHERE EXISTS (SELECT 1 FROM sessions s "
        "WHERE s.device_id = d.id AND s.revoked = 0 AND s.expires_at > ?) ORDER BY d.created_at, d.id",
        (db.now(),)).fetchall()
    return [{"id": r["id"], "label": device_display(r["name"]), "paired": _date(r["created_at"]),
             "seen": seen_ago(r["last_seen"]), "current": r["id"] == current} for r in rows]


def _ids(ids):
    ids = sorted(int(i) for i in ids)
    return ", ".join(map(str, ids[:20])) + (f" (+{len(ids) - 20} more)" if len(ids) > 20 else "") if ids else "none"


def revoke_device(conn, device_id, source="Settings remove"):
    """#154: revoke every unrevoked session of ONE device. None if there is no such device, else the number
    revoked (0: it was already signed out, e.g. a second tap). Other devices are never touched."""
    if conn.execute("SELECT 1 FROM devices WHERE id = ?", (device_id,)).fetchone() is None:
        log.info("remove device (%s): no device %d; nothing revoked", source, device_id)
        return None
    n = conn.execute("UPDATE sessions SET revoked = 1 WHERE device_id = ? AND revoked = 0", (device_id,)).rowcount
    conn.commit()
    log.info("remove device (%s): device %d, %d session(s) revoked", source, device_id, n)
    return n


def create_session(conn, device_name, device_id=None):
    """New session; returns the raw token for the cookie (only its hash is stored).

    device_id None: a new devices row. Otherwise (#153 re-pair, the id from current_device()):
    that row is reused, its unrevoked sessions are revoked, and its label and last_seen refreshed,
    all in one transaction. Other devices are never touched."""
    now = db.now()
    label = device_label(device_name)
    if device_id is None:
        device_id = conn.execute("INSERT INTO devices (name, created_at, last_seen) VALUES (?, ?, ?)",
                                 (label, now, now)).lastrowid
    else:
        n = conn.execute("UPDATE sessions SET revoked = 1 WHERE device_id = ? AND revoked = 0", (device_id,)).rowcount
        conn.execute("UPDATE devices SET name = ?, last_seen = ? WHERE id = ?", (label, now, device_id))
        log.info("pair (re-pair): device %d reused, %d old session(s) revoked", device_id, n)
    token = secrets.token_urlsafe(32)
    conn.execute("INSERT INTO sessions (device_id, token_hash, created_at, expires_at) VALUES (?, ?, ?, ?)",
                 (device_id, hash_token(token), now, now + SESSION_DAYS * 86400))
    conn.commit()
    return token


def set_session_cookie(resp, token):
    # No Secure flag: the frame is plain HTTP on its own hotspot (HTTPS is out of scope),
    # and a Secure cookie would never be sent back.
    resp.set_cookie(COOKIE, token, max_age=SESSION_DAYS * 86400, httponly=True,
                    samesite="Strict", path="/")
    return resp


def clear_session_cookie(resp):
    """Same attributes as set_session_cookie, so the browser really drops it."""
    resp.set_cookie(COOKIE, "", max_age=0, expires=0, httponly=True, samesite="Strict", path="/")
    return resp


def current_device():
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    conn = db.get_db()
    # Join last_seen so we can skip the write while it is still fresh. A no-op UPDATE
    # still opens a SQLite write transaction (commit or rollback), which storms the WAL
    # under frequent admin/status polling (#130 / #97 S2).
    row = conn.execute(
        "SELECT s.device_id, d.last_seen FROM sessions s "
        "JOIN devices d ON d.id = s.device_id "
        "WHERE s.token_hash = ? AND s.revoked = 0 AND s.expires_at > ?",
        (hash_token(token), db.now())).fetchone()
    if row is None:
        return None
    now = db.now()
    if row["last_seen"] is not None and row["last_seen"] >= now - LAST_SEEN_EVERY:
        return row["device_id"]
    conn.execute("UPDATE devices SET last_seen = ? WHERE id = ?", (now, row["device_id"]))
    conn.commit()
    return row["device_id"]


def paired_count(conn):
    return conn.execute("SELECT COUNT(DISTINCT device_id) FROM sessions WHERE revoked = 0 "
                        "AND expires_at > ?", (db.now(),)).fetchone()[0]


def sign_out_all(conn, source="unknown"):
    ids = [r[0] for r in conn.execute("SELECT DISTINCT device_id FROM sessions WHERE revoked = 0")]
    n = conn.execute("UPDATE sessions SET revoked = 1 WHERE revoked = 0").rowcount
    conn.commit()
    log.info("sign out all (%s): %d session(s) on %d device(s) revoked; devices %s", source, n, len(ids), _ids(ids))
    return n


def require_session(kind):
    """before_request hook: admin -> 302 to /pair, api -> 401 JSON."""
    def hook():
        g.device_id = current_device()
        if g.device_id is None:
            if kind == "api":
                return jsonify({"error": "not paired", "pair": "/pair"}), 401
            return redirect("/pair", code=302)
        return None
    return hook


def login_required(view):           # for single routes outside admin/api
    @wraps(view)
    def wrapped(*a, **kw):
        if current_device() is None:
            return jsonify({"error": "not paired"}), 401
        return view(*a, **kw)
    return wrapped
