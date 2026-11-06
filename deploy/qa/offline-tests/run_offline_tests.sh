#!/bin/bash
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
echo "=== offline tests $(date -Is) ==="
FAIL=0
run() {
  echo "--- $1 ---"
  if bash "$HERE/$1"; then echo "OK $1"; else echo "FAILED $1"; FAIL=$((FAIL+1)); fi
}
runpy() {
  echo "--- $1 ---"
  if python3 "$HERE/$1"; then echo "OK $1"; else echo "FAILED $1"; FAIL=$((FAIL+1)); fi
}
run test_monitor_hang_cannot_block_s2t1.sh
runpy test_csrf.py
runpy test_measure.py
runpy test_photosize.py
run test_http_bounds_and_asserts.sh

# Static checks on repaired script
REP="$HERE/../run-s2-s3-repaired.sh"
echo "--- static repaired script ---"
grep -n 'TARGET_HEAD\|6711333' "$REP" | head -5
grep -n 'wait_pids_bounded\|cleanup_monitors\|TEE_PID\|never bare' "$REP" | head -20
# Strip comments for command-level checks
STRIP=$(mktemp)
sed 's/#.*//' "$REP" > "$STRIP"
if grep -E '(^|[[:space:]])wait([[:space:]]*$|[[:space:]]+\|\|)' "$STRIP"; then
  echo "FAILED: bare wait still present"; FAIL=$((FAIL+1))
else
  echo "PASS: no bare wait in repaired script"
fi
if grep -E '(^|[[:space:]])pkill([[:space:]]|$)' "$STRIP"; then
  echo "FAILED: pkill present"; FAIL=$((FAIL+1))
else
  echo "PASS: no pkill in repaired script"
fi
if grep -n 'max-time\|CURL_MAX_TIME' "$REP" >/dev/null; then
  echo "PASS: HTTP time bounds present"
else
  echo "FAILED: missing HTTP bounds"; FAIL=$((FAIL+1))
fi
if grep -n 'sudo python3' "$REP" >/dev/null; then
  echo "PASS: privileged python read of capture dir"
else
  echo "FAILED: missing sudo python3"; FAIL=$((FAIL+1))
fi
if grep -n 'photos-' "$REP" >/dev/null; then
  echo "PASS: run-isolated photos path"
else
  echo "FAILED: photos isolation"; FAIL=$((FAIL+1))
fi
if grep -E '(^|[[:space:]])(reboot|raspi-config)([[:space:]]|$)' "$STRIP" || grep -E 'memoryframe-mode\.txt>' "$STRIP"; then
  echo "FAILED: forbidden mode/reboot mutation"; FAIL=$((FAIL+1))
else
  echo "PASS: no reboot/mode mutation commands"
fi
rm -f "$STRIP"

echo "=== OFFLINE SUMMARY fail=$FAIL ==="
[ "$FAIL" -eq 0 ]
