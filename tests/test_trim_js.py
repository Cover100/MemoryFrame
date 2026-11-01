"""#103: the trim screen's pure helpers in static/trim.js, run in Node (skipped without node)."""
import json
import os
import shutil
import subprocess

import pytest

JS = os.path.join(os.path.dirname(__file__), "..", "static", "trim.js")
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="needs node")


def js(expr):
    out = subprocess.run(["node", "-e", open(JS).read() + "\nconsole.log(JSON.stringify(" + expr + "))"],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def test_default_is_first_30s_or_whole_clip():
    assert js("[mfTrimDefault(74000), mfTrimDefault(12500), mfTrimDefault(400)]") == [
        {"start": 0, "end": 30000}, {"start": 0, "end": 12500}, {"start": 0, "end": 400}]


def test_handles_stay_one_second_apart_and_inside():
    assert js('mfTrimMove({start: 0, end: 30000}, "start", 29800, 74000)') == {"start": 29000, "end": 30000}
    assert js('mfTrimMove({start: 6000, end: 30000}, "end", 100, 74000)') == {"start": 6000, "end": 7000}
    assert js('mfTrimMove({start: 6000, end: 30000}, "end", 99999, 74000)') == {"start": 6000, "end": 74000}
    assert js('mfTrimMove({start: 6000, end: 30000}, "start", -5, 74000)') == {"start": 0, "end": 30000}


@pytest.mark.parametrize("key,which,expect", [
    ("ArrowRight", "start", 500), ("ArrowLeft", "end", 29500), ("PageUp", "start", 5000),
    ("PageDown", "end", 25000), ("Home", "start", 0), ("End", "end", 74000), ("Home", "end", 1000)])
def test_keys(key, which, expect):
    assert js('mfTrimKey({start: 0, end: 30000}, "%s", "%s", 74000)' % (which, key))[which] == expect


def test_other_keys_ignored():
    assert js('mfTrimKey({start: 0, end: 30000}, "start", "a", 74000)') is None


def test_messages_match_the_frame():
    assert js("[mfTrimError({start: 0, end: 42000}), mfTrimError({start: 0, end: 900}), mfTrimError({start: 0, end: 30000})]") == [
        "That part is 0:42. Choose 30 seconds or less.", "That part is under 1 second. Choose at least 1 second.", ""]


def test_milestones_once_each():
    assert js("[mfMilestone(0, 10), mfMilestone(10, 26), mfMilestone(26, 30), mfMilestone(30, 80), mfMilestone(80, 99)]") == [
        0, 25, 0, 75, 0]


def test_upload_sends_whole_ms_fields_only_after_the_picker():
    src = open(os.path.join(os.path.dirname(JS), "upload.js")).read()
    assert 'form.append("trim_start_ms", String(trim.start))' in src and "mfTrimPick(files[i], dur)" in src
