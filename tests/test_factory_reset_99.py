"""#99: Frame tools (3 s hold) -> Reset the frame. Decision A1: Erase everything removes photos, the DB
(pairings, sessions, settings) and the frame message; the hotspot name/password stay. Keep my photos
resets pairing, sessions, settings and the message only."""
import hashlib
import os
import subprocess
from pathlib import Path

import pytest

from test_frontend_browser import evaluate, net, page, server, text  # noqa: F401  (fixtures)
from test_mf51_product_default import ev, visible

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "deploy" / "factory-reset.sh"
LOCAL = {"REMOTE_ADDR": "127.0.0.1"}
ERASED = ["originals", "display", "thumbs", "tmp", "backups", "memoryframe.db", "memoryframe.db-wal",
          "memoryframe.db-shm", "frame_message.txt"]


# ---------------- root helper (temp dirs + stubs) ----------------

@pytest.fixture
def fr(tmp_path):
    data = tmp_path / "var-lib-memoryframe"
    for d in ("originals", "display", "thumbs", "tmp", "backups"):
        (data / d).mkdir(parents=True)
        (data / d / "a.jpg").write_bytes(os.urandom(64))
    for f in ("memoryframe.db", "memoryframe.db-wal", "memoryframe.db-shm", "frame_message.txt"):
        (data / f).write_text(f)
    (data / "keep-me.txt").write_text("not in the erase set")
    outside = tmp_path / "outside"                          # stands in for /opt, ssh, /etc/memoryframe, boot
    outside.mkdir()
    for f in ("authorized_keys", "hotspot.conf", "memoryframe-mode.txt", "app.py"):
        (outside / f).write_text(f + "-content")
    run_dir = tmp_path / "run-app"
    run_dir.mkdir(mode=0o700)
    stub = tmp_path / "stub"
    stub.mkdir()
    log = tmp_path / "log"
    (stub / "systemctl").write_text(f'#!/bin/sh\necho "systemctl $*" >> {log}\n')
    (stub / "fstrim").write_text(f'#!/bin/sh\necho "fstrim $*" >> {log}\n')
    for s in ("systemctl", "fstrim"):
        os.chmod(stub / s, 0o755)
    env = dict(os.environ, MEMORYFRAME_DATA_DIR=str(data), MEMORYFRAME_RESET_REQ=str(run_dir / "factory-reset"),
               SYSTEMCTL=str(stub / "systemctl"), FSTRIM=str(stub / "fstrim"), SYNC="true")

    class F:
        pass
    f = F()
    f.data, f.outside, f.req, f.log, f.env, f.stub = data, outside, run_dir / "factory-reset", log, env, stub
    f.run = lambda **kw: subprocess.run(["bash", str(SCRIPT)], env={**env, **kw}, capture_output=True,
                                        text=True, timeout=30)
    f.request = lambda body="erase\n": f.req.write_text(body)
    return f


def hashes(d):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(d.iterdir())}


def end_state_ok(f):
    names = sorted(p.name for p in f.data.iterdir())
    assert names == sorted(["backups", "display", "keep-me.txt", "originals", "thumbs", "tmp"]), names
    for d in ("originals", "display", "thumbs", "tmp", "backups"):
        assert list((f.data / d).iterdir()) == [] and oct((f.data / d).stat().st_mode & 0o777) == "0o750"
    assert not (f.data / ".factory-reset").exists()


def test_erase_exact_set_and_nothing_else(fr):
    before = hashes(fr.outside)
    fr.request()
    r = fr.run()
    assert r.returncode == 0, r.stderr
    end_state_ok(fr)
    assert (fr.data / "keep-me.txt").read_text() == "not in the erase set"
    assert hashes(fr.outside) == before
    assert not fr.req.exists()
    log = fr.log.read_text().splitlines()
    assert log[0] == "systemctl stop memoryframe.service"
    assert log[-1] == "systemctl --no-block start memoryframe.service"
    assert any(l.startswith("fstrim ") for l in log)
    s = SCRIPT.read_text()
    assert "nmcli" not in s and "hotspot.conf" not in s.split("set -euo")[1]   # Wi-Fi never touched


def test_marker_removed_last(fr):
    s = SCRIPT.read_text().split("erase() {", 1)[1].split("\n}\n", 1)[0]
    assert s.index('rm -f "$MARKER"') > s.index("rm -rf --one-file-system -- \"$t\"") > s.index("mv -f -t")
    assert s.index('rm -f "$MARKER"') < s.index("--no-block start")


