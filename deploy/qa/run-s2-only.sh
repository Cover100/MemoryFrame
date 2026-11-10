#!/bin/bash
# SC2317: EXIT traps and the retained post-exit S3 baseline are live code that
# ShellCheck's reachability pass does not follow.
# shellcheck disable=SC2317
# Corrected external S2-only SD-wear harness.
# Baseline: run-s2-only-7e16c4d.sh
#   SHA256 b222f24f9b3555926858c869d5097799969d842c440d309ddbed1a10d36eda99
# This file is not deploy/qa/run-s2-s3-repaired.sh and does not change PR #129.
#
# Corrections, and only these:
#   - Archive listing runs as root. An unprivileged glob of mode-0700 OUT
#     stays literal (`S2-iw*`) and `sudo test -e` then preserves nothing.
#   - Monitor/evidence paths are run-scoped and are never truncated in place.
#   - Watcher establish, sentinel, exit, and window coverage are recorded.
#   - WAL attribution is fail-closed. UNAVAILABLE is not a zero count.
#   - Card MB/h and WAL attribution are separate results.
#   - The intentional S2-only stop writes a completion record before exiting.
#   - Scored outputs are a verified tmpfs. Capture and the verdict use that
#     same run-scoped directory. sudo audit policy is left unchanged.
#   - Tmpfs evidence is not copied anywhere after the run. A physical remeasure
#     needs its own authorization and must collect these files after S2-t1
#     before the tmpfs disappears. The S3 text after the S2-only exit does not run.
# The absent 2026-11-06 20:44–21:14 watcher bytes are not explained by the
# archive glob. This script does not assign a cause for that gap.
# Safe cleanup: only kill tracked monitor process trees (no broad pkill).
set -eu

REPO="${REPO:-$HOME/Documents/GitHub/MemoryFrame}"
# Scored files default onto a tmpfs. /root and $HOME are on the SD card on a Pi
# and would be counted in card_bytes_written. The mount is checked, not assumed.
RUN_ID="${RUN_ID:-$(date +%Y%m%dT%H%M%S%z)}"
RUN_TAG=$(printf '%s' "$RUN_ID" | tr -c 'A-Za-z0-9._-' '-')
S2_SCORE_PARENT="${S2_SCORE_PARENT:-/dev/shm}"
OUT="${OUT:-$S2_SCORE_PARENT/mf-s2-${RUN_TAG}/out}"
BASE="${BASE:-$S2_SCORE_PARENT/mf-s2-${RUN_TAG}/base}"
TARGET_HEAD="${TARGET_HEAD:-6711333}"
JAR="$BASE/cookies.txt"
LOG="$BASE/run-${RUN_ID}.log"
PHOTOS="$BASE/photos-${RUN_ID}"
CAP="$REPO/deploy/sd-wear-capture.sh"
HELPER="$BASE/csrf-${RUN_ID}.py"
SUMMARY="$BASE/summary-${RUN_ID}.txt"
S2_CARD_FILE="$BASE/s2-card-${RUN_ID}.txt"
S2_ATTR_FILE="$BASE/s2-attribution-${RUN_ID}.txt"
S2_WATCH_TIMEOUT="${S2_WATCH_TIMEOUT:-90}"

MON_PIDS=()
TEE_PID=""
IW_LOG=""
IW_PID=""
IW_IDENTITY=""
WATCHER_ERR="${WATCHER_ERR:-}"
WATCHER_HEALTH=""
SENTINEL_TOKEN=""
SENTINEL_SEEN=0
S2_START=""
S2_FINISH_DONE=0
S2_IN_EXIT_TRAP=0
S2_WINDOW_OPEN=0
S2_SUDO_BOUNDARY=0
S2_SUDO_IN_WINDOW=0

# Bounded curl defaults (override via env if needed)
CURL_CONNECT_TIMEOUT="${CURL_CONNECT_TIMEOUT:-5}"
CURL_MAX_TIME="${CURL_MAX_TIME:-30}"
curl_q() {
  curl -sS --connect-timeout "$CURL_CONNECT_TIMEOUT" --max-time "$CURL_MAX_TIME" "$@"
}
# HTTP code helper: curl still emits %{http_code} on timeout; never append via ||
http_code() {
  local c
  c=$(curl -sS --connect-timeout "$CURL_CONNECT_TIMEOUT" --max-time "$CURL_MAX_TIME" -o /dev/null -w "%{http_code}" "$@" 2>/dev/null || true)
  [ -z "$c" ] && c=000
  printf '%s' "$c"
}

# List and move prior labeled captures as root. The caller must not glob OUT:
# without search permission the shell leaves a literal asterisk path, sudo
# test -e of that path is false, and the real files stay in place.
preserve_prior_evidence() {
  local out="$1"
  local run_id="$2"
  local archive="${3:-$out/archive-${run_id}}"
  # shellcheck disable=SC2016
  sudo bash -c '
    set -eu
    out="$1"
    archive="$2"
    shift 2
    if [ -L "$out" ]; then
      echo "PRESERVE_REFUSE_SYMLINK out=$out" >&2
      exit 2
    fi
    if [ ! -d "$out" ]; then
      echo "PRESERVE_NO_OUT"
      exit 0
    fi
    if [ -L "$archive" ]; then
      echo "PRESERVE_REFUSE_SYMLINK archive=$archive" >&2
      exit 2
    fi
    mkdir -p -- "$archive"
    preserved=0
    kept=0
    for pat in "$@"; do
      while IFS= read -r -d "" src; do
        base=$(basename -- "$src")
        dest="$archive/$base"
        if [ -e "$dest" ] || [ -L "$dest" ]; then
          echo "PRESERVE_KEPT src=$src dest=$dest reason=dest_exists"
          kept=$((kept + 1))
          continue
        fi
        mv -n -- "$src" "$dest"
        if [ -e "$src" ]; then
          echo "PRESERVE_KEPT src=$src dest=$dest reason=mv_declined"
          kept=$((kept + 1))
          continue
        fi
        echo "PRESERVE_OK src=$src dest=$dest"
        preserved=$((preserved + 1))
      done < <(find "$out" -mindepth 1 -maxdepth 1 -type f -name "${pat}*" -print0)
    done
    echo "PRESERVE_SUMMARY preserved=$preserved kept=$kept archive=$archive"
    if [ "$kept" -gt 0 ]; then
      echo "PRESERVE_ABORT kept=$kept" >&2
      exit 2
    fi
  ' bash "$out" "$archive" \
    S2-t0 S2-t1 S3-t0 S3-t1 S2-pidstat S2-iotop S2-iostat S2-iw S2-evidence
}

