"""MF-51: boot defaults to product; 'dev' mode file is a persistent guard; menu Dev mode lasts
until reboot; install guard; never modify client profiles."""
import re
import time

import pytest

from test_frontend_browser import evaluate, net, page, server, text  # noqa: F401  (fixtures)
from test_install_never_arms_product import install
from test_netmode_script import HOME, HOT, OTHER, ac, active, env, run, state  # noqa: F401

LOCAL = {"REMOTE_ADDR": "127.0.0.1"}


def client_modifies(env):
    log = (env["d"] / "log").read_text().splitlines()
    return [l for l in log if " modify " in l and HOT not in l and "MemoryFrame-" not in l]


def reboot(env, tmp):
    """/run is tmpfs: wipe the state dir and change the boot id, then run the boot service."""
    import shutil
    shutil.rmtree(env["state"], ignore_errors=True)
    (tmp / "boot_id").write_text(f"boot-{time.time()}\n")
    r = run(env, "boot")
    assert r.returncode == 0, r.stderr
    return r


def test_boot_without_mode_file_is_product(env):
    reboot(env, env["tmp"])
    assert active(env) == HOT and state(env)["mode"] == "product"
    assert ac(env, HOT) == "yes" and client_modifies(env) == []
    assert "armed for next boot: product" in run(env, "status").stdout


@pytest.mark.parametrize("content", ["dev\n", " DEV \r\n"])
def test_dev_file_is_persistent_dev(env, content):
    (env["boot"] / "memoryframe-mode.txt").write_bytes(content.encode())
    for _ in range(2):
        reboot(env, env["tmp"])
        assert active(env) == HOME and state(env)["mode"] == "dev"
    assert (env["boot"] / "memoryframe-mode.txt").read_bytes() == content.encode()
    assert client_modifies(env) == []


def test_menu_dev_mode_until_reboot(env):
    reboot(env, env["tmp"])
    assert active(env) == HOT
    r = run(env, "dev-temp")
    assert r.returncode == 0, r.stderr
    assert active(env) == HOME and state(env)["requested"] == "dev-temp"
    assert not (env["boot"] / "memoryframe-mode.txt").exists()     # temporary: no mode file
    assert ac(env, HOT) == "yes"                                    # hotspot still the boot default
    reboot(env, env["tmp"])
    assert active(env) == HOT and state(env)["mode"] == "product"
    assert client_modifies(env) == []


def test_menu_dev_mode_home_wifi_fails_falls_back_to_hotspot(env):
    reboot(env, env["tmp"])
    (env["d"] / "fail").write_text(HOME + "\n" + OTHER + "\n")
    run(env, "dev-temp")
    assert active(env) == HOT and client_modifies(env) == []


def test_hotspot_failure_at_boot_keeps_previous(env):
    (env["d"] / "fail").write_text(HOT + "\n")
    reboot(env, env["tmp"])
    assert active(env) == HOME and state(env)["hotspot_error"] is True
    assert client_modifies(env) == []


def test_install_guard_writes_dev_on_home_wifi(env, tmp_path):
    log = install(tmp_path, env)
    assert re.search(r"tee \S*/boot/memoryframe-mode\.txt", log), log


def test_install_guard_skips_when_file_exists_or_on_hotspot(env, tmp_path):
    (env["boot"] / "memoryframe-mode.txt").write_text("product\n")
    assert "memoryframe-mode.txt" not in install(tmp_path, env)
    (env["boot"] / "memoryframe-mode.txt").unlink()
    (env["d"] / "active").write_text(HOT + "\n")
    assert "memoryframe-mode.txt" not in install(tmp_path, env)


# ---------- app endpoint ----------

def _token(client):
    html = client.get("/", environ_base=LOCAL).get_data(as_text=True)
    return html.split('name="mf-frame-token" content="', 1)[1].split('"', 1)[0]


@pytest.fixture
def stub(mf_app, tmp_path):
    out = tmp_path / "dev.log"
    mf_app.config["DEV_MODE_CMD"] = ["sh", "-c", f"echo dev-temp >> {out}"]
    mf_app.config["DEV_MODE_DELAY"] = 0.05
    return out


def test_endpoint_runs_helper_with_token(client, stub):
    r = client.post("/frame/dev-mode", headers={"X-Frame-Token": _token(client)}, environ_base=LOCAL)
    assert r.status_code == 200
    time.sleep(0.5)
    assert stub.read_text().strip() == "dev-temp"


def test_endpoint_rejects_phone_and_missing_token(client, stub):
    t = _token(client)
    assert client.post("/frame/dev-mode", environ_base=LOCAL).status_code == 403
    assert client.post("/frame/dev-mode", headers={"X-Frame-Token": t},
                       environ_base={"REMOTE_ADDR": "10.42.0.23"}).status_code == 403
    time.sleep(0.3)
    assert not stub.exists()


def test_sudoers_allows_dev_temp_only_addition():
    from pathlib import Path
    s = (Path(__file__).resolve().parents[1] / "deploy" / "memoryframe-sudoers").read_text()
    assert "memoryframe-netmode dev-temp" in s
    assert not re.search(r"memoryframe-netmode (product|dev|boot)\b(?!-)", s)


def test_menu_dev_mode_confirm(page, server, mf_app, net, tmp_path):
    out = tmp_path / "d.log"
    mf_app.config["DEV_MODE_CMD"] = ["sh", "-c", f"echo dev-temp >> {out}"]
    mf_app.config["DEV_MODE_DELAY"] = 0.05
    page.pump(2.5)
    evaluate(page, "memoryFramePairing.openMenu()")
    page.pump(0.3)
    evaluate(page, "document.getElementById('menuDevMode').click()")
    page.pump(0.3)
    assert "Switch to Dev mode?" in text(page, "devModeBox")
    evaluate(page, "document.getElementById('devModeConfirm').click()")
    page.pump(1.0)
    assert "home Wi-Fi" in text(page, "devModeBox") and out.read_text().strip() == "dev-temp"