def test_no_request_no_marker_does_nothing(fr):
    before = sorted(p.name for p in fr.data.iterdir())
    r = fr.run()
    assert r.returncode == 0 and sorted(p.name for p in fr.data.iterdir()) == before
    assert not fr.log.exists()


@pytest.mark.parametrize("body", ["keep\n", "erase B\n", "", "rm -rf /\n"])
def test_bad_request_refused(fr, body):
    fr.request(body)
    r = fr.run()
    assert r.returncode == 2 and (fr.data / "originals" / "a.jpg").exists() and not fr.req.exists()


def test_symlink_request_refused(fr, tmp_path):
    target = tmp_path / "t"
    target.write_text("erase\n")
    fr.req.symlink_to(target)
    r = fr.run()
    assert r.returncode == 2 and (fr.data / "originals" / "a.jpg").exists()


@pytest.mark.parametrize("kill_at", [1, 2, 3])
def test_power_cut_then_boot_finishes(fr, kill_at):
    """Kill the helper at each sync point (after the marker, after the moves, after the marker is gone);
    the boot run (no request) must reach the same end state."""
    cnt = fr.stub / "n"
    cnt.write_text("0")
    sync = fr.stub / "sync"
    sync.write_text(f'#!/bin/sh\nn=$(( $(cat {cnt}) + 1 )); echo $n > {cnt}\n'
                    f'[ "$n" -ge {kill_at} ] && kill -9 $PPID\nexit 0\n')
    os.chmod(sync, 0o755)
    fr.request()
    fr.run(SYNC=str(sync))
    if kill_at < 3:
        assert (fr.data / ".factory-reset").exists()
    r = fr.run()                                             # boot
    assert r.returncode == 0, r.stderr
    end_state_ok(fr)
    assert not any(p.name.startswith(".erasing-") for p in fr.data.iterdir())


def test_boot_finishes_half_moved(fr):
    (fr.data / ".factory-reset").write_text("erase 123\n")
    (fr.data / ".erasing-123").mkdir()
    os.rename(fr.data / "originals", fr.data / ".erasing-123" / "originals")
    r = fr.run()
    assert r.returncode == 0
    end_state_ok(fr)
    assert "stop" not in fr.log.read_text()                 # boot: the app has not started yet


def test_root_ignores_test_hooks():
    s = SCRIPT.read_text()
    assert 'case "$_v" in MEMORYFRAME_*|SYSTEMCTL|FSTRIM|SYNC) unset "$_v"' in s
    assert "export PATH=/usr/sbin:/usr/bin:/sbin:/bin" in s
    assert "iflag=nofollow,nonblock" in s


def test_unit_runs_at_boot_before_app():
    u = (ROOT / "deploy" / "units" / "memoryframe-factory-reset.service").read_text()
    assert "Before=memoryframe.service" in u and "WantedBy=multi-user.target" in u
    assert "ExecStart=/usr/local/sbin/memoryframe-factory-reset" in u and "Type=oneshot" in u
    inst = (ROOT / "deploy" / "install.sh").read_text()
    assert "/usr/local/sbin/memoryframe-factory-reset" in inst and "enable memoryframe-factory-reset.service" in inst
    assert "memoryframe-factory-reset" in (ROOT / "deploy" / "uninstall.sh").read_text()


# ---------------- app ----------------

def _token(client):
    html = client.get("/", environ_base=LOCAL).get_data(as_text=True)
    return html.split('name="mf-frame-token" content="', 1)[1].split('"', 1)[0]


def _seed(mf_app):
    from memoryframe import db as mfdb, message
    with mf_app.app_context():
        conn = mfdb.get_db()
        with conn:
            conn.execute("INSERT INTO devices (id, name, created_at) VALUES (1, 'p', 0)")
            conn.execute("INSERT INTO sessions (device_id, token_hash, created_at, expires_at) VALUES (1,'h',0,9e9)")
            conn.execute("INSERT INTO settings (key, value) VALUES ('k', 'v')")
        message.save(mf_app.config["DATA_DIR"], "hello")


def test_keep_photos_resets_db_rows_and_message(client, mf_app):
    _seed(mf_app)
    r = client.post("/frame/reset", json={"mode": "keep-photos"}, headers={"X-Frame-Token": _token(client)},
                    environ_base=LOCAL)
    assert r.status_code == 200 and r.get_json()["ok"] is True
    from memoryframe import db as mfdb
    with mf_app.app_context():
        c = mfdb.get_db()
        for t in ("devices", "sessions", "settings"):
            assert c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] == 0
    assert not (Path(mf_app.config["DATA_DIR"]) / "frame_message.txt").exists()