# Fail closed. Never prints a numeric WAL count unless every gate passed.
attribute_s2_wal() {
  local identity="$1"
  local window_start="$2"
  local window_end="$3"
  sudo python3 - "$identity" "$window_start" "$window_end" <<'PY'
import hashlib, os, re, stat, sys, time

def emit(attr, wal, reason):
    if attr != "VALID" or re.fullmatch(r"\d+", str(wal)) is None:
        attr = "INVALID"
        wal = "UNAVAILABLE"
        if reason == "ok":
            reason = "bad_count"
    sys.stdout.write(
        "S2_ATTRIBUTION=%s\nS2_WAL_CLOSE_WRITE=%s\nS2_ATTRIBUTION_REASON=%s\n"
        % (attr, wal, reason)
    )
    raise SystemExit(0)

def invalid(reason):
    emit("INVALID", "UNAVAILABLE", reason)

if len(sys.argv) != 4:
    invalid("bad_window")
identity_path, window_raw, end_raw = sys.argv[1:]
try:
    window_start = int(window_raw)
    window_end = int(end_raw)
except ValueError:
    invalid("bad_window")
if window_end < window_start:
    invalid("bad_window")
if not os.path.isfile(identity_path) or os.path.islink(identity_path):
    invalid("missing_identity")
vals = {}
with open(identity_path, "r", encoding="utf-8", errors="replace") as fh:
    for line in fh:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        vals[key] = value
required = [
    "RUN_ID", "IW_LOG", "ESTABLISH_EPOCH", "DEVICE", "INODE",
    "SENTINEL_TOKEN", "SENTINEL_SEEN", "COVERAGE_EPOCH", "COVERAGE_ALIVE",
    "WINDOW_START", "WINDOW_END", "WINDOW_CLOSED", "WATCHER_HEALTH",
    "BARRIER_TOKEN", "BARRIER_SEEN", "STDERR_PATH", "PREFIX_SHA256",
    "PREFIX_STABLE", "BOUND_LO", "BOUND_HI", "BOUND_SOURCE",
    "CLOCK_CONSISTENT",
]
for key in required:
    if vals.get(key, "") == "":
        invalid("missing_identity")
try:
    if int(vals["WINDOW_START"]) != window_start or int(vals["WINDOW_END"]) != window_end:
        invalid("mismatched_identity")
    if int(vals["BOUND_LO"]) != window_start or int(vals["BOUND_HI"]) != window_end:
        invalid("mismatched_identity")
    establish = int(vals["ESTABLISH_EPOCH"])
    coverage = int(vals["COVERAGE_EPOCH"])
    bound_lo = int(vals["BOUND_LO"])
    bound_hi = int(vals["BOUND_HI"])
except ValueError:
    invalid("missing_identity")
if vals["BOUND_SOURCE"] not in ("snapshot_date", "probe_clock", "counter_bracket"):
    invalid("unbound_window")
if vals["CLOCK_CONSISTENT"] != "1":
    invalid("clock_inconsistent")
iw = vals["IW_LOG"]
run_id = vals["RUN_ID"]
if run_id not in os.path.basename(iw):
    invalid("legacy_unscoped")
if not os.path.lexists(iw) or os.path.islink(iw):
    invalid("missing_log")
st = os.lstat(iw)
if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
    invalid("mismatched_identity")
if str(st.st_dev) != vals["DEVICE"] or str(st.st_ino) != vals["INODE"]:
    invalid("mismatched_identity")
if vals["WATCHER_HEALTH"] != "healthy":
    invalid("unhealthy")
if vals["SENTINEL_SEEN"] != "1" or vals["SENTINEL_TOKEN"] == "":
    invalid("sentinel_missing")
if vals["COVERAGE_ALIVE"] != "1":
    invalid("early_exit")
if vals["WINDOW_CLOSED"] != "1":
    invalid("partial_uncovered")
if establish > window_start or coverage < window_end:
    invalid("partial_uncovered")
if vals["BARRIER_SEEN"] != "1" or vals["BARRIER_TOKEN"] == "":
    invalid("barrier_missing")
if vals["PREFIX_STABLE"] != "1":
    invalid("prefix_unstable")
stderr_path = vals["STDERR_PATH"]
if not os.path.isfile(stderr_path) or os.path.islink(stderr_path):
    invalid("stderr_missing")
with open(stderr_path, "r", encoding="utf-8", errors="replace") as fh:
    for raw in fh:
        line = raw.rstrip("\n")
        if line == "" or line.startswith("Setting up watches") or line.startswith("Watches established"):
            continue
        invalid("stderr_loss")

def norm(path):
    return path.rstrip("/")

def root_loss(event, path, roots):
    ev = event.upper()
    if "Q_OVERFLOW" in ev or "UNMOUNT" in ev:
        return True
    if norm(path) not in roots:
        return False
    return any(name in ev for name in ("DELETE_SELF", "MOVE_SELF", "IGNORED"))

root_list = [norm(p) for p in vals.get("WATCH_ROOTS", "").split("|") if p]
id_list = [p for p in vals.get("WATCH_IDS", "").split("|") if p]
if root_list and len(id_list) != len(root_list):
    invalid("missing_identity")
for root, ident in zip(root_list, id_list):
    if not os.path.isdir(root) or os.path.islink(root):
        invalid("root_lost")
    st_root = os.lstat(root)
    if ident != "%s:%s" % (st_root.st_dev, st_root.st_ino):
        invalid("root_lost")
roots = set(root_list)

line_re = re.compile(r"^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2}) (\S+) (.*)$")
token = vals["SENTINEL_TOKEN"]
barrier = vals["BARRIER_TOKEN"]
prefix = []
saw_barrier = False
loss = False
with open(iw, "r", encoding="utf-8", errors="replace") as fh:
    for raw in fh:
        line = raw.rstrip("\n")
        if barrier in line:
            saw_barrier = True
            break
        prefix.append(line)
        if line == "":
            continue
        match = line_re.match(line)
        if not match:
            invalid("undated_log")
        event = match.group(3)
        if root_loss(event, match.group(4), roots):
            loss = True
if not saw_barrier:
    invalid("barrier_missing")
# A loss marker after the barrier still means the queue was not intact.
with open(iw, "r", encoding="utf-8", errors="replace") as fh:
    for raw in fh:
        line = raw.rstrip("\n")
        match = line_re.match(line) if line else None
        if match and root_loss(match.group(3), match.group(4), roots):
            loss = True
        elif line and not match:
            invalid("undated_log")
if loss:
    invalid("watcher_loss")
digest = hashlib.sha256(("".join(line + "\n" for line in prefix)).encode()).hexdigest()
if digest != vals["PREFIX_SHA256"]:
    invalid("prefix_unstable")
sentinel_ok = False
count = 0
ambiguous = False
for line in prefix:
    if line == "":
        continue
    match = line_re.match(line)
    date_s, time_s, event, path = match.groups()
    if token in path or barrier in path:
        if token in path:
            sentinel_ok = True
        continue
    try:
        epoch = int(time.mktime(time.strptime(date_s + " " + time_s, "%Y-%m-%d %H:%M:%S")))
    except ValueError:
        invalid("undated_log")
    if "CLOSE_WRITE" in event and path.endswith("memoryframe.db-wal"):
        # inotifywait stamps whole seconds when it prints, not when the
        # kernel saw the event. The snapshot date is also a whole second.
        # Only an open interval is unambiguous. A boundary or late line
        # makes an exact count unavailable.
        if bound_lo < epoch < bound_hi:
            count += 1
        else:
            # Print time cannot be placed inside the card-counter samples.
            ambiguous = True
if not sentinel_ok:
    invalid("sentinel_missing")
if ambiguous:
    invalid("unplaceable")
if count != 0 and vals["BOUND_SOURCE"] not in ("snapshot_date", "counter_bracket"):
    invalid("unbound_window")
emit("VALID", str(count), "ok")
PY
}

# Record attribution without replacing an existing result file.
s2_write_attribution() {
  local start="$1"
  local end="$2"
  if [ -z "${IW_IDENTITY:-}" ]; then
    return 0
  fi
  if [ -e "$S2_ATTR_FILE" ] || [ -L "$S2_ATTR_FILE" ]; then
    echo "ATTR_REFUSE_OVERWRITE $S2_ATTR_FILE" >&2
    return 2
  fi
  mkdir -p -- "$BASE"
  local tmp
  tmp=$(mktemp "$BASE/.attr-${RUN_ID}.XXXXXX")
  attribute_s2_wal "$IW_IDENTITY" "$start" "$end" > "$tmp"
  cat -- "$tmp"
  mv -n -- "$tmp" "$S2_ATTR_FILE"
  if [ -e "$tmp" ]; then
    rm -f -- "$tmp"
    echo "ATTR_REFUSE_OVERWRITE $S2_ATTR_FILE" >&2
    return 2
  fi
}

s2_card_verdict() {
  local out="$1"
  local dest="$2"
  local t0_label="${3:-S2-t0}"
  local t1_label="${4:-S2-t1}"
  sudo python3 - "$out" "$dest" "$t0_label" "$t1_label" <<'PY'
import os, re, sys

out = sys.argv[1]
dest = sys.argv[2]
t0_label = sys.argv[3]
t1_label = sys.argv[4]

def write(lines, code):
    if os.path.lexists(dest):
        sys.stderr.write("CARD_REFUSE_OVERWRITE %s\n" % dest)
        raise SystemExit(2)
    text = "\n".join(lines) + "\n"
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(dest, flags, 0o644)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    sys.stdout.write(text)
    raise SystemExit(code)

def unavailable(reason):
    write([
        "S2_CARD_VERDICT=UNAVAILABLE",
        "S2_CARD_REASON=%s" % reason,
        "S2_CARD_LIMIT=40",
    ], 2)

def snap(label):
    path = os.path.join(out, label + ".txt")
    if not os.path.isfile(path) or os.path.islink(path):
        return None
    text = open(path, "r", encoding="utf-8", errors="replace").read()
    card = re.search(r"card_bytes_written=(\d+)", text)
    up = re.search(r"uptime_s=([0-9.]+)", text)
    date = re.search(r"date=(.+)", text)
    if not card or not up:
        return None
    return int(card.group(1)), float(up.group(1)), (date.group(1).strip() if date else "")

t0 = snap(t0_label)
t1 = snap(t1_label)
if t0 is None or t1 is None:
    unavailable("missing_snapshot")
c0, u0, d0 = t0
c1, u1, d1 = t1
delta = c1 - c0
if delta < 0 or u1 < u0:
    unavailable("invalid_delta")
hours = (u1 - u0) / 3600.0
if hours <= 0:
    unavailable("invalid_delta")
mb_h = delta / 1048576.0 / hours
limit = 40
verdict = "PASS" if mb_h <= limit else "FAIL"
passed = "True" if verdict == "PASS" else "False"
write([
    "S2 card_bytes delta=%d hours=%.4f MB_h=%.4f limit=%d pass=%s" % (delta, hours, mb_h, limit, passed),
    "S2 dates %s -> %s" % (d0, d1),
    "S2_CARD_BYTES_DELTA=%d" % delta,
    "S2_CARD_HOURS=%.4f" % hours,
    "S2_CARD_MB_H=%.4f" % mb_h,
    "S2_CARD_LIMIT=%d" % limit,
    "S2_CARD_VERDICT=%s" % verdict,
], 0)
PY
}

