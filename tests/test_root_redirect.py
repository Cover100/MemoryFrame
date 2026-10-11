"""GET / : the kiosk (loopback peer) gets the slideshow; phones are sent to /admin/ (-> /pair)."""
import pytest

PHONE = {"REMOTE_ADDR": "10.42.0.23"}


@pytest.mark.parametrize("addr", ["127.0.0.1", "::1"])
def test_kiosk_gets_slideshow(client, addr):
    r = client.get("/", environ_base={"REMOTE_ADDR": addr})
    assert r.status_code == 200 and b'id="pairOverlay"' in r.data


@pytest.mark.parametrize("host", ["10.42.0.1", "localhost", "127.0.0.1", "memoryframe.lan"])
def test_phone_redirected_whatever_the_host_header(client, host):
    r = client.get("/", environ_base=PHONE, headers={"Host": host})
    assert r.status_code == 302 and r.headers["Location"].endswith("/admin/")


def test_forwarded_headers_do_not_make_a_phone_local(client):
    r = client.get("/", environ_base=PHONE, headers={"X-Forwarded-For": "127.0.0.1", "X-Real-IP": "127.0.0.1"})
    assert r.status_code == 302


def test_unpaired_phone_ends_on_pair_page(client):
    r = client.get("/", environ_base=PHONE, follow_redirects=True)
    assert r.status_code == 200 and r.request.path == "/pair"


def test_paired_phone_ends_on_admin_home(paired):
    r = paired.get("/", environ_base=PHONE, follow_redirects=True)
    assert r.status_code == 200 and r.request.path == "/admin/"
