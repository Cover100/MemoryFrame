"""MF-12: pairing and session auth."""
import hashlib

import pytest

from memoryframe import auth
from memoryframe import db as mfdb

LOCAL = {"REMOTE_ADDR": "127.0.0.1"}
PHONE = {"REMOTE_ADDR": "10.42.0.23"}


@pytest.fixture
def clock(monkeypatch):
    t = {"now": 1_000_000.0}
    monkeypatch.setattr(auth, "clock", lambda: t["now"])
    return t


def start(client):
    return client.post("/pairing/start", environ_base=LOCAL).get_json()


def rows(app, sql):
    conn = mfdb.connect(app.config["DB_PATH"])
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


# ---------- every admin/api route needs a session ----------

def protected_rules(app):
    return [r for r in app.url_map.iter_rules()
            if r.rule.startswith("/admin") or r.rule.startswith("/api/v1")]


def test_every_admin_and_api_route_requires_session(mf_app, client):
    rules = protected_rules(mf_app)
    assert len(rules) >= 3
    for r in rules:
        url = r.rule.replace("<path:", "<").split("<")[0] or "/"
        for m in r.methods - {"HEAD", "OPTIONS"}:
            resp = client.open(url, method=m, environ_base=PHONE)
            if r.rule.startswith("/api/v1"):
                assert resp.status_code == 401, (m, r.rule)
            else:
                assert resp.status_code == 302 and resp.location.endswith("/pair"), (m, r.rule)


def test_paired_phone_reaches_admin_and_api(paired):
    assert paired.get("/admin/", environ_base=PHONE).status_code == 200
    assert paired.get("/api/v1/status", environ_base=PHONE).status_code == 200


# ---------- pairing ----------

def test_code_pairs_and_lands_on_admin(client, mf_app):
    code = start(client)["round"]["code"]
    assert len(code) == 6 and code.isdigit()
    r = client.post("/pair", data={"code": code}, environ_base=PHONE)
    assert r.status_code == 303 and r.location.endswith("/admin/")
    cookie = r.headers["Set-Cookie"]
    assert "mf_session=" in cookie and "HttpOnly" in cookie and "SameSite=Strict" in cookie
    token = client.get_cookie("mf_session").value
    stored = [row[0] for row in rows(mf_app, "SELECT token_hash FROM sessions")]
    assert stored == [hashlib.sha256(token.encode()).hexdigest()] and token not in stored


def test_qr_token_pairs(client):
    st = start(client)
    assert "<svg" in st["round"]["qr_svg"] and st["url"] == "http://10.42.0.1/pair"
    token = client.application.extensions["mf_pairing"].token
    page = client.get(f"/pair?t={token}", environ_base=PHONE)
    assert page.status_code == 200 and token.encode() in page.data      # GET does not pair
    assert client.get("/admin/", environ_base=PHONE).status_code == 302
    r = client.post("/pair", data={"token": token}, environ_base=PHONE)
    assert r.status_code == 303


def test_code_is_single_use(mf_app, client):
    code = start(client)["round"]["code"]
    assert client.post("/pair", data={"code": code}, environ_base=PHONE).status_code == 303
    other = mf_app.test_client()
    assert other.post("/pair", data={"code": code}, environ_base={"REMOTE_ADDR": "10.42.0.24"}).status_code == 400
    assert client.post("/pairing/start", environ_base=LOCAL).get_json()["paired"] == 1


def test_code_and_token_expire_after_120s(client, clock):
    st = start(client)
    token = client.application.extensions["mf_pairing"].token
    clock["now"] += 119
    assert client.get("/pairing/state", environ_base=LOCAL).get_json()["round"] is not None
    clock["now"] += 1
    assert client.get("/pairing/state", environ_base=LOCAL).get_json()["round"] is None
    assert client.post("/pair", data={"code": st["round"]["code"]}, environ_base=PHONE).status_code == 400
    assert client.post("/pair", data={"token": token}, environ_base=PHONE).status_code == 400


def test_five_wrong_codes_rotate(client):
    code = start(client)["round"]["code"]
    wrong = "000000" if code != "000000" else "111111"
    for i in range(5):
        assert client.post("/pair", json={"code": wrong}, environ_base={"REMOTE_ADDR": f"10.42.0.{50+i}"}).status_code == 400
    new = client.get("/pairing/state", environ_base=LOCAL).get_json()["round"]["code"]
    p = client.application.extensions["mf_pairing"]
    assert p.wrong == 0
    # the old code no longer works unless the rotation happened to draw the same digits
    if new != code:
        assert client.post("/pair", json={"code": code}, environ_base=PHONE).status_code == 400


def test_rate_limit_429(client, clock):
    start(client)
    codes = [client.post("/pair", json={"code": "x"}, environ_base=PHONE).status_code for _ in range(11)]
    assert codes[:10].count(429) == 0 and codes[10] == 429
    assert client.post("/pair", data={"code": "x"}, environ_base=PHONE).status_code == 429
    clock["now"] += 61
    assert client.post("/pair", json={"code": "x"}, environ_base=PHONE).status_code == 400


def test_no_round_means_no_pairing(client):
    assert client.post("/pair", json={"code": "123456"}, environ_base=PHONE).status_code == 400


def test_pair_page_redirects_when_signed_in(paired):
    assert paired.get("/pair", environ_base=PHONE).status_code == 302


# ---------- frame endpoints are localhost only ----------

@pytest.mark.parametrize("method,path", [("get", "/pairing/state"), ("post", "/pairing/start"),
                                         ("post", "/pairing/sign-out-all")])
def test_frame_pairing_routes_localhost_only(client, method, path):
    assert getattr(client, method)(path, environ_base=PHONE).status_code == 403


def test_state_shows_ssid_and_count(client, monkeypatch):
    monkeypatch.setenv("MEMORYFRAME_SSID", "Frame-Wifi")
    st = client.get("/pairing/state", environ_base=LOCAL).get_json()
    assert st["ssid"] == "Frame-Wifi" and st["paired"] == 0 and st["round"] is None


# ---------- sign out all ----------

def test_sign_out_all_from_site(mf_app, paired):
    from conftest import pair_client
    second = pair_client(mf_app.test_client())
    r = paired.post("/admin/sign-out-all", environ_base=PHONE)
    assert r.status_code == 303
    assert second.get("/api/v1/status", environ_base=PHONE).status_code == 401
    assert paired.get("/api/v1/status", environ_base=PHONE).status_code == 401
    pair_client(paired)                       # can re-pair
    assert paired.get("/api/v1/status", environ_base=PHONE).status_code == 200


def test_sign_out_all_from_frame(paired):
    r = paired.post("/pairing/sign-out-all", environ_base=LOCAL)
    assert r.get_json() == {"revoked": 1}
    assert paired.get("/api/v1/status", environ_base=PHONE).status_code == 401
    assert paired.get("/pairing/state", environ_base=LOCAL).get_json()["paired"] == 0


def test_expired_session_rejected(paired, mf_app):
    conn = mfdb.connect(mf_app.config["DB_PATH"])
    conn.execute("UPDATE sessions SET expires_at = 0")
    conn.commit()
    conn.close()
    assert paired.get("/api/v1/status", environ_base=PHONE).status_code == 401
