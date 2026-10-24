# SD card wear audit (#97)

Measure first. This is step 1 of Juniper's design on #97. It changes nothing; the fixes come later and only where the numbers are over a threshold.
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
   - `sudo inotifywait -m -r -e close_write,create,delete,moved_to --timefmt %T --format '%T %e %w%f' /var/lib/memoryframe /home/memoryframe/.cache /var/log /etc /var/lib/systemd -o /tmp/iw.log`
3. `sudo deploy/sd-wear-capture.sh S1-t1`
4. Summaries:
   - `awk '{print $3}' /tmp/iw.log | sed 's#[^/]*$##' | sort | uniq -c | sort -rn | head -20`
   - `journalctl -b -o json | jq -r '._SYSTEMD_UNIT' | sort | uniq -c | sort -rn | head`

MB/h = (t1 `card_bytes_written` - t0) / 1048576 / hours. Do the same per process.

| Scenario | What | Duration |
|---|---|---|
| S1 | idle slideshow, no phone | 60 min |
| S2 | one phone on `/admin/`, brightness drags | 30 min |
| S3 | upload 20 photos (~4 MB each); amplification = card bytes written / bytes uploaded | until done |
| S4 | soak; read the card stat and `journalctl --disk-usage` | 24 h |

**Power-cut check (during and after S3):** `echo b | sudo tee /proc/sysrq-trigger`. Then confirm:
- `sqlite3 /var/lib/memoryframe/memoryframe.db 'PRAGMA integrity_check'` = `ok`
- every listed photo opens
- settings are intact

## Thresholds
Rowan agreed the idle limits (S1). The rest are Juniper's proposal, to be agreed.
- S1: at most 20 MB/h total, no process over 5 MB/h (agreed)
- S2: at most 40 MB/h
- S3: at most 4x amplification
- Journal: at most 1 MB/h idle, at most 50 MB total

## Results
| Scenario | Card MB/h before | Top process (MB/h) | Journal growth | Amplification | Card MB/h after | Pass |
|---|---|---|---|---|---|---|
| S1 | | | | n/a | | |
| S2 | | | | n/a | | |
| S3 | | | | | | |
| S4 | | | | n/a | | |

Attach the `/tmp/sd-wear/*.txt` snapshots and summaries to #97.
