#!/bin/bash
# Shared monitor lifecycle helpers (mirrors repaired harness; offline-safe).
list_descendants() {
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
  printf '%s\n' "${seen[@]}"
}

kill_tree_bounded() {
  local root="$1"
  local deadline_s="${2:-5}"
  local pids
  pids=$(list_descendants "$root" | awk 'NF' | sort -u)
  [ -z "$pids" ] && return 0
  local p
  for p in $(echo "$pids" | sort -nr); do
    kill -TERM "$p" 2>/dev/null || true
  done
  local waited=0
  while [ "$waited" -lt "$deadline_s" ]; do
    local alive=0
    for p in $pids; do
      if kill -0 "$p" 2>/dev/null; then alive=1; break; fi
    done
    [ "$alive" -eq 0 ] && break
    sleep 0.2
    waited=$((waited + 1))
  done
  for p in $(echo "$pids" | sort -nr); do
    if kill -0 "$p" 2>/dev/null; then
      kill -KILL "$p" 2>/dev/null || true
    fi
  done
}

wait_pids_bounded() {
  local deadline_s="${1:-5}"; shift
  local tee_pid="${TEE_PID:-}"
  local pid
  for pid in "$@"; do
    [ -z "$pid" ] && continue
    if [ -n "$tee_pid" ] && [ "$pid" = "$tee_pid" ]; then
      echo "skip wait tee_pid=$pid"
      continue
    fi
    local waited=0
    while kill -0 "$pid" 2>/dev/null; do
      if [ "$waited" -ge "$deadline_s" ]; then
        echo "wait timeout pid=$pid"
        break
      fi
      sleep 0.2
      waited=$((waited + 1))
    done
    wait "$pid" 2>/dev/null || true
  done
}