write_s2_only_completion() {
  local status="$1"
  local rc="${2:-0}"
  local done="$BASE/DONE-S2-${RUN_ID}"
  local done_legacy="$BASE/DONE-${RUN_ID}"
  mkdir -p -- "$BASE"
  if [ -e "$done" ] || [ -L "$done" ] || [ -e "$SUMMARY" ] || [ -L "$SUMMARY" ] || [ -e "$done_legacy" ] || [ -L "$done_legacy" ]; then
    echo "COMPLETION_REFUSE_OVERWRITE done=$done" >&2
    return 2
  fi
  local tmp
  tmp=$(mktemp "$BASE/.completion-${RUN_ID}.XXXXXX")
  {
    echo "STATUS=$status"
    echo "RUN_ID=$RUN_ID"
    echo "EXIT_RC=$rc"
    echo "HEAD=${HEAD_NOW:-}"
    echo "IW_LOG=${IW_LOG:-}"
    echo "NOTE=card_verdict_and_wal_attribution_are_independent"
    echo "NOTE=UNAVAILABLE_is_not_zero"
    echo "NOTE=scored_files_are_on_verified_tmpfs_when_the_run_starts"
    echo "NOTE=sudo_auth_and_audit_policy_unchanged"
    echo "NOTE=sudo_log_bytes_inside_the_card_delta_are_not_subtracted"
    echo "NOTE=offcard_placement_does_not_claim_a_new_s2_result"
    echo "NOTE=tmpfs_evidence_is_not_archived_automatically"
    echo "NOTE=physical_run_needs_separate_authorization_and_post_t1_collection"
    echo "NOTE=unreachable_s3_tail_is_not_s3_coverage"
    echo "S2_SUDO_BOUNDARY=${S2_SUDO_BOUNDARY:-0}"
    echo "S2_SUDO_IN_WINDOW=${S2_SUDO_IN_WINDOW:-0}"
    if [ -f "$S2_CARD_FILE" ]; then
      cat -- "$S2_CARD_FILE"
    else
      echo "S2_CARD_VERDICT=UNAVAILABLE"
      echo "S2_CARD_REASON=missing_card_result"
    fi
    if [ -f "$S2_ATTR_FILE" ]; then
      cat -- "$S2_ATTR_FILE"
    else
      echo "S2_ATTRIBUTION=INVALID"
      echo "S2_WAL_CLOSE_WRITE=UNAVAILABLE"
      echo "S2_ATTRIBUTION_REASON=missing_attribution"
    fi
  } > "$tmp"
  mv -n -- "$tmp" "$done"
  if [ -e "$tmp" ]; then
    rm -f -- "$tmp"
    echo "COMPLETION_REFUSE_OVERWRITE done=$done" >&2
    return 2
  fi
  cp -n -- "$done" "$done_legacy"
  cp -n -- "$done" "$SUMMARY"
  if [ -e "$BASE/DONE" ] && [ ! -L "$BASE/DONE" ]; then
    echo "POINTER_KEPT $BASE/DONE reason=not_a_symlink"
  else
    ln -sfn -- "DONE-${RUN_ID}" "$BASE/DONE"
  fi
  if [ -e "$BASE/DONE-S2" ] && [ ! -L "$BASE/DONE-S2" ]; then
    echo "POINTER_KEPT $BASE/DONE-S2 reason=not_a_symlink"
  else
    ln -sfn -- "DONE-S2-${RUN_ID}" "$BASE/DONE-S2"
  fi
  S2_FINISH_DONE=1
  echo "S2_ONLY_COMPLETION status=$status file=$done"
}

watcher_alive() {
  local pid="${1:-}"
  if [ -z "$pid" ]; then
    return 1
  fi
  if kill -0 "$pid" 2>/dev/null; then
    return 0
  fi
  if sudo kill -0 "$pid" 2>/dev/null; then
    return 0
  fi
  return 1
}

s2_write_identity() {
  local health="$1"
  local sentinel_seen="$2"
  local establish_epoch="$3"
  sudo python3 - "$IW_IDENTITY" "$IW_LOG" "$RUN_ID" "${IW_PID:-}" "$health" "$sentinel_seen" "${SENTINEL_TOKEN:-}" "$establish_epoch" <<'PY'
import os, sys
identity, iw, run_id, pid, health, seen, token, establish = sys.argv[1:]
if identity == "" or os.path.lexists(identity):
    sys.stderr.write("IDENTITY_EXISTS %s\n" % identity)
    raise SystemExit(2)
if run_id == "" or run_id not in os.path.basename(iw):
    sys.stderr.write("IW_NOT_RUN_SCOPED %s\n" % iw)
    raise SystemExit(2)
device = "0"
inode = "0"
if os.path.isfile(iw) and not os.path.islink(iw):
    st = os.lstat(iw)
    device = str(st.st_dev)
    inode = str(st.st_ino)
text = "\n".join([
    "RUN_ID=%s" % run_id,
    "IW_LOG=%s" % iw,
    "WATCHER_PID=%s" % pid,
    "WATCHER_HEALTH=%s" % health,
    "ESTABLISH_EPOCH=%s" % establish,
    "DEVICE=%s" % device,
    "INODE=%s" % inode,
    "SENTINEL_TOKEN=%s" % token,
    "SENTINEL_SEEN=%s" % seen,
]) + "\n"
flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
if hasattr(os, "O_NOFOLLOW"):
    flags |= os.O_NOFOLLOW
parent = os.path.dirname(identity)
os.makedirs(parent, exist_ok=True)
fd = os.open(identity, flags, 0o600)
with os.fdopen(fd, "w", encoding="utf-8") as fh:
    fh.write(text)
sys.stdout.write("IDENTITY_WROTE %s inode=%s health=%s\n" % (identity, inode, health))
PY
}

s2_record_coverage() {
  local window_start="$1"
  local window_end="$2"
  local closed="$3"
  local alive=0
  if [ "${WATCHER_HEALTH:-}" != "early_exit" ] && watcher_alive "${IW_PID:-}"; then
    alive=1
  fi
  local now
  now=$(date +%s)
  sudo python3 - "$IW_IDENTITY" "$window_start" "$window_end" "$closed" "$alive" "$now" <<'PY'
import os, sys
identity, window_start, window_end, closed, alive, now = sys.argv[1:]
if not os.path.isfile(identity) or os.path.islink(identity):
    sys.stderr.write("COVERAGE_MISSING_IDENTITY\n")
    raise SystemExit(2)
text = open(identity, "r", encoding="utf-8").read()
if "\nCOVERAGE_EPOCH=" in "\n" + text:
    sys.stdout.write("COVERAGE_KEPT %s\n" % identity)
    raise SystemExit(0)
extra = "\n".join([
    "WINDOW_START=%s" % window_start,
    "WINDOW_END=%s" % window_end,
    "WINDOW_CLOSED=%s" % closed,
    "COVERAGE_EPOCH=%s" % now,
    "COVERAGE_ALIVE=%s" % alive,
]) + "\n"
with open(identity, "a", encoding="utf-8") as fh:
    fh.write(extra)
sys.stdout.write("COVERAGE_RECORDED alive=%s closed=%s\n" % (alive, closed))
PY
}

s2_wait_established() {
  local errfile="$1"
  local pid="$2"
  local timeout_s="$3"
  local deadline
  deadline=$(( $(date +%s) + timeout_s ))
  while [ "$(date +%s)" -le "$deadline" ]; do
    if [ -f "$errfile" ] && grep -F -q "Watches established" "$errfile"; then
      if watcher_alive "$pid"; then
        return 0
      fi
      echo "WATCHER_DEAD_AFTER_ESTABLISH pid=$pid"
      return 1
    fi
    if ! watcher_alive "$pid"; then
      echo "WATCHER_EXIT_BEFORE_ESTABLISH pid=$pid"
      return 1
    fi
    sleep 0.2
  done
  echo "WATCHER_ESTABLISH_TIMEOUT pid=$pid"
  return 1
}

s2_wait_for_token() {
  local iw="$1"
  local token="$2"
  local timeout_s="$3"
  local pid="$4"
  local deadline
  deadline=$(( $(date +%s) + timeout_s ))
  while [ "$(date +%s)" -le "$deadline" ]; do
    if sudo grep -F -q -- "$token" "$iw"; then
      return 0
    fi
    if ! watcher_alive "$pid"; then
      return 1
    fi
    sleep 0.2
  done
  return 1
}

s2_fail_watcher() {
  local reason="$1"
  echo "WATCHER_UNHEALTHY reason=$reason"
  WATCHER_HEALTH=unhealthy
  SENTINEL_SEEN=0
  if [ -n "${IW_IDENTITY:-}" ] && ! sudo test -e "$IW_IDENTITY"; then
    s2_write_identity unhealthy 0 "$(date +%s)" || true
  fi
}

