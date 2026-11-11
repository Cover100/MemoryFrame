# SD card wear audit (#97)

Measure first. This is step 1 of Sage's design on #97. It changes nothing; the fixes come later and only where the numbers are over a threshold.
For Hale (Tester). Take a backup first. The card is `mmcblk0` (set `MEMORYFRAME_SD_DEV` otherwise).

## Setup (once)
`sudo apt-get install -y sysstat iotop inotify-tools`. Record the versions: `dpkg -l sysstat iotop inotify-tools | tail -3`.

## Per scenario
Warm up for 10 minutes first. Then:
1. `sudo deploy/sd-wear-capture.sh S1-t0`
2. While the scenario runs (in separate terminals):
   - `sudo pidstat -d 60 60`
   - `sudo iotop -obPa -d 60 -n 60 -qqq`
   - `sudo iostat -dkx mmcblk0 60 60`
   - `sudo inotifywait -m -r -e close_write,create,delete,moved_to --timefmt %T --format '%T %e %w%f' /var/lib/memoryframe /home/memoryframe/.cache /var/log /etc /var/lib/systemd -o /root/sd-wear/iw.log` (run step 1 first; it creates `/root/sd-wear` as root, mode 0700)
3. `sudo deploy/sd-wear-capture.sh S1-t1`
4. Summaries:
   - `awk '{print $3}' /root/sd-wear/iw.log | sed 's#[^/]*$##' | sort | uniq -c | sort -rn | head -20`
   - `journalctl -b -o json | jq -r '._SYSTEMD_UNIT' | sort | uniq -c | sort -rn | head`

MB/h = (t1 `card_bytes_written` - t0) / 1048576 / hours. Do the same per process. In this document "MB" therefore means MiB (2^20 bytes), as this formula and the S2 harness (`deploy/qa/run-s2-only.sh`, `delta / 1048576`) compute it; decimal MB (10^6 bytes) are given in parentheses where both have been quoted.

| Scenario | What | Duration |
|---|---|---|
| S1 | idle slideshow, no phone | 60 min |
| S2 | one phone on `/admin/`, brightness drags | 30 min |
| S3 | upload 20 photos (~4 MB each); amplification = card bytes written / bytes uploaded | until done |
| S4 | soak; read the card stat and `journalctl --disk-usage` (moved to #140) | 24 h |

**Power-cut check (during and after S3):** `echo b | sudo tee /proc/sysrq-trigger`. Then confirm:
- `sqlite3 /var/lib/memoryframe/memoryframe.db 'PRAGMA integrity_check'` = `ok`
- every listed photo opens
- settings are intact

## Thresholds
All limits are agreed (Reed: S1 on 2026-11-01, the rest on 2026-11-02 as pass criteria for the at-frame session).
- S1: at most 20 MB/h total, no process over 5 MB/h (agreed)
- S2: at most 40 MB/h (agreed)
- S3: at most 4x amplification (agreed)
- Journal: at most 1 MB/h idle, at most 50 MB total (agreed)

## Results
Measured on the Pi (memoryFrame) over Pi Connect. Card bytes come from `/sys/block/mmcblk0/stat`. Evidence is in `/home/box/shared/MemoryFrame-compare/sdwear-97/` on the team box.

Figures are MiB (2^20 B); the agreed limits are read in the same unit, about 4.9% looser than decimal MB; no verdict is near a limit.

| Scenario | Card MB/h before | Top process (MB/h) | Journal growth | Amplification | Card MB/h after | Pass |
|---|---|---|---|---|---|---|
| S1 (idle, 60 min) | 36.85 | after: rpi-connectd 0.71; jbd2 0.098; journald 0; Chromium none | none seen | n/a | 0.36 (2efb3d1, #119) | Yes |
| S2 (phone on `/admin/`, 30 min) | 59.22 (6711333), 60.21 (e864871), 48.39 (7e16c4d) | after: jbd2 ~1.2; app.py ~0.94 (0.98 decimal MB/h; write_bytes 491,520 B / 0.5 h) | 0 (24M to 24M) | n/a | 4.33 (78e2bb1, #133; run 20261111T073515) | Yes |
| S3 (20 uploads) | n/a | n/a | 0 (24M to 24M) | 0.505 (6711333, run 20261106T133615; 20/20 HTTP 201) | n/a | Yes (see note 3) |
| S4 (24 h soak) | | | | n/a | | Not run: moved to #140 |

Notes:
1. S2 runs used the same load: 842 or 843 admin loops with no failures. The 78e2bb1 run had heavier brightness traffic (1,686 changes compared with 168 in the 7e16c4d run).
2. App write_bytes was the same 480 KiB per 30 min in every S2 run (page-dirtying accounting, a lower bound), so it does not distinguish before/after. The evidence for #133 is the card delta at matched load together with zero WAL CLOSE_WRITE events while the WAL grew; the pre-#133 excess was unattributed to process counters (likely per-request open/close metadata commits via jbd2/kworker), and part of the drop is the off-card harness.
3. S3 ran on 6711333, before #133. Its window was short (14:17:21 to 14:17:50), with sync plus 5 s before t1, so amplification may be understated. It is still well under the 4x limit. #133 reduces writes and does not touch the upload path.
4. Journal: the size stayed at 24M through S2 and S3. The effective journald config on the Pi (run 20261111T073515, follow-up at 08:45) has `Storage=volatile` and `SystemMaxUse=50M`, and `journalctl --disk-usage` reports 24M. With volatile storage the journal is kept in RAM under `/run/log/journal`, so it writes nothing to the card, and `SystemMaxUse` has no effect. RAM use is still bounded by journald's default `RuntimeMaxUse`, which is 10% of the `/run` filesystem (capped at 4G). This meets #97's "journal capped" item, because the card limits (1 MB/h idle, 50 MB total) are met with zero card writes. Setting `RuntimeMaxUse` explicitly (for example 32M) would be optional hardening for RAM on a long-running frame, not a #97 blocker. The old `/var/log/journal` (about 17M) is a leftover on the card that is not written to.
5. Still open, moved to #140 when #97 closed: the power-cut check (no loss of photos or settings) and S4.

Attach the `/root/sd-wear/*.txt` snapshots and summaries to #97.
