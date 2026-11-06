# QA harness — #97 S2/S3 SD-wear measure runner

Throwaway / short-lived QA helpers for Pi measure runs. **Not product code.**

## `run-s2-s3-repaired.sh`

Durable fix for the S2 stall after `S2-t0` (issue #97 harness).

**SHA256:** `239fcc27785dd7498b490f652ee9acf16d71dc3ccd73e1bf801a49f989422587`

### What was wrong (`run-s2-s3-fixed.sh`)

1. **Bare `wait`** after backgrounding pidstat/iotop/iostat/inotify — waits forever on hung monitors.
2. **`exec > >(tee …)` self-wait** — the same bare `wait` also blocks on the process-substitution tee, so the script never reaches `S2-t1`.

### What this script does

- Records every monitor PID in `MON_PIDS`; records `TEE_PID` separately.
- After the S2 loop: `kill_tree_bounded` on tracked monitors only, then `wait_pids_bounded` **only** those PIDs (never bare `wait`, never wait for tee).
- Keeps the CSRF + `admin=200` pairing path; bounded curl; S3 path intact.
- Asserts `HEAD==6711333` and `mode=dev` (override `TARGET_HEAD` only if you know what you are doing).

### Pi usage (Hale)

```bash
# from MemoryFrame checkout on the Pi
git fetch origin && git checkout hale/s2s3-runner-6711333   # or the PR head
sha256sum deploy/qa/run-s2-s3-repaired.sh
# expect 239fcc27785dd7498b490f652ee9acf16d71dc3ccd73e1bf801a49f989422587
bash deploy/qa/run-s2-s3-repaired.sh
```

Shared-folder / scp twin (same bytes):  
`/home/box/shared/MemoryFrame-compare/sdwear-97/run-s2-s3-nohang.sh`

### Offline proof

```bash
bash deploy/qa/offline-tests/run_offline_tests.sh
```

See `REPAIRED-PROPOSAL.txt` and `offline-tests/offline-tests-20261106.txt`.
