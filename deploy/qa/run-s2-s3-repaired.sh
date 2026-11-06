#!/bin/bash
# Hale remote #97 S2+S3 QA harness (repaired) — Memory Frame HEAD 6711333, dev mode only.
# Fixes vs run-s2-s3-fixed.sh:
#   - Track every monitor PID; bounded tree cleanup; NEVER bare wait / NEVER wait for tee.
#   - Bounded HTTP (connect/max-time); assert meaningful admin/brightness success rates.
#   - Privileged reads of /root/sd-wear; journal + card delta measurement.
#   - Guaranteed ~4MB x20 JPEG generation (high-entropy).
#   - Run-isolated photos dir so restarts do not mix prior/generated photos.
#   - Preserve existing /root/sd-wear evidence (timestamped archive, no delete of user media).
#   - Exact HEAD=6711333 + mode=dev assertions; no reboot/mode/deploy/reset.
# Safe cleanup: only kill tracked monitor process trees (no broad pkill).
set -eu

REPO="${REPO:-$HOME/Documents/GitHub/MemoryFrame}"
OUT="${OUT:-/root/sd-wear}"
BASE="${BASE:-$HOME/sdwear-s2s3}"
TARGET_HEAD="${TARGET_HEAD:-6711333}"
RUN_ID="${RUN_ID:-$(date +%Y%m%dT%H%M%S%z)}"
JAR="$BASE/cookies.txt"
LOG="$BASE/run-${RUN_ID}.log"
PHOTOS="$BASE/photos-${RUN_ID}"
CAP="$REPO/deploy/sd-wear-capture.sh"
HELPER="$BASE/csrf-${RUN_ID}.py"
SUMMARY="$BASE/summary-${RUN_ID}.txt"

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

# Preserve prior capture evidence under OUT (archive, do not delete user media)
if sudo test -d "$OUT"; then
  ARCHIVE_DIR="${OUT}/archive-${RUN_ID}"
  sudo mkdir -p "$ARCHIVE_DIR"
  # Move prior S2/S3 labeled captures aside if present (leave unrelated media alone)
  for pat in S2-t0 S2-t1 S3-t0 S3-t1 S2-pidstat S2-iotop S2-iostat S2-iw; do
    for f in "$OUT"/${pat}*; do
      # glob may be literal if empty — check with sudo test
      if sudo test -e "$f" 2>/dev/null; then
        sudo mv "$f" "$ARCHIVE_DIR/" 2>/dev/null || true
      fi
    done
  done
  echo "preserved prior OUT labels under $ARCHIVE_DIR (best-effort)"
fi
sudo mkdir -p "$OUT"

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

# --- Monitor process-tree helpers (tracked PIDs only; no broad pkill) ---
list_descendants() {
  # Recursively list child PIDs of $1 via /proc (best-effort)
  local root="$1" q=("$1") seen=() child
  while [ ${#q[@]} -gt 0 ]; do
    local cur="${q[0]}"; q=("${q[@]:1}")
    local skip=0 s
    for s in "${seen[@]+"${seen[@]}"}"; do
      [ "$s" = "$cur" ] && skip=1 && break
    done
    [ $skip -eq 1 ] && continue
    seen+=("$cur")
    if [ -r "/proc/$cur/task/$cur/children" ]; then
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

kill_tree_bounded() {
  # SIGTERM then SIGKILL after deadline; only PIDs in the tree of $1
  local root="$1"
  local deadline_s="${2:-8}"
  local pids
  pids=$(list_descendants "$root" | awk 'NF' | sort -u)
  [ -z "$pids" ] && return 0
  echo "cleanup TERM tree root=$root pids=$(echo $pids | tr '\n' ' ')"
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
    # Reap if already exited (non-blocking if gone: wait returns immediately / errors)
    wait "$pid" 2>/dev/null || true
  done
}

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

echo "=== S2-t0 $(date -Is) ==="
sudo "$CAP" S2-t0
S2_START=$(date +%s)
MAX=$(curl_q http://127.0.0.1/brightness | python3 -c 'import sys,json; print(json.load(sys.stdin).get("maximum",255))' || echo 255)
MIN=$(curl_q http://127.0.0.1/brightness | python3 -c 'import sys,json; print(json.load(sys.stdin).get("minimum",40))' || echo 40)
echo "brightness min=$MIN max=$MAX"

# Four monitors — EVERY PID tracked
start_monitor pidstat "pidstat -d 60 30 > $OUT/S2-pidstat.txt 2>&1"
start_monitor iotop "iotop -obPa -d 60 -n 30 -qqq > $OUT/S2-iotop.txt 2>&1"
start_monitor iostat "iostat -dkx mmcblk0 60 30 > $OUT/S2-iostat.txt 2>&1"
start_monitor inotifywait "inotifywait -m -r -e close_write,create,delete,moved_to --timefmt %T --format '%T %e %w%f' /var/lib/memoryframe /home/memoryframe/.cache /var/log /etc /var/lib/systemd -o $OUT/S2-iw.log"

ADMIN_OK=0; ADMIN_FAIL=0; BR_OK=0; BR_FAIL=0; STATUS_OK=0; STATUS_FAIL=0
i=0
while [ $(($(date +%s)-S2_START)) -lt 1800 ]; do
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

# CRITICAL: bounded cleanup of tracked monitors ONLY — never bare wait / never wait tee
cleanup_monitors

echo "=== S2-t1 $(date -Is) loops=$i admin_ok=$ADMIN_OK admin_fail=$ADMIN_FAIL br_ok=$BR_OK br_fail=$BR_FAIL status_ok=$STATUS_OK status_fail=$STATUS_FAIL ==="
# Assert meaningful successful workloads (not silent all-fail)
test "$ADMIN_OK" -ge 10 || { echo "STOP: admin successes $ADMIN_OK < 10"; exit 3; }
test "$BR_OK" -ge 20 || { echo "STOP: brightness successes $BR_OK < 20"; exit 3; }
sudo "$CAP" S2-t1

# Privileged Python read of /root/sd-wear
sudo python3 - <<'PY'
import re, pathlib, sys
out=pathlib.Path('/root/sd-wear')
def card(label):
    p=out/f'{label}.txt'
    if not p.exists():
        raise SystemExit(f'missing {p}')
    t=p.read_text()
    m=re.search(r'card_bytes_written=(\d+)', t)
    if not m:
        raise SystemExit(f'no card_bytes_written in {p}')
    d=re.search(r'date=(.+)', t)
    u=re.search(r'uptime_s=([0-9.]+)', t)
    return int(m.group(1)), (d.group(1).strip() if d else ''), float(u.group(1) if u else 0)
t0,d0,u0=card('S2-t0'); t1,d1,u1=card('S2-t1')
delta=t1-t0
if delta < 0:
    raise SystemExit(f'invalid card delta {delta}')
hours=max((u1-u0)/3600.0, 1e-9)
mb_h=(delta)/1048576.0/hours
print(f'S2 card_bytes delta={delta} hours={hours:.4f} MB_h={mb_h:.2f} limit=40 pass={mb_h<=40}')
print(f'S2 dates {d0} -> {d1}')
print((out/'S2-t0.txt').read_text()); print('---'); print((out/'S2-t1.txt').read_text())
PY

echo "=== gen S3 photos into $PHOTOS $(date -Is) ==="
# High-entropy ~4MB JPEGs (do not reuse prior run photos)
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
