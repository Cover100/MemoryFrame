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
    assert 'id="frameMessage" maxlength="280"' in h and ">Hi there</textarea>" in h
    assert 'id="messageCount" aria-live="polite">8 / 140<' in h
    assert 'id="messagePreviewText">Hi there<' in h
    assert "Message on the frame" in h and "About this frame" in h
    assert 'id="aboutIp">10.42.0.1<' in h and 'id="aboutSite">http://10.42.0.1' in h
    assert 'id="aboutVersion">0.' in h


# ---------------- #75 review (Juniper) ----------------

def test_nfc_before_counting():
    decomposed = "e\u0301" * 140                               # 280 code points, 140 after NFC
    text, err = message.clean(decomposed)
    assert err is None and text == "\u00e9" * 140


def test_combining_marks_capped():
    zalgo = "q" + "\u0300\u0301\u0302\u0303\u0304\u0305" + "b"
    assert message.clean(zalgo)[0] == "q\u0300\u0301\u0302b"   # q has no precomposed forms
    assert message.clean("\u0301\u0301x")[0] == "x"             # no base: dropped


def test_preview_clipped_like_frame():
    css = (ROOT / "static" / "phone.css").read_text()
    rule = css[css.index(".msgPreview p{"):]
    rule = rule[:rule.index("}")]
    assert "-webkit-line-clamp:5" in rule and "overflow:hidden" in rule


def test_invisible_fillers_count_as_empty(paired, mf_app):
    put(paired, "Hi")
    blank = "\u115f\u1160\u3164\uffa0\u2800 \u200b"
    assert put(paired, blank).get_json()["message"] == ""
    assert not os.path.exists(os.path.join(mf_app.config["DATA_DIR"], message.FILENAME))
    assert message.clean("a\u3164b\u2800c")[0] == "abc"


def test_body_over_4k_is_413(paired):
    big = '{"message": "' + "x" * 5000 + '"}'
    r = paired.put("/api/v1/message", data=big, content_type="application/json", environ_base=PHONE)
    assert r.status_code == 413
    ok = '{"message": "' + "x" * 140 + '"}'
    assert paired.put("/api/v1/message", data=ok, content_type="application/json",
                      environ_base=PHONE).status_code == 200
    # other routes keep their own limits (uploads are big)
    assert paired.put("/api/v1/settings", json={"shuffle": True}, environ_base=PHONE).status_code == 200


def test_unchanged_message_skips_write(paired, mf_app, monkeypatch):
    put(paired, "Same")
    calls = []
    monkeypatch.setattr(os, "fsync", lambda fd: calls.append(fd))
    monkeypatch.setattr(os, "replace", lambda *a: calls.append(a))
    assert put(paired, "  Same ").status_code == 200
    assert calls == []
    assert message.save(mf_app.config["DATA_DIR"], "Same") is False


def test_joiners_only_between_joinable():
    family = "\U0001F468\u200d\U0001F469\u200d\U0001F467"
    assert message.clean(family)[0] == family
    assert message.clean("\u0915\u094d\u200d\u0937")[0] == "\u0915\u094d\u200d\u0937"
    assert message.clean("\u200dhi\u200d")[0] == "hi"
    assert message.clean("a \u200c b")[0] == "a b"
    assert message.clean("1\u200d2")[0] == "12"


def test_stale_tmp_swept_on_startup(mf_app):
    d = mf_app.config["DATA_DIR"]
    stale = os.path.join(d, "frame_message.abc123.tmp")
    open(stale, "w").write("half")
    os.utime(stale, (1, 1))                       # older than storage.SETTLE_S (#100 F1)
    keep = os.path.join(d, "other.tmp")
    open(keep, "w").write("x")
    create_app({"TESTING": True, "DATA_DIR": d, "DB_PATH": mf_app.config["DB_PATH"]})
    assert not os.path.exists(stale) and os.path.exists(keep)


def test_delete_fsyncs_dir(paired, mf_app, monkeypatch):
    put(paired, "Bye")
    calls = []
    real = os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: (calls.append(fd), real(fd)))
    put(paired, "")
    assert calls                                                # directory fsync after unlink


def test_phone_counter_mirrors_server():
    js = (ROOT / "static" / "settings.js").read_text()
    assert 'normalize("NFC")' in js and "\\u3164" in js and "[...v].length" in js


def test_remove_message_needs_confirm(paired):
    h = paired.get("/admin/settings", environ_base=PHONE).get_data(as_text=True)
    assert 'id="messageClear"' not in h
    assert 'id="messageRemove" class="msgRemove" hidden>Remove message<' in h      # nothing to remove yet
    put(paired, "Hi")
    h = paired.get("/admin/settings", environ_base=PHONE).get_data(as_text=True)
    assert 'id="messageRemove" class="msgRemove">Remove message<' in h
    assert 'id="messageConfirm" class="msgConfirm" role="alertdialog"' in h and 'hidden>' in h.split('id="messageConfirm"')[1][:120]
    assert 'rows="4"' in h
    js = (ROOT / "static" / "settings.js").read_text()
    remove_handler = js[js.index('removeBtn.addEventListener("click"'):].split("\n")[0]
    assert 'saveMessage("")' not in remove_handler                    # first tap only asks
    assert 'getElementById("messageRemoveYes").addEventListener("click", ()=> saveMessage(""))' in js
    css = (ROOT / "static" / "phone.css").read_text().replace(" ", "")   # restyle C: one phone stylesheet
    assert "white-space:nowrap" in css[css.index("#messageCount{"):].split("}")[0]
    assert ".msgRemove{" in css and "color:var(--mf-red-text)" in css[css.index(".msgRemove{"):].split("}")[0]   # dark: brick-text
