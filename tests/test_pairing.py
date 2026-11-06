"""MF-12: pairing and session auth."""
import hashlib
import re

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
        url = re.sub(r"<(?:\w+:)?\w+>", "1", r.rule)      # fill converters (<int:id> -> 1)
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
    assert r.status_code == 303 and r.location.endswith("/admin/?paired=1")
    cookie = r.headers["Set-Cookie"]
    assert "mf_session=" in cookie and "HttpOnly" in cookie and "SameSite=Strict" in cookie
    token = client.get_cookie("mf_session").value
    stored = [row[0] for row in rows(mf_app, "SELECT token_hash FROM sessions")]
    assert stored == [hashlib.sha256(token.encode()).hexdigest()] and token not in stored


def test_qr_token_pairs(client):
    st = start(client)
    assert "<svg" in st["round"]["qr_svg"] and st["url"] == "http://10.42.0.1:5000/pair"   # port 80 not served in tests
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


# ---------- Tester #22 blockers: odd inputs never 500, always count ----------

BAD_INPUTS = [{"token": 123}, {"code": 123456}, {"code": "é"}, {"code": "١٢٣٤٥٦"},
              {"token": "é"}, {"token": "токен"}, {"code": ["123456"]}, {"token": {"a": 1}},
              {"code": None}, {"code": "12345"}, {"code": "1234567"}, {"code": "12 456"}]


@pytest.mark.parametrize("payload", BAD_INPUTS)
def test_odd_inputs_are_400_and_count(client, payload):
    start(client)
    p = client.application.extensions["mf_pairing"]
    r = client.post("/pair", json=payload, environ_base=PHONE)
    assert r.status_code == 400, r.data
    assert p.wrong == 1


@pytest.mark.parametrize("form", [{"code": "é"}, {"code": "١٢٣٤٥٦"}, {"token": "é"}])
def test_odd_form_inputs_are_400(client, form):
    start(client)
    assert client.post("/pair", data=form, environ_base=PHONE).status_code == 400


def test_five_odd_inputs_rotate(client):
    code = start(client)["round"]["code"]
    for i, payload in enumerate(BAD_INPUTS[:5]):
        client.post("/pair", json=payload, environ_base={"REMOTE_ADDR": f"10.42.0.{80 + i}"})
    p = client.application.extensions["mf_pairing"]
    assert p.wrong == 0 and p.current() is not None


def test_json_non_object_body(client):
    start(client)
    assert client.post("/pair", json=["x"], environ_base=PHONE).status_code == 400


def test_rate_limiter_memory_bounded(monkeypatch, clock):
    monkeypatch.setattr(auth, "MAX_TRACKED", 50)
    p = auth.Pairing()
    for i in range(200):
        p.rate_limited(f"10.0.{i // 250}.{i % 250}")
    assert len(p.attempts) <= 50
    for _ in range(100):
        p.rate_limited("10.42.0.9")
    assert len(p.attempts["10.42.0.9"]) <= auth.RATE_LIMIT + 1
    clock["now"] += 61
    p.rate_limited("10.42.0.10")
    assert set(p.attempts) == {"10.42.0.10"}          # idle addresses forgotten


