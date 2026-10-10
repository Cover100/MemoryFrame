"""MF-13: Host allow-list, Origin check, CSRF tokens and security headers.

- Host: requests whose Host (port stripped) is not in ALLOWED_HOSTS get 400.
  This stops DNS-rebinding pages from talking to the frame.
- Origin: a non-GET request carrying an Origin/Referer from another host -> 403.
- CSRF (double submit): a random token lives in the `mf_csrf` cookie
  (HttpOnly, SameSite=Strict) and every non-GET request must echo it in the
  `X-CSRF-Token` header or a `csrf_token` form field -> otherwise 403.
  Localhost-only kiosk routes are exempt (unreachable from phones; Origin still checked).
  Templates get csrf_token() for forms; pages expose it in <meta name="csrf-token">.
- Headers: CSP default-src 'self' with frame-ancestors 'none', nosniff, no referrer.
"""
import hmac
import secrets
from urllib.parse import urlsplit

from flask import abort, current_app, g, request

DEFAULT_ALLOWED_HOSTS = ("10.42.0.1", "memoryframe", "memoryframe.lan", "memoryframe.local", "localhost",
                         "127.0.0.1", "::1")
CSRF_COOKIE = "mf_csrf"
SAFE = {"GET", "HEAD", "OPTIONS"}

CSP = ("default-src 'self'; img-src 'self' data:; media-src 'self'; object-src 'none'; "
       "base-uri 'none'; form-action 'self'; frame-ancestors 'none'")


def _hostname(host):
    host = (host or "").strip().lower()
    if host.startswith("["):                       # [::1]:5000
        host = host[1:host.find("]")] if "]" in host else host
    elif host.count(":") == 1:
        host = host.rsplit(":", 1)[0]
    return host[:-1] if host.endswith(".") else host   # FQDN form: memoryframe.lan.


def allowed(host):
    return _hostname(host) in current_app.config.get("ALLOWED_HOSTS", DEFAULT_ALLOWED_HOSTS)


def csrf_token():
    tok = getattr(g, "csrf_token", None)
    if tok is None:
        tok = request.cookies.get(CSRF_COOKIE) or secrets.token_urlsafe(32)
        g.csrf_token = tok
    return tok


def _view_is_localhost_only():
    view = current_app.view_functions.get(request.endpoint)
    return bool(getattr(view, "localhost_only", False))


def check_request():
    if not allowed(request.host):
        abort(400, "Unknown host")
    if request.method in SAFE:
        return None
    origin = request.headers.get("Origin") or request.headers.get("Referer")
    if origin and origin != "null" and not allowed(urlsplit(origin).netloc):
        abort(403, "Cross-origin request refused")
    if request.endpoint is None or _view_is_localhost_only():
        return None
    cookie = request.cookies.get(CSRF_COOKIE)
    sent = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token")
    if (not cookie or not sent or not cookie.isascii() or not sent.isascii()
            or not hmac.compare_digest(cookie.encode(), sent.encode())):
        abort(403, "Missing or invalid CSRF token")
    return None


def add_headers(resp):
    resp.headers.setdefault("Content-Security-Policy", CSP)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    tok = csrf_token()                 # every response makes sure the browser has a token
    if request.cookies.get(CSRF_COOKIE) != tok:
        resp.set_cookie(CSRF_COOKIE, tok, httponly=True, samesite="Strict", path="/",
                        max_age=365 * 86400)
    return resp


def init_app(app):
    import os
    extra = [h.strip().lower().rstrip(".") for h in os.environ.get("MEMORYFRAME_EXTRA_HOSTS", "").split(",")
             if h.strip()]
    app.config.setdefault("ALLOWED_HOSTS", tuple(DEFAULT_ALLOWED_HOSTS) + tuple(extra))
    app.before_request(check_request)
    app.after_request(add_headers)
    app.jinja_env.globals["csrf_token"] = csrf_token
