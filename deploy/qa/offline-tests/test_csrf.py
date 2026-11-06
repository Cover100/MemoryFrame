#!/usr/bin/env python3
"""CSRF cookie parse path (mirrors csrf helper)."""
import http.cookiejar, os, tempfile, pathlib, sys

def write_netscape(path, name, value):
    # Netscape cookie file format
    body = (
        "# Netscape HTTP Cookie File\n"
        f"127.0.0.1\tFALSE\t/\tFALSE\t0\t{name}\t{value}\n"
    )
    pathlib.Path(path).write_text(body)

def load_csrf(path, name="mf_csrf"):
    cj = http.cookiejar.MozillaCookieJar(path)
    cj.load(ignore_discard=True, ignore_expires=True)
    for c in cj:
        if c.name == name:
            return c.value
    raise SystemExit(f"cookie {name} not found in {path}")

def main():
    td = tempfile.mkdtemp()
    jar = os.path.join(td, "cookies.txt")
    write_netscape(jar, "mf_csrf", "test-csrf-token-abc")
    write_netscape(os.path.join(td, "other.txt"), "session", "x")
    v = load_csrf(jar)
    assert v == "test-csrf-token-abc", v
    try:
        load_csrf(os.path.join(td, "other.txt"))
        raise SystemExit("expected missing csrf to fail")
    except SystemExit as e:
        if "not found" not in str(e):
            raise
    print("PASS csrf_path")
    return 0

if __name__ == "__main__":
    sys.exit(main())
