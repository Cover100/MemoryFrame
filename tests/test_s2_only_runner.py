"""Harness regression for the corrected external S2-only runner.

The official 2026-11-06 S2 card result (48.3891 MB/h FAIL) is not remeasured
here. These tests lock the harness rules around it: a stale or uncovered
watcher log is UNAVAILABLE, never a zero WAL count, and that attribution is
separate from the card verdict.
"""
import hashlib
import os
import shutil
import signal
import subprocess
import time
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "deploy" / "qa" / "run-s2-only.sh"
BASH = shutil.which("bash")


def run(args, timeout=60, env=None):
    return subprocess.run([BASH, str(RUNNER), *args], capture_output=True, text=True, timeout=timeout, env=env)


def kv(text):
    found = {}
    for line in text.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.startswith("S2_") or key in {"STATUS", "RUN_ID", "NOTE"}:
            found.setdefault(key, value)
    return found


def assert_unavailable(text, reason):
    fields = kv(text)
    assert fields["S2_ATTRIBUTION"] == "INVALID"
    assert fields["S2_WAL_CLOSE_WRITE"] == "UNAVAILABLE"
    assert fields["S2_ATTRIBUTION_REASON"] == reason
    assert "S2_WAL_CLOSE_WRITE=0" not in text


def sudo_ok():
    return subprocess.run(["sudo", "-n", "true"], capture_output=True).returncode == 0


def sudo_write(path, text):
    subprocess.run(["sudo", "tee", str(path)], input=text, text=True, check=True, capture_output=True)


def sudo_read(path):
    result = subprocess.run(["sudo", "cat", "--", str(path)], capture_output=True, text=True, check=True)
    return result.stdout


@pytest.fixture(autouse=True)
def _restore_tmp_ownership(tmp_path):
    yield
    if sudo_ok():
        subprocess.run(["sudo", "chown", "-R", f"{os.getuid()}:{os.getgid()}", str(tmp_path)], check=False)


@pytest.fixture
def root_out(tmp_path):
    if not sudo_ok():
        pytest.skip("passwordless sudo is required for the root-only archive regression")
    out = tmp_path / "out"
    out.mkdir()
    subprocess.run(["sudo", "chown", "root:root", str(out)], check=True)
    subprocess.run(["sudo", "chmod", "700", str(out)], check=True)
    try:
        yield out
    finally:
        subprocess.run(["sudo", "chown", "-R", f"{os.getuid()}:{os.getgid()}", str(tmp_path)], check=False)


def event_line(epoch, event, path):
    stamp = datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M:%S")
    return "%s %s %s\n" % (stamp, event, path)


def write_case(tmp_path, run_id="run-1", wal_epochs=(), extra_lines="", extra_after="",
               sentinel_ok=True, **overrides):
    log = tmp_path / ("S2-iw-%s.log" % run_id)
    identity = tmp_path / ("S2-evidence-%s.identity" % run_id)
    now = int(time.time())
    start = now - 30
    end = now - 5
    establish = start - 2
    token = "s2-sentinel-%s" % run_id
    barrier = "s2-barrier-%s" % run_id
    seen = token if sentinel_ok else "not-the-sentinel"
    prefix = event_line(establish, "CLOSE_WRITE,CLOSE", str(tmp_path / "sentinel" / seen))
    for epoch in wal_epochs:
        prefix += event_line(epoch, "CLOSE_WRITE,CLOSE", "/var/lib/memoryframe/memoryframe.db-wal")
    prefix += extra_lines
    barrier_line = event_line(end, "CLOSE_WRITE,CLOSE", str(tmp_path / "sentinel" / barrier))
    log.write_text(prefix + barrier_line + extra_after)
    stderr = tmp_path / ("stderr-%s.txt" % run_id)
    stderr.write_text("Setting up watches.\nWatches established.\n")
    st = log.stat()
    fields = {
        "RUN_ID": run_id,
        "IW_LOG": str(log),
        "WATCHER_PID": "4242",
        "WATCHER_HEALTH": "healthy",
        "ESTABLISH_EPOCH": str(establish),
        "DEVICE": str(st.st_dev),
        "INODE": str(st.st_ino),
        "SENTINEL_TOKEN": token,
        "SENTINEL_SEEN": "1",
        "WINDOW_START": str(start),
        "WINDOW_END": str(end),
        "WINDOW_CLOSED": "1",
        "COVERAGE_EPOCH": str(end),
        "COVERAGE_ALIVE": "1",
        "BARRIER_TOKEN": barrier,
        "BARRIER_SEEN": "1",
        "STDERR_PATH": str(stderr),
        "PREFIX_SHA256": hashlib.sha256(prefix.encode()).hexdigest(),
        "PREFIX_STABLE": "1",
        "BOUND_LO": str(start),
        "BOUND_HI": str(end),
        "BOUND_SOURCE": "snapshot_date",
        "CLOCK_CONSISTENT": "1",
    }
    fields.update(overrides)
    # A replaced log must be described by the overridden inode/path, not re-stat'd.
    if "IW_LOG" in overrides:
        fields["IW_LOG"] = overrides["IW_LOG"]
    identity.write_text("".join("%s=%s\n" % item for item in fields.items()))
    return identity, log, start, end


