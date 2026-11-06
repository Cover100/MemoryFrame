#!/usr/bin/env python3
"""Card/journal measurement parsing + valid delta checks."""
import re, tempfile, pathlib, sys

def card_from_text(t):
    m = re.search(r'card_bytes_written=(\d+)', t)
    if not m:
        raise ValueError('no card_bytes_written')
    d = re.search(r'date=(.+)', t)
    u = re.search(r'uptime_s=([0-9.]+)', t)
    return int(m.group(1)), (d.group(1).strip() if d else ''), float(u.group(1) if u else 0)

def journal_bytes(s):
    m = re.search(r'([0-9]+(?:\.[0-9]+)?)\s*([KMGTP])', s, re.I)
    if not m:
        return 0
    val = float(m.group(1)); unit = m.group(2).upper()
    mult = {'K':1024,'M':1024**2,'G':1024**3,'T':1024**4,'P':1024**5}[unit]
    return int(val * mult)

def s2_mb_h(t0, u0, t1, u1):
    delta = t1 - t0
    if delta < 0:
        raise ValueError('negative delta')
    hours = max((u1 - u0) / 3600.0, 1e-9)
    return delta / 1048576.0 / hours, delta

def s3_amp(t0, t1, uploaded):
    delta = t1 - t0
    if delta < 0 or uploaded <= 0:
        raise ValueError('invalid')
    return delta / uploaded, delta

def main():
    sample = "date=2026-11-06T09:20:55+10:00\nuptime_s=1000.0\ncard_bytes_written=10485760\n"
    sample2 = "date=2026-11-06T09:50:55+10:00\nuptime_s=2800.0\ncard_bytes_written=14680064\n"
    c0,d0,u0 = card_from_text(sample)
    c1,d1,u1 = card_from_text(sample2)
    mb_h, delta = s2_mb_h(c0,u0,c1,u1)
    assert delta == 4194304, delta
    # 4 MiB over 1800s = 4/0.5 = 8 MB/h
    assert abs(mb_h - 8.0) < 0.01, mb_h
    assert mb_h <= 40
    print("PASS s2_measure", mb_h)

    amp, d = s3_amp(0, 12_000_000, 4_000_000 * 20)
    assert abs(amp - (12_000_000 / (4_000_000 * 20))) < 1e-9
    assert amp <= 4
    print("PASS s3_measure", amp)

    try:
        s2_mb_h(100, 1, 50, 2)
        raise SystemExit('expected negative delta fail')
    except ValueError:
        print("PASS rejects_negative_card_delta")

    j0 = journal_bytes("Archived and active journals take up 48.0M in the file system.")
    j1 = journal_bytes("Archived and active journals take up 49.5M in the file system.")
    assert j0 == int(48.0 * 1024**2)
    assert (j1 - j0) == int(1.5 * 1024**2)
    print("PASS journal_delta", j1 - j0)

    # missing field
    try:
        card_from_text("uptime_s=1\n")
        raise SystemExit('expected missing field fail')
    except ValueError:
        print("PASS rejects_missing_card_field")

    print("PASS measure_all")
    return 0

if __name__ == "__main__":
    sys.exit(main())
