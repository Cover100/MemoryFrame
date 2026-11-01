"""#102 Wren nit: the 'frame is almost full' batch summary counts photos and videos separately (upload.js)."""
import json
import os
import re
import shutil
import subprocess

import pytest

JS = os.path.join(os.path.dirname(__file__), "..", "static", "upload.js")


def summary(photos, videos):
    src = open(JS).read()
    fn = re.search(r"function mfAddedSummary\(.*?\n}\n", src, re.S).group(0)
    out = subprocess.run(["node", "-e", fn + "console.log(JSON.stringify(mfAddedSummary(%d, %d)))" % (photos, videos)],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


@pytest.mark.skipif(not shutil.which("node"), reason="needs node")
@pytest.mark.parametrize("p,v,text", [
    (0, 0, "0 photos were added; the frame is almost full."),
    (1, 0, "1 photo was added; the frame is almost full."),
    (3, 0, "3 photos were added; the frame is almost full."),
    (0, 1, "1 video was added; the frame is almost full."),
    (0, 2, "2 videos were added; the frame is almost full."),
    (2, 1, "2 photos and 1 video were added; the frame is almost full."),
    (1, 1, "1 photo and 1 video were added; the frame is almost full."),
])
def test_full_summary_counts_kinds(p, v, text):
    assert summary(p, v) == text


def test_summary_uses_response_kind():
    src = open(JS).read()
    assert 'ui.kind = body.kind === "video"' in src and "mfAddedSummary(added - addedVideos, addedVideos)" in src
