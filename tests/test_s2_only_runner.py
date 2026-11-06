"""Harness regression for the corrected external S2-only runner.

The official 2026-11-06 S2 card result (48.3891 MB/h FAIL) is not remeasured
here. These tests lock the harness rules around it: a stale or uncovered
watcher log is UNAVAILABLE, never a zero WAL count, and that attribution is
separate from the card verdict.
"""
import os
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "deploy" / "qa" / "run-s2-only.sh"
BASH = shutil.which("bash")


def run(args, timeout=60):
    return subprocess.run([BASH, str(RUNNER), *args], capture_output=True, text=True, timeout=timeout)


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


def write_case(tmp_path, run_id="run-1", wal_epochs=(), extra_lines="", **overrides):
    log = tmp_path / ("S2-iw-%s.log" % run_id)
    identity = tmp_path / ("S2-evidence-%s.identity" % run_id)
    now = int(time.time())
    start = now - 30
    end = now - 5
    establish = start - 2
    token = "s2-sentinel-%s" % run_id
    body = event_line(establish, "CLOSE_WRITE,CLOSE", str(tmp_path / "sentinel" / token))
    for epoch in wal_epochs:
        body += event_line(epoch, "CLOSE_WRITE,CLOSE", "/var/lib/memoryframe/memoryframe.db-wal")
    body += extra_lines
    log.write_text(body)
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
    assert result.returncode == 0, result.stderr
    assert "PRESERVE_KEPT" in result.stdout
    assert "preserved=0" in result.stdout
    assert sudo_read(archive / "S2-iw.log") == "ARCHIVED-ORIGINAL\n"
    assert sudo_read(root_out / "S2-iw.log") == "LIVE-DIFFERENT\n"

    again = run(["--preserve", str(root_out), "same", str(archive)])
    assert again.returncode == 0, again.stderr
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

    quiet = write_case(tmp_path, run_id="no-sentinel")
    text = quiet[1].read_text().replace("s2-sentinel-no-sentinel", "something-else")
    quiet[1].write_text(text)
    result = run(["--attribute", str(quiet[0]), str(quiet[2]), str(quiet[3])])
    assert_unavailable(result.stdout, "sentinel_missing")


def test_valid_coverage_counts_wal_and_honest_zero(tmp_path):
    start_holder = write_case(tmp_path, run_id="count")
    start, end = start_holder[2], start_holder[3]
    inside = start + 5
    outside = end + 30
    log = start_holder[1]
    token = "s2-sentinel-count"
    body = event_line(start - 2, "CLOSE_WRITE,CLOSE", str(tmp_path / token))
    body += event_line(inside, "CLOSE_WRITE,CLOSE", "/var/lib/memoryframe/memoryframe.db-wal")
    body += event_line(inside + 1, "CLOSE_WRITE,CLOSE", "/var/lib/memoryframe/memoryframe.db-wal")
    body += event_line(outside, "CLOSE_WRITE,CLOSE", "/var/lib/memoryframe/memoryframe.db-wal")
    body += event_line(inside, "CLOSE_WRITE,CLOSE", "/var/log/other")
    body += event_line(inside, "CREATE", "/var/lib/memoryframe/memoryframe.db-wal")
    log.write_text(body)
    result = run(["--attribute", str(start_holder[0]), str(start), str(end)])
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


def test_live_sentinel_on_run_scoped_log_after_root_only_preserve(root_out, tmp_path):
    if shutil.which("inotifywait") is None or shutil.which("stdbuf") is None:
        pytest.skip("inotifywait and stdbuf are required for the sentinel probe")
    stale = "17:04:22 CLOSE_WRITE,CLOSE /var/lib/memoryframe/memoryframe.db-wal\n"
    sudo_write(root_out / "S2-iw.log", stale)
    sudo_write(root_out / "S2-iw-earlier.log", "earlier-run\n")
    run_id = "live1"
    preserved = run(["--preserve", str(root_out), run_id])
    assert preserved.returncode == 0, preserved.stderr
    archive = root_out / ("archive-" + run_id)
    assert sudo_read(archive / "S2-iw.log") == stale

    probe = run(["--probe-sentinel", str(root_out), str(tmp_path), run_id], timeout=40)
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


def test_probe_does_not_overwrite_existing_attribution(root_out, tmp_path):
    if shutil.which("inotifywait") is None or shutil.which("stdbuf") is None:
        pytest.skip("inotifywait and stdbuf are required for the sentinel probe")
    run_id = "keepattr"
    dest = tmp_path / ("s2-attribution-" + run_id + ".txt")
    dest.write_text("KEEP-ATTR\n")
    result = run(["--probe-sentinel", str(root_out), str(tmp_path), run_id], timeout=40)
    assert result.returncode == 2, result.stdout + result.stderr
    assert dest.read_text() == "KEEP-ATTR\n"
    assert "ATTR_REFUSE_OVERWRITE" in result.stderr