def test_rate_limiter_threadsafe(clock):
    import threading
    p = auth.Pairing()
    results = []
    ts = [threading.Thread(target=lambda: results.append(p.rate_limited("1.2.3.4"))) for _ in range(30)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert results.count(False) == auth.RATE_LIMIT


def test_device_label_sanitised():
    assert auth.device_label("Mozilla/5.0\n\x00(Linux; Android 14)\t é") == "Mozilla/5.0 (Linux; Android 14)"
    assert len(auth.device_label("x" * 500)) == 80
    assert auth.device_label("\u202e\u200b") == "phone" and auth.device_label(None) == "phone"


def test_last_seen_throttled(paired, mf_app, monkeypatch):
    from memoryframe import db as mdb
    import time
    t = {"now": time.time() + 1}
    monkeypatch.setattr(mdb, "now", lambda: t["now"])
    seen = lambda: rows(mf_app, "SELECT last_seen FROM devices")[0][0]   # noqa: E731
    paired.get("/api/v1/status", environ_base=PHONE)
    first = seen()
    t["now"] += 30
    paired.get("/api/v1/status", environ_base=PHONE)
    assert seen() == first
    t["now"] += 31
    paired.get("/api/v1/status", environ_base=PHONE)
    assert seen() == t["now"]


def test_last_seen_no_write_txn_within_throttle(paired, mf_app, monkeypatch):
    """N session-authenticated GETs inside LAST_SEEN_EVERY issue 0 last_seen UPDATEs (#130).

    create_session already wrote last_seen at pair time, so the first poll is also
    within the throttle and must stay read-only (no UPDATE → no write txn / WAL storm).
    """
    from memoryframe import db as mdb
    import time

    t = {"now": time.time() + 1}
    monkeypatch.setattr(mdb, "now", lambda: t["now"])

    counts = {"update": 0}
    real_connect = mdb.connect

    def wrapping_connect(db_path):
        conn = real_connect(db_path)

        def tracer(sql):
            if "UPDATE devices SET last_seen" in sql:
                counts["update"] += 1

        conn.set_trace_callback(tracer)
        return conn

    monkeypatch.setattr(mdb, "connect", wrapping_connect)
    # Pairing already opened a worker connection; drop it so polls use the tracer.
    mdb.dispose_worker_connection()

    n = 20
    for _ in range(n):
        assert paired.get("/api/v1/status", environ_base=PHONE).status_code == 200
        assert paired.get("/admin/", environ_base=PHONE).status_code == 200

    # 2*n authenticated polls, all inside the 60 s throttle → no last_seen writes.
    assert counts["update"] == 0

    t["now"] += auth.LAST_SEEN_EVERY + 1
    assert paired.get("/api/v1/status", environ_base=PHONE).status_code == 200
    assert counts["update"] == 1
    assert rows(mf_app, "SELECT last_seen FROM devices")[0][0] == t["now"]


def test_wal_close_write_reuses_connection(paired, mf_app):
    """N authenticated admin/status polls must not CLOSE_WRITE the WAL N times (#132).

    #131 stopped last_seen UPDATEs inside the throttle, but get_db→close_db still
    opened/closed a WAL connection every request (~2.2 IN_CLOSE_WRITE per S2 loop).
    One thread-local connection reused across polls should produce ≪ N closes.

    Watch the data directory (not the WAL inode): when the last connection closes,
    SQLite checkpoints and may unlink/recreate *-wal, which drops a file watch.
    """
    # Opt into production reuse: TESTING teardown otherwise closes every request
    # so Werkzeug live_server threads do not leak FDs across the suite.
    mf_app.config["DB_REUSE_IN_TESTS"] = True
    import ctypes
    import ctypes.util
    import os
    import struct
    import time

    db_path = mf_app.config["DB_PATH"]
    data_dir = os.path.dirname(db_path) or "."
    wal_name = os.path.basename(db_path) + "-wal"
    wal_path = db_path + "-wal"

    # Warm the worker connection and ensure the WAL file exists (PRAGMA journal_mode=WAL).
    assert paired.get("/api/v1/status", environ_base=PHONE).status_code == 200
    assert os.path.exists(wal_path), wal_path

    libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
    IN_CLOSE_WRITE = 0x00000008
    inotify_init1 = libc.inotify_init1
    inotify_init1.restype = ctypes.c_int
    inotify_add_watch = libc.inotify_add_watch
    inotify_add_watch.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
    inotify_add_watch.restype = ctypes.c_int

    fd = inotify_init1(os.O_NONBLOCK)
    assert fd >= 0, ctypes.get_errno()
    try:
        wd = inotify_add_watch(fd, data_dir.encode(), IN_CLOSE_WRITE)
        assert wd >= 0, ctypes.get_errno()

        def drain():
            n_events = 0
            while True:
                try:
                    data = os.read(fd, 65536)
                except BlockingIOError:
                    break
                if not data:
                    break
                i = 0
                while i + 16 <= len(data):
                    _wd, mask, _cookie, name_len = struct.unpack_from("iIII", data, i)
                    name = b""
                    if name_len:
                        name = data[i + 16:i + 16 + name_len].split(b"\x00", 1)[0]
                    i += 16 + name_len
                    if (mask & IN_CLOSE_WRITE) and name.decode(errors="replace") == wal_name:
                        n_events += 1
            return n_events

        drain()  # discard warm-up / setup closes
        n = 40
        for _ in range(n):
            assert paired.get("/api/v1/status", environ_base=PHONE).status_code == 200
            assert paired.get("/admin/", environ_base=PHONE).status_code == 200
        # Brief settle so any deferred close would show up; reuse should still be ~0.
        time.sleep(0.05)
        closes = drain()
    finally:
        os.close(fd)

    total = 2 * n
    # Pre-#132 this was ~total CLOSE_WRITE events (one connect/close per request).
    # With per-thread reuse, request teardown must not close the WAL connection.
    assert closes * 10 < total, (
        f"WAL CLOSE_WRITE={closes} for {total} authenticated polls "
        f"(expected ≪ {total}; connection is being closed per request)"
    )
    assert closes <= 2, (
        f"unexpected WAL CLOSE_WRITE count during reuse window: {closes} "
        f"(N requests={total})"
    )
