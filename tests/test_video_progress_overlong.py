"""Sage (#118 nit): after an over-long ffmpeg -progress line is dropped, the rest of that line (up to its newline)
is skipped too, so its tail can never be parsed as progress. Real pipe, real process; no select patching."""
import subprocess
import sys

from memoryframe import video

EMIT = r"""
import os, sys, time
w = lambda b: (os.write(1, b), time.sleep(0.15))
w(b"out_time_us=1000000\n")
w(b"x" * 1500)                      # over MAX_PROGRESS_LINE, no newline yet: dropped
w(b"y" * 3000)                      # more of the same line in a later read
w(b"\nout_time_us=999000000")       # after the newline: a real progress line, kept
w(b"\n")
w(b"z" * 2000 + b"out_time_us=777000000\n")   # an overlong line whose tail looks like progress
w(b"out_time_us=2000000\n")
"""


def test_tail_of_overlong_line_is_never_parsed():
    seen = []
    video._run_progress([sys.executable, "-c", EMIT], 30, subprocess.Popen, on_time=seen.append)
    assert seen == [1.0, 999.0, 2.0]
    assert 777.0 not in seen


def test_tail_after_dropped_partial_is_skipped_to_its_newline():
    emit = (r"""
import os, time
os.write(1, b"z" * 1100); time.sleep(0.3)                  # over the cap, no newline: dropped
os.write(1, b"out_time_us=555000000\nout_time_us=3000000\n")   # its tail looks like a whole progress line
""")
    seen = []
    video._run_progress([sys.executable, "-c", emit], 30, subprocess.Popen, on_time=seen.append)
    assert seen == [3.0]