def test_erase_starts_helper(client, mf_app, tmp_path):
    out = tmp_path / "ran"
    mf_app.config["FACTORY_RESET_CMD"] = ["sh", "-c", f"echo erase > {out}"]
    r = client.post("/frame/reset", json={"mode": "erase"}, headers={"X-Frame-Token": _token(client)},
                    environ_base=LOCAL)
    assert r.status_code == 202 and out.read_text().strip() == "erase"


def test_erase_writes_request_and_uses_no_block(client, mf_app, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: calls.append(argv) or
                        subprocess.CompletedProcess(argv, 0))
    mf_app.config["PRIV_INPUT_DIR"] = str(tmp_path)
    r = client.post("/frame/reset", json={"mode": "erase"}, headers={"X-Frame-Token": _token(client)},
                    environ_base=LOCAL)
    assert r.status_code == 202
    assert (tmp_path / "factory-reset").read_text() == "erase\n"
    assert calls == [["systemctl", "start", "--no-block", "memoryframe-factory-reset.service"]]


def test_reset_localhost_and_token_only(client, mf_app, tmp_path):
    out = tmp_path / "ran"
    mf_app.config["FACTORY_RESET_CMD"] = ["sh", "-c", f"echo x > {out}"]
    t = _token(client)
    assert client.post("/frame/reset", json={"mode": "erase"}, environ_base=LOCAL).status_code == 403
    assert client.post("/frame/reset", json={"mode": "erase"}, headers={"X-Frame-Token": t},
                       environ_base={"REMOTE_ADDR": "10.42.0.23"}).status_code == 403
    assert client.post("/frame/reset", json={"mode": "B"}, headers={"X-Frame-Token": t},
                       environ_base=LOCAL).status_code == 400
    assert not out.exists()


def test_reset_refused_while_shutting_down_or_wifi_change(client, mf_app, tmp_path, monkeypatch):
    out = tmp_path / "ran"
    mf_app.config["FACTORY_RESET_CMD"] = ["sh", "-c", f"echo x > {out}"]
    t = _token(client)
    from memoryframe import wifi
    monkeypatch.setattr(wifi, "busy", lambda: True)
    r = client.post("/frame/reset", json={"mode": "erase"}, headers={"X-Frame-Token": t}, environ_base=LOCAL)
    assert r.status_code == 409 and "Wi-Fi change" in r.get_json()["reason"]
    monkeypatch.setattr(wifi, "busy", lambda: False)
    mf_app.extensions["mf_shutdown"] = {"synced": True}
    r = client.post("/frame/reset", json={"mode": "keep-photos"}, headers={"X-Frame-Token": t},
                    environ_base=LOCAL)
    assert r.status_code in (409, 503)                     # writes are refused once shutdown has synced
    assert not out.exists()


# ---------------- browser ----------------

def _setup(page, mf_app, tmp_path):
    out = tmp_path / "reset.log"
    mf_app.config["FACTORY_RESET_CMD"] = ["sh", "-c", f"echo erase >> {out}"]
    page.pump(2.5)
    evaluate(page, "window.mfWaitForRestart=()=>{window.__waiting=true};")
    return out, evaluate(page, "window.innerWidth")


