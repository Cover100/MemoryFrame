import builtins
import pytest
import memoryframe.brightness as brightness_module


def test_get_unsupported_is_not_500(client, no_backlight):
    r = client.get("/brightness")
    assert r.status_code == 200
    assert r.get_json() == {"status": "unsupported", "supported": False,
                            "message": r.get_json()["message"]}


def test_get_supported(client, backlight):
    r = client.get("/brightness")
    assert r.status_code == 200
    assert r.get_json()["brightness"] == 100
    assert r.get_json()["maximum"] == 255
    assert r.get_json()["minimum"] == 40


def test_autodetect_other_node(client, tmp_path, monkeypatch):
    monkeypatch.delenv("MEMORYFRAME_BACKLIGHT", raising=False)
    node = tmp_path / "rpi_backlight"
    node.mkdir()
    (node / "brightness").write_text("7")
    (node / "max_brightness").write_text("31")
    monkeypatch.setattr(brightness_module, "BACKLIGHT_ROOT", str(tmp_path))
    r = client.get("/brightness").get_json()
    assert r["supported"] and r["maximum"] == 31 and r["minimum"] == 31


@pytest.mark.parametrize("value,expected", [(150, 150), (5, 40), (999, 255), (99.6, 100)])
def test_post_clamps(client, backlight, value, expected):
    r = client.post("/brightness", json={"value": value})
    assert r.status_code == 200
    assert r.get_json()["brightness"] == expected
    assert (backlight / "brightness").read_text() == str(expected)


@pytest.mark.parametrize("body", [{"value": "abc"}, {"value": None}, {"value": True}, {}, [1]])
def test_post_invalid(client, backlight, body):
    r = client.post("/brightness", json=body)
    assert r.status_code == 400
    assert (backlight / "brightness").read_text() == "100\n"


def test_post_nan_and_non_json(client, backlight):
    assert client.post("/brightness", data='{"value": NaN}',
                       content_type="application/json").status_code == 400
    assert client.post("/brightness", data="value=5").status_code == 400


def test_post_unsupported(client, no_backlight):
    r = client.post("/brightness", json={"value": 100})
    assert r.status_code == 409
    assert r.get_json()["supported"] is False


def test_post_write_error(client, backlight, monkeypatch):
    # Fake the write failing (chmod-based tests don't work as root).
    real_open = builtins.open

    def failing_open(path, mode="r", *args, **kwargs):
        if str(path).endswith("brightness") and "w" in mode:
            raise PermissionError("read-only")
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", failing_open)
    r = client.post("/brightness", json={"value": 100})
    assert r.status_code == 503
    assert r.get_json()["status"] == "error"


def test_get_read_error_is_unsupported(client, backlight):
    (backlight / "brightness").write_text("garbage")
    r = client.get("/brightness")
    assert r.status_code == 200
    assert r.get_json()["supported"] is False


def test_old_get_with_side_effect_route_removed(client, backlight):
    assert client.get("/brightness/200").status_code == 404
    assert (backlight / "brightness").read_text() == "100\n"