def old_unprivileged_archive(out, archive):
    """The external runner's archive loop, unchanged. The shell globs before sudo."""
    script = r"""
set +f
OUT="$1"
ARCHIVE_DIR="$2"
mkdir -p "$ARCHIVE_DIR"
for pat in S2-t0 S2-t1 S3-t0 S3-t1 S2-pidstat S2-iotop S2-iostat S2-iw; do
  for f in "$OUT"/${pat}*; do
    if sudo test -e "$f" 2>/dev/null; then
      sudo mv "$f" "$ARCHIVE_DIR/" 2>/dev/null || true
    fi
  done
done
echo "preserved prior OUT labels under $ARCHIVE_DIR (best-effort)"
"""
    return subprocess.run([BASH, "-c", script, "bash", str(out), str(archive)],
                          capture_output=True, text=True, check=False)


def test_runner_does_not_keep_the_unprivileged_archive_glob():
    text = RUNNER.read_text()
    assert '"$OUT"/${pat}*' not in text
    assert "preserved prior OUT labels under" not in text
    assert "-o $OUT/S2-iw.log" not in text
    for name in (
        "S2-iw-${RUN_ID}.log",
        "S2-pidstat-${RUN_ID}.txt",
        "S2-iotop-${RUN_ID}.txt",
        "S2-iostat-${RUN_ID}.txt",
        "S2-evidence-${RUN_ID}.identity",
    ):
        assert name in text
    assert "S2_WAL_CLOSE_WRITE" in text
    assert "S2_CARD_VERDICT" in text
    assert "S2-only complete:" in text
    assert 'OUT="${OUT:-/root/sd-wear}"' not in text
    assert 'sudo "$CAP" S2-t0' not in text
    assert 'sudo "$CAP" "$label" "$OUT"' in text
    assert "s2_require_offcard" in text
    assert "WAIT_TIMEOUT" in text
    assert "Q_OVERFLOW" in text
    assert "unplaceable" in text
    assert "clock_inconsistent" in text
    assert "root_lost" in text
    assert "delete_self,move_self,unmount" in text
    loop = text.split("while [ $(($(date +%s)-S2_START)) -lt 1800 ]; do", 1)[1].split("\ndone", 1)[0]
    assert "sudo" not in loop


