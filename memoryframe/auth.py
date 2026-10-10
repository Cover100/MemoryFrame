"""Pairing and session auth (MF-12, single owner, D6).

Pairing
  The frame (localhost only) starts a pairing round: a 6-digit code and a random
  token for the QR (http://10.42.0.1/pair?t=<token>). Both are single-use and
  expire after PAIR_TTL seconds. They live only in this process's memory
  (one waitress process); a restart simply needs a new round.
  5 wrong codes rotate the code. More than RATE_LIMIT attempts per minute from
  one address -> 429.
Sessions
  A successful pair creates a device + session. The browser gets a random token
  in an HttpOnly, SameSite=Strict cookie; only its SHA-256 is stored.
  "Sign out all phones" revokes every session.
"""
import hashlib
import hmac
import secrets
import threading
import time
from collections import defaultdict, deque
from functools import wraps

from flask import current_app, g, jsonify, redirect, request

from . import db

COOKIE = "mf_session"
PAIR_TTL = 120
MAX_WRONG = 5
RATE_LIMIT = 10           # attempts per RATE_WINDOW per address
RATE_WINDOW = 60
MAX_TRACKED = 1024       # addresses the rate limiter remembers
LAST_SEEN_EVERY = 60     # seconds between last_seen writes
SESSION_DAYS = 365

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
    """Printable ASCII only, whitespace collapsed, at most 80 chars."""
    text = "".join(ch if ch.isascii() and ch.isprintable() else " " for ch in str(user_agent or ""))
    text = " ".join(text.split())[:80]
    return text or "phone"


def create_session(conn, device_name):
    now = db.now()
    cur = conn.execute("INSERT INTO devices (name, created_at, last_seen) VALUES (?, ?, ?)",
                       (device_label(device_name), now, now))
    token = secrets.token_urlsafe(32)
    conn.execute("INSERT INTO sessions (device_id, token_hash, created_at, expires_at) VALUES (?, ?, ?, ?)",
                 (cur.lastrowid, hash_token(token), now, now + SESSION_DAYS * 86400))
    conn.commit()
    return token


def set_session_cookie(resp, token):
    resp.set_cookie(COOKIE, token, max_age=SESSION_DAYS * 86400, httponly=True,
                    samesite="Strict", path="/")
    return resp


def current_device():
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    conn = db.get_db()
    row = conn.execute(
        "SELECT s.id, s.device_id FROM sessions s WHERE s.token_hash = ? AND s.revoked = 0 "
        "AND s.expires_at > ?", (hash_token(token), db.now())).fetchone()
    if row is None:
        return None
    now = db.now()
    cur = conn.execute("UPDATE devices SET last_seen = ? WHERE id = ? AND "
                       "(last_seen IS NULL OR last_seen < ?)",
                       (now, row["device_id"], now - LAST_SEEN_EVERY))
    if cur.rowcount:
        conn.commit()
    return row["device_id"]


def paired_count(conn):
    return conn.execute("SELECT COUNT(DISTINCT device_id) FROM sessions WHERE revoked = 0 "
                        "AND expires_at > ?", (db.now(),)).fetchone()[0]


def sign_out_all(conn):
    n = conn.execute("UPDATE sessions SET revoked = 1 WHERE revoked = 0").rowcount
    conn.commit()
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
