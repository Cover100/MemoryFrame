#!/bin/bash
# Prove: hung monitor + bare wait blocks forever; repaired cleanup reaches S2-t1.
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
# shellcheck source=/dev/null
source "$HERE/lib_monitor.sh"
WORKDIR=$(mktemp -d)
trap 'rm -rf "$WORKDIR"; kill $(jobs -p) 2>/dev/null || true' EXIT
cd "$WORKDIR"

PASS=0
FAIL=0
ok() { echo "PASS: $*"; PASS=$((PASS+1)); }
bad() { echo "FAIL: $*"; FAIL=$((FAIL+1)); }

# --- A: demonstrate bare wait hang (subprocess with timeout) ---
cat > bare_wait_demo.sh << 'SH'
#!/bin/bash
set -eu
# Simulate process-substitution tee + hung iotop-like child
sleep 3600 &
HUNG=$!
# Also a "finished" monitor
sleep 0.1 &
DONE=$!
wait $DONE || true
# BUG: bare wait waits for HUNG (and would wait for tee too)
# Bound the demo with outer timeout; expect this to NOT finish quickly.
wait || true
echo "REACHED_AFTER_BARE_WAIT"
SH
chmod +x bare_wait_demo.sh
if timeout 2 bash bare_wait_demo.sh > bare.out 2>&1; then
  if grep -q REACHED_AFTER_BARE_WAIT bare.out; then
    bad "bare wait should not complete within 2s while hung child exists"
  else
    ok "bare wait demo exited oddly without marker (treated as blocked)"
  fi
else
  ec=$?
  if [ "$ec" -eq 124 ]; then
    ok "bare wait blocked (timeout 2s) while hung monitor alive — matches Pi stall"
  else
    bad "bare wait demo unexpected exit $ec"
  fi
fi
# cleanup hung from demo if any leftover in this workdir's process group — already trapped

# --- B: repaired path reaches S2-t1 despite hung monitor ---
REACHED=0
# fake tee pid that must never be waited
sleep 3600 &
TEE_PID=$!
MON_PIDS=()
# hung monitor (simulates iotop)
sleep 3600 &
MON_PIDS+=($!)
# short monitor that exits
sleep 0.3 &
MON_PIDS+=($!)

# simulate S2 loop done
echo "S2-loop-done"

# repaired cleanup
for pid in "${MON_PIDS[@]}"; do
  kill_tree_bounded "$pid" 3
done
wait_pids_bounded 3 "${MON_PIDS[@]}"
# Explicitly do NOT bare wait; do NOT wait TEE_PID
echo "=== S2-t1 ==="
REACHED=1

if [ "$REACHED" -eq 1 ]; then
  ok "repaired cleanup reached S2-t1 despite hung monitor + live tee"
else
  bad "did not reach S2-t1"
fi

# tee still alive proves we did not wait/kill unrelated tee via bare wait
if kill -0 "$TEE_PID" 2>/dev/null; then
  ok "tee pid still alive (not waited/killed by monitor cleanup)"
  kill -KILL "$TEE_PID" 2>/dev/null || true
else
  bad "tee pid was reaped/killed unexpectedly"
fi

# hung monitors should be dead
alive=0
for pid in "${MON_PIDS[@]}"; do
  if kill -0 "$pid" 2>/dev/null; then alive=1; fi
done
if [ "$alive" -eq 0 ]; then
  ok "tracked monitor PIDs terminated by bounded tree cleanup"
else
  bad "some monitored PIDs still alive"
fi

# --- C: cleanup does not kill unrelated process ---
sleep 3600 &
UNRELATED=$!
sleep 3600 &
TRACKED=$!
kill_tree_bounded "$TRACKED" 3
wait_pids_bounded 2 "$TRACKED"
if kill -0 "$UNRELATED" 2>/dev/null; then
  ok "unrelated process survived targeted cleanup (no broad pkill)"
  kill -KILL "$UNRELATED" 2>/dev/null || true
else
  bad "unrelated process was killed — cleanup too broad"
fi

echo "RESULT pass=$PASS fail=$FAIL"
[ "$FAIL" -eq 0 ]
