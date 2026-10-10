"""MF-13: CSRF, Host/Origin checks, security headers."""
import pathlib
import re

import pytest

from conftest import pair_client

PHONE = {"REMOTE_ADDR": "10.42.0.23"}
LOCAL = {"REMOTE_ADDR": "127.0.0.1"}
ROOT = pathlib.Path(__file__).resolve().parent.parent


def write_rules(app):
    """Every non-GET (rule, method) that is not a localhost-only kiosk route."""
    out = []
    for r in app.url_map.iter_rules():
        view = app.view_functions[r.endpoint]
        if getattr(view, "localhost_only", False):
            continue
        for m in r.methods - {"GET", "HEAD", "OPTIONS"}:
            out.append((r.rule, m))
    return out


def test_write_routes_need_csrf_token(mf_app, paired):
    rules = write_rules(mf_app)
    assert ("/pair", "POST") in rules and ("/admin/sign-out-all", "POST") in rules
    for rule, m in rules:
        r = paired.open(rule, method=m, environ_base=PHONE, csrf=False)
        assert r.status_code == 403, (rule, m)
        r = paired.open(rule, method=m, environ_base=PHONE, csrf=False,
                        headers={"X-CSRF-Token": "wrong"})
        assert r.status_code == 403, (rule, m)
    # session still valid: nothing was signed out
    assert paired.get("/api/v1/status", environ_base=PHONE).status_code == 200


def test_form_token_accepted(client):
    page = client.get("/pair", environ_base=PHONE)
    token = re.search(rb'name="csrf_token" value="([^"]+)"', page.data).group(1).decode()
    code = client.post("/pairing/start", environ_base=LOCAL).get_json()["round"]["code"]
    r = client.post("/pair", data={"code": code, "csrf_token": token}, environ_base=PHONE, csrf=False)
    assert r.status_code == 303


def test_csrf_cookie_flags(client):
    cookie = client.get("/pair").headers["Set-Cookie"]
    assert "mf_csrf=" in cookie and "HttpOnly" in cookie and "SameSite=Strict" in cookie


def test_admin_page_exposes_token(paired):
    html = paired.get("/admin/", environ_base=PHONE).data
    assert b'<meta name="csrf-token"' in html and b'name="csrf_token"' in html


def test_kiosk_routes_exempt_but_origin_checked(client, backlight):
    assert client.post("/brightness", json={"value": 120}, environ_base=LOCAL, csrf=False).status_code == 200
    r = client.post("/brightness", json={"value": 120}, environ_base=LOCAL, csrf=False,
                    headers={"Origin": "http://evil.example"})
    assert r.status_code == 403


@pytest.mark.parametrize("host", ["10.42.0.1", "memoryframe.lan", "memoryframe.local", "localhost",
                                  "localhost:5000", "127.0.0.1:80", "[::1]:5000", "MemoryFrame.LAN"])
def test_allowed_hosts(client, host):
    assert client.get("/healthz", headers={"Host": host}).status_code == 200


@pytest.mark.parametrize("host", ["evil.example", "attacker.memoryframe.lan.evil.example", "10.42.0.1.nip.io",
                                  "192.168.50.124", "memoryframe"])
def test_dns_rebinding_hosts_rejected(client, host):
    assert client.get("/healthz", headers={"Host": host}).status_code == 400
    assert client.get("/media", headers={"Host": host}).status_code == 400


def test_cross_origin_post_rejected_even_with_token(paired):
    r = paired.post("/admin/sign-out-all", environ_base=PHONE, headers={"Origin": "http://evil.example"})
    assert r.status_code == 403
    r = paired.post("/admin/sign-out-all", environ_base=PHONE, headers={"Origin": "http://10.42.0.1"})
    assert r.status_code == 303


@pytest.mark.parametrize("path", ["/", "/pair", "/media", "/healthz", "/static/script.js", "/nope"])
def test_security_headers(client, path):
    r = client.get(path)
    csp = r.headers["Content-Security-Policy"]
    assert "default-src 'self'" in csp and "frame-ancestors 'none'" in csp
    assert r.headers["X-Content-Type-Options"] == "nosniff"


def test_no_inline_scripts_or_external_references():
    for t in (ROOT / "templates").glob("*.html"):
        html = t.read_text()
        assert not re.search(r"https?://", html), f"external reference in {t.name}"
        for m in re.finditer(r"<script([^>]*)>(.*?)</script>", html, re.S):
            attrs, body = m.groups()
            assert "src=" in attrs or 'type="application/json"' in attrs, f"inline script in {t.name}"
        assert "<style" not in html and " style=" not in html and "onclick" not in html, t.name


def test_paired_flow_still_works(mf_app):
    c = pair_client(mf_app.test_client())
    assert c.get("/admin/", environ_base=PHONE).status_code == 200


def test_extra_hosts_env(monkeypatch, tmp_path):
    from memoryframe import create_app
    monkeypatch.setenv("MEMORYFRAME_EXTRA_HOSTS", "192.168.50.124, Pi.Home")
    a = create_app({"TESTING": True, "DATA_DIR": str(tmp_path / "d"), "DB_PATH": str(tmp_path / "d.db")})
    c = a.test_client()
    assert c.get("/healthz", headers={"Host": "192.168.50.124:5000"}).status_code == 200
    assert c.get("/healthz", headers={"Host": "pi.home"}).status_code == 200
    assert c.get("/healthz", headers={"Host": "evil.example"}).status_code == 400
