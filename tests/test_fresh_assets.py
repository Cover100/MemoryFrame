"""Deploys must not leave the kiosk on stale JS: content-hashed static URLs, no-cache HTML, and install.sh
restarts the kiosk launcher (#97: kiosk-restart.sh) and never on CI."""
import os
import re
import time

import pytest

from test_install_never_arms_product import install
from test_netmode_script import env  # noqa: F401  (fixture)

LOCAL = {"REMOTE_ADDR": "127.0.0.1"}


def urls(html):
    return re.findall(r'(?:src|href)="(/static/[^"]+)"', html)


def test_kiosk_page_assets_versioned_and_html_no_cache(client):
    r = client.get("/", environ_base=LOCAL)
    found = urls(r.get_data(as_text=True))
    assert {u.split("?")[0] for u in found} >= {"/static/script.js", "/static/pairing.js", "/static/style.css"}
    assert all(re.search(r"\?v=[0-9a-f]{10}$", u) for u in found), found
    assert r.headers["Cache-Control"] == "no-cache"
    assert client.get(found[0], environ_base=LOCAL).status_code == 200


def test_no_unversioned_static_refs_left_in_templates():
    from pathlib import Path
    for f in (Path(__file__).resolve().parents[1] / "templates").glob("*.html"):
        assert not re.search(r'(?:src|href)="/static/', f.read_text()), f.name


def test_version_changes_when_file_changes(mf_app, tmp_path):
    from memoryframe import assets
    (tmp_path / "x.js").write_text("one")
    mf_app.static_folder = str(tmp_path)
    with mf_app.test_request_context():
        a = assets.static("x.js")
        time.sleep(0.01)
        (tmp_path / "x.js").write_text("two")
        os.utime(tmp_path / "x.js", ns=(time.time_ns(), time.time_ns() + 10**9))
        b = assets.static("x.js")
        assert a != b and a.startswith("/static/x.js?v=") and b.startswith("/static/x.js?v=")
        assert assets.static("missing.js") == "/static/missing.js"


def _stubs(tmp_path):
    b = tmp_path / "kbin"; b.mkdir(exist_ok=True)
    (b / "kiosk-restart").write_text('#!/bin/bash\necho "kiosk-restart $*" >> "$INSTALL_LOG"\n')
    (b / "kiosk-restart").chmod(0o755)
    return {"KIOSK_RESTART": str(b / "kiosk-restart"), "CI": ""}


def test_install_restarts_the_kiosk_launcher(env, tmp_path):
    """#97: the launcher itself is restarted (deploy/kiosk-restart.sh), not only Chromium."""
    env["env"].update(_stubs(tmp_path))
    log = install(tmp_path, env)
    assert "kiosk-restart " in log and log.split("kiosk-restart ", 1)[1].split()[0].endswith("/deploy/kiosk.sh")


@pytest.mark.parametrize("var", [{"CI": "true"}, {"MEMORYFRAME_NO_KIOSK_RELOAD": "1"}])
def test_install_never_reloads_on_ci(env, tmp_path, var):
    env["env"].update(_stubs(tmp_path))
    env["env"].update(var)
    log = install(tmp_path, env)
    assert "kiosk-restart" not in log
