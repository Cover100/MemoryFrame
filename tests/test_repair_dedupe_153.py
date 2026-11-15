"""#153: re-pairing from a phone that still holds a valid mf_session reuses that phone's device row
(Sage, MemoryFrame-design/repair-dedupe/index.md). The pairing code / QR token stays required; the cookie
only picks WHICH row, after a successful redeem. No User-Agent or IP matching.

Cookie values are never printed: comparisons are done on booleans or SHA-256 hashes."""
import hashlib
import secrets

import pytest

from memoryframe import auth
from memoryframe import db as mfdb

LOCAL = {"REMOTE_ADDR": "127.0.0.1"}
PHONE = {"REMOTE_ADDR": "10.42.0.23"}


def q(app, sql, args=()):
    conn = mfdb.connect(app.config["DB_PATH"])
    try:
        return [tuple(r) for r in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


def sha(v):
    return hashlib.sha256(v.encode()).hexdigest()


def cookie(c):
    ck = c.get_cookie(auth.COOKIE)
    return None if ck is None else ck.value


def pair(c, how="code", ua=None, env=PHONE, extra=None):
    st = c.post("/pairing/start", environ_base=LOCAL).get_json()
    data = {"code": st["round"]["code"]} if how == "code" else {"token": c.application.extensions["mf_pairing"].token}
    data.update(extra or {})
    headers = {"User-Agent": ua} if ua else {}
    if how == "json":
        r = c.post("/pair", json=data, environ_base=env, headers=headers)
        assert r.status_code == 200, r.status_code
    else:
        r = c.post("/pair", data=data, environ_base=env, headers=headers)
        assert r.status_code == 303, r.status_code
    return r


def ok(c, env=PHONE):
    return c.get("/api/v1/status", environ_base=env).status_code == 200


def with_cookie(app, value):
    other = app.test_client()
    other.set_cookie(auth.COOKIE, value)
    return other


def devices(app):
    return q(app, "SELECT id, name, created_at, last_seen FROM devices ORDER BY id")


def paired_count(app):
    return q(app, "SELECT COUNT(DISTINCT device_id) FROM sessions WHERE revoked = 0 AND expires_at > ?",
             (mfdb.now(),))[0][0]


# ---------- 1. same client, valid cookie: same row, rotated secret ----------

@pytest.mark.parametrize("how", ["code", "token", "json"])
def test_same_client_repair_reuses_its_row_and_rotates_the_secret(mf_app, client, how):
    pair(client)
    [(dev_id, *_)] = devices(mf_app)
    old = cookie(client)
    pair(client, how=how)
    new = cookie(client)
    assert [d[0] for d in devices(mf_app)] == [dev_id]               # no new devices row, same id
    assert paired_count(mf_app) == 1
    assert client.post("/pairing/start", environ_base=LOCAL).get_json()["paired"] == 1
    rotated = new is not None and new != old
    assert rotated                                                   # a new secret was issued
    live = q(mf_app, "SELECT device_id, token_hash FROM sessions WHERE revoked = 0")
    assert live == [(dev_id, sha(new))]                              # only the new hash is live
    stored = [h for (h,) in q(mf_app, "SELECT token_hash FROM sessions")]
    leaked = new in stored or old in stored
    assert not leaked                                                # never the raw cookie
    assert sha(old) in stored and q(mf_app, "SELECT revoked FROM sessions WHERE token_hash = ?", (sha(old),)) == [(1,)]
    assert not ok(with_cookie(mf_app, old))                          # previous cookie rejected
    assert ok(client) and ok(with_cookie(mf_app, new))               # new one accepted


def test_repair_refreshes_last_seen_and_label_not_created_at(mf_app, client, monkeypatch):
    t = {"now": 1_800_000_000}
    monkeypatch.setattr(mfdb, "now", lambda: t["now"])
    pair(client, ua="Old Browser/1")
    [(dev_id, name1, created1, seen1)] = devices(mf_app)
    t["now"] += 3600
    pair(client, ua="New Browser/2")
    [(dev_id2, name2, created2, seen2)] = devices(mf_app)
    assert (dev_id2, created2) == (dev_id, created1)
    assert seen2 == t["now"] and seen1 == t["now"] - 3600
    assert (name1, name2) == ("Old Browser/1", "New Browser/2")
    assert q(mf_app, "SELECT created_at, expires_at FROM sessions WHERE revoked = 0") == \
        [(t["now"], t["now"] + auth.SESSION_DAYS * 86400)]


def test_a_failed_redeem_never_rotates(mf_app, client):
    pair(client)
    before = q(mf_app, "SELECT * FROM sessions")
    start = client.post("/pairing/start", environ_base=LOCAL).get_json()["round"]["code"]
    wrong = "000000" if start != "000000" else "111111"
    assert client.post("/pair", data={"code": wrong}, environ_base=PHONE).status_code == 400
    assert client.post("/pair", data={"token": "nope"}, environ_base=PHONE).status_code == 400
    assert q(mf_app, "SELECT * FROM sessions") == before and ok(client)
    assert len(devices(mf_app)) == 1


# ---------- 2. two clients, no shared cookie: two rows ----------

def test_two_clients_get_two_rows_even_with_the_same_user_agent_and_address(mf_app, client):
    a, b = client, mf_app.test_client()
    pair(a, ua="Same Phone/1")
    pair(b, ua="Same Phone/1")                                       # no UA / IP matching
    assert len(devices(mf_app)) == 2 and paired_count(mf_app) == 2
    assert ok(a) and ok(b)


# ---------- 3. revoked / expired / missing / unknown cookie: new row ----------

def test_after_sign_out_all_repair_is_a_new_row_and_the_old_one_is_not_counted(mf_app, client):
    pair(client)
    [(old_id, *_)] = devices(mf_app)
    assert client.post("/admin/sign-out-all", environ_base=PHONE).status_code == 303
    assert not ok(client)
    pair(client)                                                     # it still sends its revoked cookie
    ids = [d[0] for d in devices(mf_app)]
    assert len(ids) == 2 and ids[0] == old_id                        # revoked rows are not deleted
    assert paired_count(mf_app) == 1
    assert q(mf_app, "SELECT DISTINCT device_id FROM sessions WHERE revoked = 0") == [(ids[1],)]


def test_revoked_cookie_still_presented_is_a_new_row(mf_app, client):
    pair(client)
    old = cookie(client)
    [(old_id, *_)] = devices(mf_app)
    client.post("/pairing/sign-out-all", environ_base=LOCAL)
    c = with_cookie(mf_app, old)                                     # the revoked cookie IS sent
    pair(c)
    ids = [d[0] for d in devices(mf_app)]
    assert len(ids) == 2 and old_id in ids and paired_count(mf_app) == 1
    assert q(mf_app, "SELECT revoked FROM sessions WHERE device_id = ?", (old_id,)) == [(1,)]


def test_expired_cookie_is_a_new_row(mf_app, client):
    pair(client)
    [(old_id, *_)] = devices(mf_app)
    conn = mfdb.connect(mf_app.config["DB_PATH"])
    conn.execute("UPDATE sessions SET expires_at = 0")
    conn.commit()
    conn.close()
    pair(client)
    ids = [d[0] for d in devices(mf_app)]
    assert len(ids) == 2 and old_id in ids and paired_count(mf_app) == 1
    assert q(mf_app, "SELECT expires_at FROM sessions WHERE device_id = ?", (old_id,)) == [(0,)]   # left alone


@pytest.mark.parametrize("presented", ["missing", "unknown", "empty"])
def test_missing_or_unknown_cookie_is_a_new_row(mf_app, client, presented):
    pair(client)
    [(old_id, *_)] = devices(mf_app)
    c = mf_app.test_client()
    if presented == "unknown":
        c.set_cookie(auth.COOKIE, secrets.token_urlsafe(32))
    elif presented == "empty":
        c.set_cookie(auth.COOKIE, "")
    pair(c)
    assert len(devices(mf_app)) == 2 and paired_count(mf_app) == 2
    assert ok(client)                                               # the first phone is untouched


# ---------- 4. A and B paired; A re-pairs: B untouched ----------

def test_a_repairs_b_untouched(mf_app, client):
    a, b = client, mf_app.test_client()
    pair(a, ua="Phone A")
    pair(b, ua="Phone B")
    (a_id, *_), b_dev = devices(mf_app)
    b_sessions = q(mf_app, "SELECT * FROM sessions WHERE device_id = ?", (b_dev[0],))
    pair(a, ua="Phone A")
    pair(a, how="token", ua="Phone A")
    assert [d[0] for d in devices(mf_app)] == [a_id, b_dev[0]]
    assert devices(mf_app)[1] == b_dev                               # B's row untouched
    assert q(mf_app, "SELECT * FROM sessions WHERE device_id = ?", (b_dev[0],)) == b_sessions
    assert ok(b) and ok(a) and paired_count(mf_app) == 2


def test_a_device_id_in_the_request_body_is_ignored(mf_app, client):
    a, b = client, mf_app.test_client()
    pair(a)
    pair(b)
    (a_id, *_), (b_id, *_) = devices(mf_app)
    pair(a, extra={"device_id": str(b_id), "device": str(b_id)})
    pair(a, how="json", extra={"device_id": b_id})
    assert ok(b) and paired_count(mf_app) == 2
    assert q(mf_app, "SELECT COUNT(*) FROM sessions WHERE device_id = ? AND revoked = 0", (a_id,)) == [(1,)]
    assert q(mf_app, "SELECT COUNT(*) FROM sessions WHERE device_id = ?", (b_id,)) == [(1,)]
    c = mf_app.test_client()                                         # no cookie + a device id: still a new row
    pair(c, extra={"device_id": str(a_id)})
    assert len(devices(mf_app)) == 3 and ok(a)


# ---------- 5. GET redirect and cookie flags unchanged ----------

def test_get_pair_with_valid_cookie_still_redirects(mf_app, client):
    pair(client)
    pair(client)
    r = client.get("/pair", environ_base=PHONE)
    assert r.status_code == 302 and r.location.endswith("/admin/")
    token = mf_app.extensions["mf_pairing"].token
    assert client.get(f"/pair?t={token or 'x'}", environ_base=PHONE).status_code == 302


@pytest.mark.parametrize("how", ["code", "json"])
def test_rotated_cookie_has_the_same_flags(mf_app, client, how):
    first = pair(client).headers.getlist("Set-Cookie")
    again = pair(client, how=how).headers.getlist("Set-Cookie")
    for hdrs in (first, again):
        [h] = [x for x in hdrs if x.startswith(auth.COOKIE + "=")]
        attrs = sorted(p.strip().split("=")[0].lower() for p in h.split(";")[1:])
        assert attrs == ["expires", "httponly", "max-age", "path", "samesite"]   # no Secure, no Domain
        assert "Max-Age=31536000" in h and "Path=/" in h and "SameSite=Strict" in h and "HttpOnly" in h