def test_shellcheck_clean():
    shellcheck = shutil.which("shellcheck")
    if shellcheck is None:
        pytest.skip("shellcheck not installed")
    result = subprocess.run([shellcheck, str(RUNNER)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_unprivileged_glob_preserves_nothing_then_corrected_archive_moves(root_out, tmp_path):
    stale = "17:04:22 CLOSE_WRITE,CLOSE /var/lib/memoryframe/memoryframe.db-wal\n"
    sudo_write(root_out / "S2-iw.log", stale)
    sudo_write(root_out / "S2-t0.txt", "t0-bytes\n")
    sudo_write(root_out / "notes.txt", "leave-me\n")
    listed = subprocess.run(["ls", str(root_out)], capture_output=True, text=True)
    assert listed.returncode != 0

    old = old_unprivileged_archive(root_out, tmp_path / "old-archive")
    assert old.returncode == 0
    assert "best-effort" in old.stdout
    assert sudo_read(root_out / "S2-iw.log") == stale
    assert list((tmp_path / "old-archive").iterdir()) == []

    run_id = "corrected-1"
    archive = root_out / ("archive-" + run_id)
    moved = run(["--preserve", str(root_out), run_id, str(archive)])
    assert moved.returncode == 0, moved.stderr
    assert "PRESERVE_OK" in moved.stdout
    assert "preserved=2" in moved.stdout
    assert "S2-iw*" not in moved.stdout
    assert sudo_read(archive / "S2-iw.log") == stale
    assert sudo_read(archive / "S2-t0.txt") == "t0-bytes\n"
    assert sudo_read(root_out / "notes.txt") == "leave-me\n"
    remaining = subprocess.run(["sudo", "ls", "-1", str(root_out)], capture_output=True, text=True, check=True)
    names = set(remaining.stdout.split())
    assert "S2-iw.log" not in names and "S2-t0.txt" not in names
    assert not any("*" in name for name in names)


def test_corrected_archive_does_not_clobber_existing_evidence(root_out):
    archive = root_out / "archive-same"
    subprocess.run(["sudo", "mkdir", "-p", str(archive)], check=True)
    sudo_write(archive / "S2-iw.log", "ARCHIVED-ORIGINAL\n")
    sudo_write(root_out / "S2-iw.log", "LIVE-DIFFERENT\n")
    result = run(["--preserve", str(root_out), "same", str(archive)])
    assert result.returncode == 2, result.stderr
    assert "PRESERVE_KEPT" in result.stdout
    assert "PRESERVE_ABORT" in result.stderr
    assert "preserved=0" in result.stdout
    assert sudo_read(archive / "S2-iw.log") == "ARCHIVED-ORIGINAL\n"
    assert sudo_read(root_out / "S2-iw.log") == "LIVE-DIFFERENT\n"

    again = run(["--preserve", str(root_out), "same", str(archive)])
    assert again.returncode == 2, again.stderr
    assert sudo_read(archive / "S2-iw.log") == "ARCHIVED-ORIGINAL\n"


def test_preserve_refuses_symlink_out(tmp_path):
    if not sudo_ok():
        pytest.skip("passwordless sudo is required")
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    result = run(["--preserve", str(link), "run"])
    assert result.returncode == 2
    assert "PRESERVE_REFUSE_SYMLINK" in result.stderr


def test_stale_log_fed_as_evidence_is_unavailable_not_zero(tmp_path):
    stale = tmp_path / "S2-iw.log"
    stale.write_text("17:04:22 CLOSE_WRITE,CLOSE /var/lib/memoryframe/memoryframe.db-wal\n" * 3)
    missing = tmp_path / "no-such-identity"
    for evidence in (stale, missing):
        result = run(["--attribute", str(evidence), "100", "200"])
        assert result.returncode == 0, result.stderr
        assert_unavailable(result.stdout, "missing_identity")


def test_legacy_unscoped_log_is_unavailable_not_zero(tmp_path):
    legacy = tmp_path / "S2-iw.log"
    legacy.write_text("17:04:22 CLOSE_WRITE,CLOSE /var/lib/memoryframe/memoryframe.db-wal\n")
    identity, _log, start, end = write_case(tmp_path, IW_LOG=str(legacy))
    result = run(["--attribute", str(identity), str(start), str(end)])
    assert result.returncode == 0, result.stderr
    assert_unavailable(result.stdout, "legacy_unscoped")


def test_missing_log_inode_mismatch_and_bad_window(tmp_path):
    identity, _log, start, end = write_case(tmp_path, wal_epochs=(int(time.time()) - 10,))
    missing = write_case(
        tmp_path, run_id="missing-log", IW_LOG=str(tmp_path / "absent" / "S2-iw-missing-log.log")
    )
    result = run(["--attribute", str(missing[0]), str(missing[2]), str(missing[3])])
    assert_unavailable(result.stdout, "missing_log")

    mismatched = write_case(tmp_path, run_id="mismatch", wal_epochs=(int(time.time()) - 10,))
    mismatched_log = mismatched[1]
    mismatched[0].write_text(
        mismatched[0].read_text().replace(
            "INODE=%s" % mismatched_log.stat().st_ino,
            "INODE=%s" % (mismatched_log.stat().st_ino + 1),
        )
    )
    result = run(["--attribute", str(mismatched[0]), str(mismatched[2]), str(mismatched[3])])
    assert_unavailable(result.stdout, "mismatched_identity")

    result = run(["--attribute", str(identity), str(end), str(start)])
    assert_unavailable(result.stdout, "bad_window")

    drifted = write_case(tmp_path, run_id="drift")
    result = run(["--attribute", str(drifted[0]), str(drifted[2] + 5), str(drifted[3])])
    assert_unavailable(result.stdout, "mismatched_identity")


def test_early_exit_partial_and_undated_do_not_publish_a_count(tmp_path):
    inside = int(time.time()) - 10
    early = write_case(tmp_path, run_id="early", wal_epochs=(inside,), COVERAGE_ALIVE="0")
    result = run(["--attribute", str(early[0]), str(early[2]), str(early[3])])
    assert_unavailable(result.stdout, "early_exit")
    assert "S2_WAL_CLOSE_WRITE=1" not in result.stdout

    partial = write_case(tmp_path, run_id="partial", wal_epochs=(inside,), WINDOW_CLOSED="0")
    result = run(["--attribute", str(partial[0]), str(partial[2]), str(partial[3])])
    assert_unavailable(result.stdout, "partial_uncovered")

    late = write_case(tmp_path, run_id="late", wal_epochs=(inside,))
    # Rewrite establish to after the claimed window start.
    text = late[0].read_text().replace(
        "ESTABLISH_EPOCH=%s" % kv_one(late[0], "ESTABLISH_EPOCH"),
        "ESTABLISH_EPOCH=%s" % (late[2] + 1),
    )
    late[0].write_text(text)
    result = run(["--attribute", str(late[0]), str(late[2]), str(late[3])])
    assert_unavailable(result.stdout, "partial_uncovered")

    undated = write_case(
        tmp_path,
        run_id="undated",
        extra_lines="17:04:22 CLOSE_WRITE,CLOSE /var/lib/memoryframe/memoryframe.db-wal\n",
    )
    result = run(["--attribute", str(undated[0]), str(undated[2]), str(undated[3])])
    assert_unavailable(result.stdout, "undated_log")


def test_unhealthy_and_sentinel_missing_are_unavailable(tmp_path):
    unhealthy = write_case(tmp_path, run_id="sick", WATCHER_HEALTH="unhealthy")
    result = run(["--attribute", str(unhealthy[0]), str(unhealthy[2]), str(unhealthy[3])])
    assert_unavailable(result.stdout, "unhealthy")

    quiet = write_case(tmp_path, run_id="no-sentinel", sentinel_ok=False)
    result = run(["--attribute", str(quiet[0]), str(quiet[2]), str(quiet[3])])
    assert_unavailable(result.stdout, "sentinel_missing")


def test_valid_coverage_counts_wal_and_honest_zero(tmp_path):
    ident, log, start, end = write_case(tmp_path, run_id="count")
    token = "s2-sentinel-count"
    barrier = "s2-barrier-count"
    prefix = event_line(start - 2, "CLOSE_WRITE,CLOSE", str(tmp_path / "sentinel" / token))
    prefix += event_line(start + 5, "CLOSE_WRITE,CLOSE", "/var/lib/memoryframe/memoryframe.db-wal")
    prefix += event_line(start + 6, "CLOSE_WRITE,CLOSE", "/var/lib/memoryframe/memoryframe.db-wal")
    prefix += event_line(start + 5, "CLOSE_WRITE,CLOSE", "/var/log/other")
    prefix += event_line(start + 5, "CREATE", "/var/lib/memoryframe/memoryframe.db-wal")
    # After the barrier: excluded from the scored prefix, not an exact-count failure.
    after = event_line(end + 30, "CLOSE_WRITE,CLOSE", "/var/lib/memoryframe/memoryframe.db-wal")
    barrier_line = event_line(end, "CLOSE_WRITE,CLOSE", str(tmp_path / "sentinel" / barrier))
    log.write_text(prefix + barrier_line + after)
    digest = hashlib.sha256(prefix.encode()).hexdigest()
    ident.write_text(ident.read_text().replace(
        "PREFIX_SHA256=%s" % kv_one(ident, "PREFIX_SHA256"),
        "PREFIX_SHA256=%s" % digest,
    ))
    result = run(["--attribute", str(ident), str(start), str(end)])
    assert result.returncode == 0, result.stderr
    fields = kv(result.stdout)
    assert fields["S2_ATTRIBUTION"] == "VALID"
    assert fields["S2_WAL_CLOSE_WRITE"] == "2"
    assert fields["S2_ATTRIBUTION_REASON"] == "ok"

    zero = write_case(tmp_path, run_id="zero")
    result = run(["--attribute", str(zero[0]), str(zero[2]), str(zero[3])])
    fields = kv(result.stdout)
    assert fields == {
        "S2_ATTRIBUTION": "VALID",
        "S2_WAL_CLOSE_WRITE": "0",
        "S2_ATTRIBUTION_REASON": "ok",
    }


def kv_one(path, key):
    for line in path.read_text().splitlines():
        if line.startswith(key + "="):
            return line.split("=", 1)[1]
    raise AssertionError(key)


def snapshot(path, card, uptime):
    path.write_text(
        "label=S2\ndate=2026-11-06T20:44:19+10:00\nuptime_s=%s\ncard_bytes_written=%s\n" % (uptime, card)
    )


def test_card_verdict_is_independent_of_attribution(tmp_path):
    out = tmp_path / "snaps"
    out.mkdir()
    snapshot(out / "S2-t0.txt", 0, "100")
    snapshot(out / "S2-t1.txt", 10 * 1048576, "3700")
    dest = tmp_path / "card.txt"
    passed = run(["--card", str(out), str(dest)])
    assert passed.returncode == 0, passed.stderr
    assert kv(passed.stdout)["S2_CARD_VERDICT"] == "PASS"
    assert kv(passed.stdout)["S2_CARD_MB_H"] == "10.0000"
    assert "S2_WAL_CLOSE_WRITE" not in passed.stdout

    fail_out = tmp_path / "fail"
    fail_out.mkdir()
    snapshot(fail_out / "S2-t0.txt", 150692352, "1000")
    snapshot(fail_out / "S2-t1.txt", 150692352 + 50 * 1048576, "4600")
    fail_dest = tmp_path / "card-fail.txt"
    failed = run(["--card", str(fail_out), str(fail_dest)])
    assert failed.returncode == 0, failed.stderr
    assert kv(failed.stdout)["S2_CARD_VERDICT"] == "FAIL"
    assert float(kv(failed.stdout)["S2_CARD_MB_H"]) == 50.0
    assert "S2_WAL" not in failed.stdout

    again = run(["--card", str(out), str(dest)])
    assert again.returncode == 2
    assert dest.read_text() == passed.stdout

    (tmp_path / "empty").mkdir()
    missing = run(["--card", str(tmp_path / "empty"), str(tmp_path / "missing.txt")])
    assert missing.returncode == 2
    assert kv(missing.stdout)["S2_CARD_VERDICT"] == "UNAVAILABLE"
    assert "S2_CARD_MB_H" not in missing.stdout

    bad = tmp_path / "bad"
    bad.mkdir()
    snapshot(bad / "S2-t0.txt", 20, "5")
    snapshot(bad / "S2-t1.txt", 10, "15")
    negative = run(["--card", str(bad), str(tmp_path / "bad.txt")])
    assert negative.returncode == 2
    assert kv(negative.stdout)["S2_CARD_VERDICT"] == "UNAVAILABLE"


def test_s2_only_completion_separates_verdicts_and_keeps_old_evidence(tmp_path):
    base = tmp_path / "base"
    base.mkdir()
    run_id = "complete-1"
    (base / ("s2-card-" + run_id + ".txt")).write_text(
        "S2_CARD_MB_H=50.0000\nS2_CARD_LIMIT=40\nS2_CARD_VERDICT=FAIL\n"
    )
    (base / ("s2-attribution-" + run_id + ".txt")).write_text(
        "S2_ATTRIBUTION=INVALID\nS2_WAL_CLOSE_WRITE=UNAVAILABLE\nS2_ATTRIBUTION_REASON=early_exit\n"
    )
    (base / "DONE").write_text("previous-pointer\n")
    result = run(["--complete", str(base), run_id, "S2_ONLY_COMPLETE", "0"])
    assert result.returncode == 0, result.stderr
    done = (base / ("DONE-S2-" + run_id)).read_text()
    assert "STATUS=S2_ONLY_COMPLETE" in done
    assert "S2_CARD_VERDICT=FAIL" in done
    assert "S2_WAL_CLOSE_WRITE=UNAVAILABLE" in done
    assert "S2_WAL_CLOSE_WRITE=0" not in done
    assert "NOTE=card_verdict_and_wal_attribution_are_independent" in done
    assert "NOTE=UNAVAILABLE_is_not_zero" in done
    assert (base / "DONE").read_text() == "previous-pointer\n"
    assert "POINTER_KEPT" in result.stdout
    assert (base / ("DONE-" + run_id)).read_text() == done
    assert (base / ("summary-" + run_id + ".txt")).read_text() == done

    again = run(["--complete", str(base), run_id, "S2_ONLY_COMPLETE", "0"])
    assert again.returncode == 2
    assert (base / ("DONE-S2-" + run_id)).read_text() == done


def test_incomplete_exit_writes_completion_without_inventing_zero(tmp_path):
    base = tmp_path / "base"
    base.mkdir()
    run_id = "exit-7"
    result = run(["--exit-incomplete", str(base), run_id, "7"])
    assert result.returncode == 7, result.stderr
    done = (base / ("DONE-S2-" + run_id)).read_text()
    assert "STATUS=S2_ONLY_INCOMPLETE" in done
    assert "EXIT_RC=7" in done
    assert kv(done)["S2_CARD_VERDICT"] == "UNAVAILABLE"
    assert kv(done)["S2_WAL_CLOSE_WRITE"] == "UNAVAILABLE"
    assert "S2_WAL_CLOSE_WRITE=0" not in done
    before = done
    second = run(["--exit-incomplete", str(base), run_id, "7"])
    assert second.returncode == 7
    assert (base / ("DONE-S2-" + run_id)).read_text() == before


def shm_dir(name):
    path = Path("/dev/shm") / name
    subprocess.run(["sudo", "rm", "-rf", str(path)], check=False)
    path.mkdir()
    return path


def test_live_sentinel_on_run_scoped_log_after_root_only_preserve(root_out):
    if shutil.which("inotifywait") is None or shutil.which("stdbuf") is None:
        pytest.skip("inotifywait and stdbuf are required for the sentinel probe")
    base = shm_dir("mf-live-%s" % os.getpid())
    stale = "17:04:22 CLOSE_WRITE,CLOSE /var/lib/memoryframe/memoryframe.db-wal\n"
    sudo_write(root_out / "S2-iw.log", stale)
    sudo_write(root_out / "S2-iw-earlier.log", "earlier-run\n")
    run_id = "live1"
    preserved = run(["--preserve", str(root_out), run_id])
    assert preserved.returncode == 0, preserved.stderr
    archive = root_out / ("archive-" + run_id)
    assert sudo_read(archive / "S2-iw.log") == stale

    try:
        probe = run(["--probe-sentinel", str(root_out), str(base), run_id], timeout=40)
    finally:
        subprocess.run(["sudo", "rm", "-rf", str(base)], check=False)
    assert probe.returncode == 0, probe.stdout + probe.stderr
    fields = kv(probe.stdout)
    assert fields["S2_ATTRIBUTION"] == "VALID"
    assert fields["S2_WAL_CLOSE_WRITE"] == "0"
    assert fields["S2_ATTRIBUTION_REASON"] == "ok"
    iw = root_out / ("S2-iw-" + run_id + ".log")
    body = sudo_read(iw)
    assert "s2-sentinel-" + run_id in body
    assert body.splitlines()[0][0:4].isdigit()
    names = subprocess.run(["sudo", "ls", "-1", str(root_out)], capture_output=True, text=True, check=True).stdout
    assert "S2-iw.log" not in names.split()
    assert sudo_read(archive / "S2-iw.log") == stale
    identity = sudo_read(root_out / ("S2-evidence-" + run_id + ".identity"))
    assert "IW_LOG=%s" % iw in identity
    assert "WATCHER_HEALTH=healthy" in identity
    assert "COVERAGE_ALIVE=1" in identity


def test_probe_does_not_overwrite_existing_attribution(root_out):
    if shutil.which("inotifywait") is None or shutil.which("stdbuf") is None:
        pytest.skip("inotifywait and stdbuf are required for the sentinel probe")
    run_id = "keepattr"
    base = shm_dir("mf-keep-%s" % os.getpid())
    dest = base / ("s2-attribution-" + run_id + ".txt")
    dest.write_text("KEEP-ATTR\n")
    try:
        result = run(["--probe-sentinel", str(root_out), str(base), run_id], timeout=40)
        assert result.returncode == 2, result.stdout + result.stderr
        assert dest.read_text() == "KEEP-ATTR\n"
        assert "ATTR_REFUSE_OVERWRITE" in result.stderr
    finally:
        subprocess.run(["sudo", "rm", "-rf", str(base)], check=False)


def test_overflow_stderr_boundary_and_missing_barrier_are_not_valid_zero(tmp_path):
    overflow = write_case(
        tmp_path,
        run_id="overflow",
        extra_lines=event_line(int(time.time()) - 20, "Q_OVERFLOW", "/var/lib/memoryframe"),
    )
    result = run(["--attribute", str(overflow[0]), str(overflow[2]), str(overflow[3])])
    assert_unavailable(result.stdout, "watcher_loss")

    stderr_case = write_case(tmp_path, run_id="stderr")
    stderr_path = Path(kv_one(stderr_case[0], "STDERR_PATH"))
    stderr_path.write_text(stderr_path.read_text() + "Couldn't watch /var/lib/memoryframe: No such file or directory\n")
    result = run(["--attribute", str(stderr_case[0]), str(stderr_case[2]), str(stderr_case[3])])
    assert_unavailable(result.stdout, "stderr_loss")

    bound = write_case(tmp_path, run_id="bound")
    start, end = bound[2], bound[3]
    prefix = event_line(start - 2, "CLOSE_WRITE,CLOSE", str(tmp_path / "sentinel" / "s2-sentinel-bound"))
    prefix += event_line(end, "CLOSE_WRITE,CLOSE", "/var/lib/memoryframe/memoryframe.db-wal")
    barrier = event_line(end, "CLOSE_WRITE,CLOSE", str(tmp_path / "sentinel" / "s2-barrier-bound"))
    bound[1].write_text(prefix + barrier)
    digest = hashlib.sha256(prefix.encode()).hexdigest()
    bound[0].write_text(bound[0].read_text().replace(
        "PREFIX_SHA256=%s" % kv_one(bound[0], "PREFIX_SHA256"),
        "PREFIX_SHA256=%s" % digest,
    ))
    result = run(["--attribute", str(bound[0]), str(start), str(end)])
    assert_unavailable(result.stdout, "unplaceable")

    late = write_case(
        tmp_path,
        run_id="lateprint",
        extra_lines=event_line(int(time.time()) + 60, "CLOSE_WRITE,CLOSE", "/var/lib/memoryframe/memoryframe.db-wal"),
    )
    result = run(["--attribute", str(late[0]), str(late[2]), str(late[3])])
    assert_unavailable(result.stdout, "unplaceable")

    missing_barrier = write_case(tmp_path, run_id="nobarrier", BARRIER_SEEN="0")
    result = run(["--attribute", str(missing_barrier[0]), str(missing_barrier[2]), str(missing_barrier[3])])
    assert_unavailable(result.stdout, "barrier_missing")


def test_scored_directory_must_be_verified_tmpfs(tmp_path):
    disk = run(["--check-offcard", str(tmp_path)])
    assert disk.returncode == 2
    assert "not a verified tmpfs" in disk.stderr
    parent = Path("/dev/shm") / ("mf-off-%s" % os.getpid())
    parent.mkdir()
    try:
        good = run(["--check-offcard", str(parent)])
        assert good.returncode == 0, good.stderr
        assert "OFFCARD_OK" in good.stdout
        assert "fstype=tmpfs" in good.stdout
    finally:
        parent.rmdir()


def test_later_capture_does_not_truncate_an_earlier_snapshot():
    if not sudo_ok():
        pytest.skip("passwordless sudo is required")
    parent = Path("/dev/shm") / ("mf-cap-%s" % os.getpid())
    out = parent / "out"
    subprocess.run(["sudo", "mkdir", "-p", str(out)], check=True)
    subprocess.run(["sudo", "chown", "root:root", str(out)], check=True)
    subprocess.run(["sudo", "chmod", "700", str(out)], check=True)
    try:
        sudo_write(out / "S2-t0-earlier.txt", "EARLIER-SNAPSHOT\n")
        env = dict(os.environ, REPO=str(ROOT))
        first = run(["--capture", str(out), "S2-t0-runA"], env=env)
        assert first.returncode == 0, first.stderr + first.stdout
        assert sudo_read(out / "S2-t0-earlier.txt") == "EARLIER-SNAPSHOT\n"
        first_bytes = sudo_read(out / "S2-t0-runA.txt")
        assert "label=S2-t0-runA" in first_bytes
        again = run(["--capture", str(out), "S2-t0-runA"], env=env)
        assert again.returncode == 2
        assert "CAPTURE_REFUSE_EXISTS" in again.stderr
        assert sudo_read(out / "S2-t0-runA.txt") == first_bytes
        second = run(["--capture", str(out), "S2-t0-runB"], env=env)
        assert second.returncode == 0, second.stderr
        assert sudo_read(out / "S2-t0-earlier.txt") == "EARLIER-SNAPSHOT\n"
        assert sudo_read(out / "S2-t0-runA.txt") == first_bytes
        assert "label=S2-t0-runB" in sudo_read(out / "S2-t0-runB.txt")
    finally:
        subprocess.run(["sudo", "rm", "-rf", str(parent)], check=False)


def test_reap_returns_while_resistant_child_is_alive():
    started = time.time()
    proc = subprocess.Popen(
        [BASH, str(RUNNER), "--reap-self"],
        start_new_session=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        stdout, stderr = proc.communicate(timeout=8)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        raise AssertionError("reap blocked on a child that ignored the deadline")
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=5)
    elapsed = time.time() - started
    assert proc.returncode == 0, stdout + stderr
    assert "WAIT_TIMEOUT" in stdout
    assert "REAP_CHILD_STILL_ALIVE" in stdout
    assert elapsed < 6


def test_disk_backed_watcher_paths_are_refused(tmp_path):
    base = shm_dir("mf-base-%s" % os.getpid())
    try:
        disk_err = tmp_path / "disk-watcher.err"
        disk_sent = tmp_path / "disk-sentinel"
        bad_err = run(["--check-under-base", str(base), str(disk_err)])
        assert bad_err.returncode == 2
        assert "outside verified BASE" in bad_err.stderr
        bad_sent = run(["--check-under-base", str(base), str(disk_sent)])
        assert bad_sent.returncode == 2
        inside = base / "sentinel"
        good = run(["--check-under-base", str(base), str(inside)])
        assert good.returncode == 0, good.stderr
        assert "OFFCARD_OK" in good.stdout
        assert not disk_err.exists()
    finally:
        subprocess.run(["sudo", "rm", "-rf", str(base)], check=False)


def test_disk_override_does_not_start_the_watcher(root_out, tmp_path):
    if shutil.which("inotifywait") is None:
        pytest.skip("inotifywait is required")
    base = shm_dir("mf-ovr-%s" % os.getpid())
    disk_err = tmp_path / "in-window.err"
    env = dict(os.environ, WATCHER_ERR=str(disk_err))
    try:
        result = run(["--probe-sentinel", str(root_out), str(base), "ovr1"], env=env, timeout=20)
    finally:
        subprocess.run(["sudo", "rm", "-rf", str(base)], check=False)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "S2_WAL_CLOSE_WRITE=0" not in result.stdout
    text = disk_err.read_text() if disk_err.exists() else ""
    assert "Watches established" not in text


def test_root_lifecycle_events_are_not_a_valid_zero(tmp_path):
    root = tmp_path / "data-root"
    root.mkdir()
    st = root.stat()
    ident_bits = {
        "WATCH_ROOTS": str(root),
        "WATCH_IDS": "%s:%s" % (st.st_dev, st.st_ino),
    }
    for name, event in (("delself", "DELETE_SELF"), ("moveself", "MOVE_SELF"), ("ignored", "IGNORED")):
        case = write_case(
            tmp_path,
            run_id=name,
            extra_lines=event_line(int(time.time()) - 15, event, str(root) + "/"),
            **ident_bits,
        )
        result = run(["--attribute", str(case[0]), str(case[2]), str(case[3])])
        assert_unavailable(result.stdout, "watcher_loss")
    child = write_case(
        tmp_path,
        run_id="child",
        extra_lines=event_line(int(time.time()) - 15, "DELETE_SELF", str(root / "sub") + "/"),
        **ident_bits,
    )
    result = run(["--attribute", str(child[0]), str(child[2]), str(child[3])])
    assert kv(result.stdout)["S2_ATTRIBUTION"] == "VALID"
    assert kv(result.stdout)["S2_WAL_CLOSE_WRITE"] == "0"


def test_replacing_the_watched_root_is_not_a_valid_zero(tmp_path):
    if shutil.which("inotifywait") is None or shutil.which("stdbuf") is None:
        pytest.skip("inotifywait and stdbuf are required")
    base = shm_dir("mf-rootmv-%s" % os.getpid())
    out = base / "out"
    root = tmp_path / "data-root"
    root.mkdir()
    (root / "sub").mkdir()
    try:
        result = run(
            ["--probe-root-move", str(out), str(base), "rootmv", str(root)],
            timeout=40,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert kv(result.stdout)["S2_WAL_CLOSE_WRITE"] == "UNAVAILABLE"
        assert kv(result.stdout)["S2_ATTRIBUTION"] == "INVALID"
        assert "S2_WAL_CLOSE_WRITE=0" not in result.stdout
        log = sudo_read(out / "S2-iw-rootmv.log")
        assert "MOVE_SELF" in log
        assert "s2-barrier-rootmv" in log
    finally:
        subprocess.run(["sudo", "rm", "-rf", str(base)], check=False)
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(Path(str(root) + ".moved"), ignore_errors=True)


def test_counter_gap_and_clock_inconsistency_are_not_exact(tmp_path):
    gap = write_case(
        tmp_path,
        run_id="gap",
        extra_lines=event_line(101, "CLOSE_WRITE,CLOSE", "/var/lib/memoryframe/memoryframe.db-wal"),
        WINDOW_START="102",
        WINDOW_END="400",
        BOUND_LO="102",
        BOUND_HI="400",
        ESTABLISH_EPOCH="100",
        COVERAGE_EPOCH="400",
        BOUND_SOURCE="counter_bracket",
        CLOCK_CONSISTENT="1",
    )
    result = run(["--attribute", str(gap[0]), "102", "400"])
    assert_unavailable(result.stdout, "unplaceable")
    assert "S2_WAL_CLOSE_WRITE=1" not in result.stdout

    clocks = write_case(tmp_path, run_id="clocks", CLOCK_CONSISTENT="0")
    result = run(["--attribute", str(clocks[0]), str(clocks[2]), str(clocks[3])])
    assert_unavailable(result.stdout, "clock_inconsistent")

    bracket = run(["--bracket", "100", "10", "102", "400", "310", "401"])
    assert bracket.returncode == 0, bracket.stderr
    lo, hi, ok = bracket.stdout.splitlines()
    assert lo == "102" and hi == "400" and ok == "1"
    backwards = run(["--bracket", "200", "10", "201", "100", "20", "202"])
    assert backwards.returncode == 0, backwards.stderr
    assert backwards.stdout.splitlines()[2] == "0"