s2_begin_watcher() {
  local iw="$1"
  local sentinel_dir="$2"
  shift 2
  IW_LOG="$iw"
  IW_IDENTITY="$OUT/S2-evidence-${RUN_ID}.identity"
  WATCHER_ERR="${WATCHER_ERR:-$BASE/S2-watcher-${RUN_ID}.err}"
  WATCHER_HEALTH=unhealthy
  SENTINEL_SEEN=0
  SENTINEL_TOKEN="s2-sentinel-${RUN_ID}"
  mkdir -p -- "$BASE"
  # Effective overrides must sit on the same verified tmpfs as BASE. A
  # disk-backed stderr or sentinel path would be written during the window.
  if ! s2_require_under_base "$BASE" "$sentinel_dir"; then
    s2_fail_watcher offcard_sentinel
    return 2
  fi
  if ! s2_require_under_base "$BASE" "$WATCHER_ERR"; then
    s2_fail_watcher offcard_watcher_err
    return 2
  fi
  mkdir -p -- "$sentinel_dir"
  echo "evidence_iw=$IW_LOG evidence_identity=$IW_IDENTITY watcher_err=$WATCHER_ERR sentinel_dir=$sentinel_dir"
  if sudo test -e "$IW_IDENTITY" || sudo test -L "$IW_IDENTITY"; then
    echo "IDENTITY_EXISTS $IW_IDENTITY"
    return 0
  fi
  if sudo test -e "$iw" || sudo test -L "$iw"; then
    echo "IW_EXISTS_REFUSING_OVERWRITE $iw"
    s2_fail_watcher outfile_exists
    return 0
  fi
  if [ -e "$WATCHER_ERR" ] || [ -L "$WATCHER_ERR" ]; then
    echo "WATCHER_ERR_EXISTS $WATCHER_ERR"
    s2_fail_watcher watcher_err_exists
    return 0
  fi
  if ! command -v inotifywait >/dev/null 2>&1 || ! command -v stdbuf >/dev/null 2>&1; then
    : > "$WATCHER_ERR"
    s2_fail_watcher missing_tool
    return 0
  fi
  ( umask 077; : > "$WATCHER_ERR" )
  # Create the run-scoped log as root before inotifywait opens it, and refuse
  # if it already exists. stdout is appended so the inode stays put.
  if ! sudo bash -c 'set -eu
    iw="$1"
    if [ -e "$iw" ] || [ -L "$iw" ]; then exit 2; fi
    umask 077
    mkdir -p -- "$(dirname -- "$iw")"
    : > "$iw"
    chmod 600 -- "$iw"
  ' bash "$iw"; then
    s2_fail_watcher outfile_create_failed
    return 0
  fi
  # stderr stays on the unprivileged health log. Event bytes go to the root log.
  # shellcheck disable=SC2024
  sudo bash -c '
    set -eu
    iw="$1"
    sentinel="$2"
    shift 2
    stdbuf -oL -eL inotifywait -m -r -e close_write,create,delete,moved_to,delete_self,move_self,unmount \
      --timefmt "%F %T" --format "%T %e %w%f" "$@" "$sentinel" >> "$iw"
  ' bash "$iw" "$sentinel_dir" "$@" 2>>"$WATCHER_ERR" &
  IW_PID=$!
  MON_PIDS+=("$IW_PID")
  echo "monitor start label=inotifywait pid=$IW_PID"
  if ! s2_wait_established "$WATCHER_ERR" "$IW_PID" "$S2_WATCH_TIMEOUT"; then
    s2_fail_watcher establish_failed
    return 0
  fi
  echo "Watches established."
  if ! sudo bash -c 'set -eu; d="$1"; t="$2"; mkdir -p -- "$d"; : > "$d/$t"' bash "$sentinel_dir" "$SENTINEL_TOKEN"; then
    s2_fail_watcher sentinel_create_failed
    return 0
  fi
  if ! s2_wait_for_token "$iw" "$SENTINEL_TOKEN" "$S2_WATCH_TIMEOUT" "$IW_PID"; then
    s2_fail_watcher sentinel_missing
    return 0
  fi
  SENTINEL_SEEN=1
  local establish_epoch
  establish_epoch=$(date +%s)
  if ! s2_write_identity healthy 1 "$establish_epoch"; then
    s2_fail_watcher identity_write_failed
    return 0
  fi
  WATCHER_HEALTH=healthy
  if [ "$#" -gt 0 ]; then
    s2_record_watch_roots "$@" || return 2
  fi
  echo "WATCHER_HEALTH=healthy sentinel_seen=$SENTINEL_SEEN establish_epoch=$establish_epoch"
}

s2_record_watch_roots() {
  sudo python3 - "$IW_IDENTITY" "$@" <<'PY'
import os, sys
identity = sys.argv[1]
roots = sys.argv[2:]
ids = []
for root in roots:
    if os.path.islink(root) or not os.path.isdir(root):
        ids.append("missing")
        continue
    st = os.lstat(root)
    ids.append("%s:%s" % (st.st_dev, st.st_ino))
with open(identity, "a", encoding="utf-8") as fh:
    fh.write("WATCH_ROOTS=%s\nWATCH_IDS=%s\n" % ("|".join(roots), "|".join(ids)))
sys.stdout.write("ROOTS_RECORDED %s\n" % len(roots))
PY
}

