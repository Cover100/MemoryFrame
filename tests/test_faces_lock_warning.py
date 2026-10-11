"""Face-detector lock held > 60 s -> one warning (not one per copy)."""
import logging
import threading
import time

import numpy as np

from memoryframe import faces


def test_stuck_lock_warns_once(monkeypatch, caplog):
    release = threading.Event()
    monkeypatch.setattr(faces, "_run_detector", lambda arr: release.wait(10))
    monkeypatch.setattr(faces, "STUCK_S", 0.3)
    monkeypatch.setattr(faces, "_logged", set())
    arr = np.zeros((8, 8, 3), np.uint8)
    try:
        assert faces._run_with_budget(arr, 0.1) is None      # worker now hangs, holding the lock
        with caplog.at_level(logging.WARNING, logger="memoryframe.faces"):
            faces._run_with_budget(arr, 0.1)                  # held < STUCK_S: no stuck warning
            assert not [r for r in caplog.records if "lock held" in r.getMessage()]
            time.sleep(0.35)
            for _ in range(3):
                assert faces._run_with_budget(arr, 0.05) is None
        stuck = [r for r in caplog.records if "lock held for over" in r.getMessage()]
        assert len(stuck) == 1
    finally:
        release.set()
    for _ in range(50):                                       # lock freed when the call returns
        if faces._held_since is None:
            break
        time.sleep(0.05)
    assert faces._held_since is None and not faces._lock.locked()
