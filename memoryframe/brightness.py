"""Raspberry Pi DSI backlight access (MF-03).

The official DSI display usually exposes /sys/class/backlight/10-0045, but the
node name can differ, so it is detected at runtime.
Override with MEMORYFRAME_BACKLIGHT=/sys/class/backlight/<node>.
"""
import os

BACKLIGHT_ROOT = "/sys/class/backlight"
PREFERRED_BACKLIGHT = "10-0045"
MIN_BRIGHTNESS = 40


def find_backlight():
    """Return the backlight sysfs directory, or None if unsupported."""
    override = os.environ.get("MEMORYFRAME_BACKLIGHT")
    if override:
        candidates = [override]
    else:
        candidates = [os.path.join(BACKLIGHT_ROOT, PREFERRED_BACKLIGHT)]
        if os.path.isdir(BACKLIGHT_ROOT):
            candidates += [os.path.join(BACKLIGHT_ROOT, n) for n in sorted(os.listdir(BACKLIGHT_ROOT))]
    for path in candidates:
        if (os.path.isfile(os.path.join(path, "brightness"))
                and os.path.isfile(os.path.join(path, "max_brightness"))):
            return path
    return None


def read_int(path):
    with open(path, "r") as f:
        return int(f.read().strip())
