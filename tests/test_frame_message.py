"""#73: the giver's message - storage, phone API, settings field, About, and the frame menu."""
import os
from pathlib import Path

from memoryframe import create_app, message

PHONE = {"REMOTE_ADDR": "10.42.0.23"}
FRAME = {"REMOTE_ADDR": "127.0.0.1"}
ROOT = Path(__file__).resolve().parents[1]


def put(c, value, **kw):
    return c.put("/api/v1/message", json={"message": value}, environ_base=PHONE, **kw)


def test_set_get_and_clear(paired, mf_app):
    assert paired.get("/api/v1/message", environ_base=PHONE).get_json() == {"message": "", "max": 140}
    r = put(paired, "  Happy 80th,\n Nana!  ")
    assert r.status_code == 200 and r.get_json()["message"] == "Happy 80th, Nana!"
    assert paired.get("/api/v1/message", environ_base=PHONE).get_json()["message"] == "Happy 80th, Nana!"
    assert put(paired, "").get_json()["message"] == ""
    assert not os.path.exists(os.path.join(mf_app.config["DATA_DIR"], message.FILENAME))


def test_length_limit(paired):
    assert put(paired, "x" * 140).status_code == 200
    r = put(paired, "y" * 141)
    assert r.status_code == 400 and "140" in r.get_json()["error"]
    assert paired.get("/api/v1/message", environ_base=PHONE).get_json()["message"] == "x" * 140
    assert put(paired, "é" * 140).status_code == 200          # characters, not bytes
    assert put(paired, "a" + "\x00" * 200 + "b").get_json()["message"] == "ab"   # stripped before the limit


def test_bad_bodies(paired):
    for body in (None, [], {"message": 5}, {"message": None}, {"message": "a", "x": 1}, {}):
        assert paired.put("/api/v1/message", json=body, environ_base=PHONE).status_code == 400


def test_control_and_format_chars_stripped():
    assert message.clean("a\x07b\x1bc\u202ed\u200be\x7f\tf")[0] == "abcde f"
    assert message.clean("x\r\ny\u2028z")[0] == "x y z"


def test_auth_and_csrf(client, paired):
    with client.application.test_client() as anon:
        assert anon.get("/api/v1/message", environ_base=PHONE).status_code == 401
        assert put(anon, "hi").status_code == 401
    assert paired.put("/api/v1/message", json={"message": "hi"}, environ_base=PHONE, csrf=False).status_code == 403
    r = paired.put("/api/v1/message", json={"message": "hi"}, environ_base=PHONE,
                   headers={"Origin": "http://evil.example"})
    assert r.status_code == 403


def test_persists_across_restart_and_atomic(paired, mf_app):
    put(paired, "Love from all of us")
    d = mf_app.config["DATA_DIR"]
    assert sorted(os.listdir(d)).count(message.FILENAME) == 1
    assert not [n for n in os.listdir(d) if n.startswith(".frame_message.")]   # no temp left behind
    app2 = create_app({"TESTING": True, "DATA_DIR": d, "DB_PATH": mf_app.config["DB_PATH"]})
    with app2.test_client() as c:
        assert "Love from all of us" in c.get("/", environ_base=FRAME).get_data(as_text=True)


def test_stored_under_data_dir_not_repo():
    src = (ROOT / "memoryframe" / "message.py").read_text()
    assert "os.replace" in src and "fsync" in src
    install = (ROOT / "deploy" / "install.sh").read_text()
    assert message.FILENAME not in install                     # install.sh never writes it


def test_escaping_everywhere(paired, client):
    evil = '<script>alert(1)</script><img src=x onerror=alert(2)>'
    assert put(paired, evil).get_json()["message"] == evil       # stored as plain text
    frame = client.get("/", environ_base=FRAME).get_data(as_text=True)
    phone = paired.get("/admin/settings", environ_base=PHONE).get_data(as_text=True)
    for html in (frame, phone):
        assert "<script>alert(1)" not in html and "<img src=x" not in html
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    for tpl in ("index.html", "settings.html"):
        assert "|safe" not in (ROOT / "templates" / tpl).read_text().replace(" ", "")
    js = (ROOT / "static" / "script.js").read_text()
    fn = js[js.index("function applyMessage"):js.index("async function pollControl")]
    assert "textContent" in fn and "innerHTML" not in fn
    assert "innerHTML" not in (ROOT / "static" / "settings.js").read_text()


def test_menu_renders_stored_message_and_poll(paired, client):
    html = client.get("/", environ_base=FRAME).get_data(as_text=True)
    assert 'id="menuNote" hidden' in html and "No message yet" in html
    put(paired, "Happy birthday, Nana!")
    html = client.get("/", environ_base=FRAME).get_data(as_text=True)
    assert '<p id="menuMessage">Happy birthday, Nana!</p>' in html and 'id="menuNoteEmpty" hidden' in html
    assert client.get("/show-now", environ_base=FRAME).get_json()["message"] == "Happy birthday, Nana!"
    assert "message" not in client.get("/show-now", environ_base=PHONE).get_json()   # not for the LAN


def test_settings_page_field_preview_counter_about(paired):
    put(paired, "Hi there")
    h = paired.get("/admin/settings", environ_base=PHONE).get_data(as_text=True)
    assert 'id="frameMessage" maxlength="140"' in h and ">Hi there</textarea>" in h
    assert 'id="messageCount" aria-live="polite">8 / 140<' in h
    assert 'id="messagePreviewText">Hi there<' in h
    assert "Message on the frame" in h and "About this frame" in h
    assert 'id="aboutIp">10.42.0.1<' in h and 'id="aboutSite">http://10.42.0.1' in h
    assert 'id="aboutVersion">0.' in h
