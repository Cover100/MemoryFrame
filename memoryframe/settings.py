"""MF-17 (slim): slideshow settings stored in the settings table."""
from .db import get_setting, set_setting

SLIDE_SECONDS = (5, 10, 30, 60, 300)
TRANSITIONS = ("fade", "none")
DEFAULTS = {"slide_seconds": 10, "transition": "fade", "shuffle": True}


def get_all(conn):
    out = dict(DEFAULTS)
    try:
        v = int(get_setting(conn, "slide_seconds", DEFAULTS["slide_seconds"]))
        out["slide_seconds"] = v if v in SLIDE_SECONDS else DEFAULTS["slide_seconds"]
    except (TypeError, ValueError):
        pass
    t = get_setting(conn, "transition", DEFAULTS["transition"])
    out["transition"] = t if t in TRANSITIONS else DEFAULTS["transition"]
    out["shuffle"] = get_setting(conn, "shuffle", "1") != "0"
    return out


def validate(data):
    """Partial update -> (clean dict, error). Anything unknown or out of range is an error."""
    if not isinstance(data, dict) or not data:
        return None, "expected a JSON object with slide_seconds, transition and/or shuffle"
    clean = {}
    for k, v in data.items():
        if k == "slide_seconds":
            if isinstance(v, bool) or not isinstance(v, int) or v not in SLIDE_SECONDS:
                return None, "slide_seconds must be one of 5, 10, 30, 60, 300"
            clean[k] = v
        elif k == "transition":
            if v not in TRANSITIONS:
                return None, "transition must be fade or none"
            clean[k] = v
        elif k == "shuffle":
            if not isinstance(v, bool):
                return None, "shuffle must be true or false"
            clean[k] = v
        else:
            return None, f"unknown setting {k!r}"[:80]
    return clean, None


def save(conn, clean):
    for k, v in clean.items():
        set_setting(conn, k, ("1" if v else "0") if k == "shuffle" else v)
    conn.commit()
