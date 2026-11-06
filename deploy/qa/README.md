# QA harness — #97 S2/S3 SD-wear measure runner

Throwaway / short-lived QA helpers for Pi measure runs. **Not product code.**

## `run-s2-s3-repaired.sh`

Durable fix for the S2 stall after `S2-t0` (issue #97 harness).

**SHA256:** `f659f992dade776765b77f49727666d667a979294918e66b0bf1da9374ec0662`

### What was wrong (`run-s2-s3-fixed.sh`)

1. **Bare `wait`** after backgrounding pidstat/iotop/iostat/inotify — waits forever on hung monitors.
2. **`exec > >(tee …)` self-wait** — the same bare `wait` also blocks on the process-substitution tee, so the script never reaches `S2-t1`.

### What this script does

- Records every monitor PID in `MON_PIDS`; records `TEE_PID` separately.
- After the S2 loop: `kill_tree_bounded` on tracked monitors only, then `wait_pids_bounded` **only** those PIDs (never bare `wait`, never wait for tee).
- Keeps the CSRF + `admin=200` pairing path; bounded curl; S3 path intact.
- Before `S3-t1` capture: `sync` + 5s settle (logged) so card counters reflect uploads. No `drop_caches` (not used elsewhere in this harness).
- Asserts `HEAD==6711333` and `mode=dev` (override `TARGET_HEAD` only if you know what you are doing).

### Pass / fail — read the numbers (not just exit 0)

**`DONE` / exit 0 is NOT an automatic PASS.** The script finishing only means the harness completed its steps and its internal gates. Assess the printed `pass=` lines against the agreed limits yourself:

- S2: `MB_h=… pass=…` vs limit **≤ 40 MB/h**
- S3: `amplification=… pass=…` vs limit **≤ 4×**

Also note:

- S2’s internal gate is only `ADMIN_OK >= 10` over 30 minutes (~10 successful admin reads as a floor). That is a weak “did anything work” check, not evidence of a full healthy workload.
- Offline `lib_monitor.sh` uses `sleep 0.2` while counting loop iterations as whole seconds, so a “5s” offline deadline is about **5× short** (~1s real). Offline hang tests still prove the no-bare-wait path; do not treat those deadlines as wall-clock seconds.

### Pi usage (Hale)

**Do not `git checkout` the runner branch on the Pi.** That changes `HEAD` and fights the script’s `HEAD==6711333` product-code guard. Leave the device checkout on **main / the 6711333 deploy HEAD**, and install a **copy** of the runner outside the tree (same pattern as Hale’s live run).

```bash
# On the Pi: stay on 6711333 product HEAD
cd ~/Documents/GitHub/MemoryFrame   # or $REPO
git rev-parse --short HEAD          # expect 6711333
test "$(cat /boot/firmware/memoryframe-mode.txt)" = dev

mkdir -p ~/sdwear-s2s3
# Prefer fetch + show (no checkout of the runner branch):
git fetch origin hale/s2s3-runner-6711333
git show origin/hale/s2s3-runner-6711333:deploy/qa/run-s2-s3-repaired.sh   > ~/sdwear-s2s3/run-s2-s3-repaired.sh
chmod +x ~/sdwear-s2s3/run-s2-s3-repaired.sh
sha256sum ~/sdwear-s2s3/run-s2-s3-repaired.sh
# expect f659f992dade776765b77f49727666d667a979294918e66b0bf1da9374ec0662
# (or whatever README/PR head currently lists)

# Or scp the shared twin (same bytes when refreshed):
#   /home/box/shared/MemoryFrame-compare/sdwear-97/run-s2-s3-nohang.sh

bash ~/sdwear-s2s3/run-s2-s3-repaired.sh
```

Shared-folder / scp twin:  
`/home/box/shared/MemoryFrame-compare/sdwear-97/run-s2-s3-nohang.sh`  
(checksums sit beside it; refresh from the PR branch without checking that branch out on the Pi).

### Offline proof

```bash
bash deploy/qa/offline-tests/run_offline_tests.sh
```

See `REPAIRED-PROPOSAL.txt` and `offline-tests/offline-tests-20261106.txt`.