def _hold_open(page, w):
    ev(page, "pointerdown", w // 2, 240); page.pump(3.3); ev(page, "pointerup", w // 2, 240)
    page.pump(0.2)


def _btn(page, id_, t, x=None):
    evaluate(page, f"(()=>{{const b=document.getElementById('{id_}');const r=b.getBoundingClientRect();"
                   f"b.dispatchEvent(new PointerEvent('{t}',{{bubbles:true,cancelable:true,pointerId:9,"
                   f"clientX:r.left+r.width/2,clientY:r.top+r.height/2}}));}})()")


def test_tools_menu_defaults_and_fit(page, server, mf_app, net, tmp_path):
    out, w = _setup(page, mf_app, tmp_path)
    _hold_open(page, w)
    assert visible(page, "toolsOverlay")
    assert evaluate(page, "document.activeElement.id") == "toolsClose"
    t = text(page, "toolsMenu")
    assert "Frame tools" in t and "Reset the frame" in t and "Join developer Wi-Fi" in t
    r = evaluate(page, "(()=>{const r=document.getElementById('toolsBox').getBoundingClientRect();"
                       "return [r.top>=0, r.bottom<=innerHeight, r.left>=0, r.right<=innerWidth];})()")
    assert all(r)
    evaluate(page, "document.getElementById('toolsReset').click()")
    assert evaluate(page, "document.activeElement.id") == "resetKeep"
    evaluate(page, "document.getElementById('resetErase').click()")
    assert evaluate(page, "document.activeElement.id") == "resetEraseCancel"
    assert "The Wi-Fi name and password stay the same" in text(page, "resetEraseAsk")
    evaluate(page, "document.getElementById('resetEraseCancel').click()")
    assert not visible(page, "toolsOverlay") and not out.exists()


def test_erase_needs_2s_hold_early_release_cancels(page, server, mf_app, net, tmp_path):
    out, w = _setup(page, mf_app, tmp_path)
    _hold_open(page, w)
    evaluate(page, "document.getElementById('toolsReset').click()")
    evaluate(page, "document.getElementById('resetErase').click()")
    evaluate(page, "document.getElementById('resetEraseHold').click()")      # a tap never erases
    page.pump(0.3)
    assert not out.exists()
    _btn(page, "resetEraseHold", "pointerdown"); page.pump(1.5); _btn(page, "resetEraseHold", "pointerup")
    page.pump(1.0)
    assert not out.exists() and evaluate(page, "document.getElementById('resetCover').hidden")
    _btn(page, "resetEraseHold", "pointerdown"); page.pump(2.4)
    assert not evaluate(page, "document.getElementById('resetCover').hidden")
    assert "Don't unplug" in text(page, "resetCover")
    page.pump(0.6)
    assert out.read_text().strip() == "erase" and evaluate(page, "window.__waiting === true")


def test_keep_photos_confirm_then_reload(page, server, mf_app, net, tmp_path):
    out, w = _setup(page, mf_app, tmp_path)
    _hold_open(page, w)
    evaluate(page, "document.getElementById('toolsReset').click()")
    evaluate(page, "document.getElementById('resetKeep').click()")
    assert evaluate(page, "document.activeElement.id") == "resetKeepCancel"
    assert "Photos stay." in text(page, "resetKeepAsk")
    evaluate(page, "window.__before = 1; document.getElementById('resetKeepConfirm').click()")
    page.pump(2.5)
    assert evaluate(page, "window.__before === undefined")                   # reloaded
    assert not out.exists()


# ---------------- Sage MF-113-1 / MF-113-2: planted symlinks in the app-owned data dir ----------------

def test_planted_marker_tmp_and_marker_symlinks_never_written_through(fr):
    victim = fr.outside / "authorized_keys"
    before = victim.read_text()
    (fr.data / ".factory-reset.tmp").symlink_to(victim)      # the old fixed tmp name
    (fr.data / ".factory-reset").symlink_to(fr.outside / "hotspot.conf")
    fr.request()
    r = fr.run()
    assert r.returncode == 0, r.stderr
    assert victim.read_text() == before and (fr.outside / "hotspot.conf").read_text() == "hotspot.conf-content"
    assert fr.log.read_text().splitlines()[0] == "systemctl stop memoryframe.service"   # stop before the marker
    s = SCRIPT.read_text()
    assert 'mktemp "$DATA/.factory-reset.XXXXXX"' in s and 'mv -fT -- "$tmp" "$MARKER"' in s
    assert '> "$MARKER.tmp"' not in s


def test_planted_erasing_symlink_on_demand_is_unlinked_not_followed(fr, tmp_path):
    import time
    trap = tmp_path / "attacker"
    trap.mkdir()
    now = int(time.time())
    for i in range(-2, 30):                                  # the id is date +%s: cover the run
        (fr.data / f".erasing-{now + i}").symlink_to(trap)
    fr.request()
    r = fr.run()
    assert r.returncode == 0, r.stderr
    assert list(trap.iterdir()) == []                        # nothing moved into the attacker's dir
    end_state_ok(fr)


def test_planted_erasing_symlink_and_foreign_dir_on_boot(fr, tmp_path):
    trap = tmp_path / "attacker"
    trap.mkdir()
    (fr.data / ".factory-reset").write_text("erase 4242\n")   # mfapp chose the id
    (fr.data / ".erasing-4242").symlink_to(trap)
    r = fr.run()
    assert r.returncode == 0, r.stderr
    assert list(trap.iterdir()) == []
    end_state_ok(fr)
    # a pre-made directory that isn't ours/0700 is removed, not reused
    (fr.data / ".factory-reset").write_text("erase 4343\n")
    d = fr.data / ".erasing-4343"
    d.mkdir(mode=0o755); os.chmod(d, 0o755)
    (fr.data / "frame_message.txt").write_text("m")
    r = fr.run()
    assert r.returncode == 0, r.stderr
    end_state_ok(fr)