# A path that will receive watcher bytes must be inside BASE and on tmpfs.
# Do not create missing disk directories while checking.
s2_require_under_base() {
  local base="$1"
  local path="$2"
  local base_real probe real
  if [ ! -d "$base" ] || [ -L "$base" ]; then
    echo "STOP: BASE $base is not a real directory" >&2
    return 2
  fi
  base_real=$(readlink -f -- "$base")
  probe="$path"
  while [ ! -e "$probe" ] && [ "$probe" != "/" ]; do
    probe=$(dirname -- "$probe")
  done
  if [ -L "$probe" ] || [ ! -d "$probe" ]; then
    probe=$(dirname -- "$probe")
  fi
  real=$(readlink -f -- "$probe")
  case "$real/" in
    "$base_real"/*|"$base_real/") ;;
    *)
      echo "STOP: $path is outside verified BASE $base_real" >&2
      return 2
      ;;
  esac
  s2_require_offcard "$probe"
}

# EXIT trap for the measurement (`trap s2_on_exit EXIT`). ShellCheck 0.11
# does not treat trap as a call, so this handler looks unused (SC2329).
# shellcheck disable=SC2329
s2_on_exit() {
  local rc=$?
  if [ "$S2_IN_EXIT_TRAP" = "1" ]; then
    return 0
  fi
  S2_IN_EXIT_TRAP=1
  set +e
  if [ "$S2_FINISH_DONE" != "1" ]; then
    if [ -n "$IW_IDENTITY" ] && sudo test -f "$IW_IDENTITY"; then
      if ! sudo grep -q '^COVERAGE_EPOCH=' "$IW_IDENTITY"; then
        local end
        end=$(date +%s)
        s2_record_coverage "${S2_START:-0}" "$end" 0
      fi
      local ws we
      ws=$(sudo sed -n 's/^WINDOW_START=//p' "$IW_IDENTITY" | head -1)
      we=$(sudo sed -n 's/^WINDOW_END=//p' "$IW_IDENTITY" | head -1)
      s2_write_attribution "${ws:-0}" "${we:-0}"
    fi
    write_s2_only_completion S2_ONLY_INCOMPLETE "$rc"
  fi
  cleanup_monitors
  exit "$rc"
}

# EXIT trap for --probe-sentinel and --probe-root-move. Same SC2329 limit.
# shellcheck disable=SC2329
s2_probe_cleanup() {
  local rc=$?
  if [ "$S2_IN_EXIT_TRAP" = "1" ]; then
    return 0
  fi
  S2_IN_EXIT_TRAP=1
  set +e
  cleanup_monitors
  exit "$rc"
}

s2_note_sudo() {
  if [ "${S2_WINDOW_OPEN:-0}" = "1" ]; then
    S2_SUDO_IN_WINDOW=$((S2_SUDO_IN_WINDOW + 1))
  else
    S2_SUDO_BOUNDARY=$((S2_SUDO_BOUNDARY + 1))
  fi
}

# fstype must be tmpfs and the source must not be a disk device. This refuses
# /root and $HOME, which sit on the SD card on a Pi. It does not change sudoers
# or audit policy.
s2_require_offcard() {
  local dir="$1"
  local line fstype source
  if [ ! -d "$dir" ] || [ -L "$dir" ]; then
    echo "STOP: $dir is not a real directory to verify" >&2
    return 2
  fi
  line=$(findmnt -T "$dir" -n -o FSTYPE,SOURCE 2>/dev/null | head -1 || true)
  fstype=${line%% *}
  source=${line#* }
  if [ -z "$line" ] || [ "$fstype" != "tmpfs" ]; then
    echo "STOP: $dir fstype=${fstype:-missing} source=${source:-missing} is not a verified tmpfs" >&2
    return 2
  fi
  case "$source" in
    *mmcblk*|*nvme*|/dev/sd*|/dev/vd*|/dev/xvd*|/dev/hd*)
      echo "STOP: $dir source=$source is a block device" >&2
      return 2
      ;;
  esac
  echo "OFFCARD_OK dir=$dir fstype=$fstype source=$source"
}

s2_prepare_score_dirs() {
  mkdir -p -- "$(dirname -- "$OUT")" "$BASE"
  if [ ! -d "$OUT" ]; then
    s2_note_sudo
    sudo mkdir -p -- "$OUT"
  fi
  if [ "$(stat -c %u -- "$OUT")" != "0" ]; then
    echo "STOP: $OUT is not root-owned; refusing to chown a pre-existing directory" >&2
    return 2
  fi
  s2_note_sudo
  sudo chmod 700 -- "$OUT"
  s2_require_offcard "$OUT"
  s2_require_offcard "$BASE"
}

# Run-scoped label, same OUT the verdict reads. Refuse an existing file so a
# later capture cannot truncate a snapshot that archive failed to move.
s2_capture() {
  local label="$1"
  local dest="$OUT/${label}.txt"
  s2_note_sudo
  if sudo test -e "$dest" || sudo test -L "$dest"; then
    echo "CAPTURE_REFUSE_EXISTS $dest" >&2
    return 2
  fi
  s2_note_sudo
  sudo "$CAP" "$label" "$OUT"
}

s2_prefix_sha() {
  sudo python3 - "$IW_LOG" "$BARRIER_TOKEN" <<'PY'
import hashlib, sys
path, token = sys.argv[1], sys.argv[2]
try:
    text = open(path, "r", encoding="utf-8", errors="replace").read()
except OSError:
    sys.stdout.write("\n")
    raise SystemExit(0)
prefix = []
for line in text.splitlines(keepends=True):
    if token in line:
        break
    prefix.append(line)
sys.stdout.write(hashlib.sha256("".join(prefix).encode()).hexdigest() + "\n")
PY
}

# After S2-t1, wait until a barrier event is in the log, then require the
# bytes before that line to stay put. The barrier itself is not a WAL event.
# inotifywait can still be holding events that happened before t1.
s2_observe_drain() {
  local bound_lo="$1"
  local bound_hi="$2"
  local bound_source="$3"
  local clock_ok="${4:-0}"
  BARRIER_TOKEN="s2-barrier-${RUN_ID}"
  local seen=0 h1 h2 stable=0
  if [ -n "${IW_PID:-}" ] && kill -0 "$IW_PID" 2>/dev/null; then
    sudo bash -c 'set -eu; d="$1"; t="$2"; : > "$d/$t"' bash "$S2_SENTINEL_DIR" "$BARRIER_TOKEN" || true
    if s2_wait_for_token "$IW_LOG" "$BARRIER_TOKEN" "${S2_BARRIER_TIMEOUT:-15}" "$IW_PID"; then
      seen=1
    fi
  fi
  h1=$(s2_prefix_sha || true)
  sleep "${S2_PREFIX_SETTLE:-0.3}"
  h2=$(s2_prefix_sha || true)
  if [ "$seen" = "1" ] && [ -n "$h1" ] && [ "$h1" = "$h2" ]; then
    stable=1
  fi
  sudo python3 - "$IW_IDENTITY" "$BARRIER_TOKEN" "$seen" "$stable" "$h2" "${WATCHER_ERR:-}" "$bound_lo" "$bound_hi" "$bound_source" "$clock_ok" <<'PY'
import os, sys
identity, token, seen, stable, digest, err, lo, hi, source, clock_ok = sys.argv[1:]
if not os.path.isfile(identity) or os.path.islink(identity):
    sys.stderr.write("DRAIN_MISSING_IDENTITY\n")
    raise SystemExit(2)
text = open(identity, "r", encoding="utf-8").read()
if "\nBARRIER_TOKEN=" in "\n" + text:
    sys.stdout.write("DRAIN_KEPT\n")
    raise SystemExit(0)
extra = "\n".join([
    "BARRIER_TOKEN=%s" % token,
    "BARRIER_SEEN=%s" % seen,
    "STDERR_PATH=%s" % err,
    "PREFIX_SHA256=%s" % digest,
    "PREFIX_STABLE=%s" % stable,
    "BOUND_LO=%s" % lo,
    "BOUND_HI=%s" % hi,
    "BOUND_SOURCE=%s" % source,
    "CLOCK_CONSISTENT=%s" % clock_ok,
]) + "\n"
with open(identity, "a", encoding="utf-8") as fh:
    fh.write(extra)
sys.stdout.write("DRAIN_RECORDED seen=%s stable=%s\n" % (seen, stable))
PY
}

s2_snapshot_epoch() {
  sudo python3 - "$1" <<'PY'
import datetime, re, sys
text = open(sys.argv[1], encoding="utf-8", errors="replace").read()
match = re.search(r"^date=(.+)$", text, re.M)
if not match:
    raise SystemExit(2)
print(int(datetime.datetime.fromisoformat(match.group(1).strip()).timestamp()))
PY
}

# date= is printed before uptime and card_bytes. The counter sample is therefore
# after that date and before this script returns. The only interval that is
# certainly inside both counter samples is (post_t0, t1_date).
s2_counter_bracket() {
  python3 - "$@" <<'PY'
import sys
nums = sys.argv[1:]
w0, u0, post0, w1, u1, post1 = nums[:6]
w0, post0, w1, post1 = int(float(w0)), int(float(post0)), int(float(w1)), int(float(post1))
u0, u1 = float(u0), float(u1)
pre0 = int(float(nums[6])) if len(nums) > 6 else w0
pre1 = int(float(nums[7])) if len(nums) > 7 else w1
# date= is taken inside the capture, after pre and before post.
if pre0 > w0 + 1 or pre1 > w1 + 1 or post0 < w0 or post1 < w1 or w1 <= post0 or u1 < u0:
    consistent = 0
else:
    slack = (post0 - w0) + (post1 - w1) + 2
    consistent = 1 if abs((w1 - w0) - (u1 - u0)) <= slack else 0
sys.stdout.write("%s\n%s\n%s\n" % (post0, w1, consistent))
PY
}

s2_probe_sentinel() {
  OUT="$1"
  BASE="$2"
  RUN_ID="$3"
  S2_CARD_FILE="$BASE/s2-card-${RUN_ID}.txt"
  S2_ATTR_FILE="$BASE/s2-attribution-${RUN_ID}.txt"
  SUMMARY="$BASE/summary-${RUN_ID}.txt"
  S2_SENTINEL_DIR="${S2_SENTINEL_DIR:-$BASE/sentinel-${RUN_ID}}"
  S2_WATCH_TIMEOUT="${S2_WATCH_TIMEOUT:-20}"
  trap s2_probe_cleanup EXIT
  mkdir -p -- "$BASE"
  sudo mkdir -p -- "$OUT"
  sudo chown root:root -- "$OUT"
  sudo chmod 700 -- "$OUT"
  s2_begin_watcher "$OUT/S2-iw-${RUN_ID}.log" "$S2_SENTINEL_DIR"
  S2_START=$(date +%s)
  sleep "${S2_PROBE_SLEEP:-1}"
  local end
  end=$(date +%s)
  s2_observe_drain "$S2_START" "$end" probe_clock 1
  s2_record_coverage "$S2_START" "$end" 1
  s2_write_attribution "$S2_START" "$end"
  echo "PROBE_IW=$IW_LOG"
}

# Watch a data root and the sentinel, then replace the root. The sentinel
# barrier can still succeed; the score must not become a valid zero.
s2_probe_root_move() {
  OUT="$1"
  BASE="$2"
  RUN_ID="$3"
  local root="$4"
  S2_CARD_FILE="$BASE/s2-card-${RUN_ID}.txt"
  S2_ATTR_FILE="$BASE/s2-attribution-${RUN_ID}.txt"
  SUMMARY="$BASE/summary-${RUN_ID}.txt"
  S2_SENTINEL_DIR="${S2_SENTINEL_DIR:-$BASE/sentinel-${RUN_ID}}"
  S2_WATCH_TIMEOUT="${S2_WATCH_TIMEOUT:-20}"
  trap s2_probe_cleanup EXIT
  mkdir -p -- "$BASE"
  sudo mkdir -p -- "$OUT"
  sudo chown root:root -- "$OUT"
  sudo chmod 700 -- "$OUT"
  s2_begin_watcher "$OUT/S2-iw-${RUN_ID}.log" "$S2_SENTINEL_DIR" "$root"
  mv -- "$root" "$root.moved"
  mkdir -p -- "$root"
  S2_START=$(date +%s)
  local end
  end=$(date +%s)
  s2_observe_drain "$S2_START" "$end" probe_clock 1
  s2_record_coverage "$S2_START" "$end" 1
  s2_write_attribution "$S2_START" "$end"
  echo "PROBE_ROOT_MOVED=$root"
}

# --- Monitor process-tree helpers (tracked PIDs only; no broad pkill) ---
# Only kill_tree_bounded calls this. That function is itself only reached
# from the EXIT traps, which ShellCheck 0.11 does not count (SC2329).
# shellcheck disable=SC2329
list_descendants() {
  # Recursively list child PIDs of $1 via /proc (best-effort)
  local root="$1" q=("$1") seen=() child
  while [ ${#q[@]} -gt 0 ]; do
    local cur="${q[0]}"; q=("${q[@]:1}")
    local skip=0 s
    for s in "${seen[@]+"${seen[@]}"}"; do
      [ "$s" = "$cur" ] && skip=1 && break
    done
    [ "$skip" -eq 1 ] && continue
    seen+=("$cur")
    if [ -r "/proc/$cur/task/$cur/children" ]; then
      # PIDs are integers; the original walker splits this children list on whitespace.
      # shellcheck disable=SC2013
      for child in $(cat "/proc/$cur/task/$cur/children" 2>/dev/null); do
        q+=("$child")
      done
    else
      for child in $(ps -o pid= --ppid "$cur" 2>/dev/null || true); do
        q+=("$child")
      done
    fi
  done
  # emit all except the root first? include root for kill list
  printf '%s\n' "${seen[@]}"
}

# Only cleanup_monitors calls this, and that runs from the EXIT traps above.
# shellcheck disable=SC2329
kill_tree_bounded() {
  # SIGTERM then SIGKILL after deadline; only PIDs in the tree of $1
  local root="$1"
  local deadline_s="${2:-8}"
  local pids
  pids=$(list_descendants "$root" | awk 'NF' | sort -u)
  [ -z "$pids" ] && return 0
  echo "cleanup TERM tree root=$root pids=$(echo "$pids" | tr '\n' ' ')"
  # TERM leaves first (reverse numeric = children before parents roughly)
  local p
  for p in $(echo "$pids" | sort -nr); do
    sudo kill -TERM "$p" 2>/dev/null || kill -TERM "$p" 2>/dev/null || true
  done
  local waited=0
  while [ "$waited" -lt "$deadline_s" ]; do
    local alive=0
    for p in $pids; do
      if kill -0 "$p" 2>/dev/null || sudo kill -0 "$p" 2>/dev/null; then
        alive=1
        break
      fi
    done
    [ "$alive" -eq 0 ] && break
    sleep 1
    waited=$((waited + 1))
  done
  for p in $(echo "$pids" | sort -nr); do
    if kill -0 "$p" 2>/dev/null || sudo kill -0 "$p" 2>/dev/null; then
      echo "cleanup KILL pid=$p"
      sudo kill -KILL "$p" 2>/dev/null || kill -KILL "$p" 2>/dev/null || true
    fi
  done
}

wait_pids_bounded() {
  # Wait ONLY listed PIDs; never bare wait; never wait for TEE_PID.
  local deadline_s="${1:-10}"; shift
  local pid
  for pid in "$@"; do
    [ -z "$pid" ] && continue
    if [ "$pid" = "${TEE_PID:-}" ]; then
      echo "skip wait for tee_pid=$pid"
      continue
    fi
    local waited=0
    while kill -0 "$pid" 2>/dev/null; do
      if [ "$waited" -ge "$deadline_s" ]; then
        echo "wait_pids_bounded: timeout pid=$pid after ${deadline_s}s (leaving; not bare-waiting)"
        break
      fi
      sleep 1
      waited=$((waited + 1))
    done
    # wait on a live child blocks until it exits. Reap only after it is gone.
    if kill -0 "$pid" 2>/dev/null; then
      echo "WAIT_TIMEOUT pid=$pid still_alive=1"
      continue
    fi
    wait "$pid" 2>/dev/null || true
  done
}

# Called from s2_on_exit and s2_probe_cleanup. Both are EXIT traps.
# shellcheck disable=SC2329
cleanup_monitors() {
  local pid
  echo "=== cleanup_monitors count=${#MON_PIDS[@]} $(date -Is) ==="
  for pid in "${MON_PIDS[@]+"${MON_PIDS[@]}"}"; do
    kill_tree_bounded "$pid" 8
  done
  wait_pids_bounded 5 "${MON_PIDS[@]+"${MON_PIDS[@]}"}"
  MON_PIDS=()
}

start_monitor() {
  # Usage: start_monitor <label> <command string run under sudo bash -c>
  local label="$1"
  local cmd="$2"
  # shellcheck disable=SC2024
  sudo bash -c "$cmd" &
  local pid=$!
  MON_PIDS+=("$pid")
  echo "monitor start label=$label pid=$pid"
}

case "${1:-}" in
  --preserve)
    preserve_prior_evidence "$2" "$3" "${4:-}"
    exit $?
    ;;
  --attribute)
    attribute_s2_wal "$2" "$3" "$4"
    exit $?
    ;;
  --card)
    s2_card_verdict "$2" "$3"
    exit $?
    ;;
  --complete)
    BASE="$2"
    RUN_ID="$3"
    SUMMARY="$BASE/summary-${RUN_ID}.txt"
    S2_CARD_FILE="$BASE/s2-card-${RUN_ID}.txt"
    S2_ATTR_FILE="$BASE/s2-attribution-${RUN_ID}.txt"
    write_s2_only_completion "$4" "${5:-0}"
    exit $?
    ;;
  --exit-incomplete)
    BASE="$2"
    RUN_ID="$3"
    SUMMARY="$BASE/summary-${RUN_ID}.txt"
    S2_CARD_FILE="$BASE/s2-card-${RUN_ID}.txt"
    S2_ATTR_FILE="$BASE/s2-attribution-${RUN_ID}.txt"
    trap s2_on_exit EXIT
    exit "${4:-7}"
    ;;
  --probe-sentinel)
    s2_probe_sentinel "$2" "$3" "$4"
    exit $?
    ;;
  --check-offcard)
    s2_require_offcard "$2"
    exit $?
    ;;
  --check-under-base)
    s2_require_under_base "$2" "$3"
    exit $?
    ;;
  --bracket)
    s2_counter_bracket "$2" "$3" "$4" "$5" "$6" "$7"
    exit $?
    ;;
  --probe-root-move)
    s2_probe_root_move "$2" "$3" "$4" "$5"
    exit $?
    ;;
  --capture)
    OUT="$2"
    s2_capture "$3"
    exit $?
    ;;
  --reap-self)
    set -m
    bash -c 'trap "" TERM; sleep 30' &
    pid=$!
    wait_pids_bounded 1 "$pid"
    if kill -0 "$pid" 2>/dev/null; then
      echo "REAP_CHILD_STILL_ALIVE pid=$pid"
      kill -KILL -- "-$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null || true
    fi
    wait "$pid" 2>/dev/null || true
    exit 0
    ;;
  --*)
    echo "unknown command $1" >&2
    exit 2
    ;;
esac

mkdir -p "$BASE" "$PHOTOS"
cd "$REPO"

# --- Logging without hanging bare wait on tee ---
# Process-substitution tee is recorded and EXCLUDED from all waits.
: > "$LOG"
exec > >(tee -a "$LOG") 2>&1
TEE_PID=$!
# Shell children we own for monitor lifecycle (never includes TEE_PID)
MON_PIDS=()

echo "=== start $(date -Is) RUN_ID=$RUN_ID HEAD=$(git rev-parse --short HEAD) mode=$(cat /boot/firmware/memoryframe-mode.txt) tee_pid=$TEE_PID ==="

# Exact target SHA + dev-only
HEAD_NOW=$(git rev-parse --short HEAD)
test "$HEAD_NOW" = "$TARGET_HEAD" || { echo "STOP: HEAD=$HEAD_NOW want=$TARGET_HEAD"; exit 2; }
test "$(cat /boot/firmware/memoryframe-mode.txt)" = "dev" || { echo "STOP: mode not dev"; exit 2; }
test -f "$CAP"

trap s2_on_exit EXIT

# Archive and directory setup happen before S2-t0, outside the scored window.
s2_prepare_score_dirs
preserve_prior_evidence "$OUT" "$RUN_ID"

# --- CSRF helper ---
cat > "$HELPER" << 'PY'
import http.cookiejar, sys
path = sys.argv[1]
name = sys.argv[2] if len(sys.argv) > 2 else "mf_csrf"
cj = http.cookiejar.MozillaCookieJar(path)
cj.load(ignore_discard=True, ignore_expires=True)
for c in cj:
    if c.name == name:
        print(c.value)
        raise SystemExit(0)
raise SystemExit("cookie %s not found in %s" % (name, path))
PY

# --- Pairing / CSRF ---
rm -f "$JAR"
curl_q -c "$JAR" -b "$JAR" -o /dev/null http://127.0.0.1/pair || true
CSRF=$(python3 "$HELPER" "$JAR")
echo "csrf_len=${#CSRF}"
CODE=$(curl_q -X POST http://127.0.0.1/pairing/start | python3 -c 'import sys,json; print(json.load(sys.stdin)["round"]["code"])')
echo "pair_code=$CODE"
PAIR_RESP=$(curl_q -c "$JAR" -b "$JAR" -w "\nHTTP:%{http_code}" -X POST \
  -H 'Content-Type: application/json' -H 'Origin: http://127.0.0.1' -H "X-CSRF-Token: $CSRF" \
  -d "{\"code\":\"$CODE\"}" http://127.0.0.1/pair || true)
echo "$PAIR_RESP" | tail -3
CSRF=$(python3 "$HELPER" "$JAR")
ADMIN=$(http_code -c "$JAR" -b "$JAR" http://127.0.0.1/admin/)
echo "admin=$ADMIN"
test "$ADMIN" = "200"

# Journal baseline (privileged)
J0_RAW=$(sudo journalctl --disk-usage 2>/dev/null || journalctl --disk-usage 2>/dev/null || echo "unavailable")
echo "journal_t0=$J0_RAW"
J0_BYTES=$(python3 - <<PY
import re,sys
s='''$J0_RAW'''
# journalctl --disk-usage examples: "Archived and active journals take up 48.0M in the file system."
m=re.search(r'([0-9]+(?:\.[0-9]+)?)\s*([KMGTP])', s, re.I)
if not m:
    print(0); raise SystemExit
val=float(m.group(1)); unit=m.group(2).upper()
mult={'K':1024,'M':1024**2,'G':1024**3,'T':1024**4,'P':1024**5}[unit]
print(int(val*mult))
PY
)

echo "=== warmup 10m $(date -Is) ==="
sleep 600

IW_LOG="$OUT/S2-iw-${RUN_ID}.log"
PIDSTAT_LOG="$OUT/S2-pidstat-${RUN_ID}.txt"
IOTOP_LOG="$OUT/S2-iotop-${RUN_ID}.txt"
IOSTAT_LOG="$OUT/S2-iostat-${RUN_ID}.txt"
S2_SENTINEL_DIR="${S2_SENTINEL_DIR:-$BASE/sentinel-${RUN_ID}}"
echo "evidence_pidstat=$PIDSTAT_LOG evidence_iotop=$IOTOP_LOG evidence_iostat=$IOSTAT_LOG"

for fresh in "$PIDSTAT_LOG" "$IOTOP_LOG" "$IOSTAT_LOG"; do
  if sudo test -e "$fresh" || sudo test -L "$fresh"; then
    echo "STOP: refusing to overwrite $fresh"
    exit 2
  fi
done

# Watcher must be up before S2-t0 so the card window is covered. pidstat/iotop/iostat
# keep their original sample counts; their paths are run-scoped.
s2_begin_watcher "$IW_LOG" "$S2_SENTINEL_DIR" \
  /var/lib/memoryframe /home/memoryframe/.cache /var/log /etc /var/lib/systemd
start_monitor pidstat "pidstat -d 60 30 > $(printf '%q' "$PIDSTAT_LOG") 2>&1"
start_monitor iotop "iotop -obPa -d 60 -n 30 -qqq > $(printf '%q' "$IOTOP_LOG") 2>&1"
start_monitor iostat "iostat -dkx mmcblk0 60 30 > $(printf '%q' "$IOSTAT_LOG") 2>&1"

echo "=== S2-t0 $(date -Is) ==="
S2_PRE_T0=$(date +%s)
s2_capture "S2-t0-${RUN_TAG}"
S2_POST_T0=$(date +%s)
S2_WINDOW_OPEN=1
S2_START=$(date +%s)
MAX=$(curl_q http://127.0.0.1/brightness | python3 -c 'import sys,json; print(json.load(sys.stdin).get("maximum",255))' || echo 255)
MIN=$(curl_q http://127.0.0.1/brightness | python3 -c 'import sys,json; print(json.load(sys.stdin).get("minimum",40))' || echo 40)
echo "brightness min=$MIN max=$MAX"

ADMIN_OK=0; ADMIN_FAIL=0; BR_OK=0; BR_FAIL=0; STATUS_OK=0; STATUS_FAIL=0
i=0
while [ $(($(date +%s)-S2_START)) -lt 1800 ]; do
  # The monitor pid is owned by this user, so a plain kill -0 is enough.
  # The 30-minute loop must not append an auth-log line every sample.
  if [ "${WATCHER_HEALTH:-}" = "healthy" ] && ! kill -0 "${IW_PID:-}" 2>/dev/null; then
    WATCHER_HEALTH=early_exit
    echo "WATCHER_EARLY_EXIT pid=${IW_PID:-} epoch=$(date +%s)"
  fi
  AC=$(curl_q -c "$JAR" -b "$JAR" -o /dev/null -w "%{http_code}" http://127.0.0.1/admin/ || echo 000)
  if [ "$AC" = "200" ]; then ADMIN_OK=$((ADMIN_OK+1)); else ADMIN_FAIL=$((ADMIN_FAIL+1)); fi
  SC=$(curl_q -c "$JAR" -b "$JAR" -o /dev/null -w "%{http_code}" http://127.0.0.1/api/v1/status || echo 000)
  if [ "$SC" = "200" ]; then STATUS_OK=$((STATUS_OK+1)); else STATUS_FAIL=$((STATUS_FAIL+1)); fi
  VAL=$(( MIN + (i % 20) * (MAX - MIN) / 20 ))
  BC=$(curl_q -o /dev/null -w "%{http_code}" -X POST -H 'Content-Type: application/json' \
    -d "{\"value\":$VAL}" http://127.0.0.1/brightness || echo 000)
  if [ "$BC" = "200" ] || [ "$BC" = "204" ]; then BR_OK=$((BR_OK+1)); else BR_FAIL=$((BR_FAIL+1)); fi
  VAL2=$(( MAX - (i % 20) * (MAX - MIN) / 20 ))
  BC2=$(curl_q -o /dev/null -w "%{http_code}" -X POST -H 'Content-Type: application/json' \
    -d "{\"value\":$VAL2}" http://127.0.0.1/brightness || echo 000)
  if [ "$BC2" = "200" ] || [ "$BC2" = "204" ]; then BR_OK=$((BR_OK+1)); else BR_FAIL=$((BR_FAIL+1)); fi
  i=$((i+1)); sleep 2
done

echo "=== S2-t1 $(date -Is) loops=$i admin_ok=$ADMIN_OK admin_fail=$ADMIN_FAIL br_ok=$BR_OK br_fail=$BR_FAIL status_ok=$STATUS_OK status_fail=$STATUS_FAIL ==="
# Assert meaningful successful workloads (not silent all-fail). Exit still writes
# an incomplete S2-only completion via the EXIT trap. Monitors stay up through t1.
test "$ADMIN_OK" -ge 10 || { echo "STOP: admin successes $ADMIN_OK < 10"; exit 3; }
test "$BR_OK" -ge 20 || { echo "STOP: brightness successes $BR_OK < 20"; exit 3; }
S2_PRE_T1=$(date +%s)
s2_capture "S2-t1-${RUN_TAG}"
S2_POST_T1=$(date +%s)
S2_WINDOW_OPEN=0
S2_T0_EPOCH=$(s2_snapshot_epoch "$OUT/S2-t0-${RUN_TAG}.txt" || true)
S2_T1_EPOCH=$(s2_snapshot_epoch "$OUT/S2-t1-${RUN_TAG}.txt" || true)
S2_U0=$(sudo python3 - "$OUT/S2-t0-${RUN_TAG}.txt" <<'PY' || true
import re, sys
text = open(sys.argv[1], encoding="utf-8", errors="replace").read()
m = re.search(r"^uptime_s=([0-9.]+)$", text, re.M)
print(m.group(1) if m else "")
PY
)
S2_U1=$(sudo python3 - "$OUT/S2-t1-${RUN_TAG}.txt" <<'PY' || true
import re, sys
text = open(sys.argv[1], encoding="utf-8", errors="replace").read()
m = re.search(r"^uptime_s=([0-9.]+)$", text, re.M)
print(m.group(1) if m else "")
PY
)
if [ -n "${S2_T0_EPOCH:-}" ] && [ -n "${S2_T1_EPOCH:-}" ] && [ -n "${S2_U0:-}" ] && [ -n "${S2_U1:-}" ]; then
  bracket=$(s2_counter_bracket "$S2_T0_EPOCH" "$S2_U0" "$S2_POST_T0" "$S2_T1_EPOCH" "$S2_U1" "$S2_POST_T1" "$S2_PRE_T0" "$S2_PRE_T1")
  S2_T0_EPOCH=$(printf '%s\n' "$bracket" | sed -n '1p')
  S2_T1_EPOCH=$(printf '%s\n' "$bracket" | sed -n '2p')
  S2_CLOCK_OK=$(printf '%s\n' "$bracket" | sed -n '3p')
  S2_BOUND_SOURCE=counter_bracket
else
  S2_BOUND_SOURCE=unparsed
  S2_T0_EPOCH=0
  S2_T1_EPOCH=0
  S2_CLOCK_OK=0
fi

# Drain the watcher after the card sample. Score only a stable prefix.
# Bounds are the post-t0 return and the t1 date, which bracket the counters.
s2_observe_drain "$S2_T0_EPOCH" "$S2_T1_EPOCH" "$S2_BOUND_SOURCE" "$S2_CLOCK_OK"
s2_record_coverage "$S2_T0_EPOCH" "$S2_T1_EPOCH" 1
s2_write_attribution "$S2_T0_EPOCH" "$S2_T1_EPOCH"

set +e
s2_card_verdict "$OUT" "$S2_CARD_FILE" "S2-t0-${RUN_TAG}" "S2-t1-${RUN_TAG}"
card_rc=$?
set -e

if [ "$card_rc" -ne 0 ]; then
  write_s2_only_completion S2_ONLY_INCOMPLETE "$card_rc"
  echo "=== S2-only incomplete: card snapshots unusable; exiting before S3 RUN_ID=$RUN_ID ==="
  exit "$card_rc"
fi
write_s2_only_completion S2_ONLY_COMPLETE 0
echo "=== S2-only complete: exiting before S3 photo generation RUN_ID=$RUN_ID ==="
exit 0

echo "=== gen S3 photos into $PHOTOS $(date -Is) ==="
# High-entropy ~4MB JPEGs (do not reuse prior run photos). Retained from the
# external S2-only baseline; the exit above is the S2-only stop.
python3 - <<PY
import os, secrets
from PIL import Image
out = os.environ.get("PHOTOS_DIR") or "$PHOTOS"
os.makedirs(out, exist_ok=True)
TARGET = 4_000_000
MIN_OK = 3_800_000
MAX_OK = 4_500_000
for i in range(20):
    # Start large enough that JPEG of noise lands near 4MB
    w, h = 2200, 2200
    path = f"{out}/s3-{i:02d}.jpg"
    # iterate dimensions / quality until in band
    for attempt in range(8):
        raw = secrets.token_bytes(w * h * 3)
        img = Image.frombytes("RGB", (w, h), raw)
        q = 92
        img.save(path, "JPEG", quality=q, optimize=False, subsampling=0)
        sz = os.path.getsize(path)
        if sz < MIN_OK:
            # increase resolution
            w = int(w * 1.12); h = int(h * 1.12)
            continue
        if sz > MAX_OK:
            w = int(w * 0.92); h = int(h * 0.92)
            continue
        break
    else:
        # last resort: pad with APP markers is invalid for "genuine" — regenerate once more at fixed size
        w, h = 2400, 2400
        raw = secrets.token_bytes(w * h * 3)
        img = Image.frombytes("RGB", (w, h), raw)
        img.save(path, "JPEG", quality=95, optimize=False, subsampling=0)
        sz = os.path.getsize(path)
        if sz < MIN_OK:
            # Append entropy as JPEG comment is still a valid JPEG; prefer resize loop
            while sz < MIN_OK and w < 4000:
                w += 100; h += 100
                raw = secrets.token_bytes(w * h * 3)
                img = Image.frombytes("RGB", (w, h), raw)
                img.save(path, "JPEG", quality=95, optimize=False, subsampling=0)
                sz = os.path.getsize(path)
    sz = os.path.getsize(path)
    if sz < MIN_OK:
        raise SystemExit(f"photo {path} too small: {sz}")
    print(i, sz, w, h)
print("photos_ok", out)
PY

# Size gate before upload
python3 - <<PY
import os, glob, sys
photos=sorted(glob.glob("$PHOTOS/s3-*.jpg"))
assert len(photos)==20, photos
for p in photos:
    sz=os.path.getsize(p)
    assert 3_800_000 <= sz <= 5_000_000, (p, sz)
print("size_gate_pass", len(photos))
PY

curl_q -c "$JAR" -b "$JAR" -o /dev/null http://127.0.0.1/admin/
CSRF=$(python3 "$HELPER" "$JAR")
echo "=== S3-t0 $(date -Is) ==="
sudo "$CAP" S3-t0
UP=0; OK=0; FAIL=0
for f in "$PHOTOS"/s3-*.jpg; do
  SZ=$(stat -c%s "$f")
  RESP=$(curl_q -c "$JAR" -b "$JAR" -w "\nHTTP:%{http_code}" -X POST \
    -H 'Origin: http://127.0.0.1' -H "X-CSRF-Token: $CSRF" \
    -F "file=@${f};type=image/jpeg" http://127.0.0.1/api/v1/media || true)
  echo "$RESP" | tail -2
  CODE=$(echo "$RESP" | sed -n 's/.*HTTP:\([0-9]*\).*/\1/p' | tail -1)
  if [ "$CODE" = 201 ] || [ "$CODE" = 200 ]; then OK=$((OK+1)); UP=$((UP+SZ)); else FAIL=$((FAIL+1)); fi
  if [ $((OK % 5)) -eq 0 ] && [ "$OK" -gt 0 ]; then
    curl_q -c "$JAR" -b "$JAR" -o /dev/null http://127.0.0.1/admin/
    CSRF=$(python3 "$HELPER" "$JAR")
  fi
done
echo "=== S3-t1 $(date -Is) uploaded=$UP ok=$OK fail=$FAIL ==="
test "$OK" -ge 18 || { echo "STOP: S3 upload ok=$OK < 18"; exit 4; }
test "$UP" -ge $((18 * 3800000)) || { echo "STOP: uploaded bytes too low UP=$UP"; exit 4; }
# Short settle so card_bytes_written reflects uploads before S3-t1 capture.
# sync only (no drop_caches — not used elsewhere in this harness; keep QA impact small).
echo "=== S3 settle before t1: sync + 5s $(date -Is) ==="
sync
sleep 5
sudo "$CAP" S3-t1

sudo python3 - <<PY
import re, pathlib, sys
out=pathlib.Path('/root/sd-wear')
def card(label):
    t=(out/f'{label}.txt').read_text()
    m=re.search(r'card_bytes_written=(\d+)', t)
    if not m:
        raise SystemExit(f'no card_bytes_written in {label}')
    u=re.search(r'uptime_s=([0-9.]+)', t)
    return int(m.group(1)), float(u.group(1) if u else 0)
t0,u0=card('S3-t0'); t1,u1=card('S3-t1')
up=int('$UP')
delta=t1-t0
if delta < 0 or up <= 0:
    raise SystemExit(f'invalid S3 measurement delta={delta} up={up}')
amp=delta/up
print(f'S3 card_delta={delta} uploaded={up} amplification={amp:.3f} limit=4 pass={amp<=4}')
print((out/'S3-t0.txt').read_text()); print('---'); print((out/'S3-t1.txt').read_text())
PY

J1_RAW=$(sudo journalctl --disk-usage 2>/dev/null || journalctl --disk-usage 2>/dev/null || echo "unavailable")
echo "journal_t1=$J1_RAW"
J1_BYTES=$(python3 - <<PY
import re
s='''$J1_RAW'''
m=re.search(r'([0-9]+(?:\.[0-9]+)?)\s*([KMGTP])', s, re.I)
if not m:
    print(0); raise SystemExit
val=float(m.group(1)); unit=m.group(2).upper()
mult={'K':1024,'M':1024**2,'G':1024**3,'T':1024**4,'P':1024**5}[unit]
print(int(val*mult))
PY
)
echo "journal_delta_bytes=$((J1_BYTES - J0_BYTES)) j0=$J0_BYTES j1=$J1_BYTES"
findmnt /run/mfkiosk || true
echo "MemAvailable=$(awk '/MemAvailable/{print $2}' /proc/meminfo)"
findmnt -no OPTIONS / || true

{
  echo "RUN_ID=$RUN_ID"
  echo "HEAD=$HEAD_NOW"
  echo "ADMIN_OK=$ADMIN_OK ADMIN_FAIL=$ADMIN_FAIL BR_OK=$BR_OK BR_FAIL=$BR_FAIL"
  echo "S3_OK=$OK S3_FAIL=$FAIL UPLOADED=$UP"
  echo "JOURNAL_DELTA=$((J1_BYTES - J0_BYTES))"
  echo "PHOTOS=$PHOTOS"
  echo "LOG=$LOG"
} | tee "$SUMMARY"

echo DONE > "$BASE/DONE-${RUN_ID}"
# Keep a stable DONE pointer for collectors without erasing prior DONE-* evidence
ln -sfn "DONE-${RUN_ID}" "$BASE/DONE"
echo "=== finished $(date -Is) ==="
# Intentionally do NOT wait on TEE_PID — process exits; tee flushes on close.
