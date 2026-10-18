"""#75 (Hollis at c4d8d9b): the phone's Remove message / Remove / Keep buttons render as designed,
and Save can't silently clear the message. Headless Chrome at 412x915."""
import json
import os

from memoryframe import message
from test_frontend_browser import evaluate, net, page, server  # noqa: F401  (fixtures)
from test_wifi_browser import _phone

RED, GREEN, WHITE = "rgb(168, 53, 42)", "rgb(42, 97, 64)", "rgb(255, 255, 255)"
SHOT_DIR = os.environ.get("MF_SHOT_DIR")


def style(page, sel):
    return json.loads(evaluate(page, f"""JSON.stringify((()=>{{const e=document.querySelector('{sel}');
        const c=getComputedStyle(e); return {{color:c.color, bg:c.backgroundColor, border:c.borderTopColor,
        bw:c.borderTopWidth, w:e.getBoundingClientRect().width}};}})())"""))


def test_remove_prompt_buttons_and_save_guard(page, server, mf_app, net):
    message.save(mf_app.config["DATA_DIR"], "Happy birthday, Nana!")
    page.call("Emulation.setDeviceMetricsOverride", {"width": 412, "height": 915, "deviceScaleFactor": 1, "mobile": True})
    _phone(page, server, mf_app, "/admin/settings")
    evaluate(page, "document.getElementById('messageRemove').click()")
    page.pump(0.3)
    assert evaluate(page, "!document.getElementById('messageConfirm').hidden")
    assert evaluate(page, "document.activeElement.id") == "messageRemoveNo"
    page_w = evaluate(page, "document.getElementById('messageSection').getBoundingClientRect().width")

    rm, yes, keep = style(page, "#messageRemove"), style(page, "#messageRemoveYes"), style(page, "#messageRemoveNo")
    assert rm["color"] == RED and rm["bg"] in ("rgba(0, 0, 0, 0)", "transparent"), rm
    assert yes["bg"] == RED and yes["color"] == WHITE, yes
    assert keep["bg"] in ("rgba(0, 0, 0, 0)", "transparent") and keep["color"] == GREEN and keep["border"] == GREEN, keep
    for b in (rm, yes, keep):
        assert b["w"] < page_w * 0.6, (b, page_w)                    # not full width
    assert evaluate(page, "getComputedStyle(document.getElementById('messageRemove')).minHeight") in ("0px", "auto")

    if SHOT_DIR:
        import base64
        os.makedirs(SHOT_DIR, exist_ok=True)
        evaluate(page, "document.getElementById('messageConfirm').scrollIntoView({block:'center'})")
        png = page.call("Page.captureScreenshot", {"format": "png"})["result"]["data"]
        open(os.path.join(SHOT_DIR, "phone-remove-prompt.png"), "wb").write(base64.b64decode(png))

    # Save: enabled with text, disabled when the cleaned box is empty while a message exists
    assert evaluate(page, "document.getElementById('messageSave').disabled") is False
    evaluate(page, "(()=>{const t=document.getElementById('frameMessage'); t.value='  \\u3164 \\n';"
                   "t.dispatchEvent(new Event('input'));})()")
    assert evaluate(page, "document.getElementById('messageSave').disabled") is True
    evaluate(page, "document.getElementById('messageSave').click()")
    page.pump(0.5)
    assert message.get(mf_app.config["DATA_DIR"]) == "Happy birthday, Nana!"     # not cleared
    # Remove -> cleared; with nothing stored, an empty box no longer disables Save
    evaluate(page, "document.getElementById('messageRemoveYes').click()")
    page.pump(1)
    assert message.get(mf_app.config["DATA_DIR"]) == ""
    assert evaluate(page, "document.getElementById('messageRemove').hidden") is True
    assert evaluate(page, "document.getElementById('messageSave').disabled") is False
