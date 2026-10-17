"""Deploys must not leave the kiosk on stale JS: content-hashed static URLs, no-cache HTML, and install.sh
restarts the kiosk Chromium (kiosk.sh relaunches it) only when one is running and never on CI."""
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


def _stubs(tmp_path, running):
    b = tmp_path / "kbin"; b.mkdir(exist_ok=True)
    (b / "pgrep").write_text(f'#!/bin/bash\necho "pgrep $*" >> "$INSTALL_LOG"\nexit {0 if running else 1}\n')
    (b / "pkill").write_text('#!/bin/bash\necho "pkill $*" >> "$INSTALL_LOG"\n')
    for n in ("pgrep", "pkill"):
        (b / n).chmod(0o755)
    return {"PGREP": str(b / "pgrep"), "PKILL": str(b / "pkill"), "CI": ""}


@pytest.mark.parametrize("running", [True, False])
def test_install_reloads_kiosk_only_if_running(env, tmp_path, running):
    env["env"].update(_stubs(tmp_path, running))
    log = install(tmp_path, env)
    assert "pgrep -u" in log and "--user-data-dir=" in log and "chromium-profile" in log
    assert ("pkill -u" in log) is running


@pytest.mark.parametrize("var", [{"CI": "true"}, {"MEMORYFRAME_NO_KIOSK_RELOAD": "1"}])
def test_install_never_reloads_on_ci(env, tmp_path, var):
    env["env"].update(_stubs(tmp_path, True))
    env["env"].update(var)
    log = install(tmp_path, env)
    assert "pgrep" not in log and "pkill" not in log
